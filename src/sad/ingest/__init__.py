"""Capture normalization for Burp XML, normalized JSON, HAR and raw HTTP.

All imports are offline; URL lists are deliberately a separate crawler input.
"""

from __future__ import annotations

import base64
import binascii
import json
from pathlib import Path
from typing import Any
from urllib.parse import urljoin
from xml.etree.ElementTree import ParseError

from defusedxml import ElementTree

from sad.models import Dataset, HTTPExchange, Resource

MAX_IMPORT_BYTES = 64_000_000


def read_bounded(path: str | Path, limit: int = MAX_IMPORT_BYTES) -> bytes:
    with Path(path).open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"input exceeds {limit} bytes: {path}")
    return data


def raw_message(data: bytes | str) -> tuple[str, dict[str, str], str]:
    raw = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else data
    head, separator, body = raw.partition("\r\n\r\n")
    if not separator:
        head, _, body = raw.partition("\n\n")
    lines = head.splitlines()
    headers = {}
    for line in lines[1:]:
        key, colon, value = line.partition(":")
        if colon:
            headers[key.lower()] = value.strip()
    return lines[0] if lines else "", headers, body


def decode_capture(text: str, encoded: bool) -> str:
    if not encoded:
        return text
    try:
        return base64.b64decode("".join(text.split()), validate=True).decode(
            "utf-8", errors="replace"
        )
    except (ValueError, binascii.Error) as exc:
        raise ValueError("invalid base64 capture") from exc


class BurpImporter:
    @staticmethod
    def from_xml(path: str | Path) -> Dataset:
        try:
            root = ElementTree.fromstring(read_bounded(path))
        except ParseError as exc:
            raise ValueError(f"malformed Burp XML: {exc}") from exc
        dataset = Dataset()
        for index, item in enumerate(root.findall(".//item")):
            try:
                url = item.findtext("url") or ""
                request = item.find("request")
                response = item.find("response")
                req = (
                    decode_capture(request.text or "", request.get("base64") == "true")
                    if request is not None
                    else ""
                )
                resp = (
                    decode_capture(response.text or "", response.get("base64") == "true")
                    if response is not None
                    else ""
                )
                first, headers, body = raw_message(req)
                status_line, response_headers, response_body = raw_message(resp)
                status_parts = status_line.split()
                dataset.exchanges.append(
                    HTTPExchange(
                        url=url,
                        method=item.findtext("method") or (first.split()[0] if first else "GET"),
                        request_headers=headers,
                        request_body=body or None,
                        status=int(status_parts[1])
                        if len(status_parts) > 1 and status_parts[1].isdigit()
                        else None,
                        response_headers=response_headers,
                        response_body=response_body or None,
                        source_file=f"{path}#item-{index}",
                    )
                )
            except ValueError as exc:
                dataset.diagnostics.append(f"{path} item {index}: {exc}")
        return dataset

    @staticmethod
    def from_json(path: str | Path) -> Dataset:
        value = json.loads(read_bounded(path))
        if isinstance(value, dict) and "log" in value:
            return HARImporter.from_value(value, str(path))
        records = (
            value.get("exchanges", value.get("items", [])) if isinstance(value, dict) else value
        )
        if not isinstance(records, list):
            raise ValueError("normalized JSON must be a list or an object with exchanges/items")
        dataset = Dataset()
        for i, record in enumerate(records):
            try:
                if not isinstance(record, dict) or not isinstance(record.get("url"), str):
                    raise ValueError("record must contain a string url")
                allowed = HTTPExchange.__dataclass_fields__
                fields = {k: v for k, v in record.items() if k in allowed}
                fields.setdefault("source_file", f"{path}#item-{i}")
                for key in ("request_headers", "response_headers"):
                    if key in fields:
                        if not isinstance(fields[key], dict):
                            raise ValueError(f"{key} must be an object")
                        fields[key] = {str(k).lower(): str(v) for k, v in fields[key].items()}
                dataset.exchanges.append(HTTPExchange(**fields))
            except (TypeError, ValueError) as exc:
                dataset.diagnostics.append(f"{path} item {i}: {exc}")
        return dataset

    @staticmethod
    def load(path: str | Path, *, base_url: str | None = None) -> Dataset:
        suffix = Path(path).suffix.lower()
        if suffix == ".xml":
            return BurpImporter.from_xml(path)
        if suffix in (".json", ".har"):
            return BurpImporter.from_json(path)
        return RawHTTPImporter.from_file(path, base_url=base_url)


class HARImporter:
    @staticmethod
    def from_file(path: str | Path) -> Dataset:
        return HARImporter.from_value(json.loads(read_bounded(path)), str(path))

    @staticmethod
    def from_value(value: dict[str, Any], source: str = "<har>") -> Dataset:
        dataset = Dataset()
        for index, entry in enumerate(value.get("log", {}).get("entries", [])):
            try:
                request, response = entry["request"], entry.get("response", {})
                content = response.get("content", {})
                body = decode_capture(content.get("text", ""), content.get("encoding") == "base64")
                headers = {h["name"].lower(): h["value"] for h in response.get("headers", [])}
                headers.setdefault("content-type", content.get("mimeType", ""))
                dataset.exchanges.append(
                    HTTPExchange(
                        url=request["url"],
                        method=request.get("method", "GET"),
                        request_headers={
                            h["name"].lower(): h["value"] for h in request.get("headers", [])
                        },
                        request_body=request.get("postData", {}).get("text"),
                        status=response.get("status"),
                        response_headers=headers,
                        response_body=body or None,
                        source_file=f"{source}#entry-{index}",
                    )
                )
            except (KeyError, TypeError, ValueError) as exc:
                dataset.diagnostics.append(f"{source} entry {index}: {exc}")
        return dataset


class RawHTTPImporter:
    @staticmethod
    def from_file(path: str | Path, *, base_url: str | None = None) -> Dataset:
        return RawHTTPImporter.from_text(
            read_bounded(path).decode("utf-8", errors="replace"),
            base_url=base_url,
            source=str(path),
        )

    @staticmethod
    def from_text(raw: str, *, base_url: str | None = None, source: str = "<http>") -> Dataset:
        first, headers, body = raw_message(raw)
        fields = first.split()
        if first.startswith("HTTP/"):
            if not base_url:
                raise ValueError("a raw response requires base_url (its original request URL)")
            return Dataset(
                resources=[Resource(base_url, body, headers.get("content-type", ""), source)]
            )
        if len(fields) < 2:
            raise ValueError("invalid HTTP request line")
        if not base_url and not fields[1].startswith(("http://", "https://")):
            raise ValueError("a relative raw request requires base_url; scheme is not guessed")
        return Dataset(
            exchanges=[
                HTTPExchange(
                    url=urljoin(base_url or "", fields[1]),
                    method=fields[0],
                    request_headers=headers,
                    request_body=body or None,
                    source_file=source,
                )
            ]
        )
