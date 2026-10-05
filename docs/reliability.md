# Reliability and measured support

The local Linux validation on 2026-10-05 passed **650 tests**, including the stress suite, on Python **3.11.15, 3.12.13, 3.13.13 and 3.14.4**. Coverage on Python 3.14 measured separately was **97.94% of executable lines** and **93.45% of branches**. The gates require at least 95% and 90%, respectively. Code coverage measures exercised implementation paths; it does not prove that arbitrary JavaScript exposes every possible API.

The subsequent canonical-redirect fix expanded the suite to **642 passing tests** on Python 3.14, with **97.91% line coverage** and **93.41% branch coverage**. Its 35 collection/CLI tests also passed on Python 3.11. Regressions cover host/`www` seed redirects, HTTP-to-HTTPS upgrades, asset collection and URL context, plus excluded destinations, unrelated hosts, credential-bearing URLs, changed ports, downgrade redirects and repeated `www` prefixes. A manual source-only crawl of `https://intuity.it/` confirmed that collection passes the canonical redirect; analysis still reports exhausted budgets on complex minified scripts, so that site's result remains incomplete.

The budget regressions additionally verify that an expensive source or uncalled function cannot starve an independent source, partially completed ESM/CommonJS exports survive exhaustion, the global bound remains enforced, and the CLI accepts a step budget and marks partial results. A manual Google source capture was analyzed offline with default and increased budgets. Raising the budget to two million steps exposed an additional JavaScript call but did not eliminate branch, loop and recursion limits. This manual result remains incomplete and is not included in labeled-corpus recall.

The independently labeled detection corpus contains **409 cases**, including **84 multi-file cases** and **83 entirely negative cases**. All 326 expected operations were detected, with zero additional operations and zero unresolved corpus cases. Negative-category cases also include one mixed valid/invalid sink case. Property-test examples and fuzz iterations are excluded from these counts. Expectations specify operations, forbidden detections, symbolic parameters, evidence and diagnostic requirements; they are not snapshots produced by SAD.

| Category | Cases | Expected operations | Detected | False positives |
| --- | ---: | ---: | ---: | ---: |
| Language and data flow | 168 | 168 | 168 | 0 |
| Modules | 84 | 84 | 84 | 0 |
| Clients and protocols | 73 | 73 | 73 | 0 |
| Negative and mixed inputs | 84 | 1 | 1 | 0 |

Additional regression suites exercise captures, crawling, schemas, exports, CLI behavior, source maps, bundles, resource limits, aliasing, recursion, malformed inputs and caching. The suite prohibits network connections and child-process execution. Crawler acquisition uses mocked HTTP responses. Target JavaScript is parsed and interpreted abstractly; it is never executed. Finding an API never queues it for collection.

## Reviewed source examples

[Upstream fixture provenance](../tests/fixtures/upstream/README.md) records locally stored examples from Axios, OpenAPI Generator, tRPC and Connect, pinned to commits with retained licenses and SHA-256 checksums. Reduced scenarios have independently inspected expectations. Bundle fixtures exercise representative Webpack, Vite, Rollup and Parcel structures; they do not claim support for every compiler version or plugin. Ordinary tests never download these repositories or contact example services.

## Reproduction and CI

```sh
python -m pip install -e '.[dev]'
pytest --cov=sad --cov-branch --cov-report=json:artifacts/coverage.json --junitxml=artifacts/tests.xml
python scripts/reliability_report.py
python scripts/benchmark.py --operations 10000
```

Pull-request CI runs deterministic offline tests and separate coverage gates on Python 3.11–3.14. Windows and macOS smoke jobs exercise paths, imports, CLI, collection and wheel installation. These operating-system jobs are configured for CI; the local measurements above were made on Linux. Weekly/manual CI includes the larger fuzz and performance suites. Artifacts contain coverage JSON/XML, JUnit results, detection recall, false positives, unresolved cases and per-category timings. Supported cases must pass without `xfail`.

The local synthetic benchmark analyzed a 248,889-byte source containing 10,000 distinct fetch calls in a median **0.369 seconds** over three runs, detecting every call. The parser cache recorded one miss and five hits. This measures a simple repeated-call workload, not arbitrary application performance.

## Limits and incomplete analysis

SAD retains symbolic endpoints when a proven sink has an unresolved URL, and reports structured issues with a code, location and reason alongside existing string diagnostics. `analysis_incomplete` identifies known missing information, parse failures and exhausted budgets. Absence of an issue does not certify exhaustive discovery.

Loops, recursive calls, branches, source maps and source acquisition have finite budgets. Dynamic routing values supplied only at runtime, opaque encoded service descriptors, computed remote chunk manifests, proxy traps and decorator-driven dependency injection remain research patterns. [The research list](../tests/fixtures/research.json) is excluded from supported-corpus recall. Missing capture assets and unsupplied local dependencies cannot be reconstructed by offline analysis. Supplied TypeScript path mappings resolve only within explicitly provided inputs.

Measured recall applies to the committed, labeled corpus. There is no universal completeness guarantee for arbitrary JavaScript, and further patterns require new independently labeled regressions.
