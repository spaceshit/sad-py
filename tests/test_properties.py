from hypothesis import given
from hypothesis import strategies as st

from sad import Analyzer, Dataset, Resource
from sad.normalization import normalize_url

names = st.from_regex(r"[a-z][a-z0-9]{0,10}", fullmatch=True).filter(
    lambda n: (
        n
        not in {
            "fetch",
            "if",
            "for",
            "do",
            "var",
            "let",
            "const",
            "return",
            "class",
            "import",
            "export",
            "await",
            "try",
            "catch",
            "new",
            "while",
            "switch",
            "else",
            "case",
            "default",
            "in",
            "of",
            "this",
            "true",
            "false",
            "null",
            "with",
            "throw",
            "super",
            "yield",
            "void",
            "delete",
            "break",
            "continue",
            "debugger",
            "function",
            "instanceof",
            "typeof",
        }
    )
)


def operations(result):
    return {(e.method, e.url, e.protocol) for e in result.endpoints}


@given(names)
def test_identifier_renaming(name):
    result = Analyzer().analyze_js(f'const {name}="/users";fetch({name})')
    assert operations(result) == {("GET", "/users", "http")}


@given(
    st.sampled_from(["", " ", "\n", "\t", " /* x */ "]), st.sampled_from(["GET", "POST", "DELETE"])
)
def test_format_equivalence(space, method):
    source = f'fetch{space}({space}"/users"{space},{space}{{method:"{method}"}}{space})'
    assert operations(Analyzer().analyze_js(source)) == {(method, "/users", "http")}


@given(st.permutations([0, 1, 2]))
def test_module_order(order):
    resources = [
        Resource("/a.js", "export const send=p=>fetch(p)"),
        Resource("/b.js", 'export {send} from "./a"'),
        Resource("/c.js", 'import {send} from "./b";send("/u")'),
    ]
    assert operations(
        Analyzer().analyze_dataset(Dataset(resources=[resources[i] for i in order]))
    ) == {("GET", "/u", "http")}


@given(
    st.text(
        alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/-_%",
        min_size=0,
        max_size=60,
    )
)
def test_normalization_idempotence(path):
    url = "HTTPS://EXAMPLE.TEST:443/" + path + "?x=1#fragment"
    canonical = normalize_url(url)
    assert normalize_url(canonical) == canonical


@given(st.sampled_from(["GET", "POST", "PUT"]), names)
def test_cache_equivalence(method, identifier):
    code = f'const {identifier}="/cache";fetch({identifier},{{method:"{method}"}})'
    analyzer = Analyzer()
    first = analyzer.analyze_js(code).to_dict()
    assert analyzer.analyze_js(code).to_dict() == first
    assert Analyzer().analyze_js(code).to_dict() == first
    assert analyzer.cache.hits > 0
