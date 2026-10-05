"""Abstract values used by the bounded interpreter."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tree_sitter import Node


@dataclass(frozen=True)
class Unknown:
    label: str = "dynamic"

    def __str__(self) -> str:
        return "{" + self.label + "}"


@dataclass
class Function:
    node: Node
    env: dict[str, Any]
    module: str
    name: str


@dataclass
class Client:
    framework: str
    base: str = ""
    headers: dict[str, Any] = field(default_factory=dict)
    member: str = ""
    config: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.config.setdefault("headers", self.headers)


@dataclass
class Bound:
    client: Client
    member: str


def symbolic(value: Any) -> str:
    if isinstance(value, (str, int, float, Unknown)):
        return str(value)
    return "{dynamic}"


def serializable(value: Any, seen: set[int] | None = None) -> Any:
    seen = set() if seen is None else seen
    if isinstance(value, Unknown):
        return str(value)
    if isinstance(value, (dict, list, Alternatives)):
        if id(value) in seen:
            return "{cyclic}"
        seen.add(id(value))
        try:
            if isinstance(value, dict):
                return {k: serializable(v, seen) for k, v in value.items()}
            items = value.values if isinstance(value, Alternatives) else value
            return [serializable(v, seen) for v in items]
        finally:
            seen.remove(id(value))
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    return None


@dataclass
class Alternatives:
    """A bounded union of possible values at a control-flow join."""

    values: list[Any]


def alternatives(*values: Any) -> Any:
    flattened: list[Any] = []
    for value in values:
        for item in value.values if isinstance(value, Alternatives) else [value]:
            if not any(
                item is existing
                or (
                    isinstance(item, (str, int, float, type(None), Unknown))
                    and type(item) is type(existing)
                    and item == existing
                )
                for existing in flattened
            ):
                flattened.append(item)
    if len(flattened) > 16:
        return Alternatives([*flattened[:16], Unknown("branch-limit")])
    return flattened[0] if len(flattened) == 1 else Alternatives(flattened)


class Scope(dict[str, Any]):
    """Lexical binding cells. Writes search parents; declarations stay local."""

    def __init__(self, parent: dict[str, Any] | None = None) -> None:
        super().__init__()
        self.parent = parent
        self.scope_id = id(self)

    def get(self, key: str, default: Any = None) -> Any:
        if dict.__contains__(self, key):
            return dict.__getitem__(self, key)
        return self.parent.get(key, default) if self.parent is not None else default

    def assign(self, key: str, value: Any) -> None:
        if dict.__contains__(self, key) or self.parent is None:
            self[key] = value
        elif isinstance(self.parent, Scope):
            self.parent.assign(key, value)
        else:
            self.parent[key] = value


@dataclass
class LiveBinding:
    env: dict[str, Any]
    name: str


@dataclass
class CallableMember:
    target: Any
    member: str


@dataclass
class Partial:
    target: Any
    receiver: Any
    args: list[Any]


@dataclass
class PromiseValue:
    value: Any


@dataclass
class Flow:
    returns: list[Any] = field(default_factory=list)
    continues: bool = True
    breaks: bool = False


def clone(value: Any, memo: dict[int, Any] | None = None) -> Any:
    """Copy abstract heaps, retaining aliases and cycles without copying AST nodes."""
    memo = {} if memo is None else memo
    if id(value) in memo:
        return memo[id(value)]
    if isinstance(value, dict):
        result: dict[str, Any] = Scope() if isinstance(value, Scope) else {}
        memo[id(value)] = result
        result.update({k: clone(v, memo) for k, v in value.items()})
        if isinstance(result, Scope) and isinstance(value, Scope):
            result.parent = clone(value.parent, memo)
            result.scope_id = value.scope_id
        return result
    if isinstance(value, list):
        copied: list[Any] = []
        memo[id(value)] = copied
        copied.extend(clone(v, memo) for v in value)
        return copied
    if isinstance(value, Function):
        fn = Function(value.node, {}, value.module, value.name)
        memo[id(value)] = fn
        fn.env = clone(value.env, memo)
        return fn
    if isinstance(
        value, (Client, Bound, Alternatives, LiveBinding, Partial, CallableMember, PromiseValue)
    ):
        from copy import copy

        copied_value = copy(value)
        memo[id(value)] = copied_value
        for key, item in vars(value).items():
            setattr(copied_value, key, clone(item, memo))
        return copied_value
    return value


def join(left: Any, right: Any, memo: dict[tuple[int, int], Any] | None = None) -> Any:
    memo = {} if memo is None else memo
    if left is right:
        return left
    key = (id(left), id(right))
    if key in memo:
        return memo[key]
    if isinstance(left, dict) and isinstance(right, dict):
        result: dict[str, Any] = Scope() if isinstance(left, Scope) else {}
        memo[key] = result
        for name in sorted(left.keys() | right.keys()):
            result[name] = join(left.get(name, Unknown(name)), right.get(name, Unknown(name)), memo)
        if isinstance(result, Scope) and isinstance(left, Scope) and isinstance(right, Scope):
            result.parent = join(left.parent, right.parent, memo)
            result.scope_id = left.scope_id
        return result
    if (
        isinstance(left, Client)
        and isinstance(right, Client)
        and (left.framework, left.base, left.member) == (right.framework, right.base, right.member)
    ):
        client = Client(left.framework, left.base, member=left.member)
        memo[key] = client
        client.headers = join(left.headers, right.headers, memo)
        client.config = join(left.config, right.config, memo)
        return client
    if isinstance(left, Bound) and isinstance(right, Bound) and left.member == right.member:
        return Bound(join(left.client, right.client, memo), left.member)
    if isinstance(left, Function) and isinstance(right, Function) and left.node == right.node:
        return left
    return alternatives(left, right)


def dereference(value: Any) -> Any:
    seen: set[int] = set()
    while isinstance(value, LiveBinding):
        if id(value) in seen:
            return Unknown("cyclic-binding")
        seen.add(id(value))
        value = value.env.get(value.name, Unknown(value.name))
    return value


def rebase(value: Any, target: Scope, seen: set[int] | None = None) -> Any:
    """Reconnect copied closure environments to the live lexical scope after joins."""
    seen = set() if seen is None else seen
    if isinstance(value, Scope) and value.scope_id == target.scope_id:
        return target
    if id(value) in seen:
        return value
    seen.add(id(value))
    if isinstance(value, dict):
        for key, item in list(value.items()):
            value[key] = rebase(item, target, seen)
        if isinstance(value, Scope):
            value.parent = rebase(value.parent, target, seen)
    elif isinstance(value, (Function, LiveBinding)):
        value.env = rebase(value.env, target, seen)
    elif isinstance(value, Partial):
        value.target = rebase(value.target, target, seen)
        value.receiver = rebase(value.receiver, target, seen)
    elif isinstance(value, Alternatives):
        value.values = [rebase(item, target, seen) for item in value.values]
    return value
