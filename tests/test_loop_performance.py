"""Local work limits and snapshot avoidance without losing independent sinks."""

import json

import pytest

from sad import Analyzer, Dataset, Resource
from sad.cli import main
from sad.javascript import engine as engine_module
from sad.javascript.engine import Engine
from sad.javascript.parser import ASTCache
from sad.protocols import SinkRegistry


def test_expensive_initialization_keeps_later_bindings_and_entry_points():
    engine = Engine(ASTCache(), SinkRegistry(), max_context_steps=80, max_steps=2000)
    result = engine.analyze(
        [
            Resource(
                "app.js",
                """
        function render(){for(let i=0;i<64;i++){for(let j=0;j<64;j++){Math.abs(j)}}}
        render();
        const route='/after-initialization';
        function send(){fetch(route)}
    """,
            )
        ]
    )
    assert {e.url for e in result.endpoints} == {"/after-initialization"}
    assert result.analysis_incomplete
    assert any(i.code == "analysis-budget" and i.source_line for i in result.issues)
    assert engine.steps <= 2000
    assert not engine.work_budgets and not engine.stack and not engine.active_calls


def test_expensive_symbolic_entry_point_does_not_starve_later_function():
    result = Analyzer(max_context_steps=60, max_steps=500).analyze_js("""
        function render(){for(let i=0;i<64;i++){for(let j=0;j<64;j++){Math.abs(j)}}}
        function send(){fetch('/independent')}
    """)
    assert {e.url for e in result.endpoints} == {"/independent"}
    assert result.analysis_incomplete


def test_nested_loop_limit_belongs_to_outer_loop_and_continues():
    engine = Engine(ASTCache(), SinkRegistry(), max_loop_steps=50, max_context_steps=1000)
    result = engine.analyze(
        [
            Resource(
                "app.js",
                """
        for(let i=0;i<64;i++){
            for(let j=0;j<64;j++){Math.abs(j)}
        }
        fetch('/after-loop')
    """,
            )
        ]
    )
    assert {e.url for e in result.endpoints} == {"/after-loop"}
    issues = [i for i in result.issues if i.code == "loop-limit"]
    assert len(issues) == 1 and issues[0].source_line == 2
    assert not engine.work_budgets


def test_concrete_loop_does_not_copy_heap_and_retains_all_known_iterations(monkeypatch):
    def unexpected_clone(*args):
        pytest.fail("a known condition needs no zero-iteration snapshot")

    monkeypatch.setattr(engine_module, "clone", unexpected_clone)
    result = Analyzer().analyze_js('for(let i=0;i<63;i++){fetch("/page/"+i)}')
    assert {e.url for e in result.endpoints} == {f"/page/{i}" for i in range(63)}
    assert not result.analysis_incomplete


def test_readonly_guard_and_unmodeled_dom_calls_need_no_snapshot(monkeypatch):
    def unexpected_clone(*args):
        pytest.fail("read-only branches need no heap snapshot")

    monkeypatch.setattr(engine_module, "clone", unexpected_clone)
    result = Analyzer().analyze_js("""
        function send(flag){
            if(flag){document.querySelector('#status').focus();return '/early'}
            fetch('/later');
        }
    """)
    assert {e.url for e in result.endpoints} == {"/later"}
    assert not result.analysis_incomplete


def test_continuing_original_branch_retains_globals():
    result = Analyzer().analyze_js("""
        function send(flag){
            if(flag){return '/early'}else{let local=1}
            fetch('/later');
        }
    """)
    assert {e.url for e in result.endpoints} == {"/later"}


def test_readonly_classification_uses_current_callback_binding():
    result = Analyzer().analyze_js("""
        function run(callback){if(flag){callback()}}
        run(unknown);
        run(()=>fetch('/callback'));
    """)
    assert {e.url for e in result.endpoints} == {"/callback"}


def test_branch_writes_preserve_aliases_and_closures():
    result = Analyzer().analyze_js("""
        const state={url:'/initial'}; const alias=state;
        const send=()=>fetch(state.url);
        if(flag){state.url='/yes'}else{alias.url='/no'}
        send();
        state.url='/later';send();
    """)
    assert {e.url for e in result.endpoints} == {"/yes", "/no", "/later"}


def test_symbolic_loop_iterations_configurable_and_marked_partial():
    source = 'let route="/a";while(flag){route=route+"b"}fetch(route)'
    short = Analyzer(max_symbolic_loop_iterations=1).analyze_js(source)
    longer = Analyzer(max_symbolic_loop_iterations=3).analyze_js(source)
    assert {e.url for e in short.endpoints} == {"/a", "/ab"}
    assert "/abbb" in {e.url for e in longer.endpoints}
    assert short.analysis_incomplete and longer.analysis_incomplete
    assert any(i.code == "loop-limit" and i.source_line == 1 for i in short.issues)


def test_local_limits_leave_other_sources_and_partial_exports_available():
    result = Analyzer(max_loop_steps=40, max_context_steps=100).analyze_dataset(
        Dataset(
            resources=[
                Resource("/app.js", 'import {route} from "./vendor";fetch(route);fetch("/local")'),
                Resource(
                    "/vendor.js", 'export const route="/export";for(let i=0;i<64;i++){Math.abs(i)}'
                ),
            ]
        )
    )
    assert {e.url for e in result.endpoints} == {"/export", "/local"}
    assert result.analysis_incomplete


def test_loop_budget_controls_exposed_in_cli(tmp_path, capsys):
    path = tmp_path / "app.js"
    path.write_text('for(let i=0;i<64;i++){Math.abs(i)}fetch("/later")')
    assert (
        main(
            [
                "analyze-js",
                str(path),
                "--max-context-steps",
                "100",
                "--max-loop-steps",
                "20",
                "--max-symbolic-loop-iterations",
                "1",
                "--format",
                "json",
                "--strict",
            ]
        )
        == 1
    )
    result = json.loads(capsys.readouterr().out)
    assert {e["url"] for e in result["endpoints"]} == {"/later"}
    assert result["analysis_incomplete"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_context_steps": 0},
        {"max_loop_steps": 0},
        {"max_symbolic_loop_iterations": 0},
        {"max_symbolic_loop_iterations": 65},
    ],
)
def test_invalid_local_limits_rejected(kwargs):
    with pytest.raises(ValueError):
        Analyzer(**kwargs)
