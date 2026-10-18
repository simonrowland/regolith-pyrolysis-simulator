"""The family proxy is central among the actual samples in its cited extracts."""

import pytest

from tests.chemistry.conftest import _load_yaml


@pytest.mark.parametrize('key, central, interval, samples', [
    ('lunar_mare_low_ti', 41., [12., 70.], ('12032', '15020')),
    ('lunar_highland', 81.5, [57., 106.], ('60050', '65701')),
    ('lunar_pkt_kreep_average', 53., [46., 74.], ('14240', '14149', '14148')),
])
def test_lunar_proxy_is_median_of_cited_samples(key, central, interval, samples):
    observation = _load_yaml('feedstocks.yaml')[key]['iron_speciation']['is_over_feo']
    assert observation['value'] == central
    assert observation['interval'] == interval
    assert observation['authority'] == 'literature_typical'
    assert 'median' in observation['note'].lower()
    for sample in samples:
        assert sample in observation['note']
