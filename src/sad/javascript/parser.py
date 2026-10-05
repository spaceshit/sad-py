"""Bounded content-addressed AST cache; no target code is ever executed."""

from __future__ import annotations

from collections import OrderedDict
from hashlib import sha256
from threading import RLock

import tree_sitter_javascript as javascript
import tree_sitter_typescript as typescript
from tree_sitter import Language, Parser, Tree


class ASTCache:
    def __init__(self, capacity: int = 128, max_bytes: int = 8_000_000) -> None:
        self.capacity = capacity
        self.max_bytes = max_bytes
        self.hits = 0
        self.misses = 0
        self._cache: OrderedDict[tuple[str, str], Tree] = OrderedDict()
        self._lock = RLock()
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
                return self._cache[key]
            self.misses += 1
            tree = Parser(self._languages[language]).parse(raw)
            self._cache[key] = tree
            while len(self._cache) > self.capacity:
                self._cache.popitem(last=False)
            return tree
