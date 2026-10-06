"""Correctness and work bounds for large inputs and reusable analyzers."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest

from sad import Analyzer, Dataset, HTTPExchange, Resource
from sad.javascript import parser as parser_module
from sad.javascript.parser import ASTCache
from sad.models import Endpoint
from sad.normalization import correlate, matches, normalize


def test_symbolic_diagnostics_are_retained_and_deduplicated():
    source = ";".join(f"fetch(`/api/{i}/${{id}}`)" for i in range(3000))
    result = Analyzer().analyze_js(source)
    assert len(result.endpoints) == len(result.issues) == len(result.diagnostics) == 3000
    assert result.analysis_incomplete
    assert all(issue.code == "symbolic-url" for issue in result.issues)


def test_large_capture_correlates_exact_paths_without_regex(monkeypatch):
    def unexpected_match(*args):
        pytest.fail("exact paths must use indexed lookup")

    monkeypatch.setattr("sad.normalization.matches", unexpected_match)
    source = ";".join(f'fetch("/api/{i}")' for i in range(3000))
    result = Analyzer().analyze_dataset(
        Dataset(
            resources=[Resource("app.js", source)],
            exchanges=[HTTPExchange(f"/api/{i}") for i in range(3000)],
        )
    )
    assert len(result.endpoints) == 3000
    assert all(e.origin == "observed+static" and len(e.observations) == 1 for e in result.endpoints)
    assert not result.analysis_incomplete


@pytest.mark.parametrize(
    "observed_path", ["/users/a", "/users/prefix-a", "/users/fixed", "/other/a", "/x/{bad"]
)
def test_indexed_correlation_keeps_original_matching_precedence(observed_path):
    templates = [
        normalize(Endpoint("GET", path))
        for path in [
            "/users/{id}",
            "/users/{name}",
            "/users/prefix-{id}",
            "/users/fixed",
            "/{any}/{id}",
            "/x/{bad",
            "/x/{bad}",
        ]
    ]
    observation = normalize(Endpoint("GET", observed_path, origin="observed"))
    observation.observations.append({"url": observed_path})
    candidates = sorted(
        (e for e in templates if matches(e, observation)),
        key=lambda e: ("{" in (e.path or ""), e.url),
    )
    correlate([*templates, observation])
    assert [e for e in templates if e.observations] == candidates[:1]


def test_cache_source_byte_budget_and_oversized_uncached_trees():
    cache = ASTCache(max_bytes=100, max_cache_bytes=12)
    cache.parse("const a=1;", "a.js")
    cache.parse("const b=2;", "b.js")
    assert cache.cached_bytes == 10
    cache.parse("const a=1;", "a.js")
    assert cache.misses == 3
    first = cache.parse("const large=12345;", "large.js")
    assert first.root_node.type == "program"
    cache.parse("const large=12345;", "large.js")
    assert cache.misses == 5
    assert cache.cached_bytes <= cache.max_cache_bytes


def test_concurrent_identical_parses_share_tree():
    cache = ASTCache()
    barrier = Barrier(8)
    source = ";".join(f"const x{i}={i}" for i in range(10000))

    def parse(_):
        barrier.wait(timeout=5)
        return cache.parse(source, "app.js")

    with ThreadPoolExecutor(max_workers=8) as executor:
        trees = list(executor.map(parse, range(8)))
    assert all(tree is trees[0] for tree in trees)
    assert cache.misses == 1 and cache.hits == 7


def test_cache_hits_and_other_misses_do_not_wait_for_blocked_parse(monkeypatch):
    cache = ASTCache()
    cached = cache.parse("const cached=1;", "cached.js")
    started, release = Event(), Event()
    original = parser_module.Parser

    class BlockingParser:
        def __init__(self, language):
            self.parser = original(language)

        def parse(self, raw):
            if raw == b"const blocked=1;":
                started.set()
                assert release.wait(timeout=5)
            return self.parser.parse(raw)

    monkeypatch.setattr(parser_module, "Parser", BlockingParser)
    with ThreadPoolExecutor(max_workers=3) as executor:
        blocked = executor.submit(cache.parse, "const blocked=1;", "blocked.js")
        try:
            assert started.wait(timeout=5)
            hit = executor.submit(cache.parse, "const cached=1;", "cached.js")
            other = executor.submit(cache.parse, "const other=1;", "other.js")
            assert hit.result(timeout=2) is cached
            assert other.result(timeout=2).root_node.type == "program"
        finally:
            release.set()
        assert blocked.result(timeout=2).root_node.type == "program"


def test_shared_analyzer_keeps_concurrent_results_isolated():
    analyzer = Analyzer()
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda i: analyzer.analyze_js(f'fetch("/api/{i}")'), range(20)))
    assert [{e.url for e in result.endpoints} for result in results] == [
        {f"/api/{i}"} for i in range(20)
    ]


def test_long_source_configurable_from_cli(tmp_path, capsys):
    from sad.cli import main

    path = tmp_path / "large.js"
    path.write_text("/*" + "x" * 8_000_000 + '*/fetch("/tail")')
    assert main(["analyze-js", str(path), "--max-resource-bytes", "9000000", "--strict"]) == 0
    assert "/tail" in capsys.readouterr().out


def test_repeated_sink_retains_all_locations():
    result = Analyzer().analyze_js(";".join('fetch("/same")' for _ in range(5000)))
    assert len(result.endpoints) == 1
    endpoint = result.endpoints[0]
    assert len(endpoint.evidence) == 5000
    assert len({e.source_column for e in endpoint.evidence}) == 5000
    assert len(endpoint.request_variants) == 1


def test_merge_indexes_preserve_json_equality_and_order():
    first = normalize(Endpoint("POST", "/same", body={"value": [1]}, observations=[{"n": 1}]))
    equal = normalize(Endpoint("POST", "/same", body={"value": [1.0]}, observations=[{"n": 1.0}]))
    different = normalize(Endpoint("POST", "/same", body={"value": [2]}, observations=[{"n": 2}]))
    result = correlate([first, equal, different])
    assert len(result) == 1
    assert [v["body"] for v in result[0].request_variants] == [{"value": [1]}, {"value": [2]}]
    assert result[0].observations == [{"n": 1}, {"n": 2}]


def test_concurrent_parse_failure_reaches_waiters_and_allows_retry(monkeypatch):
    from concurrent.futures import Future

    cache = ASTCache()
    started, joined, release = Event(), Event(), Event()
    original = parser_module.Parser

    class WaitingFuture(Future):
        def result(self, timeout=None):
            joined.set()
            return super().result(timeout)

    class FailingParser:
        def __init__(self, language):
            pass

        def parse(self, raw):
            started.set()
            assert release.wait(timeout=5)
            raise ValueError("parse failure")

    monkeypatch.setattr(parser_module, "Future", WaitingFuture)
    monkeypatch.setattr(parser_module, "Parser", FailingParser)
    with ThreadPoolExecutor(max_workers=2) as executor:
        owner = executor.submit(cache.parse, 'fetch("/retry")', "app.js")
        try:
            assert started.wait(timeout=5)
            waiter = executor.submit(cache.parse, 'fetch("/retry")', "app.js")
            assert joined.wait(timeout=5)
        finally:
            release.set()
        for future in (owner, waiter):
            with pytest.raises(ValueError, match="parse failure"):
                future.result(timeout=2)
    monkeypatch.setattr(parser_module, "Parser", original)
    assert cache.parse('fetch("/retry")', "app.js").root_node.type == "program"
    assert cache.misses == 2 and cache.hits == 1


@pytest.mark.stress
def test_fifty_thousand_operations_in_large_source():
    source = ";".join(f'fetch("/large/{i}")' for i in range(50000))
    source += "\n/*" + "x" * 10_000_000 + "*/"
    analyzer = Analyzer(max_steps=1_000_000, max_resource_bytes=16_000_000)
    result = analyzer.analyze_js(source)
    assert len(result.endpoints) == 50000
    assert not result.analysis_incomplete
    assert analyzer.analyze_js(source).to_dict() == result.to_dict()
    assert analyzer.cache.misses == 1


@pytest.mark.parametrize("kwargs", [{"capacity": -1}, {"max_bytes": 0}, {"max_cache_bytes": -1}])
def test_invalid_cache_limits_rejected(kwargs):
    with pytest.raises(ValueError, match="cache limits"):
        ASTCache(**kwargs)


def test_large_iife_and_unicode_evidence():
    source = "(function(){/*" + "x" * 1_000_000 + '*/fetch("/wrapped");})();'
    result = Analyzer().analyze_js(source)
    assert {e.url for e in result.endpoints} == {"/wrapped"}
    assert not result.analysis_incomplete
    call = 'fetch("/unicode",{body:"' + "🌍" * 1500 + '"})'
    evidence = Analyzer().analyze_js(call).endpoints[0].evidence[0]
    assert evidence.detail == call[:1000]
