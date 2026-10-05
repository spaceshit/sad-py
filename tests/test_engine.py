import json

import pytest

from sad import Analyzer, Dataset, Resource
from sad.protocols import SinkRegistry


@pytest.mark.parametrize(
    ("code", "method", "url"),
    [
        ('fetch("/api/users")', "GET", "/api/users"),
        ('fetch("/api/users/" + id, {method:"DELETE"})', "DELETE", "/api/users/{id}"),
        ('const API="/api/v2"; axios.post(`${API}/users`, data)', "POST", "/api/v2/users"),
        (
            'const c=axios.create({baseURL:"/api/v1"}); c.post("/users", payload)',
            "POST",
            "/api/v1/users",
        ),
        ('const f=fetch; f("/alias")', "GET", "/alias"),
        ('const {get:g}=axios; g("/destructure")', "GET", "/destructure"),
        ('const paths={users:"/users"}; axios.get(paths.users)', "GET", "/users"),
        ('const paths={users:"/users"}; axios.get(paths["users"])', "GET", "/users"),
        (
            'function req(p,o){return fetch("/api"+p,o)} function del(id){return req(`/users/${id}`,{method:"DELETE"})}',
            "DELETE",
            "/api/users/{id}",
        ),
        (
            'function a(p){return fetch(p)} function b(p){return a(p)} b("/nested")',
            "GET",
            "/nested",
        ),
        (
            'function factory(base){return p=>fetch(base+p)} const c=factory("/v1"); c("/users")',
            "GET",
            "/v1/users",
        ),
        ('const c={get(p){return fetch(p)}}; c.get("/object")', "GET", "/object"),
        ('const x=new XMLHttpRequest(); x.open("POST","/xhr"); x.send(data)', "POST", "/xhr"),
        ('navigator.sendBeacon("/events", data)', "POST", "/events"),
        ('new WebSocket("wss://example.com/ws")', "GET", "wss://example.com/ws"),
        ('new EventSource("/stream")', "GET", "/stream"),
        ('import ky from "ky"; ky.get("/ky")', "GET", "/ky"),
        ('import got from "got"; got.post("/got")', "POST", "/got"),
        (
            'import {io} from "socket.io-client"; io("https://example.com")',
            "GET",
            "https://example.com/",
        ),
        ('$.ajax({url:"/jquery", type:"POST", data:{x:1}})', "POST", "/jquery"),
        (
            'import {request} from "graphql-request"; request("/graphql", "query { users {id} }")',
            "POST",
            "/graphql",
        ),
        ('const f = (id: string) => fetch(`/u/${id}`); f("42")', "GET", "/u/42"),
        ('fetch("\\x2fescaped")', "GET", "/escaped"),
        ('function unused(){fetch("/hidden")}', "GET", "/hidden"),
        ('function f(fetch){fetch("/fake")} f(x=>x)', None, None),
        ('const store={get(x){return x}}; store.get("/not-network")', None, None),
        ('unknown.get("/not-proven")', None, None),
        ('const fetch = x=>x; fetch("/shadowed")', None, None),
    ],
)
def test_patterns(code, method, url):
    result = Analyzer().analyze_js(code, filename="input.ts")
    endpoints = {(e.method, e.url) for e in result.endpoints}
    if url is None:
        assert endpoints == set()
    else:
        assert (method, url) in endpoints, (endpoints, result.diagnostics)
        assert all(e.evidence for e in result.endpoints)


def test_imports_reexports_and_aliases():
    result = Analyzer().analyze_dataset(
        Dataset(
            resources=[
                Resource("/app/client.ts", 'export const req=(path)=>fetch("/api"+path);'),
                Resource("/app/index.ts", 'export {req as send} from "./client";'),
                Resource("/app/main.ts", 'import {send as call} from "./index"; call("/users");'),
            ]
        )
    )
    assert any(e.url == "/api/users" for e in result.endpoints)
    assert result.call_graph


