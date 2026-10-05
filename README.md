# SAD: Statistic API Discovery

SAD is a Python library and CLI for evidence-backed static discovery of frontend API operations, including operations absent from captured traffic. Intended for authorized application analysis and security testing. Target JavaScript is never executed.

**Status:** early release, with a bounded abstract interpreter and explicit coverage limits; not a claim of complete JavaScript semantics or exhaustive discovery.

## Install

Requires Python 3.11+. From this repository:

```sh
pip install .
# Contributors
pip install -e '.[dev]'
```

The proposed distribution name is `sad-api-discovery` (availability must be confirmed before publishing); the import and executable are `sad`. No release has been published by this repository setup.

## Python

```python
from sad import Analyzer
from sad.ingest import BurpImporter

analyzer = Analyzer()
result = analyzer.analyze_url("https://example.com")
for endpoint in result.endpoints:
    print(endpoint.method, endpoint.url, endpoint.evidence)

result = analyzer.analyze_dataset(BurpImporter.from_xml("history.xml"))
result = analyzer.analyze_js('fetch(`/users/${id}`, {method: "DELETE"})')
result = analyzer.analyze_path("./assets")
```

## CLI

```sh
sad crawl https://example.com --depth 2 --max-pages 30
sad analyze ./assets --format json
sad analyze-js app.js --base-url https://example.com
sad import-burp history.xml --format jsonl
sad import-burp requests.json --format csv
sad import-burp traffic.har
sad import-burp request.http --base-url https://example.com
```

The crawler follows canonical redirects of the starting URL between a host and its `www` variant, and from HTTP to HTTPS on standard ports. It adds the redirected origin to the collection scope so its pages and scripts can be collected. Other origins require explicit scope configuration through `--allow-origin` or `CrawlConfig.allowed_origins`; exclusions and redirect limits still apply. Discovering an API never causes it to be requested.

## Analysis budgets and partial results

`--depth` and `--max-pages` control collection. They do not increase the JavaScript analysis budget. Every command accepts `--max-steps` (default: 200,000), corresponding to `Analyzer(max_steps=...)`:

```sh
sad crawl https://www.google.com/ --depth 2 --max-pages 30 --max-steps 2000000 --format json --strict
```

The global step budget is divided among supplied JavaScript sources so one expensive script cannot prevent independent sources from being analyzed. When a source exhausts its share, completed discoveries and export bindings are retained, a located `analysis-budget` issue is emitted and other sources continue. Raising the budget allows more exploration, but does not guarantee completeness or remove other limits.

`Argument combinations exceeded 16` reports widening of too many possible argument combinations. SAD keeps explored alternatives and a symbolic remainder and sets `analysis_incomplete=true`; it does not claim that every concrete combination was analyzed. Loops, recursion and missing inputs can also cause partial results. The CLI explicitly marks incomplete output, and `--strict` exits with status 1 when diagnostics occur.

`high` confidence applies to an individual detected operation, not to coverage of the whole application. An HTML form can produce an endpoint even if JavaScript analysis is incomplete; inspect its `evidence` in JSON to distinguish form declarations from JavaScript sinks.

## Burp history: observed and previously unseen APIs

SAD imports the captured requests **and analyzes captured response bodies**. HTML responses contribute inline scripts and forms; JavaScript responses feed the same static analysis engine used for source files. This can discover operations that were never invoked during the captured session. Assets absent from the capture are not downloaded by an offline import.

For example, a captured `app.js` response might contain:

```javascript
fetch("/api/users", {method: "POST"});
fetch(`/api/users/${id}`, {method: "DELETE"});
fetch("/api/admin/export");
```

If the history contains `POST /api/users`, SAD correlates that request with the first static discovery. If it also contains `DELETE /api/users/123`, SAD can correlate it with the symbolic `/api/users/{id}`. The export operation remains a static discovery even if no export request appears in the history. Original observations and contributing evidence are retained.

## Endpoint origin states

The `origin` field describes **where the evidence came from**:

| State | Meaning | Example |
| --- | --- | --- |
| `static-analysis` | Discovered by reading source code or an HTML declaration, including code inside captured responses. No matching request was supplied. | A captured script calls `fetch("/api/admin/export")`, but that URL is absent from the history. |
| `observed` | A request exists in the supplied traffic, with no matching static discovery. SAD did not make that request. | Burp recorded `GET /api/profile`, but the supplied code does not expose a corresponding call. |
| `observed+static` | An observed request was correlated with a static discovery. The logical endpoint retains both evidence sources. | Code declares `DELETE /api/users/{id}` and Burp recorded `DELETE /api/users/123`. |
| `inferred` | Reserved for future discoveries based on indirect heuristics that cannot yet be traced to a resolved network sink or an observed request. **The current engine does not assign this state automatically.** | A future adapter might suggest a route from incomplete framework metadata. |

`origin` and `confidence` answer different questions: origin identifies the evidence source; confidence expresses certainty in the mapping. An unresolved URL such as `/api/users/{id}` remains `static-analysis` with symbolic values and appropriate confidence. Resolving a wrapper to `fetch` is also static analysis; the word “inference” does not automatically imply `origin="inferred"`.

These states never cause SAD to invoke an endpoint. SAD never executes target JavaScript. Live crawling only collects source resources; importing files or captured traffic is offline.

See [measured reliability and limitations](docs/reliability.md), [architecture and coverage](docs/architecture.md), [integration contract](docs/integrations.md), and [release checklist](docs/releasing.md). JSON, JSONL, CSV and table outputs share the same library models. Diagnostics indicate malformed inputs and exhausted budgets; incomplete results are retained.
