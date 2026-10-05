"""Human-authored detection oracles. Products exercise distinct verb/flow combinations."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Case:
    name: str
    files: dict[str, str]
    expected: frozenset[tuple[str | None, str, str]] = frozenset()
    forbidden: frozenset[str] = frozenset()
    category: str = "language"
    issue_codes: frozenset[str] = frozenset()
    symbolic: frozenset[str] = frozenset()
    evidence: frozenset[str] = frozenset({"ast-call"})
    path_mappings: dict[str, str] = field(default_factory=dict)


VERBS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
# Each template has a separately reviewable flow to the sole expected /users operation.
FLOWS = {
    "literal": 'fetch("/users", OPT)',
    "constant": 'const p="/users";fetch(p,OPT)',
    "concatenation": 'const root="/";const leaf="users";fetch(root+leaf,OPT)',
    "template": 'const leaf="users";fetch(`/${leaf}`,OPT)',
    "alias": 'const f=fetch;f("/users",OPT)',
    "window-alias": 'const f=window.fetch;f("/users",OPT)',
    "globalThis": 'globalThis.fetch("/users",OPT)',
    "object-config": 'const x={path:"/users",options:OPT};fetch(x.path,x.options)',
    "computed-key": 'const x={path:"/users"};const k="path";fetch(x[k],OPT)',
    "destructure": 'const {p}={p:"/users"};fetch(p,OPT)',
    "destructure-alias": 'const {p:q}={p:"/users"};fetch(q,OPT)',
    "array-destructure": 'const [p]=["/users"];fetch(p,OPT)',
    "wrapper": 'function request(p,o){return fetch(p,o)}request("/users",OPT)',
    "nested-wrapper": 'function inner(p,o){return fetch(p,o)}function outer(p){return inner(p,OPT)}outer("/users")',
    "closure-factory": 'function factory(o){return p=>fetch(p,o)}const f=factory(OPT);f("/users")',
    "object-method": 'const c={request(p){return fetch(p,OPT)}};c.request("/users")',
    "class-method": 'class C{request(p){return fetch(p,OPT)}}const c=new C();c.request("/users")',
    "request-object": 'fetch(new Request("/users",OPT))',
    "call": 'fetch.call(null,"/users",OPT)',
    "apply": 'fetch.apply(null,["/users",OPT])',
    "bound-path": 'const f=fetch.bind(null,"/users",OPT);f()',
    "callback": '["/users"].forEach(p=>fetch(p,OPT))',
    "promise": 'Promise.resolve("/users").then(p=>fetch(p,OPT))',
    "typed-function": 'function req(p:string){return fetch(p,OPT)}req("/users")',
}


def positive_cases():
    for name, template in FLOWS.items():
        for verb in VERBS:
            yield Case(
                f"{name}-{verb}",
                {"/input.ts": template.replace("OPT", '{method:"' + verb + '"}')},
                frozenset({(verb, "/users", "http")}),
            )


# Negative oracles assert zero endpoints, including superficially network-like names.
NEGATIVE_FLOWS = {
    "plain-object": 'const obj={METHOD(x){return x}};obj.METHOD("/fake")',
    "unknown-object": 'unknown.METHOD("/fake")',
    "shadow-function": 'function fetch(x){return x}fetch("/fake")',
    "shadow-arrow": 'const fetch=x=>x;fetch("/fake")',
    "shadow-parameter": 'function f(fetch){fetch("/fake")}f(x=>x)',
    "shadow-window": 'const window={fetch(x){return x}};window.fetch("/fake")',
    "shadow-global": 'const globalThis={fetch(x){return x}};globalThis.fetch("/fake")',
    "shadow-axios": 'const axios={METHOD(x){return x}};axios.METHOD("/fake")',
    "string-literal": 'const text="fetch(\\"/fake\\")";',
    "comment": '// fetch("/fake")\nconst x=1;',
    "block-comment": '/* axios.METHOD("/fake") */',
    "object-url": 'const data={url:"/fake",method:"POST"};',
    "unused-client-factory": 'const c=axios.create({baseURL:"/fake"});',
    "request-construction": 'const r=new Request("/fake");',
    "url-construction": 'const r=new URL("/fake","https://e.test");',
    "form-construction": 'const f=document.createElement("form");f.action="/fake";',
    "xhr-construction": "const x=new XMLHttpRequest();",
    "unrelated-class": 'class C{METHOD(x){return x}}new C().METHOD("/fake")',
    "shadow-beacon": 'const navigator={sendBeacon(x){return x}};navigator.sendBeacon("/fake")',
    "lexical-before-declaration": 'fetch("/fake");let fetch=x=>x;',
}


def negative_cases():
    for name, template in NEGATIVE_FLOWS.items():
        for member in ("get", "post", "request", "query"):
            yield Case(
                f"negative-{name}-{member}",
                {"/input.js": template.replace("METHOD", member)},
                forbidden=frozenset({"/fake"}),
                category="negative",
            )


def module_cases():
    for verb in VERBS:
        body = 'fetch(p,{method:"' + verb + '"})'
        variants = {
            "named": {
                "/client.js": "export function send(p){return " + body + "}",
                "/main.js": 'import {send} from "./client";send("/users")',
            },
            "default": {
                "/client.js": "export default function send(p){return " + body + "}",
                "/main.js": 'import send from "./client";send("/users")',
            },
            "arrow": {
                "/client.js": "export const send=p=>" + body,
                "/main.js": 'import {send} from "./client";send("/users")',
            },
            "namespace": {
                "/client.js": "export function send(p){return " + body + "}",
                "/main.js": 'import * as c from "./client";c.send("/users")',
            },
            "renamed": {
                "/client.js": "export function send(p){return " + body + "}",
                "/main.js": 'import {send as go} from "./client";go("/users")',
            },
            "reexport": {
                "/client.js": "export const send=p=>" + body,
                "/index.js": 'export {send as go} from "./client"',
                "/main.js": 'import {go} from "./index";go("/users")',
            },
            "export-star": {
                "/client.js": "export const send=p=>" + body,
                "/index.js": 'export * from "./client"',
                "/main.js": 'import {send} from "./index";send("/users")',
            },
            "commonjs-default": {
                "/client.js": "module.exports=p=>" + body,
                "/main.js": 'const send=require("./client");send("/users")',
            },
            "commonjs-named": {
                "/client.js": "exports.send=p=>" + body,
                "/main.js": 'const {send}=require("./client");send("/users")',
            },
            "ts-extension": {
                "/client.ts": "export const send=(p:string)=>" + body,
                "/main.ts": 'import {send} from "./client.ts";send("/users")',
            },
            "index-resolution": {
                "/client/index.js": "export const send=p=>" + body,
                "/main.js": 'import {send} from "./client";send("/users")',
            },
            "cyclic": {
                "/a.js": 'import {go} from "./b";export const p="/users";go()',
                "/b.js": 'import {p} from "./a";export function go(){return ' + body + "}",
            },
        }
        for name, files in variants.items():
            yield Case(
                f"module-{name}-{verb}",
                files,
                frozenset({(verb, "/users", "http")}),
                category="modules",
            )


CASES = [*positive_cases(), *negative_cases(), *module_cases()]


def protocol_cases():
    for package in ("axios", "ky", "got", "superagent", "request", "ofetch", "jquery"):
        for verb in VERBS:
            code = f'import c from "{package}";c.{verb.lower()}("/users")'
            yield Case(
                f"client-{package}-{verb}",
                {"/input.js": code},
                frozenset({(verb, "/users", "http")}),
                category="protocols",
            )
    for package in ("axios", "ky", "got"):
        for verb in VERBS:
            basekey = "prefixUrl" if package == "ky" else "baseURL"
            code = f'import client from "{package}";const c=client.create({{{basekey}:"/v2"}});c.{verb.lower()}("users")'
            yield Case(
                f"factory-{package}-{verb}",
                {"/input.js": code},
                frozenset({(verb, "/v2/users", "http")}),
                category="protocols",
            )
    for constructor, protocol, url in [
        ("WebSocket", "websocket", "wss://e.test/ws"),
        ("EventSource", "sse", "/events"),
    ]:
        yield Case(
            constructor,
            {"/input.js": f'new {constructor}("{url}")'},
            frozenset({("GET", url, protocol)}),
            category="protocols",
        )
    yield Case(
        "beacon",
        {"/input.js": 'navigator.sendBeacon("/telemetry",data)'},
        frozenset({("POST", "/telemetry", "http")}),
        category="protocols",
    )
    for bad in (
        'fetch.get("/fake")',
        'fetch.create({baseURL:"/fake"})("/x")',
        'new URL("/fake","https://e.test").post("/x")',
        'fetch("/real").get("/fake")',
    ):
        expected = frozenset({("GET", "/real", "http")}) if "/real" in bad else frozenset()
        yield Case(
            "invalid-method-" + bad, {"/input.js": bad}, expected, frozenset({"/fake"}), "negative"
        )


CASES.extend(protocol_cases())
