import pytest

from sad import Analyzer, Dataset, Resource
from tests.corpus import CASES


@pytest.mark.corpus
@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
def test_detection_corpus(case):
    result = Analyzer(path_mappings=case.path_mappings).analyze_dataset(
        Dataset(resources=[Resource(name, text) for name, text in case.files.items()])
    )
    actual = {(e.method, e.url, e.protocol) for e in result.endpoints}
    assert actual == set(case.expected), result.to_dict()
    assert not ({e.url for e in result.endpoints} & case.forbidden)
    assert case.issue_codes <= {i.code for i in result.issues}
    for endpoint in result.endpoints:
        assert case.evidence <= {e.kind for e in endpoint.evidence}
        assert endpoint.source_line and endpoint.source_column
    for symbol in case.symbolic:
        assert any("{" + symbol + "}" in e.url for e in result.endpoints)


def test_corpus_contract():
    assert len({case.name for case in CASES}) == len(CASES)
    assert sum(len(case.files) > 1 for case in CASES) >= 60
    assert sum(case.category == "negative" for case in CASES) >= 80
