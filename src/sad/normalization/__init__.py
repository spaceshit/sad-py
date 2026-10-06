"""Conservative normalization and correlation, preserving observed requests."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any
from urllib.parse import parse_qsl, urljoin, urlsplit, urlunsplit

from sad.models import Endpoint


def normalize_url(url: str, base_url: str | None = None) -> str:
    if base_url:
        url = urljoin(base_url, url)
    parts = urlsplit(url)
    # Preserve path case, encoded delimiters, query order and symbolic components.
    host = parts.netloc.lower()
    if (parts.scheme.lower() == "https" and host.endswith(":443")) or (
        parts.scheme.lower() == "http" and host.endswith(":80")
    ):
        host = host.rsplit(":", 1)[0]
    return urlunsplit(
        (parts.scheme.lower(), host, parts.path or ("/" if host else ""), parts.query, "")
    )


def normalize(endpoint: Endpoint, base_url: str | None = None) -> Endpoint:
    endpoint.url = normalize_url(endpoint.url, base_url)
    parts = urlsplit(endpoint.url)
    endpoint.base_url = f"{parts.scheme}://{parts.netloc}" if parts.netloc else None
    endpoint.path = parts.path
    endpoint.query_parameters.extend(
        {"name": k, "value": v}
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if {"name": k, "value": v} not in endpoint.query_parameters
    )
    if endpoint.method:
        endpoint.method = endpoint.method.upper()
    return endpoint


def matches(template: Endpoint, observed: Endpoint) -> bool:
    if template.protocol != observed.protocol:
        return False
    if template.method != observed.method:
        return False
    if template.base_url != observed.base_url:
        return False
    pattern = re.escape(template.path or "")
    pattern = re.sub(r"\\\{[^}]+\\\}", "[^/]+", pattern)
    return re.fullmatch(pattern, observed.path or "") is not None


def _value_hash(value: Any) -> int:
    """Equality-compatible hashes for JSON values; collisions are checked below."""
    if isinstance(value, dict):
        return hash(frozenset((_value_hash(k), _value_hash(v)) for k, v in value.items()))
    if isinstance(value, (list, tuple)):
        return hash(tuple(_value_hash(v) for v in value))
    try:
        return hash(value)
    except TypeError:
        return 0


class _ListIndex:
    def __init__(self, values: list[Any]) -> None:
        self.values = values
        self.buckets: dict[int, list[Any]] = defaultdict(list)
        for value in values:
            self.buckets[self.key(value)].append(value)

    @staticmethod
    def key(value: Any) -> int:
        try:
            return _value_hash(value)
        except RecursionError:
            return 0

    def append(self, value: Any) -> None:
        bucket = self.buckets[self.key(value)]
        if value not in bucket:
            bucket.append(value)
            self.values.append(value)


class _MergeIndex:
    def __init__(self, endpoint: Endpoint) -> None:
        self.variants = _ListIndex(endpoint.request_variants)
        self.evidence = _ListIndex(endpoint.evidence)
        self.observations = _ListIndex(endpoint.observations)
        self.parameters = _ListIndex(endpoint.query_parameters)


def merge(target: Endpoint, other: Endpoint, *, _index: _MergeIndex | None = None) -> None:
    index = _index or _MergeIndex(target)
    for endpoint in (target, other):
        index.variants.append(
            {
                "url": endpoint.url,
                "method": endpoint.method,
                "headers": endpoint.headers,
                "body": endpoint.body,
                "content_type": endpoint.content_type,
                "source_file": endpoint.source_file,
            }
        )
    for variant in other.request_variants:
        index.variants.append(variant)
    for evidence in other.evidence:
        index.evidence.append(evidence)
    for observation in other.observations:
        index.observations.append(observation)
    for parameter in other.query_parameters:
        index.parameters.append(parameter)
    origins = {target.origin, other.origin}
    if "observed+static" in origins or ("observed" in origins and "static-analysis" in origins):
        target.origin = "observed+static"


def correlate(endpoints: list[Endpoint]) -> list[Endpoint]:
    merge_indexes: dict[int, _MergeIndex] = {}

    def indexed_merge(target: Endpoint, other: Endpoint) -> None:
        index = merge_indexes.get(id(target))
        if index is None:
            index = _MergeIndex(target)
            merge_indexes[id(target)] = index
        merge(target, other, _index=index)

    static = [e for e in endpoints if e.origin != "observed"]
    observed = [e for e in endpoints if e.origin == "observed"]
    # Exact paths are hash lookups. Symbolic paths are indexed by their
    # literal prefix and compiled once, avoiding regex cache thrashing and
    # a full static x observed scan on large captures.
    exact: dict[tuple[str, str | None, str | None, str], Endpoint] = {}
    symbolic: dict[
        tuple[str, str | None, str | None, str], list[tuple[re.Pattern[str], Endpoint]]
    ] = defaultdict(list)
    prefix_lengths: dict[tuple[str, str | None, str | None], set[int]] = defaultdict(set)
    for endpoint in static if observed else []:
        path = endpoint.path or ""
        prefix, separator, _ = path.partition("{")
        index_key = (endpoint.protocol, endpoint.method, endpoint.base_url, prefix)
        if separator:
            pattern = re.sub(r"\\\{[^}]+\\\}", "[^/]+", re.escape(path))
            symbolic[index_key].append((re.compile(pattern), endpoint))
            prefix_lengths[(endpoint.protocol, endpoint.method, endpoint.base_url)].add(len(prefix))
        elif index_key not in exact or endpoint.url < exact[index_key].url:
            exact[index_key] = endpoint
    remaining: list[Endpoint] = []
    for observation in observed:
        path = observation.path or ""
        match_group = (observation.protocol, observation.method, observation.base_url)
        target = exact.get((*match_group, path))
        if target is None:
            candidates = (
                endpoint
                for index in prefix_lengths.get(match_group, ())
                if index <= len(path)
                for pattern, endpoint in symbolic.get((*match_group, path[:index]), ())
                if pattern.fullmatch(path) is not None
            )
            target = min(candidates, key=lambda e: e.url, default=None)
        if target is not None:
            indexed_merge(target, observation)
        else:
            remaining.append(observation)
    groups: dict[tuple[str, str | None, str], list[Endpoint]] = defaultdict(list)
    for observation in remaining:
        pattern = re.sub(r"(?<=/)\d+(?=/|$|\?)", "{id}", observation.url)
        groups[(observation.protocol, observation.method, pattern)].append(observation)
    for (_, _, pattern), group in groups.items():
        if len({e.path for e in group}) >= 2 and "{id}" in pattern:
            target = group[0]
            target.url = pattern
            normalize(target)
            for other in group[1:]:
                indexed_merge(target, other)
            static.append(target)
        else:
            static.extend(group)
    unique: dict[tuple[str, str | None, str], Endpoint] = {}
    for endpoint in static:
        key = (endpoint.protocol, endpoint.method, endpoint.url)
        if key in unique:
            indexed_merge(unique[key], endpoint)
        else:
            unique[key] = endpoint
    return sorted(unique.values(), key=lambda e: (e.url, e.method or "", e.protocol))
