"""Independent expected endpoints for concrete misses verified during planning."""

import pytest

from sad import Analyzer, Dataset, Resource


@pytest.mark.parametrize(
    ("code", "urls"),
    [
        ('function p(x){if(x)return "/a";return "/b"} fetch(p(flag))', {"/a", "/b"}),
        ('for(const p of ["/users","/orders"])fetch(p)', {"/users", "/orders"}),
        ('const c={url:"/a"};if(flag){c.url="/b"}fetch(c.url)', {"/a", "/b"}),
        ('const c={url:"/a"};const d=c;if(flag){d.url="/b"}fetch(c.url)', {"/a", "/b"}),
        ('const f=fetch.bind(null,"/bound");f()', {"/bound"}),
        ('fetch.call(null,"/call");fetch.apply(null,["/apply"])', {"/call", "/apply"}),
        ('const a={base:"/v1",get(p){return fetch(this.base+p)}};a.get("/u")', {"/v1/u"}),
        ('{const fetch=x=>x;fetch("/fake")} fetch("/real")', {"/real"}),
        ('function a(){return ()=>()=>fetch("/nested")} ', {"/nested"}),
        ('for(let i=0;i<3;i++){fetch("/page/"+i)}', {"/page/0", "/page/1", "/page/2"}),
        ('Promise.resolve("/async").then(p=>fetch(p))', {"/async"}),
        ('["/a","/b"].forEach(p=>fetch(p))', {"/a", "/b"}),
        (
            'class Base {get(p){return fetch(p)}} class API extends Base {} new API().get("/inherited")',
            {"/inherited"},
        ),
        ('function f(n){if(n===0)return "/end";return f(n-1)} fetch(f(2))', {"/end"}),
    ],
)
def test_verified_misses(code, urls):
    result = Analyzer().analyze_js(code)
    assert {e.url for e in result.endpoints} == urls


def test_wire_descriptor():
    code = """import {createClient} from "@connectrpc/connect";
    import {createConnectTransport} from "@connectrpc/connect-web";
    const svc={typeName:"UserService",methods:{getUser:{name:"GetUser"}}};
    const c=createClient(svc,createConnectTransport({baseUrl:"https://e.test"}));
    c.getUser({id:1});"""
    assert Analyzer().analyze_js(code).endpoints[0].url == "https://e.test/UserService/GetUser"


def test_cyclic_live_bindings():
    resources = [
        Resource("/a.js", 'import {go} from "./b"; export const base="/v1"; go();'),
        Resource("/b.js", 'import {base} from "./a"; export function go(){fetch(base+"/u")}'),
    ]
    for ordered in (resources, list(reversed(resources))):
        assert {
            e.url for e in Analyzer().analyze_dataset(Dataset(resources=ordered)).endpoints
        } == {"/v1/u"}


@pytest.mark.parametrize("loader", ("webpack", "parcel"))
def test_bundle_table(loader):
    args = "require,module,exports" if loader == "parcel" else "module,exports,require"
    source = (
        "const modules={0:function("
        + args
        + '){const send=require(1);send("/bundled")},1:function('
        + args
        + "){module.exports=p=>fetch(p)}};"
    )
    assert {e.url for e in Analyzer().analyze_js(source).endpoints} == {"/bundled"}


def test_typescript_path_mapping():
    result = Analyzer(path_mappings={"@api/*": "/lib/*"}).analyze_dataset(
        Dataset(
            resources=[
                Resource("/lib/client.ts", "export const send=p=>fetch(p)"),
                Resource("/main.ts", 'import {send} from "@api/client";send("/mapped")'),
            ]
        )
    )
    assert {e.url for e in result.endpoints} == {"/mapped"}


def test_xhr_mutation_and_superagent_chain():
    result = Analyzer().analyze_js(
        'const x=new XMLHttpRequest();x.open("POST","/xhr");x.setRequestHeader("x-test","yes");x.send("body")'
    )
    assert result.endpoints[0].headers == {"x-test": "yes"}
    assert result.endpoints[0].body == "body"
    result = Analyzer().analyze_js(
        'import r from "superagent";r.post("/agent").set("x-test","yes").send({id:1})'
    )
    assert result.endpoints[0].headers == {"x-test": "yes"}
    assert result.endpoints[0].body == {"id": 1}


def test_defaults_and_variants():
    result = Analyzer().analyze_js(
        'const c=axios.create();c.defaults.baseURL="/v2";c.defaults.headers["x-test"]="yes";c.post("/u",{id:1});c.post("/u",{id:2})'
    )
    endpoint = result.endpoints[0]
    assert endpoint.url == "/v2/u"
    assert endpoint.headers == {"x-test": "yes"}
    assert {v["body"]["id"] for v in endpoint.request_variants} == {1, 2}
