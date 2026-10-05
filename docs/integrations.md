# Integration contract

Use Python 3.11+ and the public API; importing SAD does not start a crawler or server. No CLI parsing is needed.

```python
from sad import Analyzer, Dataset, HTTPExchange, Resource
from sad.ingest import BurpImporter, HARImporter, RawHTTPImporter

analyzer = Analyzer(base_url="https://example.com")
dataset = BurpImporter.from_xml("history.xml")
result = analyzer.analyze_dataset(dataset)

result = analyzer.analyze_exchanges([
    HTTPExchange(
        url="https://example.com/app.js",
        response_headers={"content-type": "application/javascript"},
        response_body='fetch("/not-in-history", {method:"POST"})',
    )
])
assert any(e.url.endswith("/not-in-history") for e in result.endpoints)
```

An HTTPExchange represents an observed request and optional response. Supplying only a response should use `Resource` (or `RawHTTPImporter.from_text(..., base_url=original_url)`), so SAD does not invent a GET observation. HTTPExchange headers should have lowercase keys. `Dataset.resources` can contain local or URL-named captured HTML, JavaScript, TypeScript, or source maps. A source file is never fetched merely because it appears in a dataset. URL batches are explicit `analyze_urls([...])` calls; pass stripped nonempty lines from a URL-list file.

## Formats

Burp XML reads `<items><item>` records, URL/method fields and optional base64 request/response messages. HAR reads `log.entries` and base64 response content. Binary Burp project archives are not supported. Export selected traffic as XML or use an extension to normalize it.

Normalized JSON accepts an array or `{"exchanges": [...]}` / `{"items": [...]}`:

```json
[{"url":"https://example.com/app.js","method":"GET","status":200,
  "request_headers":{},"request_body":null,
  "response_headers":{"content-type":"application/javascript"},
  "response_body":"fetch('/unseen')","source_file":"proxy-history:42"}]
```

Raw requests need an explicit base URL if their target is relative, since the scheme cannot be determined from `Host`. Raw responses always need their original URL. Export decoded, dechunked bodies. Capture files are bounded to 64 MB per import; split larger histories upstream.

The engine preserves unknown strings, diagnostic messages, original observations and evidence. Inspect structured issues and analysis_incomplete, alongside diagnostics, before treating an inventory as complete. `AnalysisResult.to_dict()` is JSON-compatible and contains schema_version `1.0`. The JSON Schema is in `docs/result.schema.json`. JSONL has one endpoint per line, including schema_version; diagnostics go to CLI stderr. CSV has selected columns and JSON evidence; JSON is the lossless export.

## Burp extension boundary

Modern Burp extensions can use Montoya on the JVM; SAD requires CPython and is not a Jython library. A companion should serialize normalized captures to a local CPython worker, then consume result JSON. This repository includes `examples/burp_worker.py`, a bounded, line-oriented offline worker with no listening network port. Start it from the extension with a fixed executable and arguments, and write JSON to stdin; never interpolate captured content into a shell command. A native Burp UI/extension JAR is not included.

Requests/responses remain sensitive. The worker preserves them as evidence; extension authors should provide redaction and retention controls. Burp issues should link to evidence and distinguish observed from potentially invokable endpoints.

## Custom clients

```python
from sad import Analyzer
from sad.protocols import SinkRegistry

registry = SinkRegistry()
registry.register_package("@company/http", framework="axios")
result = Analyzer(registry=registry).analyze_js(
    'import http from "@company/http"; http.post("/users", data)'
)
```

Registration asserts a package's calling convention. Prefer supplying wrapper source so ordinary inter-procedural analysis can prove the sink. Do not register generic global names such as `client` or `api` merely because they have a `get` method.


Result additions are backward-compatible: `issues`, `analysis_incomplete`, and endpoint `request_variants` supplement existing fields. An observed request belongs to supplied traffic; SAD never generates requests to validate an endpoint. See the README origin-state table for `static-analysis`, `observed`, `observed+static`, and the reserved `inferred` value.
