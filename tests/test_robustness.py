import json
import runpy
import sys

import pytest
from jsonschema import validate

from sad import AnalysisResult, Analyzer, Dataset, HTTPExchange, Resource
from sad.cli import main
from sad.ingest import BurpImporter, RawHTTPImporter, raw_message, read_bounded
from sad.javascript.parser import ASTCache
from sad.models import Endpoint
from sad.normalization import correlate, normalize
from sad.output import render


@pytest.mark.parametrize(
    "kwargs",
    [
        {"url": 1, "content": ""},
        {"url": "x", "content": []},
        {"url": "x", "content": "", "media_type": None},
    ],
)
def test_resource_input_validation(kwargs):
    with pytest.raises(ValueError):
        Resource(**kwargs)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"url": 3},
        {"url": "x", "method": None},
        {"url": "x", "request_headers": []},
        {"url": "x", "response_headers": {"a": 3}},
        {"url": "x", "request_body": {}},
    ],
)
def test_exchange_input_validation(kwargs):
    with pytest.raises(ValueError):
        HTTPExchange(**kwargs)


def test_issue_deduplication_and_schema():
    from pathlib import Path

    result = AnalysisResult()
    result.add_issue("budget", "stopped", "app.js", 2, 3)
    result.add_issue("budget", "stopped", "app.js", 2, 3)
    assert len(result.issues) == 1 and result.analysis_incomplete
    validate(
        result.to_dict(),
        json.loads((Path(__file__).parents[1] / "docs/result.schema.json").read_text()),
    )


def test_capture_read_limits(tmp_path):
    file = tmp_path / "big"
    file.write_bytes(b"123456")
    with pytest.raises(ValueError):
        read_bounded(file, 3)
    assert raw_message(b"GET / HTTP/1.1\r\nInvalid\r\nX-A: b\r\n\r\nbody")[1] == {"x-a": "b"}


@pytest.mark.parametrize(
    "value",
    [
        {"items": []},
        {"exchanges": []},
        {"items": [None, {"url": 1}, {"url": "x", "request_headers": []}]},
        {"items": [{"url": "x", "request_headers": {"A": 1}}]},
    ],
)
def test_normalized_shapes(tmp_path, value):
    path = tmp_path / "capture.json"
    path.write_text(json.dumps(value))
    result = BurpImporter.load(path)
    assert isinstance(result, Dataset)
    if value.get("items") and value["items"][0] is None:
        assert len(result.diagnostics) == 3


@pytest.mark.parametrize("value", [None, 7, {"exchanges": None}])
def test_invalid_json_shape(tmp_path, value):
    path = tmp_path / "capture.json"
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        BurpImporter.from_json(path)


def test_har_via_autoimport_and_bad_entry(tmp_path):
    path = tmp_path / "capture.har"
    path.write_text(
        json.dumps(
            {
                "log": {
                    "entries": [
                        {},
                        {
                            "request": {"url": "https://e.test"},
                            "response": {"content": {"text": "%%%", "encoding": "base64"}},
                        },
                    ]
                }
            }
        )
    )
    assert len(BurpImporter.load(path).diagnostics) == 2


def test_raw_files_and_empty_inputs(tmp_path):
    path = tmp_path / "request.http"
    path.write_text("GET https://e.test/a HTTP/1.1\r\n\r\n")
    assert BurpImporter.load(path).exchanges[0].url == "https://e.test/a"
    for raw in ("", "HTTP/1.1 200 OK\r\n\r\n"):
        with pytest.raises(ValueError):
            RawHTTPImporter.from_text(raw)


def test_mixed_directory_and_bounds(tmp_path):
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "ignored.js").write_text('fetch("/ignored")')
    (tmp_path / "nested").mkdir()
    (tmp_path / "a.html").write_text('<form action="/form"></form>')
    (tmp_path / "traffic.json").write_text('[{"url":"https://e.test/a"}]')
    (tmp_path / "broken.json").write_text("{")
    (tmp_path / "large.js").write_text("x" * 100)
    result = Analyzer(max_resource_bytes=60).analyze_path(tmp_path)
    assert result.diagnostics and result.analysis_incomplete
    assert "/ignored" not in {e.url for e in result.endpoints}
    assert "/form" in {e.url for e in result.endpoints}
    assert Analyzer(max_resource_bytes=1).analyze_js('fetch("/a")').analysis_incomplete


def test_duplicate_resources_and_invalid_url():
    resource = Resource("a.js", 'fetch("/a")')
    assert len(Analyzer().analyze_dataset(Dataset(resources=[resource, resource])).endpoints) == 1
    result = Analyzer().analyze_js('fetch("http://[bad")')
    assert result.analysis_incomplete


def test_lru_eviction_and_byte_bounds():
    cache = ASTCache(capacity=1, max_bytes=20)
    cache.parse('fetch("/a")', "a.js")
    cache.parse('fetch("/b")', "b.ts")
    cache.parse('fetch("/a")', "a.js")
    assert cache.misses == 3
    with pytest.raises(ValueError):
        cache.parse("x" * 21, "big.js")


def test_protocol_separation_and_query_preservation():
    endpoints = [
        normalize(Endpoint("GET", "https://e.test/a?x=1", protocol="websocket")),
        normalize(Endpoint("GET", "https://e.test/a?x=2", origin="observed")),
        normalize(Endpoint("GET", "https://other.test/a", origin="observed")),
    ]
    assert len(correlate(endpoints)) == 3
    assert endpoints[0].query_parameters == [{"name": "x", "value": "1"}]
    target = normalize(Endpoint("POST", "/x", body={"a": 1}))
    other = normalize(
        Endpoint("POST", "/x", body={"a": 2}, query_parameters=[{"name": "x", "value": "3"}])
    )
    result = correlate([target, other])
    assert len(result) == 1 and len(result[0].request_variants) == 2


def test_unknown_method_and_export_errors():
    result = Analyzer().analyze_js('fetch("/x",{method:unknown})')
    assert result.endpoints[0].method is None
    assert "?" in render(result, "table")
    with pytest.raises(ValueError):
        render(result, "not-a-format")


def test_cli_output_strict_and_main_module(tmp_path, monkeypatch, capsys):
    source = tmp_path / "a.js"
    source.write_text("fetch(`/u/${id}`)")
    dest = tmp_path / "out.json"
    assert main(["analyze-js", str(source), "--strict", "--format", "json", "-o", str(dest)]) == 1
    assert json.loads(dest.read_text())["analysis_incomplete"]
    monkeypatch.setattr(sys, "argv", ["sad", "--version"])
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("sad", run_name="__main__")
    assert exc.value.code == 0
    assert "0.1.0" in capsys.readouterr().out


def test_schema_accepts_legacy_result_without_additive_fields():
    from pathlib import Path

    result = Analyzer().analyze_js('fetch("/legacy")').to_dict()
    result.pop("issues")
    result.pop("analysis_incomplete")
    for endpoint in result["endpoints"]:
        endpoint.pop("request_variants")
    validate(
        result, json.loads((Path(__file__).parents[1] / "docs/result.schema.json").read_text())
    )
