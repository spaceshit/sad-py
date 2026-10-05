"""Deterministic serialization shared by every frontend."""

from __future__ import annotations

import csv
import io
import json
from dataclasses import asdict

from sad.models import AnalysisResult


def render(result: AnalysisResult, format: str = "json") -> str:
    if format == "json":
        return json.dumps(result.to_dict(), indent=2, ensure_ascii=False) + "\n"
    if format == "jsonl":
        return "".join(
            json.dumps({"schema_version": result.schema_version, **asdict(e)}, ensure_ascii=False)
            + "\n"
            for e in result.endpoints
        )
    if format == "csv":
        stream = io.StringIO(newline="")
        fields = [
            "method",
            "url",
            "protocol",
            "confidence",
            "origin",
            "source_file",
            "source_line",
            "evidence",
        ]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for endpoint in result.endpoints:
            row = asdict(endpoint)
            row["evidence"] = json.dumps(row["evidence"])
            writer.writerow({key: row[key] for key in fields})
        return stream.getvalue()
    if format == "table":
        return "METHOD   CONFIDENCE ORIGIN           URL\n" + "".join(
            f"{e.method or '?':8} {e.confidence:10} {e.origin:16} {e.url}\n"
            for e in result.endpoints
        )
    raise ValueError(f"unknown output format: {format}")
