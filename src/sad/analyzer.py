"""Library-first analysis orchestration; all CLI and integrations use this API."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from sad.crawler import CrawlConfig, Crawler
from sad.html import HTMLCollector
from sad.ingest import BurpImporter, read_bounded
from sad.javascript.engine import Engine
from sad.javascript.parser import ASTCache
from sad.javascript.resources import inline_source_map, references, source_map
from sad.javascript.sourcemaps import SourceMapIndex
from sad.models import (
    AnalysisIssue,
    AnalysisResult,
    Dataset,
    Endpoint,
    Evidence,
    HTTPExchange,
    Resource,
)
from sad.normalization import correlate, normalize
from sad.protocols import SinkRegistry


class Analyzer:
    """Reusable analyzer. AST cache is shared; each analysis has isolated value state.

    Input files and datasets are offline. Only analyze_url/analyze_urls make requests.
    Environment substitutions are explicit; the host environment is never imported.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        environment: dict[str, str] | None = None,
        registry: SinkRegistry | None = None,
        max_steps: int = 200_000,
        max_depth: int = 20,
        max_symbolic_loop_iterations: int = 2,
        max_loop_steps: int = 2000,
        max_context_steps: int = 1000,
        max_resource_bytes: int = 8_000_000,
        max_cache_bytes: int = 64_000_000,
        path_mappings: dict[str, str] | None = None,
    ) -> None:
        if max_steps < 1:
            raise ValueError("max_steps must be positive")
        if not 1 <= max_symbolic_loop_iterations <= 64:
            raise ValueError("max_symbolic_loop_iterations must be between 1 and 64")
        if max_loop_steps < 1:
            raise ValueError("max_loop_steps must be positive")
        if max_context_steps < 1:
            raise ValueError("max_context_steps must be positive")
        self.max_context_steps = max_context_steps
        self.max_loop_steps = max_loop_steps
        self.max_symbolic_loop_iterations = max_symbolic_loop_iterations
        self.path_mappings = path_mappings or {}
        self.base_url = base_url
        self.environment = environment
        self.registry = registry or SinkRegistry()
        self.max_steps = max_steps
        self.max_depth = max_depth
        self.cache = ASTCache(max_bytes=max_resource_bytes, max_cache_bytes=max_cache_bytes)

    def analyze_js(self, source: str, *, filename: str = "input.js") -> AnalysisResult:
        return self.analyze_dataset(Dataset(resources=[Resource(filename, source)]))

    def analyze_url(self, url: str, *, config: CrawlConfig | None = None) -> AnalysisResult:
        return self.analyze_urls([url], config=config)

    def analyze_urls(self, urls: list[str], *, config: CrawlConfig | None = None) -> AnalysisResult:
        return self.analyze_dataset(Crawler(config, cache=self.cache).collect(urls))

    def analyze_exchanges(self, exchanges: list[HTTPExchange]) -> AnalysisResult:
        return self.analyze_dataset(Dataset(exchanges=exchanges))

    def analyze_path(self, path: str | Path) -> AnalysisResult:
        root = Path(path)
        if not root.exists():
            raise FileNotFoundError(path)
        dataset = Dataset()
        files = sorted(root.rglob("*")) if root.is_dir() else [root]
        for file in files:
            if not file.is_file() or file.is_symlink():
                continue
            if any(part in ("node_modules", ".git", ".venv", "__pycache__") for part in file.parts):
                continue
            suffix = file.suffix.lower()
            if suffix in (".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".html", ".htm", ".map"):
                try:
                    content = read_bounded(file, self.cache.max_bytes).decode(
                        "utf-8", errors="replace"
                    )
                    media = (
                        "text/html"
                        if suffix in (".html", ".htm")
                        else "application/json"
                        if suffix == ".map"
                        else "application/javascript"
                    )
                    dataset.resources.append(Resource(file.as_posix(), content, media))
                except (OSError, ValueError) as exc:
                    dataset.diagnostics.append(str(exc))
            elif suffix in (".xml", ".har", ".http", ".json"):
                try:
                    imported = BurpImporter.load(file, base_url=self.base_url)
                    dataset.resources.extend(imported.resources)
                    dataset.exchanges.extend(imported.exchanges)
                    dataset.diagnostics.extend(imported.diagnostics)
                except (ValueError, OSError) as exc:
                    dataset.diagnostics.append(f"{file}: {exc}")
        return self.analyze_dataset(dataset)

    def analyze_dataset(self, dataset: Dataset) -> AnalysisResult:
        result = AnalysisResult(diagnostics=list(dataset.diagnostics))
        resources = list(dataset.resources)
        for exchange in dataset.exchanges:
            result.endpoints.append(
                Endpoint(
                    method=exchange.method,
                    url=exchange.url,
                    protocol="websocket"
                    if exchange.request_headers.get("upgrade", "").lower() == "websocket"
                    else "http",
                    headers=exchange.request_headers or None,
                    body=exchange.request_body,
                    source_file=exchange.source_file,
                    content_type=exchange.request_headers.get("content-type"),
                    origin="observed",
                    evidence=[
                        Evidence(
                            "captured-request",
                            f"{exchange.method} {exchange.url}",
                            exchange.source_file,
                        )
                    ],
                    observations=[
                        {
                            "url": exchange.url,
                            "method": exchange.method,
                            "status": exchange.status,
                            "headers": exchange.request_headers,
                            "body": exchange.request_body,
                        }
                    ],
                )
            )
            if exchange.response_body is not None:
                resources.append(
                    Resource(
                        exchange.url,
                        exchange.response_body,
                        exchange.response_headers.get("content-type", ""),
                        exchange.source_file,
                    )
                )
        javascript = []
        maps: dict[str, SourceMapIndex] = {}
        map_aliases: dict[str, list[str]] = {}
        origins: dict[str, str | None] = {}
        seen: set[tuple[str, str]] = set()
        for resource in resources:
            key = (resource.url, resource.content)
            if key in seen:
                continue
            seen.add(key)
            if len(resource.content.encode("utf-8")) > self.cache.max_bytes:
                result.diagnostics.append(f"resource too large: {resource.url}")
                continue
            source = resource.source_file or resource.url
            base = (
                self.base_url
                or resource.document_url
                or (resource.url if urlsplit(resource.url).scheme in ("http", "https") else None)
            )
            origins[source] = base
            path = urlsplit(resource.url).path
            try:
                if "html" in resource.media_type or path.endswith((".html", ".htm")):
                    parser = HTMLCollector(resource)
                    parser.feed(resource.content)
                    javascript.extend(parser.inline)
                    result.endpoints.extend(parser.forms)
                elif path.endswith(".map"):
                    maps[resource.url] = SourceMapIndex(resource)
                    maps[resource.url.removesuffix(".map")] = maps[resource.url]
                    expanded = source_map(resource, self.cache.max_bytes)
                    javascript.extend(expanded)
                    for item in expanded:
                        origins[item.source_file or item.url] = base
                elif (
                    "javascript" in resource.media_type
                    or "ecmascript" in resource.media_type
                    or path.endswith((".js", ".mjs", ".cjs", ".ts", ".jsx", ".tsx"))
                ):
                    javascript.append(resource)
                    for reference in references(resource, self.cache):
                        if reference.startswith("data:"):
                            inline = inline_source_map(reference, self.cache.max_bytes)
                            inline = Resource(
                                resource.url + ".map", inline.content, inline.media_type
                            )
                            maps[resource.source_file or resource.url] = SourceMapIndex(inline)
                            expanded = source_map(inline, self.cache.max_bytes)
                            javascript.extend(expanded)
                            for item in expanded:
                                origins[item.source_file or item.url] = base
                        else:
                            map_aliases.setdefault(resource.source_file or resource.url, []).append(
                                reference
                            )
            except (ValueError, RecursionError) as exc:
                result.diagnostics.append(f"{resource.url}: {exc}")
        for source, candidates in map_aliases.items():
            for reference in candidates:
                if reference in maps:
                    maps[source] = maps[reference]
        engine = Engine(
            self.cache,
            self.registry,
            max_steps=self.max_steps,
            max_depth=self.max_depth,
            max_symbolic_loop_iterations=self.max_symbolic_loop_iterations,
            max_loop_steps=self.max_loop_steps,
            max_context_steps=self.max_context_steps,
            environment=self.environment,
            path_mappings=self.path_mappings,
        )
        static = engine.analyze(javascript)
        result.endpoints.extend(static.endpoints)
        result.diagnostics.extend(static.diagnostics)
        result.call_graph = static.call_graph
        result.issues.extend(static.issues)
        result.analysis_incomplete = static.analysis_incomplete
        valid = []
        for endpoint in result.endpoints:
            try:
                valid.append(normalize(endpoint, origins.get(endpoint.source_file, self.base_url)))
                mapping = maps.get(endpoint.source_file)
                location = (
                    mapping.lookup(endpoint.source_line or 1, endpoint.source_column or 1)
                    if mapping
                    else None
                )
                if location:
                    endpoint.evidence.append(
                        Evidence(
                            "source-map",
                            f"Mapped from {endpoint.source_file}:{endpoint.source_line}",
                            location.source_file,
                            location.line,
                            location.column,
                        )
                    )
                    endpoint.source_file, endpoint.source_line, endpoint.source_column = (
                        location.source_file,
                        location.line,
                        location.column,
                    )
            except ValueError as exc:
                result.diagnostics.append(f"Invalid endpoint {endpoint.url!r}: {exc}")
        result.endpoints = correlate(valid)
        issue_reasons = {issue.reason for issue in result.issues}
        for diagnostic in dict.fromkeys(result.diagnostics):
            if diagnostic not in issue_reasons:
                result.issues.append(AnalysisIssue("input-incomplete", diagnostic))
                result.analysis_incomplete = True
        result.diagnostics = sorted(set(result.diagnostics))
        result.issues.sort(
            key=lambda issue: (
                issue.source_file or "",
                issue.source_line or 0,
                issue.code,
                issue.reason,
            )
        )
        return result
