import hashlib
import json
from pathlib import Path

import pytest

from sad import Analyzer, Dataset, Resource

ROOT = Path(__file__).parent / "fixtures"
EXPECTED = {
    "axios.js": {
        ("GET", "https://api.example.test/v2/users"),
        ("POST", "https://write.example.test/users"),
    },
    "openapi.ts": {
        ("GET", "https://petstore.example.test/v1/pets/12"),
        ("POST", "https://petstore.example.test/v1/pets"),
    },
    "trpc.ts": {
        ("GET", "http://localhost:2022/greeting"),
        ("POST", "http://localhost:2022/user.create"),
    },
    "connect.ts": {
        ("POST", "https://demo.connectrpc.com/connectrpc.eliza.v1.ElizaService/Say"),
        ("POST", "https://demo.connectrpc.com/connectrpc.eliza.v1.ElizaService/Introduce"),
    },
}


@pytest.mark.parametrize("filename", EXPECTED)
def test_adapted_upstream(filename):
    result = Analyzer().analyze_js((ROOT / "adapted" / filename).read_text(), filename=filename)
    assert {(e.method, e.url) for e in result.endpoints} == EXPECTED[filename]
    assert all(e.evidence for e in result.endpoints)


def test_upstream_provenance_hashes():
    for entry in json.loads((ROOT / "upstream/manifest.json").read_text()):
        assert len(entry["commit"]) == 40
        assert any(name.endswith("LICENSE") for name in entry["files"])
        for name, expected in entry["files"].items():
            assert hashlib.sha256((ROOT / "upstream" / name).read_bytes()).hexdigest() == expected


def test_unchanged_connect_component_with_supplied_descriptor():
    app = (ROOT / "upstream/connectrpc/App.tsx").read_text()
    descriptor = 'export const ElizaService={typeName:"connectrpc.eliza.v1.ElizaService",methods:{say:{name:"Say"},introduce:{name:"Introduce"}}};'
    result = Analyzer().analyze_dataset(
        Dataset(
            resources=[
                Resource("/src/App.tsx", app),
                Resource("/src/gen/connectrpc/eliza/v1/eliza_pb.js", descriptor),
            ]
        )
    )
    assert {(e.method, e.url) for e in result.endpoints} == EXPECTED["connect.ts"]
    assert result.analysis_incomplete  # React/UI dependencies are deliberately absent.
