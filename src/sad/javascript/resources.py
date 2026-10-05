"""AST module references and source-map embedded source collection."""

from __future__ import annotations

import base64
import json
import re
from urllib.parse import urljoin

from sad.models import Resource

from .engine import child, children, txt, walk
from .parser import ASTCache
from .sourcemaps import sections


def references(resource: Resource, cache: ASTCache) -> list[str]:
    root = cache.parse(resource.content, resource.url).root_node
    found = []
    for node in walk(root):
        source = child(node, "source")
        if node.type in ("import_statement", "export_statement") and source:
            spec = txt(source)[1:-1]
            if spec.startswith((".", "/", "http")):
                found.append(urljoin(resource.url, spec))
        if node.type == "call_expression" and txt(child(node, "function")) in ("import", "require"):
            args = child(node, "arguments")
            if args and children(args) and children(args)[0].type == "string":
                spec = txt(children(args)[0])[1:-1]
                if spec.startswith((".", "/", "http")):
                    found.append(urljoin(resource.url, spec))
        if node.type == "comment":
            match = re.search(r"[#@]\s*sourceMappingURL\s*=\s*(\S+)", txt(node))
            if match:
                found.append(urljoin(resource.url, match[1]))
    return list(dict.fromkeys(found))


def source_map(resource: Resource, max_bytes: int = 8_000_000) -> list[Resource]:
    result = []
    total = 0
    for section_index, (value, _, _) in enumerate(sections(json.loads(resource.content))):
        sources, contents = value.get("sources", []), value.get("sourcesContent", [])
        if not isinstance(sources, list) or not isinstance(contents, list):
            raise ValueError("source map sources and sourcesContent must be arrays")
        root = value.get("sourceRoot", "")
        if not isinstance(root, str):
            raise ValueError("sourceRoot must be a string")
        for i, (source, content) in enumerate(zip(sources, contents, strict=False)):
            if not isinstance(content, str) or not isinstance(source, str):
                continue
            total += len(content.encode("utf-8"))
            if total > max_bytes:
                raise ValueError("source map exceeds expanded source budget")
            name = urljoin(resource.url, root + source)
            result.append(
                Resource(
                    name,
                    content,
                    source_file=f"{resource.url}#section-{section_index}:source-{i}:{source}",
                )
            )
    return result


def inline_source_map(url: str, max_bytes: int = 8_000_000) -> Resource:
    header, separator, payload = url.partition(",")
    if not separator or ";base64" not in header:
        raise ValueError("inline source maps must use base64 data URLs")
    if len(payload) > max_bytes * 2:
        raise ValueError("inline source map exceeds budget")
    content = base64.b64decode(payload, validate=True)
    if len(content) > max_bytes:
        raise ValueError("inline source map exceeds budget")
    return Resource("inline.map", content.decode("utf-8"), "application/json")
