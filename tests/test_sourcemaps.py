import base64
import json

import pytest

from sad import Analyzer, Dataset, Resource
from sad.javascript.parser import ASTCache
from sad.javascript.resources import inline_source_map, references, source_map
from sad.javascript.sourcemaps import SourceMapIndex, sections, vlq


@pytest.mark.parametrize(
    ("encoded", "expected"),
    [
        ("A", [0]),
        ("C", [1]),
        ("D", [-1]),
        ("gB", [16]),
        ("hB", [-16]),
        ("AAAA", [0, 0, 0, 0]),
        ("gBC", [16, 1]),
    ],
)
def test_vlq_known_vectors(encoded, expected):
    assert vlq(encoded) == expected


@pytest.mark.parametrize("encoded", ["!", "g", "//////////A"])
def test_vlq_invalid(encoded):
    with pytest.raises(ValueError):
        vlq(encoded)


@pytest.mark.parametrize(
    "value",
    [
        None,
        {},
        {"version": 2},
        {"version": 3, "sections": None},
        {"version": 3, "sections": [{}]},
        {"version": 3, "sections": [{"map": {"version": 3}, "offset": {"line": -1, "column": 0}}]},
    ],
)
def test_invalid_sections(value):
    with pytest.raises(ValueError):
        sections(value)


def test_section_depth():
    value = {"version": 3}
    for _ in range(18):
        value = {"version": 3, "sections": [{"offset": {"line": 0, "column": 0}, "map": value}]}
    with pytest.raises(ValueError):
        sections(value)


@pytest.mark.parametrize(
    "patch",
    [
        {"sources": None},
        {"mappings": []},
        {"mappings": "AA"},
        {"mappings": "ACAA"},
        {"mappings": "AADA"},
        {"sources": [7], "mappings": "AAAA"},
    ],
)
def test_bad_mapping(patch):
    value = {"version": 3, "sources": ["a.ts"], "mappings": "AAAA", **patch}
    with pytest.raises(ValueError):
        SourceMapIndex(Resource("/a.js.map", json.dumps(value)))


def test_lookup_and_unmapped_segment():
    index = SourceMapIndex(
        Resource(
            "/a.js.map",
            json.dumps({"version": 3, "sources": ["src/a.ts"], "mappings": "AAEE,K;AACA"}),
        )
    )
    assert index.lookup(1, 1).source_file == "/src/a.ts"
    assert (index.lookup(1, 1).line, index.lookup(1, 1).column) == (3, 3)
    assert index.lookup(1, 6) is None
    assert index.lookup(3, 1) is None
    assert index.lookup(1, 0) is None


def test_indexed_map_expansion_and_endpoint_location():
    value = {
        "version": 3,
        "sections": [
            {
                "offset": {"line": 1, "column": 0},
                "map": {"version": 3, "sources": ["original.ts"], "mappings": "AAGE"},
            }
        ],
    }
    result = Analyzer().analyze_dataset(
        Dataset(
            resources=[
                Resource("/a.js", '\nfetch("/mapped")'),
                Resource("/a.js.map", json.dumps(value)),
            ]
        )
    )
    endpoint = result.endpoints[0]
    assert (endpoint.source_file, endpoint.source_line, endpoint.source_column) == (
        "/original.ts",
        4,
        3,
    )
    assert "source-map" in {e.kind for e in endpoint.evidence}
    value["sections"][0]["map"]["sourcesContent"] = ['fetch("/original")']
    assert source_map(Resource("/a.js.map", json.dumps(value)))[0].content == 'fetch("/original")'


@pytest.mark.parametrize(
    "value",
    [
        {"version": 3, "sources": None},
        {"version": 3, "sourcesContent": {}},
        {"version": 3, "sourceRoot": 7},
    ],
)
def test_invalid_source_shapes(value):
    with pytest.raises(ValueError):
        source_map(Resource("/a.map", json.dumps(value)))


def test_expansion_budget_and_absent_source():
    value = {"version": 3, "sources": ["a.ts", "b.ts"], "sourcesContent": [None, "long"]}
    resource = Resource("/a.map", json.dumps(value))
    assert len(source_map(resource)) == 1
    with pytest.raises(ValueError):
        source_map(resource, max_bytes=2)


@pytest.mark.parametrize(
    ("data", "budget"),
    [
        ("data:application/json,{}", 100),
        ("data:;base64," + "A" * 50, 5),
        ("data:;base64," + base64.b64encode(b"123456").decode(), 5),
        ("data:;base64,%%%!", 100),
    ],
)
def test_inline_map_bounds(data, budget):
    with pytest.raises(ValueError):
        inline_source_map(data, budget)


def test_only_source_references():
    code = 'import "axios";export {x} from "./x.js";import("/lazy.js");require("pkg");fetch("/api");import(variable); //# sourceMappingURL=a.map'
    assert references(Resource("https://e.test/app.js", code), ASTCache()) == [
        "https://e.test/x.js",
        "https://e.test/lazy.js",
        "https://e.test/a.map",
    ]


def test_named_external_map_with_nonterminal_directive():
    code = '//# sourceMappingURL=custom.map\nimport "./helper.js";\nfetch("/mapped")'
    value = {"version": 3, "sources": ["original.ts"], "mappings": ";;AAKA"}
    result = Analyzer().analyze_dataset(
        Dataset(
            resources=[
                Resource("/app.js", code),
                Resource("/custom.map", json.dumps(value)),
                Resource("/helper.js", "export const x=1"),
            ]
        )
    )
    endpoint = result.endpoints[0]
    assert (endpoint.source_file, endpoint.source_line) == ("/original.ts", 6)


@pytest.mark.parametrize(
    "value",
    [
        {"version": 3, "sourceRoot": 3},
        {"version": 3, "sections": [{"offset": None, "map": {"version": 3}}]},
    ],
)
def test_malformed_mapping_input_is_diagnostic(value):
    result = Analyzer().analyze_dataset(
        Dataset(resources=[Resource("/app.js.map", json.dumps(value))])
    )
    assert result.analysis_incomplete
