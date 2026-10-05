import string

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from sad import Analyzer


@pytest.mark.stress
@settings(max_examples=2000, deadline=None)
@given(st.text(alphabet=string.printable, max_size=500))
def test_malformed_javascript_fuzz(source):
    result = Analyzer(max_steps=5000, max_depth=8).analyze_js(source)
    assert isinstance(result.endpoints, list)
    assert all(endpoint.evidence for endpoint in result.endpoints)


@pytest.mark.stress
def test_large_bundle_all_operations_and_cache():
    source = ";".join(f'fetch("/large/{i}")' for i in range(5000))
    analyzer = Analyzer()
    first = analyzer.analyze_js(source)
    assert len(first.endpoints) == 5000
    assert not first.analysis_incomplete
    misses = analyzer.cache.misses
    assert analyzer.analyze_js(source).to_dict() == first.to_dict()
    assert analyzer.cache.misses == misses
