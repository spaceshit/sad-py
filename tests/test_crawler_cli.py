import json

import httpx

from sad import Analyzer
from sad.cli import main
from sad.crawler import CrawlConfig, Crawler
from sad.output import render


def test_crawl_scope_chunks_and_redirects():
    visited = []

    def handler(request):
        visited.append(str(request.url))
        fixtures = {
            "/": (
                "text/html",
                '<script src="/app.js"></script><a href="/redirect">link</a><a href="https://evil.test/">offsite</a>',
            ),
            "/app.js": ("application/javascript", 'import("./chunk.js"); fetch("/api")'),
            "/chunk.js": ("application/javascript", 'fetch("/lazy")'),
        }
        if request.url.path == "/redirect":
            return httpx.Response(302, headers={"location": "https://evil.test/private"})
        mime, body = fixtures[request.url.path]
        return httpx.Response(200, headers={"content-type": mime}, text=body)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        dataset = Crawler(client=client).collect(["https://e.test/"])
    result = Analyzer().analyze_dataset(dataset)
    assert {"https://e.test/api", "https://e.test/lazy"} <= {e.url for e in result.endpoints}
    assert not any("evil.test" in url for url in visited)
    assert any("blocked redirect" in d for d in result.diagnostics)


def test_crawl_byte_budget():
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"x" * 100))
    ) as client:
        dataset = Crawler(CrawlConfig(max_bytes=10), client=client).collect(["https://e.test"])
    assert not dataset.resources
    assert dataset.diagnostics


def test_cli_and_output(tmp_path, capsys):
    path = tmp_path / "app.js"
    path.write_text('fetch("/users")')
    assert main(["analyze-js", str(path), "--format", "json"]) == 0
    value = json.loads(capsys.readouterr().out)
    assert value["endpoints"][0]["url"] == "/users"
    result = Analyzer().analyze_path(tmp_path)
    assert "GET" in render(result, "table")
    assert json.loads(render(result, "jsonl"))["schema_version"] == "1.0"
    assert "evidence" in render(result, "csv")
    assert main(["analyze", str(tmp_path / "missing")]) == 2


def test_schema_tracks_public_model():
    from dataclasses import fields
    from pathlib import Path

    from sad.models import AnalysisResult, Endpoint

    schema = json.loads((Path(__file__).parents[1] / "docs/result.schema.json").read_text())
    assert set(schema["$defs"]["Endpoint"]["properties"]) == {f.name for f in fields(Endpoint)}
    assert set(schema["$defs"]["AnalysisResult"]["properties"]) == {
        f.name for f in fields(AnalysisResult)
    }


def test_exclusions_and_page_depth():
    visited = []

    def handler(request):
        visited.append(str(request.url))
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text='<a href="/next">next</a><script src="/excluded.js"></script>',
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        Crawler(CrawlConfig(depth=0, exclusions=("*/excluded.js",)), client=client).collect(
            ["https://e.test/"]
        )
    assert visited == ["https://e.test/"]
