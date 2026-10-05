from pathlib import Path

import pytest

from sad import Analyzer

ROOT = Path(__file__).parent / "fixtures/bundles"


@pytest.mark.parametrize(
    ("filename", "expected"),
    [("parcel.js", {"/parcel"}), ("webpack.js", {"/webpack"}), ("rollup.js", {"/rollup/users"})],
)
def test_bundle_shapes(filename, expected):
    assert {e.url for e in Analyzer().analyze_path(ROOT / filename).endpoints} == expected


def test_vite_lazy_chunk_source_supplied():
    from sad import Dataset, Resource

    resources = [
        Resource("/" + name, (ROOT / name).read_text()) for name in ("vite.js", "vite-chunk.js")
    ]
    result = Analyzer().analyze_dataset(Dataset(resources=resources))
    assert {e.url for e in result.endpoints} == {"/vite", "/vite-hidden"}