def test_commonjs():
    result = Analyzer().analyze_dataset(
        Dataset(
            resources=[
                Resource("/a.js", "module.exports=(p)=>fetch(p)"),
                Resource("/b.js", 'const fn=require("./a"); fn("/commonjs")'),
            ]
        )
    )
    assert any(e.url == "/commonjs" for e in result.endpoints)


def test_environment_and_payload():
    result = Analyzer(environment={"API": "/v3"}).analyze_js("""
        fetch(process.env.API + "/items", {method:"POST", headers:{"Content-Type":"application/json"},
          body:JSON.stringify({jsonrpc:"2.0", method:"create", params:{id: userId}})});
    """)
    endpoint = result.endpoints[0]
    assert endpoint.url == "/v3/items"
    assert endpoint.protocol == "json-rpc"
    assert endpoint.body["params"]["id"] == "{userId}"
    assert endpoint.content_type == "application/json"


def test_source_map():
    data = {
        "version": 3,
        "sources": ["original.ts"],
        "sourcesContent": ['fetch("/mapped")'],
        "mappings": "",
    }
    result = Analyzer().analyze_dataset(
        Dataset(resources=[Resource("/app.js.map", json.dumps(data))])
    )
    assert result.endpoints[0].url == "/mapped"
    assert "original.ts" in result.endpoints[0].source_file


def test_cache_and_budget():
    analyzer = Analyzer()
    analyzer.analyze_js('fetch("/cached")')
    misses = analyzer.cache.misses
    analyzer.analyze_js('fetch("/cached")')
    assert analyzer.cache.misses == misses
    assert analyzer.cache.hits > 0
    result = Analyzer(max_steps=5).analyze_js('fetch("/a"); fetch("/b"); fetch("/c")')
    assert any("budget" in message for message in result.diagnostics)


def test_custom_package_registration():
    registry = SinkRegistry()
    registry.register_package("my-http-client")
    result = Analyzer(registry=registry).analyze_js(
        'import c from "my-http-client"; c.get("/extension")'
    )
    assert result.endpoints[0].url == "/extension"


def test_parse_recovery_and_minification():
    result = Analyzer().analyze_js('(()=>{const a="/min";fetch(a)})(); function broken(')
    assert any(e.url == "/min" for e in result.endpoints)
    assert result.diagnostics


def test_class_wrapper():
    result = Analyzer().analyze_js("""
    class API { constructor(base){this.base=base} get(path){return fetch(this.base+path)} }
    const c=new API('/v1'); c.get('/users');
    """)
    assert any(e.url == "/v1/users" for e in result.endpoints)


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ('let p="/a"; if(flag){p="/b"} fetch(p)', {"/a", "/b"}),
        ('fetch(flag ? "/a" : "/b")', {"/a", "/b"}),
        ('const p=flag ? "/a" : "/b"; fetch("/api"+p)', {"/api/a", "/api/b"}),
        ('const c=new Request("/request",{method:"POST"}); fetch(c)', {"/request"}),
        ('fetch(new URL("/absolute", "https://e.test"))', {"https://e.test/absolute"}),
        (
            'const c=axios.create({baseURL:"/a"}); const d=axios.create({baseURL:"/b"}); c.get("/x"); d.get("/x")',
            {"/a/x", "/b/x"},
        ),
        ('import w from "wretch"; w("/fluent").post({name:"a"})', {"/fluent"}),
        (
            'import {GraphQLClient} from "graphql-request"; const c=new GraphQLClient("/graphql"); c.request("query {u}")',
            {"/graphql"},
        ),
        (
            'import {ApolloClient} from "@apollo/client"; const c=new ApolloClient({uri:"/gql"}); c.query({query:"query {u}"})',
            {"/gql"},
        ),
        (
            'const f=document.createElement("form"); f.action="/submit"; f.method="POST"; f.submit()',
            {"/submit"},
        ),
    ],
)
def test_extended_patterns(code, expected):
    result = Analyzer().analyze_js(code)
    assert {e.url for e in result.endpoints} == expected


