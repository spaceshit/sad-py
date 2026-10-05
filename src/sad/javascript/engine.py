"""Bounded, context-sensitive abstract interpretation of JS/TS ASTs.

Branch alternatives are explored; loops receive one symbolic iteration. Imported
modules share live environments. No JS runtime, eval, or subprocess is used.
"""

from __future__ import annotations

import json
import posixpath
from dataclasses import dataclass, field
from itertools import islice, product
from typing import Any
from urllib.parse import urljoin

from tree_sitter import Node

from sad.models import AnalysisResult, Endpoint, Evidence, Resource
from sad.protocols import SinkRegistry

from .parser import ASTCache
from .values import (
    Alternatives,
    Bound,
    CallableMember,
    Client,
    Flow,
    Function,
    LiveBinding,
    Partial,
    PromiseValue,
    Scope,
    Unknown,
    alternatives,
    clone,
    dereference,
    join,
    rebase,
    serializable,
    symbolic,
)

FUNCTIONS = {"function_declaration", "function_expression", "arrow_function", "method_definition"}
METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}


def txt(node: Node | None) -> str:
    return node.text.decode("utf-8", errors="replace") if node and node.text else ""


def children(node: Node) -> list[Node]:
    return [part for part in node.named_children if part.type != "comment"]


def child(node: Node, name: str) -> Node | None:
    return node.child_by_field_name(name)


def walk(node: Node) -> list[Node]:
    result = []
    stack = [node]
    while stack:
        current = stack.pop()
        result.append(current)
        stack.extend(reversed(current.named_children))
    return result


@dataclass
class Module:
    resource: Resource
    root: Node
    env: dict[str, Any] = field(default_factory=Scope)
    exports: dict[str, Any] = field(default_factory=dict)
    loaded: bool = False


