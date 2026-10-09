"""Pin the existing declaration census before reusing its owning walker."""
import json
from pathlib import Path
from unittest.mock import patch

from simulator.config import load_config_bundle
from simulator.reference_data.janaf import feedstock_element_symbols


def test_all_feedstock_declaration_censuses_are_unchanged():
    pins = json.loads((Path(__file__).parent / "fixtures" /
                       "t1139_b4_element_census_before.json").read_text())
    feedstocks = load_config_bundle().feedstocks
    assert pins.keys() == feedstocks.keys()
    for name, feedstock in feedstocks.items():
        with patch("simulator.reference_data.janaf.load_cached_safe_yaml",
                   return_value={name: feedstock}):
            assert feedstock_element_symbols() == pins[name]
