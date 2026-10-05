import base64
import json

import pytest
from defusedxml.common import DefusedXmlException

from sad import Analyzer, Dataset, HTTPExchange, Resource
from sad.ingest import BurpImporter, HARImporter, RawHTTPImporter


def test_burp_captured_js_discovers_unseen(tmp_path):
    req = base64.b64encode(b"GET /app.js HTTP/1.1\r\nHost: example.com\r\n\r\n").decode()
    resp = base64.b64encode(
        b'HTTP/1.1 200 OK\r\nContent-Type: application/javascript\r\n\r\nfetch("/unseen",{method:"POST"})'
    ).decode()
    path = tmp_path / "burp.xml"
    path.write_text(
        f'<items><item><url>https://example.com/app.js</url><request base64="true">{req}</request><response base64="true">{resp}</response></item></items>'
    )
    result = Analyzer().analyze_dataset(BurpImporter.from_xml(path))
    endpoints = {e.url: e for e in result.endpoints}
    assert endpoints["https://example.com/app.js"].origin == "observed"
    assert endpoints["https://example.com/unseen"].origin == "static-analysis"


def test_har_and_json(tmp_path):
    path = tmp_path / "history.har"
    path.write_text(
        json.dumps(
            {
                "log": {
                    "entries": [
                        {
                            "request": {"url": "https://e.test/a.js"},
                            "response": {
                                "status": 200,
                                "content": {
                                    "mimeType": "application/javascript",
                                    "text": 'fetch("/new")',
                                },
                            },
                        }
                    ]
                }
            }
        )
    )
    result = Analyzer().analyze_dataset(HARImporter.from_file(path))
    assert any(e.url == "https://e.test/new" for e in result.endpoints)
    path.write_text(json.dumps([{"url": "https://e.test/observed", "method": "POST"}]))
    assert BurpImporter.from_json(path).exchanges[0].method == "POST"


def test_raw_request_and_response():
    dataset = RawHTTPImporter.from_text(
        "POST /a HTTP/1.1\r\nHost: e.test\r\n\r\nhello", base_url="https://e.test"
    )
    assert dataset.exchanges[0].request_body == "hello"
    dataset = RawHTTPImporter.from_text(
        'HTTP/1.1 200 OK\nContent-Type: application/javascript\n\nfetch("/hidden")',
        base_url="https://e.test/app.js",
    )
    assert Analyzer().analyze_dataset(dataset).endpoints[0].url == "https://e.test/hidden"
    with pytest.raises(ValueError):
        RawHTTPImporter.from_text("GET /a HTTP/1.1\r\nHost: e.test\r\n\r\n")


def test_correlation_preserves_observations():
    result = Analyzer().analyze_dataset(
        Dataset(
            resources=[
                Resource("https://e.test/app.js", 'fetch(`/users/${id}`,{method:"DELETE"})')
            ],
            exchanges=[
                HTTPExchange("https://e.test/users/123", "DELETE"),
                HTTPExchange("https://e.test/users/456", "DELETE"),
            ],
        )
    )
    assert len(result.endpoints) == 1
    endpoint = result.endpoints[0]
    assert endpoint.origin == "observed+static"
    assert len(endpoint.observations) == 2
    assert endpoint.url == "https://e.test/users/{id}"


def test_numeric_generalization_needs_multiple_paths():
    single = Analyzer().analyze_exchanges([HTTPExchange("https://e.test/u/123")])
    assert single.endpoints[0].url.endswith("/123")
    multiple = Analyzer().analyze_exchanges(
        [HTTPExchange("https://e.test/u/123"), HTTPExchange("https://e.test/u/456")]
    )
    assert len(multiple.endpoints) == 1
    assert multiple.endpoints[0].url.endswith("/{id}")
    assert len(multiple.endpoints[0].observations) == 2


def test_html_inline_and_forms():
    result = Analyzer().analyze_dataset(
        Dataset(
            resources=[
                Resource(
                    "https://e.test/page",
                    """
    <form method="POST" action="/submit"><input name="token" value="x"></form>
    <script>fetch('/inline')</script><script type="application/json">{"not":"code"}</script>
    """,
                    "text/html",
                )
            ]
        )
    )
    assert {e.url for e in result.endpoints} == {"https://e.test/submit", "https://e.test/inline"}
    assert next(e for e in result.endpoints if e.framework == "html-form").body == {"token": "x"}


def test_xml_entities_rejected(tmp_path):
    path = tmp_path / "evil.xml"
    path.write_text('<!DOCTYPE x [<!ENTITY a SYSTEM "file:///etc/passwd">]><items>&a;</items>')
    with pytest.raises(DefusedXmlException):
        BurpImporter.from_xml(path)


def test_malformed_capture_diagnostic(tmp_path):
    path = tmp_path / "bad.xml"
    path.write_text('<items><item><request base64="true">%%%!</request></item></items>')
    assert BurpImporter.from_xml(path).diagnostics


def test_invalid_normalized_records(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(
        json.dumps(
            [
                {"url": "https://e.test", "response_body": ["not-text"]},
                {"url": "https://e.test", "method": 4},
            ]
        )
    )
    dataset = BurpImporter.from_json(path)
    assert len(dataset.diagnostics) == 2
    assert dataset.exchanges == []


def test_case_insensitive_public_headers():
    exchange = HTTPExchange(
        "https://e.test/asset",
        response_headers={"Content-Type": "application/javascript"},
        response_body='fetch("/found")',
    )
    result = Analyzer().analyze_exchanges([exchange])
    assert any(e.url == "https://e.test/found" for e in result.endpoints)


def test_malformed_xml_input_error(tmp_path):
    from sad.cli import main

    path = tmp_path / "bad.xml"
    path.write_text("<items>")
    assert main(["import-burp", str(path)]) == 2
