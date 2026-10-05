import json

import pytest

from sad import Analyzer, Dataset, Resource
from sad.cli import main
from sad.javascript.engine import Engine
from sad.javascript.parser import ASTCache
from sad.protocols import SinkRegistry


def test_expensive_source_does_not_starve_independent_source():
    engine = Engine(ASTCache(), SinkRegistry(), max_steps=100)
    result = engine.analyze(
        [
            Resource("a-vendor.js", "for(let i=0;i<64;i++){Math.abs(i)}"),
            Resource("z-app.js", 'fetch("/still-found")'),
        ]
    )
    assert {e.url for e in result.endpoints} == {"/still-found"}
    assert result.analysis_incomplete
    assert any(
        i.code == "analysis-budget" and i.source_file == "a-vendor.js" for i in result.issues
    )
    assert engine.steps <= 100


def test_exhausted_import_keeps_other_calls_and_partial_live_exports():
    result = Analyzer(max_steps=150).analyze_dataset(
        Dataset(
            resources=[
                Resource(
                    "/a-app.js", 'import {route} from "./b-vendor";fetch(route);fetch("/local")'
                ),
                Resource(
                    "/b-vendor.js",
                    'export const route="/export";for(let i=0;i<64;i++){Math.abs(i)}',
                ),
            ]
        )
    )
    assert {e.url for e in result.endpoints} == {"/export", "/local"}
    assert any(
        i.source_file == "/b-vendor.js" and i.code == "analysis-budget" for i in result.issues
    )


def test_uncalled_expensive_function_does_not_starve_other_sources_functions():
    result = Analyzer(max_steps=160).analyze_dataset(
        Dataset(
            resources=[
                Resource("a.js", "function expensive(){for(let i=0;i<64;i++){Math.abs(i)}}"),
                Resource("z.js", 'export function send(){fetch("/entry")}'),
            ]
        )
    )
    assert {e.url for e in result.endpoints} == {"/entry"}
    assert result.analysis_incomplete


def test_exhausted_commonjs_module_retains_completed_export_assignments():
    result = Analyzer(max_steps=150).analyze_dataset(
        Dataset(
            resources=[
                Resource("/a-app.js", 'const vendor=require("./b-vendor");fetch(vendor.route)'),
                Resource(
                    "/b-vendor.js",
                    'module.exports.route="/ready";for(let i=0;i<64;i++){Math.abs(i)}',
                ),
            ]
        )
    )
    assert {e.url for e in result.endpoints} == {"/ready"}
    assert result.analysis_incomplete


def test_tiny_global_budget_is_respected_with_many_sources():
    engine = Engine(ASTCache(), SinkRegistry(), max_steps=1)
    result = engine.analyze([Resource(f"{i}.js", 'fetch("/api")') for i in range(3)])
    assert engine.steps <= 1
    assert result.analysis_incomplete
    assert any("global step budget" in i.reason and i.source_file == "1.js" for i in result.issues)


def test_cli_analysis_budget_is_configurable(tmp_path, capsys):
    path = tmp_path / "app.js"
    path.write_text('fetch("/api")')
    assert main(["analyze-js", str(path), "--max-steps", "1", "--format", "json", "--strict"]) == 1
    captured = capsys.readouterr()
    assert "analysis incomplete; results are partial" in captured.err
    result = json.loads(captured.out)
    assert result["analysis_incomplete"]
    assert main(["analyze-js", str(path), "--max-steps", "100", "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["endpoints"][0]["url"] == "/api"


@pytest.mark.parametrize("budget", [0, -1])
def test_nonpositive_analysis_budget_rejected(budget):
    with pytest.raises(ValueError, match="max_steps"):
        Analyzer(max_steps=budget)
