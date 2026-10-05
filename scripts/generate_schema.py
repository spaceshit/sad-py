"""Regenerate the public JSON Schema from dataclass annotations."""

from __future__ import annotations

import dataclasses
import json
import types
from pathlib import Path
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints

from sad.models import AnalysisResult


def main() -> None:
    definitions: dict[str, Any] = {}

    def schema(annotation: Any) -> dict[str, Any]:
        origin = get_origin(annotation)
        args = get_args(annotation)
        if annotation is Any:
            return {}
        if annotation is type(None):
            return {"type": "null"}
        if annotation in (str, int, float, bool):
            return {
                "type": {str: "string", int: "integer", float: "number", bool: "boolean"}[
                    annotation
                ]
            }
        if origin in (Union, types.UnionType):
            return {"anyOf": [schema(arg) for arg in args]}
        if origin is Literal:
            return {"enum": list(args)}
        if origin is list:
            return {"type": "array", "items": schema(args[0])}
        if origin is tuple:
            return {
                "type": "array",
                "prefixItems": [schema(arg) for arg in args],
                "minItems": len(args),
                "maxItems": len(args),
            }
        if origin is dict:
            return {"type": "object", "additionalProperties": schema(args[1])}
        if dataclasses.is_dataclass(annotation):
            name = annotation.__name__
            if name not in definitions:
                fields = get_type_hints(annotation)
                definitions[name] = {
                    "type": "object",
                    "required": [
                        key
                        for key in fields
                        if key not in {"issues", "analysis_incomplete", "request_variants"}
                    ],
                    "properties": {key: schema(value) for key, value in fields.items()},
                    "additionalProperties": True,
                }
            return {"$ref": f"#/$defs/{name}"}
        raise TypeError(annotation)

    root = schema(AnalysisResult)
    definitions["AnalysisResult"]["properties"]["schema_version"] = {"const": "1.0"}
    document = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        **root,
        "$defs": definitions,
    }
    destination = Path(__file__).resolve().parents[1] / "docs" / "result.schema.json"
    destination.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
