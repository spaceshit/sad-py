"""Conservative normalization and correlation, preserving observed requests."""

from __future__ import annotations

import re
from collections import defaultdict
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


def merge(target: Endpoint, other: Endpoint) -> None:
    for endpoint in (target, other):
        variant = {
            "url": endpoint.url,
            "method": endpoint.method,
            "headers": endpoint.headers,
            "body": endpoint.body,
            "content_type": endpoint.content_type,
            "source_file": endpoint.source_file,
        }
        if variant not in target.request_variants:
            target.request_variants.append(variant)
    for variant in other.request_variants:
        if variant not in target.request_variants:
            target.request_variants.append(variant)
    for evidence in other.evidence:
        if evidence not in target.evidence:
            target.evidence.append(evidence)
    target.observations.extend(o for o in other.observations if o not in target.observations)
    for parameter in other.query_parameters:
        if parameter not in target.query_parameters:
            target.query_parameters.append(parameter)
    origins = {target.origin, other.origin}
    if "observed+static" in origins or ("observed" in origins and "static-analysis" in origins):
        target.origin = "observed+static"


def correlate(endpoints: list[Endpoint]) -> list[Endpoint]:
    static = [e for e in endpoints if e.origin != "observed"]
    observed = [e for e in endpoints if e.origin == "observed"]
    remaining: list[Endpoint] = []
    for observation in observed:
        candidates = [s for s in static if matches(s, observation)]
        if candidates:
            # Prefer an exact path over a symbolic path. Preserve all raw observations.
            candidates.sort(key=lambda e: ("{" in (e.path or ""), e.url))
            merge(candidates[0], observation)
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
                merge(target, other)
            static.append(target)
        else:
            static.extend(group)
    unique: dict[tuple[str, str | None, str], Endpoint] = {}
    for endpoint in static:
        key = (endpoint.protocol, endpoint.method, endpoint.url)
        if key in unique:
            merge(unique[key], endpoint)
        else:
            unique[key] = endpoint
    return sorted(unique.values(), key=lambda e: (e.url, e.method or "", e.protocol))