class Engine:
    def __init__(
        self,
        cache: ASTCache,
        registry: SinkRegistry,
        *,
        max_steps: int = 200_000,
        max_depth: int = 20,
        environment: dict[str, str] | None = None,
        path_mappings: dict[str, str] | None = None,
    ) -> None:
        self.cache = cache
        self.registry = registry
        self.max_steps = max_steps
        self.max_depth = max_depth
        self.environment = environment or {}
        self.modules: dict[str, Module] = {}
        self.result = AnalysisResult()
        self.steps = 0
        self.resource_steps: dict[str, int] = {}
        self.resource_step_limit = max_steps
        self.stack: list[str] = []
        self.called: set[tuple[str, int]] = set()
        self.functions: list[Function] = []
        self.module = ""
        self.path_mappings = path_mappings or {}
        self.active_calls: set[tuple[str, str]] = set()
        self.summaries: dict[tuple[str, str], Any] = {}
        self.recursive_keys: set[tuple[str, str]] = set()
        self.bundle_factories: dict[tuple[str, str], Function] = {}
        self.bundle_exports: dict[tuple[str, str], Any] = {}
        self.bundle_dependencies: dict[tuple[str, str], dict[str, Any]] = {}
        self.parcel_modules: set[tuple[str, str]] = set()

    def analyze(self, resources: list[Resource]) -> AnalysisResult:
        for resource in sorted(resources, key=lambda r: r.url):
            try:
                root = self.cache.parse(resource.content, resource.url).root_node
            except ValueError as exc:
                self.result.diagnostics.append(str(exc))
                continue
            if root.has_error:
                self.result.add_issue(
                    "parse-recovery", f"Recovered syntax errors in {resource.url}", resource.url
                )
            self.modules[resource.url] = Module(resource, root)
        # Reserve a share for every supplied source. A vendor script must not
        # consume the entire global budget before unrelated application code.
        self.resource_step_limit = max(1, self.max_steps // max(1, len(self.modules)))
        try:
            for name in self.modules:
                self.load(name)
            for module_name, module_id in list(self.bundle_factories):
                self.load_bundle(module_name, module_id)
            # Analyze uncalled entry points with symbolic parameters. Wrappers already
            # reached through callers are not independently emitted as noisy duplicates.
            index = 0
            while index < len(self.functions):
                function = self.functions[index]
                index += 1
                key = (function.module, function.node.start_byte)
                if key not in self.called:
                    self.invoke(function, [], function.node)
        except (RecursionError, BudgetExceeded) as exc:
            self.result.add_issue(
                "analysis-budget",
                f"Analysis truncated: {exc}",
                exc.source_file if isinstance(exc, BudgetExceeded) else self.module,
            )
        self.result.call_graph = sorted(set(self.result.call_graph))
        return self.result

    def globals(self) -> dict[str, Any]:
        env: dict[str, Any] = {k: Client(v) for k, v in self.registry.globals.items()}
        env["navigator"] = {"sendBeacon": Client("beacon")}
        env["window"] = dict(env)
        env["globalThis"] = dict(env)
        env["process"] = {"env": self.environment}
        env["import.meta"] = {"env": self.environment, "url": self.module}
        env["JSON"] = {"stringify": Client("json")}
        env["Promise"] = {"resolve": Client("promise")}
        env["URL"] = Client("url")
        env["Request"] = Client("request-object")
        env["document"] = {"createElement": Client("dom-create")}
        env["module"] = {"exports": {}}
        env["exports"] = env["module"]["exports"]
        return env

    def resolve(self, spec: str) -> str | None:
        for alias, destination in self.path_mappings.items():
            prefix, _, suffix = alias.partition("*")
            if (
                "*" in alias and spec.startswith(prefix) and spec.endswith(suffix)
            ) or spec == alias:
                middle = spec[len(prefix) : len(spec) - len(suffix) if suffix else None]
                spec = destination.replace("*", middle)
                break
        if not spec.startswith((".", "/", "http:", "https:")):
            return None
        base = (
            urljoin(self.module, spec)
            if "://" in self.module
            else posixpath.normpath(posixpath.join(posixpath.dirname(self.module), spec))
        )
        for candidate in [
            base,
            *[
                base + x
                for x in (".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", "/index.js", "/index.ts")
            ],
        ]:
            if candidate in self.modules:
                return candidate
        return None

    def imported(self, spec: str) -> Any:
        if spec in self.registry.packages:
            return Client(self.registry.packages[spec])
        resolved = self.resolve(spec)
        if resolved:
            return self.load(resolved)
        self.result.add_issue(
            "unresolved-import", f"Unresolved import {spec!r} from {self.module}", self.module
        )
        return Unknown(spec)

    def load(self, name: str) -> dict[str, Any]:
        module = self.modules[name]
        if module.loaded:
            return module.exports
        module.loaded = True
        previous = self.module
        self.module = name
        module.env.update(self.globals())
        # Publish binding cells before walking imports, so cyclic users see later writes.
        for statement in children(module.root):
            if statement.type == "export_statement":
                declaration = child(statement, "declaration")
                if declaration:
                    names = (
                        [child(declaration, "name")]
                        if declaration.type in ("function_declaration", "class_declaration")
                        else [child(n, "name") for n in children(declaration)]
                    )
                    for name_node in names:
                        name_text = txt(name_node)
                        exported_name = (
                            "default"
                            if "default" in [n.type for n in statement.children]
                            else name_text
                        )
                        module.exports[exported_name] = LiveBinding(module.env, name_text)
        try:
            self.index_bundle(module)
            self.execute(module.root, module.env)
        except ResourceBudgetExceeded as exc:
            self.report_resource_budget(exc)
        finally:
            # Retain CommonJS assignments completed before a budget interruption.
            exported = module.env.get("module", {}).get("exports", {})
            if isinstance(exported, dict):
                module.exports.update(exported)
            elif not isinstance(exported, Unknown):
                module.exports["default"] = exported
            self.module = previous
        return module.exports

    def tick(self) -> None:
        if self.resource_steps.get(self.module, 0) >= self.resource_step_limit:
            raise ResourceBudgetExceeded(
                f"source step budget {self.resource_step_limit} exceeded in {self.module}",
                self.module,
            )
        if self.steps >= self.max_steps:
            raise BudgetExceeded(f"global step budget {self.max_steps} exceeded", self.module)
        self.steps += 1
        self.resource_steps[self.module] = self.resource_steps.get(self.module, 0) + 1

    def report_resource_budget(self, exc: ResourceBudgetExceeded) -> None:
        self.result.add_issue("analysis-budget", f"Analysis truncated: {exc}", exc.source_file)

    def function(self, node: Node, env: dict[str, Any]) -> Function:
        fn = Function(node, env, self.module, txt(child(node, "name")) or "<anonymous>")
        self.functions.append(fn)
        return fn

    def index_bundle(self, module: Module) -> None:
        """Recognize static numeric module tables; no loader code is executed."""
        for node in walk(module.root):
            if node.type != "object":
                continue
            pairs = children(node)
            entries: list[tuple[str, Node, Node | None]] = []
            for pair in pairs:
                value_node = child(pair, "value")
                key = txt(child(pair, "key")).strip("\"'")
                if pair.type != "pair" or not key.isdigit() or value_node is None:
                    break
                dependencies = None
                if value_node.type == "array":
                    elements = children(value_node)
                    if len(elements) != 2 or elements[1].type != "object":
                        break
                    value_node, dependencies = elements
                if value_node.type not in FUNCTIONS:
                    break
                entries.append((key, value_node, dependencies))
            if len(entries) != len(pairs) or not entries:
                continue
            for module_id, factory_node, dependencies in entries:
                bundle_key = (module.resource.url, module_id)
                self.bundle_factories[bundle_key] = self.function(factory_node, module.env)
                if dependencies is not None:
                    self.parcel_modules.add(bundle_key)
                    self.bundle_dependencies[bundle_key] = self.value(dependencies, module.env)

    def load_bundle(self, module: str, module_id: str) -> Any:
        key = (module, module_id)
        if key in self.bundle_exports:
            return self.bundle_exports[key]
        factory = self.bundle_factories.get(key)
        if factory is None:
            self.result.add_issue(
                "unresolved-bundle-module", f"Missing bundle module {module_id}", module
            )
            return Unknown("module-" + module_id)
        exports: dict[str, Any] = {}
        self.bundle_exports[key] = exports
        commonjs = {"exports": exports}
        require = Client(
            "bundle-require", module, config={"dependencies": self.bundle_dependencies.get(key, {})}
        )
        parameters = child(factory.node, "parameters")
        first = txt(children(parameters)[0]) if parameters and children(parameters) else ""
        args = (
            [require, commonjs, exports]
            if key in self.parcel_modules or first in ("require", "parcelRequire")
            else [commonjs, exports, require]
        )
        self.invoke(factory, args, factory.node)
        self.bundle_exports[key] = commonjs["exports"]
        return commonjs["exports"]

    def union(self, *values: Any) -> Any:
        result = alternatives(*values)
        if isinstance(result, Alternatives) and any(
            isinstance(v, Unknown) and v.label == "branch-limit" for v in result.values
        ):
            self.result.add_issue(
                "branch-limit",
                "Value alternatives exceeded 16; retained symbolic remainder",
                self.module,
            )
        return result

    def products(self, groups: list[list[Any]]) -> list[tuple[Any, ...]]:
        combinations = list(islice(product(*groups), 17))
        if len(combinations) > 16:
            self.result.add_issue("branch-limit", "Argument combinations exceeded 16", self.module)
            return [
                *combinations[:16],
                tuple(
                    group[0]
                    if isinstance(group[0], (Function, Bound, Client, Partial, CallableMember))
                    else Unknown("branch-limit")
                    for group in groups
                ),
            ]
        return combinations

    def update_scope(self, target: dict[str, Any], source: dict[str, Any]) -> None:
        target.clear()
        target.update(source)
        if isinstance(target, Scope):
            for name, value in list(target.items()):
                target[name] = rebase(value, target)
        if isinstance(target, Scope) and isinstance(source, Scope) and target.parent is not None:
            self.update_scope(target.parent, source.parent or {})

    def assign(self, node: Node | None, value: Any, env: dict[str, Any]) -> None:
        if node is None:
            return
        if node.type in ("member_expression", "subscript_expression"):
            obj = self.value(child(node, "object"), env)
            name = (
                txt(child(node, "property"))
                if node.type == "member_expression"
                else symbolic(self.value(child(node, "index"), env))
            )
            if isinstance(obj, dict):
                obj[name] = value
            elif isinstance(obj, Client):
                if obj.framework == "form":
                    if name == "action":
                        obj.base = symbolic(value)
                    elif name == "method":
                        obj.member = symbolic(value).upper()
                else:
                    obj.config[name] = value
        elif isinstance(env, Scope) and node.type == "identifier":
            env.assign(txt(node), value)
        else:
            self.bind(node, value, env)

    def state_key(self, scope: dict[str, Any]) -> str:
        layers = []
        current: dict[str, Any] | None = scope
        while current is not None:
            layers.append(serializable(current))
            current = current.parent if isinstance(current, Scope) else None
        return json.dumps(layers, sort_keys=True)

    def loop(self, node: Node, env: dict[str, Any]) -> Flow:
        local = Scope(env)
        returns: list[Any] = []
        if node.type == "for_in_statement":
            values = self.value(child(node, "right"), local)
            items = (
                list(values)
                if isinstance(values, (dict, list))
                else [Unknown(txt(child(node, "left")))]
            )
            for item in items[:64]:
                self.bind(child(node, "left"), item, local)
                flow = self.execute(child(node, "body"), local)
                if isinstance(flow, Flow):
                    returns.extend(flow.returns)
                    if flow.breaks:
                        break
            if len(items) > 64:
                self.result.add_issue("loop-limit", "Loop exceeded 64 known elements", self.module)
            return Flow(returns)
        initializer = child(node, "initializer")
        if initializer:
            self.execute(initializer, local)
        for iteration in range(64):
            condition_node = child(node, "condition")
            condition = (
                self.value(condition_node, local)
                if condition_node and condition_node.type != "empty_statement"
                else True
            )
            if node.type == "do_statement" and iteration == 0:
                condition = True
            if condition is False:
                return Flow(returns)
            before = clone(local)
            flow = self.execute(child(node, "body"), local)
            if isinstance(flow, Flow):
                returns.extend(flow.returns)
                if flow.breaks:
                    return Flow(returns)
                if not flow.continues and flow.returns:
                    return Flow(returns, False)
            self.value(child(node, "increment"), local)
            if not isinstance(condition, bool):
                merged = join(before, local)
                if self.state_key(merged) == self.state_key(before):
                    self.update_scope(local, merged)
                    return Flow(returns)
                self.update_scope(local, merged)
        self.result.add_issue(
            "loop-limit", "Loop fixed point did not converge within 64 iterations", self.module
        )
        return Flow(returns)

    def call(self, fn: Any, args: list[Any], node: Node) -> Any:
        fn = dereference(fn)
        if isinstance(fn, Alternatives) or any(isinstance(a, Alternatives) for a in args):
            functions = fn.values if isinstance(fn, Alternatives) else [fn]
            groups = [functions, *[a.values if isinstance(a, Alternatives) else [a] for a in args]]
            return self.union(
                *(self.call(f, list(arguments), node) for f, *arguments in self.products(groups))
            )
        if isinstance(fn, Partial):
            target = fn.target
            if isinstance(target, Function):
                target = Function(target.node, Scope(target.env), target.module, target.name)
                target.env["this"] = fn.receiver
            return self.call(target, [*fn.args, *args], node)
        if isinstance(fn, CallableMember):
            if fn.member in ("bind", "call", "apply"):
                receiver = args[0] if args else Unknown("this")
                arguments = (
                    args[1]
                    if fn.member == "apply" and len(args) > 1 and isinstance(args[1], list)
                    else args[1:]
                )
                partial = Partial(fn.target, receiver, arguments)
                return partial if fn.member == "bind" else self.call(partial, [], node)
            if isinstance(fn.target, PromiseValue):
                result = self.call(args[0], [fn.target.value], node) if args else fn.target.value
                return PromiseValue(result)
            if isinstance(fn.target, list):
                if fn.member == "join":
                    return (symbolic(args[0]) if args else ",").join(symbolic(v) for v in fn.target)
                return (
                    [
                        self.call(args[0], [value, index], node)
                        for index, value in enumerate(fn.target)
                    ]
                    if args
                    else []
                )
        if isinstance(fn, Function):
            return self.invoke(fn, args, node)
        if isinstance(fn, (Client, Bound)):
            return self.network(fn, args, node)
        return Unknown("return")

    def bind(self, pattern: Node | None, value: Any, env: dict[str, Any]) -> None:
        if pattern is None:
            return
        if pattern.type in ("identifier", "shorthand_property_identifier_pattern"):
            env[txt(pattern)] = value
        elif pattern.type in ("required_parameter", "optional_parameter"):
            parameter_pattern = child(pattern, "pattern") or children(pattern)[0]
            if isinstance(value, Unknown) and value.label == txt(pattern):
                value = Unknown(txt(parameter_pattern))
            annotation = child(pattern, "type")
            if isinstance(value, Unknown) and annotation:
                declared = env.get(txt(annotation).lstrip(": "))
                if (
                    isinstance(declared, Bound)
                    and declared.client.framework == "@angular/common/http"
                ):
                    value = declared.client
            self.bind(child(pattern, "pattern") or children(pattern)[0], value, env)
        elif pattern.type == "assignment_pattern":
            self.bind(
                child(pattern, "left"),
                self.value(child(pattern, "right"), env) if isinstance(value, Unknown) else value,
                env,
            )
        elif pattern.type == "object_pattern":
            for part in children(pattern):
                key = txt(child(part, "key")) if part.type == "pair_pattern" else txt(part)
                target = child(part, "value") if part.type == "pair_pattern" else part
                self.bind(target, self.member(value, key), env)
        elif pattern.type == "array_pattern":
            for i, part in enumerate(children(pattern)):
                self.bind(
                    part,
                    value[i] if isinstance(value, list) and i < len(value) else Unknown(txt(part)),
                    env,
                )

    def execute(self, node: Node | None, env: dict[str, Any]) -> Any:
        if node is None:
            return Unknown()
        self.tick()
        kind = node.type
        if kind in ("program", "statement_block"):
            if kind == "statement_block":
                env = Scope(env)
            for statement in children(node):
                if statement.type in ("lexical_declaration", "variable_declaration"):
                    for declaration_node in children(statement):
                        self.bind(
                            child(declaration_node, "name"),
                            Unknown(txt(child(declaration_node, "name"))),
                            env,
                        )
                target = (
                    child(statement, "declaration")
                    if statement.type == "export_statement"
                    else statement
                )
                if target and target.type == "function_declaration":
                    env[txt(child(target, "name"))] = self.function(target, env)
            returns = []
            for statement in children(node):
                value = self.execute(statement, env)
                if isinstance(value, Flow):
                    returns.extend(value.returns)
                    if not value.continues:
                        return Flow(returns, False, value.breaks)
            return Flow(returns) if returns else Unknown()
        if kind == "function_declaration":
            return env.get(txt(child(node, "name")), Unknown())
        if kind in ("lexical_declaration", "variable_declaration"):
            for variable_decl in children(node):
                if variable_decl.type == "variable_declarator":
                    self.bind(
                        child(variable_decl, "name"),
                        self.value(child(variable_decl, "value"), env),
                        env,
                    )
            return Unknown()
        if kind == "import_statement":
            source = self.value(child(node, "source"), env)
            imported = self.imported(symbolic(source))
            clause = next((n for n in children(node) if n.type == "import_clause"), None)
            if clause:
                for part in children(clause):
                    if part.type == "identifier":
                        env[txt(part)] = (
                            imported.get("default", LiveBinding(imported, "default"))
                            if isinstance(imported, dict)
                            else imported
                        )
                    elif part.type == "namespace_import":
                        env[txt(children(part)[-1])] = imported
                    else:
                        for spec in children(part):
                            original = txt(child(spec, "name"))
                            alias = txt(child(spec, "alias")) or original
                            env[alias] = (
                                LiveBinding(imported, original)
                                if isinstance(imported, dict)
                                else self.member(imported, original)
                            )
            return Unknown()
        if kind == "export_statement":
            module = self.modules[self.module]
            declaration = child(node, "declaration")
            value_node = child(node, "value")
            if declaration:
                value = self.execute(declaration, env)
                if "default" in [c.type for c in node.children]:
                    module.exports["default"] = value
                else:
                    if declaration.type == "function_declaration":
                        name = txt(child(declaration, "name"))
                        module.exports[name] = LiveBinding(env, name)
                    else:
                        for decl in children(declaration):
                            name = txt(child(decl, "name"))
                            if name in env:
                                module.exports[name] = LiveBinding(env, name)
            elif value_node:
                module.exports["default"] = self.value(value_node, env)
            else:
                source = child(node, "source")
                values = self.imported(symbolic(self.value(source, env))) if source else env
                clause = next((n for n in children(node) if n.type == "export_clause"), None)
                if clause:
                    for spec in children(clause):
                        original = txt(child(spec, "name"))
                        module.exports[txt(child(spec, "alias")) or original] = self.member(
                            values, original
                        )
                elif isinstance(values, dict):
                    module.exports.update(values)
            return Unknown()
        if kind == "else_clause":
            return self.execute(children(node)[0], env) if children(node) else Unknown()
        if kind == "return_statement":
            return Flow([self.value(children(node)[0], env) if children(node) else None], False)
        if kind in ("break_statement", "continue_statement"):
            return Flow([], False, kind == "break_statement")
        if kind == "expression_statement":
            return self.value(children(node)[0], env) if children(node) else None
        if kind == "if_statement":
            condition = self.value(child(node, "condition"), env)
            if isinstance(condition, bool):
                return self.execute(child(node, "consequence" if condition else "alternative"), env)
            branches = [clone(env), clone(env)]
            flows = [
                self.execute(child(node, "consequence"), branches[0]),
                self.execute(child(node, "alternative"), branches[1]),
            ]
            continuing = [i for i, f in enumerate(flows) if not isinstance(f, Flow) or f.continues]
            merged = (
                join(branches[0], branches[1]) if len(continuing) != 1 else branches[continuing[0]]
            )
            self.update_scope(env, merged)
            return Flow(
                [v for f in flows if isinstance(f, Flow) for v in f.returns], bool(continuing)
            )
        if kind in ("for_in_statement", "for_statement", "while_statement", "do_statement"):
            return self.loop(node, env)
        if kind == "try_statement":
            flows = [self.execute(child(node, "body"), env)]
            handler = child(node, "handler")
            if handler:
                flows.append(self.execute(child(handler, "body"), env))
            finalizer = child(node, "finalizer")
            if finalizer:
                flows.append(self.execute(child(finalizer, "body"), env))
            return Flow([v for f in flows if isinstance(f, Flow) for v in f.returns])
        if kind in ("class_declaration", "class"):
            methods: dict[str, Any] = {}
            heritage = next((n for n in children(node) if n.type == "class_heritage"), None)
            if heritage and children(heritage):
                inherited = self.value(children(heritage)[-1], env)
                if isinstance(inherited, dict):
                    methods.update(inherited)
            body = child(node, "body")
            if body:
                for method in children(body):
                    if method.type == "method_definition":
                        methods[txt(child(method, "name"))] = self.function(method, env)
            env[txt(child(node, "name"))] = methods
            return methods
        for part in children(node):
            if part.type.endswith("statement") or part.type in (
                "statement_block",
                "lexical_declaration",
            ):
                self.execute(part, env)
            else:
                self.value(part, env)
        return Unknown()

    def member(self, value: Any, key: str) -> Any:
        value = dereference(value)
        if key in ("bind", "call", "apply") and isinstance(
            value, (Function, Client, Bound, Partial)
        ):
            return CallableMember(value, key)
        if isinstance(value, PromiseValue) or isinstance(value, list):
            if key in ("then", "catch", "finally", "forEach", "map", "flatMap", "join"):
                return CallableMember(value, key)
            if isinstance(value, list) and key.isdigit():
                return value[int(key)] if int(key) < len(value) else Unknown(key)
            if key == "length" and isinstance(value, list):
                return len(value)
        if isinstance(value, Alternatives):
            return alternatives(*(self.member(v, key) for v in value.values))
        if isinstance(value, dict):
            item = dereference(value.get(key, Unknown(key)))
            return Partial(item, value, []) if isinstance(item, Function) else item
        if isinstance(value, Client):
            if value.framework in ("trpc-client", "connect-client"):
                return Client(
                    value.framework,
                    value.base,
                    value.headers,
                    ".".join(filter(None, (value.member, key))),
                    value.config,
                )
            if key == "defaults":
                return value.config
            return Bound(value, key)
        if isinstance(value, Unknown):
            return Unknown(value.label + "." + key)
        return Unknown(key)

    def value(self, node: Node | None, env: dict[str, Any]) -> Any:
        if node is None:
            return Unknown()
        self.tick()
        kind = node.type
        if kind in ("identifier", "shorthand_property_identifier", "this"):
            return dereference(env.get(txt(node), Unknown(txt(node))))
        if kind == "meta_property":
            return dereference(env.get(txt(node), Unknown(txt(node))))
        if kind == "string":
            raw = txt(node)[1:-1]
            # Decode JS escapes without evaluating the input.
            import re

            return re.sub(
                r"\\(u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|.)",
                lambda m: (
                    chr(int(m[1][1:], 16))
                    if m[1].startswith(("u", "x")) and len(m[1]) > 1
                    else {"n": "\n", "r": "\r", "t": "\t"}.get(m[1], m[1])
                ),
                raw,
            )
        if kind == "template_string":
            parts = [
                self.value(children(n)[0], env)
                if n.type == "template_substitution" and children(n)
                else txt(n)
                for n in children(node)
            ]
            combinations = [v.values if isinstance(v, Alternatives) else [v] for v in parts]
            return self.union(
                *("".join(symbolic(v) for v in group) for group in self.products(combinations))
            )
        if kind == "number":
            try:
                return json.loads(txt(node))
            except ValueError:
                return Unknown(txt(node))
        if kind in ("true", "false", "null"):
            return {"true": True, "false": False, "null": None}[kind]
        if kind in FUNCTIONS:
            return self.function(node, env)
        if kind == "object":
            obj: dict[str, Any] = {}
            for part in children(node):
                if part.type == "pair":
                    key = child(part, "key")
                    name = (
                        symbolic(self.value(key, env)) if key and key.type == "string" else txt(key)
                    )
                    obj[name] = self.value(child(part, "value"), env)
                elif part.type == "shorthand_property_identifier":
                    obj[txt(part)] = self.value(part, env)
                elif part.type == "method_definition":
                    obj[txt(child(part, "name"))] = self.function(part, env)
                elif part.type == "spread_element":
                    spread = self.value(children(part)[0], env)
                    if isinstance(spread, dict):
                        obj.update(spread)
            return obj
        if kind == "array":
            return [self.value(n, env) for n in children(node)]
        if kind in ("member_expression", "subscript_expression"):
            obj = self.value(child(node, "object"), env)
            key_node = child(node, "property") or child(node, "index")
            property_name = (
                symbolic(self.value(key_node, env))
                if kind == "subscript_expression"
                else txt(key_node)
            )
            return self.member(obj, property_name)
        if kind == "ternary_expression":
            condition = self.value(child(node, "condition"), env)
            if isinstance(condition, bool):
                return self.value(child(node, "consequence" if condition else "alternative"), env)
            return self.union(
                self.value(child(node, "consequence"), env),
                self.value(child(node, "alternative"), env),
            )
        if kind == "binary_expression":
            left = self.value(child(node, "left"), env)
            right = self.value(child(node, "right"), env)
            operator = txt(child(node, "operator"))
            if operator == "+":
                if isinstance(left, (int, float)) and isinstance(right, (int, float)):
                    return left + right
                lefts = left.values if isinstance(left, Alternatives) else [left]
                rights = right.values if isinstance(right, Alternatives) else [right]
                return self.union(
                    *(symbolic(a) + symbolic(b) for a, b in self.products([lefts, rights]))
                )
            if operator in ("||", "??", "&&"):
                if isinstance(left, (Unknown, Alternatives)):
                    return self.union(left, right)
                return (
                    (right if left else left)
                    if operator == "&&"
                    else right
                    if left is None or (operator == "||" and not left)
                    else left
                )
            if not isinstance(left, (Unknown, Alternatives)) and not isinstance(
                right, (Unknown, Alternatives)
            ):
                if operator in ("===", "==", "!==", "!="):
                    equal = type(left) is type(right) and left == right
                    return not equal if "!" in operator else equal
                if isinstance(left, (int, float)) and isinstance(right, (int, float)):
                    operations = {
                        "-": lambda: left - right,
                        "*": lambda: left * right,
                        "<": lambda: left < right,
                        ">": lambda: left > right,
                        "<=": lambda: left <= right,
                        ">=": lambda: left >= right,
                    }
                    if operator in operations:
                        return operations[operator]()
            return Unknown(txt(node)[:80])
        if kind == "update_expression":
            argument = child(node, "argument")
            old = self.value(argument, env)
            new = (
                old + (1 if "++" in txt(node) else -1)
                if isinstance(old, (int, float))
                else Unknown(txt(argument))
            )
            self.assign(argument, new, env)
            return new
        if kind == "assignment_expression":
            value = self.value(child(node, "right"), env)
            self.assign(child(node, "left"), value, env)
            return value
        if kind in ("call_expression", "new_expression"):
            target = child(node, "function") or child(node, "constructor")
            args_node = child(node, "arguments")
            args = [self.value(n, env) for n in children(args_node)] if args_node else []
            if (
                txt(target) in ("require", "import")
                and args
                and not isinstance(env.get(txt(target)), (Client, Function, Partial))
            ):
                imported = self.imported(symbolic(args[0]))
                if (
                    txt(target) == "require"
                    and isinstance(imported, dict)
                    and set(imported) == {"default"}
                ):
                    return imported["default"]
                return imported
            fn = self.value(target, env)
            if not (isinstance(fn, dict) and kind == "new_expression"):
                return self.call(fn, args, node)
            if isinstance(fn, dict) and kind == "new_expression":
                instance = dict(fn)
                for method_name, method in list(instance.items()):
                    if isinstance(method, Function):
                        instance[method_name] = Function(
                            method.node,
                            {**method.env, "this": instance},
                            method.module,
                            method.name,
                        )
                if isinstance(instance.get("constructor"), Function):
                    self.invoke(instance["constructor"], args, node)
                return instance
            return Unknown(txt(target))
        if kind in (
            "parenthesized_expression",
            "await_expression",
            "as_expression",
            "non_null_expression",
            "type_assertion",
            "extends_clause",
        ):
            return self.value(children(node)[0], env) if children(node) else Unknown()
        # Traverse unsupported syntax to retain nested direct sinks.
        for part in children(node):
            self.value(part, env)
        return Unknown(txt(node)[:80] or "dynamic")

    def invoke(self, fn: Function, args: list[Any], node: Node) -> Any:
        label = f"{fn.module}:{fn.name}:{fn.node.start_point.row + 1}:{fn.node.start_byte}"
        caller = self.stack[-1] if self.stack else self.module
        self.result.call_graph.append((caller, label))
        call_key = (label, json.dumps(serializable(args), sort_keys=True))
        if call_key in self.active_calls or len(self.stack) >= self.max_depth:
            if call_key in self.active_calls:
                self.recursive_keys.add(call_key)
            self.result.add_issue("recursion-limit", f"Recursion/depth limit at {label}", fn.module)
            return self.summaries.get(call_key, Unknown(fn.name))
        self.active_calls.add(call_key)
        self.called.add((fn.module, fn.node.start_byte))
        env = Scope(fn.env)
        previous = self.module
        self.module = fn.module
        self.stack.append(label)
        try:
            params = child(fn.node, "parameters")
            parameters = children(params) if params else [child(fn.node, "parameter")]
            for i, param in enumerate(parameters):
                self.bind(
                    param, args[i] if i < len(args) else Unknown(txt(param) or "dynamic"), env
                )
            body = child(fn.node, "body")
            returned: Any = Unknown(fn.name)
            for iteration in range(8):
                iteration_env = env if iteration == 0 else clone(env)
                value = (
                    self.execute(body, iteration_env)
                    if body and body.type == "statement_block"
                    else self.value(body, iteration_env)
                )
                returned = (
                    self.union(*value.returns)
                    if isinstance(value, Flow) and value.returns
                    else Unknown()
                    if isinstance(value, Flow)
                    else value
                )
                previous_summary = self.summaries.get(call_key)
                if previous_summary is not None:
                    returned = self.union(previous_summary, returned)
                self.summaries[call_key] = returned
                if call_key not in self.recursive_keys or serializable(
                    previous_summary
                ) == serializable(returned):
                    break
            else:
                self.result.add_issue(
                    "summary-limit", f"Recursive summary did not converge at {label}", fn.module
                )
            return returned
        except ResourceBudgetExceeded as exc:
            self.report_resource_budget(exc)
            return Unknown("analysis-budget")
        finally:
            self.active_calls.remove(call_key)
            self.stack.pop()
            self.module = previous

    def network(self, fn: Client | Bound, args: list[Any], node: Node) -> Any:
        client = fn.client if isinstance(fn, Bound) else fn
        member = (
            fn.member
            if isinstance(fn, Bound)
            else fn.member
            if fn.framework in ("trpc-client", "connect-client")
            else ""
        )
        framework = client.framework
        first = args[0] if args else Unknown("url")
        config = first if isinstance(first, dict) else {}
        if (
            framework
            in (
                "fetch",
                "unfetch",
                "beacon",
                "url",
                "request-object",
                "dom-create",
                "json",
                "promise",
            )
            and member
        ):
            return Unknown("unsupported-member")
        pending = client.config.get("_endpoint")
        if isinstance(pending, Endpoint) and member in (
            "setRequestHeader",
            "set",
            "send",
            "json",
            "query",
            "type",
        ):
            if member in ("setRequestHeader", "set"):
                extra = (
                    first
                    if isinstance(first, dict)
                    else {symbolic(first): args[1] if len(args) > 1 else Unknown("header")}
                )
                pending.headers = {**(pending.headers or {}), **serializable(extra)}
            elif member in ("send", "json"):
                pending.body = serializable(first)
            elif member == "type":
                pending.content_type = symbolic(first)
            elif isinstance(first, dict):
                pending.query_parameters.extend(
                    {"name": key, "value": symbolic(value)} for key, value in first.items()
                )
            return client
        if framework == "bundle-require":
            if not member:
                module_id = client.config.get("dependencies", {}).get(symbolic(first), first)
                return self.load_bundle(client.base, symbolic(module_id))
            if (
                member == "d"
                and isinstance(first, dict)
                and len(args) > 1
                and isinstance(args[1], dict)
            ):
                for name, getter in args[1].items():
                    first[name] = self.call(getter, [], node)
            return first
        if framework == "@trpc/client":
            if member in ("httpLink", "httpBatchLink", "httpBatchStreamLink"):
                return Client("trpc-client", symbolic(config.get("url", Unknown("url"))))
            if member in ("createTRPCProxyClient", "createTRPCClient"):
                links = config.get("links", [])
                if isinstance(links, list):
                    return next(
                        (link for link in links if isinstance(link, Client)), Unknown("transport")
                    )
            return Unknown("trpc")
        if framework == "@connectrpc/connect" and member in ("createClient", "createPromiseClient"):
            transport = args[1] if len(args) > 1 else None
            if isinstance(transport, Client):
                service = symbolic(config.get("typeName", Unknown("service")))
                return Client(
                    "connect-client", transport.base.rstrip("/") + "/" + service, config=config
                )
            return Unknown("transport")
        if framework == "promise":
            return PromiseValue(first)
        if framework == "json":
            return serializable(first)
        if framework == "url":
            return urljoin(symbolic(args[1]), symbolic(first)) if len(args) > 1 else first
        if framework == "request-object":
            request_options = args[1] if len(args) > 1 and isinstance(args[1], dict) else {}
            return {"url": first, **request_options}
        if framework == "dom-create":
            return (
                Client("form", "{document-url}", member="GET")
                if first == "form"
                else Unknown("element")
            )
        if framework == "wretch" and not member:
            return Client(framework, symbolic(first))
        if framework == "grpc-web" and member in ("GrpcWebClientBase", "AbstractClientBase"):
            return Client(framework)
        if framework == "graphql-request" and member == "GraphQLClient":
            return Client(framework, symbolic(first))
        if member in ("gql", "graphql"):
            return first
        if member in ("create", "extend", "defaults"):
            return Client(
                framework,
                symbolic(config.get("baseURL", config.get("prefixUrl", client.base))),
                serializable(config.get("headers", client.headers)) or {},
                config={**client.config, **config},
            )
        if framework == "xhr" and not member:
            return Client("xhr")
        if framework in (
            "@apollo/client",
            "urql",
            "@urql/core",
            "graphql-request",
            "@connectrpc/connect-web",
            "grpc-web",
        ) and (
            member
            in (
                "ApolloClient",
                "HttpLink",
                "createClient",
                "GraphQLClient",
                "createConnectTransport",
            )
            or (not member and config)
        ):
            link = config.get("link")
            base = config.get(
                "uri",
                config.get(
                    "url", config.get("baseUrl", link.base if isinstance(link, Client) else first)
                ),
            )
            return Client(framework, symbolic(base))
        options: dict[str, Any] = {
            **client.config,
            **(args[1] if len(args) > 1 and isinstance(args[1], dict) else {}),
        }
        method: str | None = "GET"
        url = first
        protocol = "http"
        body: Any = None
        configured_endpoint = False
        if framework == "trpc-client":
            segments = member.split(".")
            if segments[-1] not in ("query", "mutate", "subscribe"):
                return client
            protocol = "trpc"
            method = "GET" if segments[-1] == "query" else "POST"
            url = ".".join(segments[:-1]) or Unknown("procedure")
            body = first if args else None
        elif framework == "connect-client":
            descriptor = client.config.get("methods", {}).get(member, {})
            wire_name = descriptor.get("name") if isinstance(descriptor, dict) else None
            protocol, method, url = "connect-web", "POST", wire_name or member or Unknown("method")
            if not wire_name:
                self.result.add_issue(
                    "unresolved-wire-method", f"Missing wire descriptor for {member}", self.module
                )
            body = first if args else None
        elif framework == "form":
            if member not in ("submit", "requestSubmit"):
                return client
            method, url, configured_endpoint = client.member or "GET", client.base, True
        elif framework == "grpc-web":
            if member not in ("rpcCall", "unaryCall", "serverStreaming"):
                return client
            protocol, method = "grpc-web", "POST"
            body = args[1] if len(args) > 1 else None
        elif framework == "wretch":
            if member not in METHODS:
                return client
            url, method, configured_endpoint = client.base, member.upper(), True
            body = first if args else None
        elif framework == "xhr":
            if member != "open":
                return client
            method, url = symbolic(first).upper(), args[1] if len(args) > 1 else Unknown("url")
        elif framework in ("websocket", "socket.io-client", "sse"):
            if member not in ("", "io", "connect"):
                return client
            protocol = "sse" if framework == "sse" else "websocket"
        elif framework == "beacon":
            method, body = "POST", args[1] if len(args) > 1 else None
        elif framework in ("@apollo/client", "urql", "@urql/core", "graphql-request"):
            if member not in ("", "request", "query", "mutate", "mutation", "subscription"):
                return client
            protocol, method, url = "graphql", "POST", client.base or first
            configured_endpoint = bool(client.base)
            body = first if client.base else args[1] if len(args) > 1 else None
        elif member in METHODS:
            method = member.upper()
            if method in ("POST", "PUT", "PATCH") and framework not in ("ky", "got"):
                body = args[1] if len(args) > 1 else None
                options = args[2] if len(args) > 2 and isinstance(args[2], dict) else {}
        elif member in ("", "request", "ajax", "$fetch", "fetch"):
            if config:
                options = config
                url = config.get("url", Unknown("url"))
            method = symbolic(options.get("method", options.get("type", "GET"))).upper()
            body = options.get("body", options.get("data", options.get("json")))
        else:
            return client
        options = {**client.config, **options}
        extra_headers = options.get("headers")
        headers = {
            **client.headers,
            **(extra_headers if isinstance(extra_headers, dict) else {}),
        }
        if body is None:
            body = options.get("body", options.get("data", options.get("json")))
        if isinstance(body, str):
            try:
                decoded = json.loads(body)
                if isinstance(decoded, dict):
                    body = decoded
            except (ValueError, RecursionError):
                pass
        if isinstance(body, dict) and "query" in body:
            protocol = "graphql"
        content_type = str(headers.get("content-type", headers.get("Content-Type", ""))).lower()
        if "application/soap+xml" in content_type or any(
            k.lower() == "soapaction" for k in headers
        ):
            protocol = "soap"
        if "grpc" in content_type:
            protocol = "grpc-web"
        if isinstance(body, dict) and "jsonrpc" in body:
            protocol = "json-rpc"
        base = symbolic(options.get("baseURL", client.base))
        url_text = symbolic(url)
        if (
            base
            and not configured_endpoint
            and not url_text.startswith(("http://", "https://", "ws://", "wss://"))
        ):
            url_text = base.rstrip("/") + "/" + url_text.lstrip("/")
        if "{" in url_text:
            self.result.add_issue(
                "symbolic-url",
                f"Unresolved URL components: {url_text}",
                self.module,
                node.start_point.row + 1,
                node.start_point.column + 1,
            )
        if method and method.startswith("{"):
            self.result.add_issue(
                "symbolic-method",
                "Unresolved request method",
                self.module,
                node.start_point.row + 1,
            )
        resource = self.modules[self.module].resource
        line = node.start_point.row + 1 + resource.line_offset
        column = node.start_point.column + 1
        evidence = [
            Evidence(
                "ast-call", txt(node)[:1000], resource.source_file or resource.url, line, column
            )
        ]
        if self.stack:
            evidence.append(Evidence("call-chain", " -> ".join(self.stack), self.module, line))
        if base:
            evidence.append(Evidence("factory-configuration", base, self.module, line))
        self.result.endpoints.append(
            Endpoint(
                method=method if method and not method.startswith("{") else None,
                url=url_text,
                protocol=protocol,
                headers=serializable(headers) or None,
                body=serializable(body),
                content_type=headers.get("Content-Type", headers.get("content-type")),
                source_file=resource.source_file or resource.url,
                source_line=line,
                source_column=column,
                callee=txt(child(node, "function") or child(node, "constructor")),
                framework=framework,
                confidence="low"
                if url_text.startswith("{")
                else "medium"
                if "{" in url_text or protocol in ("trpc", "connect-web")
                else "high",
                evidence=evidence,
            )
        )
        if framework in ("xhr", "superagent", "wretch", "request"):
            client.config["_endpoint"] = self.result.endpoints[-1]
            return client
        return PromiseValue(Unknown("response"))


class BudgetExceeded(Exception):
    def __init__(self, reason: str, source_file: str) -> None:
        super().__init__(reason)
        self.source_file = source_file


class ResourceBudgetExceeded(BudgetExceeded):
    """One source exhausted its share; independent sources may continue."""
