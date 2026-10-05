"""Bounded resource collection with scope-checked canonical seed redirects."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from fnmatch import fnmatch
from urllib.parse import urljoin, urlsplit

import httpx

from sad.html import HTMLCollector
from sad.javascript.parser import ASTCache
from sad.javascript.resources import references
from sad.models import Dataset, Resource
from sad.normalization import normalize_url


def origin(url: str) -> tuple[str, str | None, int | None]:
    parts = urlsplit(url)
    return parts.scheme, parts.hostname, parts.port or (443 if parts.scheme == "https" else 80)


@dataclass(frozen=True)
class CrawlConfig:
    depth: int = 2
    max_pages: int = 30
    max_resources: int = 300
    max_bytes: int = 8_000_000
    max_total_bytes: int = 64_000_000
    timeout: float = 10.0
    allowed_origins: tuple[str, ...] = ()
    exclusions: tuple[str, ...] = ()
    max_redirects: int = 5

    def __post_init__(self) -> None:
        if (
            self.depth < 0
            or min(self.max_pages, self.max_resources, self.max_bytes, self.max_total_bytes) < 1
        ):
            raise ValueError("crawl limits must be positive; depth may be zero")
        if self.timeout <= 0 or self.max_redirects < 0:
            raise ValueError("invalid timeout or redirect limit")


class Crawler:
    def __init__(
        self,
        config: CrawlConfig | None = None,
        *,
        client: httpx.Client | None = None,
        cache: ASTCache | None = None,
    ) -> None:
        self.config = config or CrawlConfig()
        self.client = client
        self.cache = cache or ASTCache()

    def collect(self, seeds: list[str]) -> Dataset:
        cfg = self.config
        for seed in seeds:
            if urlsplit(seed).scheme not in ("http", "https"):
                raise ValueError("crawl seeds must be HTTP(S) URLs")
        allowed = {origin(s) for s in [*seeds, *cfg.allowed_origins]}
        seed_urls = {normalize_url(s) for s in seeds}

        def canonical_redirect(source: str, target: str) -> bool:
            source_scheme, source_host, source_port = origin(source)
            target_scheme, target_host, target_port = origin(target)
            return (
                bool(source_host and target_host)
                and (
                    source_host == target_host
                    or target_host == f"www.{source_host}"
                    or source_host == f"www.{target_host}"
                )
                and (
                    source_scheme == target_scheme
                    or (source_scheme, target_scheme) == ("http", "https")
                )
                and source_port == (443 if source_scheme == "https" else 80)
                and target_port == (443 if target_scheme == "https" else 80)
                and target_scheme in ("http", "https")
                and not urlsplit(target).username
                and not any(fnmatch(target, x) for x in cfg.exclusions)
            )

        def in_scope(url: str) -> bool:
            parts = urlsplit(url)
            return (
                parts.scheme in ("http", "https")
                and not parts.username
                and origin(url) in allowed
                and not any(fnmatch(url, x) for x in cfg.exclusions)
            )

        queue = deque((normalize_url(s), 0, True, s) for s in seeds)
        seen: set[str] = set()
        dataset = Dataset()
        pages = total_bytes = 0
        client = self.client or httpx.Client(
            timeout=cfg.timeout, follow_redirects=False, trust_env=False
        )
        try:
            while queue and len(seen) < cfg.max_resources:
                url, depth, page, document = queue.popleft()
                try:
                    url = normalize_url(url)
                except ValueError as exc:
                    dataset.diagnostics.append(f"Invalid resource URL: {exc}")
                    continue
                if url in seen or not in_scope(url) or (page and pages >= cfg.max_pages):
                    continue
                seen.add(url)
                if page:
                    pages += 1
                try:
                    current = url
                    for redirect in range(cfg.max_redirects + 1):
                        with client.stream(
                            "GET",
                            current,
                            follow_redirects=False,
                            timeout=cfg.timeout,
                            headers={"User-Agent": "SAD/0.1 static-resource-collector"},
                        ) as response:
                            if response.is_redirect:
                                target = normalize_url(
                                    urljoin(current, response.headers.get("location", ""))
                                )
                                if redirect == cfg.max_redirects:
                                    raise ValueError(f"blocked redirect to {target}")
                                if not in_scope(target):
                                    if (
                                        url in seed_urls
                                        and canonical_redirect(url, target)
                                        and canonical_redirect(current, target)
                                    ):
                                        allowed.add(origin(target))
                                    else:
                                        raise ValueError(f"blocked redirect to {target}")
                                current = target
                                continue
                            response.raise_for_status()
                            chunks = bytearray()
                            for chunk in response.iter_bytes():
                                total_bytes += len(chunk)
                                if (
                                    len(chunks) + len(chunk) > cfg.max_bytes
                                    or total_bytes > cfg.max_total_bytes
                                ):
                                    raise ValueError("download byte budget exceeded")
                                chunks.extend(chunk)
                            media = response.headers.get("content-type", "").split(";", 1)[0]
                            resource = Resource(
                                current,
                                chunks.decode("utf-8", errors="replace"),
                                media,
                                document_url=current if "html" in media else document,
                            )
                            dataset.resources.append(resource)
                            seen.add(current)
                            break
                    if total_bytes > cfg.max_total_bytes:
                        break
                    if "html" in media:
                        parser = HTMLCollector(resource)
                        parser.feed(resource.content)
                        queue.extend((s, depth, False, parser.base) for s in parser.scripts)
                        if depth < cfg.depth:
                            queue.extend((s, depth + 1, True, s) for s in parser.links)
                    elif (
                        "javascript" in media
                        or "ecmascript" in media
                        or urlsplit(current).path.endswith((".js", ".mjs", ".ts"))
                    ):
                        queue.extend(
                            (s, depth, False, document)
                            for s in references(resource, self.cache)
                            if not s.startswith("data:")
                        )
                except (httpx.HTTPError, ValueError) as exc:
                    dataset.diagnostics.append(f"{url}: {exc}")
                    if total_bytes > cfg.max_total_bytes:
                        break
            if queue:
                dataset.diagnostics.append(
                    "Collection stopped with queued resources; a configured limit was reached"
                )
        finally:
            if self.client is None:
                client.close()
        return dataset