def test_grpc_and_soap():
    grpc = Analyzer().analyze_js(
        'import * as grpc from "grpc-web"; const c=new grpc.GrpcWebClientBase({}); c.rpcCall("https://e.test/Svc/Method", msg, {}, desc, callback)'
    )
    assert grpc.endpoints[0].protocol == "grpc-web"
    soap = Analyzer().analyze_js(
        'fetch("/soap",{method:"POST",headers:{"Content-Type":"application/soap+xml"},body:xml})'
    )
    assert soap.endpoints[0].protocol == "soap"


def test_ky_options_and_angular_type():
    result = Analyzer().analyze_js('import ky from "ky"; ky.post("/users",{json:{name:"a"}})')
    assert result.endpoints[0].body == {"name": "a"}
    result = Analyzer().analyze_js(
        'import {HttpClient} from "@angular/common/http"; function send(http: HttpClient){http.get("/typed")}',
        filename="input.ts",
    )
    assert any(e.url == "/typed" for e in result.endpoints)


def test_lexical_shadow_before_declaration():
    result = Analyzer().analyze_js('fetch("/false-positive"); const fetch=x=>x;')
    assert not result.endpoints


def test_trpc_and_connect():
    result = Analyzer().analyze_js("""
      import {createTRPCProxyClient, httpBatchLink} from '@trpc/client';
      const c=createTRPCProxyClient({links:[httpBatchLink({url:'/trpc'})]});
      c.users.list.query(); c.users.create.mutate({name:'a'});
    """)
    assert {(e.method, e.url) for e in result.endpoints} == {
        ("GET", "/trpc/users.list"),
        ("POST", "/trpc/users.create"),
    }
    result = Analyzer().analyze_js("""
      import {createConnectTransport} from '@connectrpc/connect-web';
      import {createClient} from '@connectrpc/connect';
      const transport=createConnectTransport({baseUrl:'https://e.test'});
      const service={typeName:'example.UserService'};
      const c=createClient(service, transport); c.getUser({id:1});
    """)
    assert result.endpoints[0].protocol == "connect-web"
    assert result.endpoints[0].url == "https://e.test/example.UserService/getUser"


def test_typed_symbolic_parameter_name():
    result = Analyzer().analyze_js(
        "export function remove(id: string){fetch(`/users/${id}`)}", filename="input.ts"
    )
    assert result.endpoints[0].url == "/users/{id}"


def test_source_map_inline_and_invalid_shapes():
    import base64

    content = json.dumps(
        {"version": 3, "sources": ["src/original.js"], "sourcesContent": ['fetch("/original")']}
    )
    encoded = base64.b64encode(content.encode()).decode()
    result = Analyzer().analyze_js("//# sourceMappingURL=data:application/json;base64," + encoded)
    assert result.endpoints[0].url == "/original"
    for invalid in ("[]", '{"version":3,"sources":4}', '{"version":2}'):
        result = Analyzer().analyze_dataset(Dataset(resources=[Resource("bad.map", invalid)]))
        assert result.diagnostics


def test_instance_closures_are_isolated():
    result = Analyzer().analyze_js("""
        class API {constructor(base){this.base=base} get(p){return fetch(this.base+p)}}
        const a=new API('/a'); const b=new API('/b'); a.get('/x'); b.get('/x');
    """)
    assert {e.url for e in result.endpoints} == {"/a/x", "/b/x"}


def test_default_and_namespace_exports():
    result = Analyzer().analyze_dataset(
        Dataset(
            resources=[
                Resource("/a.js", "export default function send(p){return fetch(p)}"),
                Resource("/b.js", 'import * as a from "./a"; a.default("/namespace")'),
            ]
        )
    )
    assert any(e.url == "/namespace" for e in result.endpoints)
