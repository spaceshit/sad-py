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

## Large-source performance validation (2026-10-07)

The full suite passed **670 tests**, including stress tests, on local Linux/Python 3.14.4. Separate line and branch coverage were **97.74%** and **93.52%**; Ruff checks, formatting and mypy also passed.

Synthetic offline comparisons against the preceding committed implementation used three runs per scenario and a reusable analyzer on local Linux/Python 3.14. Wall-clock medians include analysis and correlation, excluding result serialization:

| Workload | Before | After |
| --- | ---: | ---: |
| 1,000 static operations plus 1,000 captured requests | 17.170 s | 0.045 s |
| 5,000 distinct symbolic URLs | 1.540 s | 0.188 s |
| 5,000 uncalled wrappers with symbolic parameters | 1.647 s | 0.313 s |
| 5,000 calls to the same endpoint, retaining every location | 1.533 s | 0.146 s |
| 50,000 distinct fetch calls in an 11.29 MB source | 1.735 s | 1.392 s |

The large-source case includes 10 MB of comment padding and explicitly raises the resource and step limits; it is not a measurement of an equally sized complex vendor bundle. Symbolic cases retain their incomplete-analysis flags. The implementation uses native AST queries for references and bundle candidates, hash indexes for issues and evidence merging, and indexed exact/symbolic correlation. Unknown-branch state copying and abstract interpretation still depend on program structure and remain bounded by existing exploration budgets.

Reproduce with `python scripts/benchmark.py --scenario captured --operations 1000`, or select `symbolic`, `wrappers`, `repeated`, or `fetch`. For the large-source case use `--operations 50000 --padding-bytes 10000000`. `--runs` controls sample count and `--output` chooses the JSON report. The local before/after report is written to `artifacts/performance-optimization.json`.

Regression tests cover source byte eviction, shared concurrent parses, delivery and recovery from concurrent parse failures, unrelated cache operations during a blocked parse, isolated concurrent analyzer results, correlation precedence, Unicode evidence, a large IIFE, a CLI file over 8 MB, and a 50,000-operation source. Timing assertions are avoided so correctness tests do not depend on machine load.

## Complex captured application validation (2026-10-07)

A user-supplied `/tmp/long.xml` capture contained one 563,867-byte JavaScript response, 430 function declarations, 643 arrow functions and 1,650 `if` statements. The capture is not distributed with the repository. Before context/loop optimizations, two default-budget analyses took 14.57 and 15.25 seconds and found no static operations: only the original captured script request survived source-budget exhaustion.

After the changes, three sequential analysis runs per configuration on local Linux/Python 3.14.4 measured:

| Context allowance | Median time | Static-analysis endpoints | Total endpoints |
| --- | ---: | ---: | ---: |
| 1,000 steps (default) | 13.28 s | 104 | 105 |
| 250 steps | 5.62 s | 104 | 105 |
| 100 steps | 2.10 s | 64 | 65 |

The additional endpoint was correlated with captured traffic (`observed+static`). **Every result remains incomplete.** Equal counts do not imply identical endpoint sets, URL alternatives, headers or bodies. The capture has not been independently labeled for recall or false positives; these counts describe engine output, not verified API coverage. Timings exclude JSON serialization. Each profile used one reusable analyzer and recorded one parser miss and five hits. The local report, including the input digest and samples, is in `artifacts/long-optimization-benchmark.json`; result files are `/tmp/long-analysis-default.json`, `/tmp/long-analysis-fast.json` and `/tmp/long-analysis-faster.json`.

Run `sad import-burp /tmp/long.xml --max-context-steps 250 --format json` for the middle profile. Top-level statements and independent symbolic entry points have separate local work allowances; nested operations still share source/global bounds. Unsupported DOM calls and read-only guards avoid unnecessary snapshots, concrete loops avoid unused snapshots, mutable branches use one snapshot, and symbolic loops have a configurable fixed-point iteration bound. Interrupted contexts emit located issues and retain completed discoveries so independent later statements/functions can be visited.

Validation passed **685 tests**, including stress tests and regressions for nested budget ownership, later bindings/exports after interruption, callbacks whose binding changes between calls, heap aliases, closures, read-only DOM guards and known loop iterations. Line coverage was **97.60%**, branch coverage **93.31%**; Ruff, formatting and mypy passed.

## Limits and incomplete analysis

SAD retains symbolic endpoints when a proven sink has an unresolved URL, and reports structured issues with a code, location and reason alongside existing string diagnostics. `analysis_incomplete` identifies known missing information, parse failures and exhausted budgets. Absence of an issue does not certify exhaustive discovery.

Loops, recursive calls, branches, source maps and source acquisition have finite budgets. Dynamic routing values supplied only at runtime, opaque encoded service descriptors, computed remote chunk manifests, proxy traps and decorator-driven dependency injection remain research patterns. [The research list](../tests/fixtures/research.json) is excluded from supported-corpus recall. Missing capture assets and unsupplied local dependencies cannot be reconstructed by offline analysis. Supplied TypeScript path mappings resolve only within explicitly provided inputs.

Measured recall applies to the committed, labeled corpus. There is no universal completeness guarantee for arbitrary JavaScript, and further patterns require new independently labeled regressions.
