"""Bounded source-map v3 decoding; source names are identifiers, never file reads."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

from sad.models import Resource

ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"


def vlq(segment: str) -> list[int]:
    values = []
    value = shift = 0
    for char in segment:
        digit = ALPHABET.find(char)
        if digit < 0 or shift > 35:
            raise ValueError("invalid source-map VLQ")
        value |= (digit & 31) << shift
        if digit & 32:
            shift += 5
        else:
            values.append(-(value >> 1) if value & 1 else value >> 1)
            value = shift = 0
    if shift:
        raise ValueError("unfinished source-map VLQ")
    return values


def sections(
    value: Any, line: int = 0, column: int = 0, depth: int = 0
) -> list[tuple[dict[str, Any], int, int]]:
    if depth > 16 or not isinstance(value, dict) or value.get("version") != 3:
        raise ValueError("invalid or excessively nested version 3 source map")
    if "sections" not in value:
        return [(value, line, column)]
    result = []
    if not isinstance(value["sections"], list):
        raise ValueError("source-map sections must be an array")
    for section in value["sections"]:
        if not isinstance(section, dict) or "map" not in section:
            raise ValueError("external source-map sections require supplied content")
        offset = section.get("offset", {})
        if not isinstance(offset, dict):
            raise ValueError("invalid section offset")
        row, col = offset.get("line"), offset.get("column")
        if not isinstance(row, int) or not isinstance(col, int) or min(row, col) < 0:
            raise ValueError("invalid section offset")
        result.extend(
            sections(section["map"], line + row, (column if row == 0 else 0) + col, depth + 1)
        )
    return result


@dataclass(frozen=True)
class Location:
    source_file: str
    line: int
    column: int


class SourceMapIndex:
    def __init__(self, resource: Resource) -> None:
        self.lines: dict[int, list[tuple[int, Location | None]]] = {}
        for value, row_offset, col_offset in sections(json.loads(resource.content)):
            source_index = original_line = original_column = 0
            sources = value.get("sources", [])
            mappings = value.get("mappings", "")
            root = value.get("sourceRoot", "")
            if not isinstance(root, str):
                raise ValueError("sourceRoot must be a string")
            if not isinstance(sources, list) or not isinstance(mappings, str):
                raise ValueError("invalid source-map sources/mappings")
            for row, encoded in enumerate(mappings.split(";")):
                generated_column = col_offset if row == 0 else 0
                for segment in filter(None, encoded.split(",")):
                    fields = vlq(segment)
                    if len(fields) not in (1, 4, 5):
                        raise ValueError("invalid mapping segment length")
                    generated_column += fields[0]
                    location = None
                    if len(fields) >= 4:
                        source_index += fields[1]
                        original_line += fields[2]
                        original_column += fields[3]
                        if (
                            not 0 <= source_index < len(sources)
                            or min(original_line, original_column) < 0
                        ):
                            raise ValueError("source-map location out of range")
                        source = sources[source_index]
                        if not isinstance(source, str):
                            raise ValueError("source-map source must be a string")
                        location = Location(
                            urljoin(resource.url, root + source),
                            original_line + 1,
                            original_column + 1,
                        )
                    self.lines.setdefault(row + row_offset + 1, []).append(
                        (generated_column + 1, location)
                    )
        for entries in self.lines.values():
            entries.sort(key=lambda item: item[0])

    def lookup(self, line: int, column: int) -> Location | None:
        location = None
        for generated, candidate in self.lines.get(line, []):
            if generated > column:
                break
            location = candidate
        return location
