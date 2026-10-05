"""Versioned, JSON-compatible public models. None means an unknown value."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Confidence = Literal["high", "medium", "low"]
Origin = Literal["static-analysis", "observed", "observed+static", "inferred"]


@dataclass(frozen=True)
class Evidence:
    kind: str
    detail: str
    source_file: str
    source_line: int | None = None
    source_column: int | None = None


@dataclass
class Endpoint:
    method: str | None
    url: str
    protocol: str = "http"
    base_url: str | None = None
    path: str | None = None
    query_parameters: list[dict[str, str | None]] = field(default_factory=list)
    headers: dict[str, Any] | None = None
    body: Any = None
    content_type: str | None = None
    source_file: str = "<unknown>"
    source_line: int | None = None
    source_column: int | None = None
    callee: str | None = None
    framework: str | None = None
    confidence: Confidence = "high"
    evidence: list[Evidence] = field(default_factory=list)
    origin: Origin = "static-analysis"
    observations: list[dict[str, Any]] = field(default_factory=list)
    request_variants: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class Resource:
    url: str
    content: str
    media_type: str = "application/javascript"
    source_file: str | None = None
    line_offset: int = 0
    document_url: str | None = None

    def __post_init__(self) -> None:
        if not all(isinstance(value, str) for value in (self.url, self.content, self.media_type)):
            raise ValueError("resource url, content and media_type must be strings")


@dataclass
class HTTPExchange:
    url: str
    method: str = "GET"
    request_headers: dict[str, str] = field(default_factory=dict)
    request_body: str | None = None
    status: int | None = None
    response_headers: dict[str, str] = field(default_factory=dict)
    response_body: str | None = None
    source_file: str = "<traffic>"

    def __post_init__(self) -> None:
        if not all(isinstance(value, str) for value in (self.url, self.method, self.source_file)):
            raise ValueError("exchange url, method and source_file must be strings")
        for body in (self.request_body, self.response_body):
            if body is not None and not isinstance(body, str):
                raise ValueError("exchange bodies must be strings or null")
        for name in ("request_headers", "response_headers"):
            headers = getattr(self, name)
            if not isinstance(headers, dict) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in headers.items()
            ):
                raise ValueError("exchange headers must map strings to strings")
            setattr(self, name, {key.lower(): value for key, value in headers.items()})


@dataclass
class Dataset:
    resources: list[Resource] = field(default_factory=list)
    exchanges: list[HTTPExchange] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AnalysisIssue:
    code: str
    reason: str
    source_file: str | None = None
    source_line: int | None = None
    source_column: int | None = None


@dataclass
class AnalysisResult:
    endpoints: list[Endpoint] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)
    call_graph: list[tuple[str, str]] = field(default_factory=list)
    schema_version: str = "1.0"
    issues: list[AnalysisIssue] = field(default_factory=list)
    analysis_incomplete: bool = False

    def add_issue(
        self,
        code: str,
        reason: str,
        source_file: str | None = None,
        source_line: int | None = None,
        source_column: int | None = None,
    ) -> None:
        issue = AnalysisIssue(code, reason, source_file, source_line, source_column)
        if issue not in self.issues:
            self.issues.append(issue)
        if reason not in self.diagnostics:
            self.diagnostics.append(reason)
        self.analysis_incomplete = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
