"""Bounded content-addressed AST cache; no target code is ever executed."""

from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import Future
from hashlib import sha256
from threading import RLock

import tree_sitter_javascript as javascript
import tree_sitter_typescript as typescript
from tree_sitter import Language, Node, Parser, Query, QueryCursor, Tree


class ASTCache:
    def __init__(
        self,
        capacity: int = 128,
        max_bytes: int = 8_000_000,
        max_cache_bytes: int = 64_000_000,
    ) -> None:
        if capacity < 0 or max_bytes < 1 or max_cache_bytes < 0:
            raise ValueError("cache limits must be nonnegative and max_bytes must be positive")
        self.capacity = capacity
        self.max_bytes = max_bytes
        # Budget in source bytes; native AST allocations also consume memory.
        self.max_cache_bytes = max_cache_bytes
        self.cached_bytes = 0
        self.hits = 0
        self.misses = 0
        self._cache: OrderedDict[tuple[str, str], tuple[Tree, int]] = OrderedDict()
        self._pending: dict[tuple[str, str], Future[Tree]] = {}
        self._lock = RLock()
        self._queries: dict[tuple[Language, str], Query] = {}
        self._languages = {
            "js": Language(javascript.language()),
            "ts": Language(typescript.language_typescript()),
            "tsx": Language(typescript.language_tsx()),
        }

    def parse(self, source: str, filename: str) -> Tree:
        raw = source.encode("utf-8")
        if len(raw) > self.max_bytes:
            raise ValueError(f"resource exceeds {self.max_bytes} bytes: {filename}")
        ext = filename.split("?", 1)[0].rsplit(".", 1)[-1]
        language = ext if ext in ("ts", "tsx") else "js"
        key = (language, sha256(raw).hexdigest())
        with self._lock:
            if key in self._cache:
                self.hits += 1
                self._cache.move_to_end(key)
                return self._cache[key][0]
            pending = self._pending.get(key)
            if pending is None:
                pending = Future()
                self._pending[key] = pending
                self.misses += 1
                owner = True
            else:
                self.hits += 1
                owner = False
        if not owner:
            return pending.result()
        try:
            # Separate parsers per miss. Do not hold the shared cache lock during
            # parsing: unrelated requests and cache hits can proceed independently.
            tree = Parser(self._languages[language]).parse(raw)
            with self._lock:
                if self.capacity and len(raw) <= self.max_cache_bytes:
                    self._cache[key] = (tree, len(raw))
                    self.cached_bytes += len(raw)
                    while (
                        len(self._cache) > self.capacity or self.cached_bytes > self.max_cache_bytes
                    ):
                        _, (_, size) = self._cache.popitem(last=False)
                        self.cached_bytes -= size
            pending.set_result(tree)
            return tree
        except BaseException as exc:
            pending.set_exception(exc)
            raise
        finally:
            with self._lock:
                self._pending.pop(key, None)

    def select_nodes(self, tree: Tree, pattern: str, root: Node | None = None) -> list[Node]:
        """Select candidate nodes in native code rather than wrapping the whole AST."""
        key = (tree.language, pattern)
        with self._lock:
            query = self._queries.get(key)
            if query is None:
                query = Query(tree.language, pattern)
                self._queries[key] = query
        return sorted(
            QueryCursor(query)
            .captures(root if root is not None else tree.root_node)
            .get("candidate", []),
            key=lambda node: node.start_byte,
        )
