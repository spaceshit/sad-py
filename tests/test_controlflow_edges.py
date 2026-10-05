import pytest

from sad import Analyzer, Dataset, Resource
from sad.javascript.engine import Engine
from sad.javascript.parser import ASTCache
from sad.javascript.values import (
    Alternatives,
    LiveBinding,
    Scope,
    clone,
    dereference,
    join,
    serializable,
)
from sad.protocols import SinkRegistry


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ('function f(p="/default"){fetch(p)} f()', {"/default"}),
        ('function f(p="/default"){fetch(p)} f("/explicit")', {"/explicit"}),
        ('const p="/x"; const opts={method:"POST"}; fetch(p,{...opts})', {"/x"}),
        ('const p="/x"; const opts={p}; fetch(opts.p)', {"/x"}),
        ('fetch(true?"/yes":"/no");fetch(false?"/no":"/other")', {"/yes", "/other"}),
        ('fetch(null??"/fallback");fetch("/known"||"/wrong")', {"/fallback", "/known"}),
        ('fetch(""||"/fallback");fetch(true&&"/yes")', {"/fallback", "/yes"}),
        ('fetch(["","joined"].join("/"))', {"/joined"}),
        ('fetch(["/indexed"][0])', {"/indexed"}),
        ('fetch("/n/"+(1+2));fetch("/n/"+(3*2))', {"/n/3", "/n/6"}),
        ('let p="/initial";p="/assigned";fetch(p)', {"/assigned"}),
        ('const c=flag?{p:"/a"}:{p:"/b"};fetch(c.p)', {"/a", "/b"}),
        (
            'try{fetch("/try")}catch(e){fetch("/catch")}finally{fetch("/finally")}',
            {"/try", "/catch", "/finally"},
        ),
        ('for(const p of ["/one","/two"]){fetch(p);break}', {"/one"}),
        ('for(const p of ["/one","/two"]){fetch(p);continue}', {"/one", "/two"}),
        ('for(let i=0;i<4;i++){fetch("/n/"+i);break}', {"/n/0"}),
        ('function f(){while(true){return "/return"}}fetch(f())', {"/return"}),
        ('let p="/a";const send=()=>fetch(p);if(flag){p="/b"}p="/after";send()', {"/after"}),
        ('do{fetch("/once")}while(false)', {"/once"}),
        ('while(flag){fetch("/loop")}', {"/loop"}),
        ('const c={get(p){return fetch(p)}};const f=c.get.bind(c,"/bound");f()', {"/bound"}),
        ('const c={};c["url"]="/subscript";fetch(c.url)', {"/subscript"}),
        ('if(false){fetch("/dead")}else{fetch("/live")}', {"/live"}),
        (
            'function f(flag){if(flag)return "/a";else return "/b";fetch("/dead")}fetch(f(unknown))',
            {"/a", "/b"},
        ),
    ],
)
def test_flow_edges(source, expected):
    assert {e.url for e in Analyzer().analyze_js(source).endpoints} == expected


def test_resource_and_call_budgets():
    engine = Engine(ASTCache(max_bytes=2), SinkRegistry())
    assert engine.analyze([Resource("a.js", 'fetch("/x")')]).diagnostics
    result = Analyzer(max_depth=2).analyze_js("function f(n){return f(n+1)}f(0)")
    assert any(i.code == "recursion-limit" for i in result.issues)
    result = Analyzer().analyze_js('for(;;){fetch("/loop")}')
    assert any(i.code == "loop-limit" for i in result.issues)
    result = Analyzer().analyze_js(
        "for(const x of [" + ",".join(map(str, range(70))) + '])fetch("/n/"+x)'
    )
    assert any(i.code == "loop-limit" for i in result.issues)


def test_alternative_budget_reports_loss():
    values = '"/end"'
    for i in range(25):
        values = f'flag{i}?"/v{i}":({values})'
    result = Analyzer().analyze_js("const p=" + values + ";fetch(p)")
    assert result.analysis_incomplete
    assert any(i.code == "branch-limit" for i in result.issues)
    assert any("branch-limit" in e.url for e in result.endpoints)


def test_scope_heap_cycles_and_live_binding_cycles():
    root = Scope()
    root["x"] = "/a"
    child = Scope(root)
    child.assign("x", "/b")
    assert root["x"] == "/b" and child.get("x") == "/b"
    child.assign("new", "value")
    assert root["new"] == "value"
    obj = {"v": 1}
    obj["self"] = obj
    copied = clone(obj)
    assert copied is not obj and copied["self"] is copied
    merged = join(obj, copied)
    assert merged["self"] is merged
    assert serializable(obj)["self"] == "{cyclic}"
    env = {}
    binding = LiveBinding(env, "x")
    env["x"] = binding
    assert str(dereference(binding)) == "{cyclic-binding}"
    assert serializable(Alternatives([1, 2])) == [1, 2]


def test_unresolved_and_optional_module_inputs():
    result = Analyzer().analyze_dataset(
        Dataset(
            resources=[
                Resource("/a.js", 'import x from "missing";fetch(x)'),
                Resource("/b.js", 'export default "/plain"'),
            ]
        )
    )
    assert result.analysis_incomplete
    assert any(i.code == "unresolved-import" for i in result.issues)
    result = Analyzer().analyze_js('import.meta.env.BASE; import("./absent");')
    assert result.issues


def test_unknown_loop_mutation_widens_and_keeps_remainder():
    result = Analyzer(max_steps=200_000).analyze_js(
        'let path="/a";while(flag){path=path+"b"}fetch(path)'
    )
    assert any(e.url == "/a" for e in result.endpoints)
    assert result.analysis_incomplete
    assert any(i.code in ("branch-limit", "loop-limit") for i in result.issues)


def test_import_meta_url_is_static_source_identity():
    result = Analyzer().analyze_js(
        'fetch(new URL("../api",import.meta.url))', filename="https://e.test/assets/app.js"
    )
    assert result.endpoints[0].url == "https://e.test/api"


def test_recursive_summary_converges_and_preserves_known_branch():
    result = Analyzer().analyze_js(
        'function route(flag){if(flag)return "/known";return route(flag)}fetch(route(x))'
    )
    assert "/known" in {e.url for e in result.endpoints}
    assert result.analysis_incomplete
    assert any(i.code == "recursion-limit" for i in result.issues)
    assert not any(i.code == "summary-limit" for i in result.issues)
