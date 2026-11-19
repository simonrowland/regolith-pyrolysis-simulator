"""One-off Studio diagnostic for t-1872's source-interval backlog report."""

from collections import Counter
import inspect
import json

import pytest

from simulator.battery.compilation_tier import (
    is_compilation_evidence,
    predict_thermo_attempt,
)
from simulator.battery.enums import Engine, PURE_STANDARD_THERMO
from simulator.battery.identity import quantity_token
from simulator.battery.score import is_compilation_source, load_score_context


def test_openimcc_source_anchor_metadata() -> None:
    import openimcc

    datapack = openimcc.load_gas_datapack()
    props = openimcc.species_thermo("FeO", "g", 2000.0, datapack=datapack)
    gas_row = datapack.gas_df.loc["FeO(g)"]
    report = {
        "datapack_type": type(datapack).__name__,
        "datapack_attrs": {
            name: type(getattr(datapack, name)).__name__
            for name in dir(datapack)
            if not name.startswith("_")
        },
        "species_thermo_fields": {
            name: getattr(props, name)
            for name in dir(props)
            if not name.startswith("_") and not callable(getattr(props, name))
        },
        "gas_columns": list(datapack.gas_df.columns),
        "gas_row": (
            [row.to_dict() for _index, row in gas_row.iterrows()]
            if hasattr(gas_row, "iterrows")
            else gas_row.to_dict()
        ),
        "public_anchor_names": sorted(
            name for name in dir(openimcc)
            if any(token in name.casefold() for token in ("anchor", "source", "provenance"))
        ),
        "gas_species_provenance_signature": str(
            inspect.signature(openimcc.gas_species_provenance)
        ),
        "gas_species_provenance_doc": inspect.getdoc(openimcc.gas_species_provenance),
    }
    pytest.skip("T1872_SOURCE_ANCHOR=" + json.dumps(report, default=str, sort_keys=True))


def test_openimcc_interval_backlog_counts_by_source() -> None:
    pytest.skip("T1872_INTERVAL_REPORT_PENDING")
