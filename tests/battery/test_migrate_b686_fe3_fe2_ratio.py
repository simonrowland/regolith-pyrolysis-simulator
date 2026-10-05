"""b-686: Fe3+/Fe2+ under the existing quantity fe3_fe2_ratio (no new quantity).

PIN COMMIT. These tests capture the reader's behaviour on green e702b2551
BEFORE the b-686 change, so the change commit shows every flipped
expectation in its diff:

* Aithala 2026 Table S-1 prints Fe3+/FeT (= Fe3+/sum Fe) for 16 quenched
  glasses; the extract declares ``quantity: Fe3+/FeT``, which is not a closed
  quantity, so all 16 rows migrate with an unknown quantity and no value.
* A row that declares ``quantity: fe3_fe2_ratio`` but prints Fe3+/sum Fe, or
  Kress-style Fe2O3 and FeO wt %, selects no number: the reader has no
  source field for fe3_fe2_ratio at all.
"""

from __future__ import annotations

from pathlib import Path

from simulator.battery.enums import EvidenceClass, Quantity, ValueKind
from simulator.battery.migrate import Migrator, select_declared_source

REPO = Path(__file__).resolve().parents[2]
AITHALA = REPO / "data/literature/extracts/aithala-2026-magmatic-iron-redox-to-2100c.yaml"

# Table S-1 (PDF p. 13, SI-7): run id -> printed Fe3+/FeT, as in the extract.
AITHALA_S1_FE3_OVER_FET = {
    "aithala_2026_vf271_xanes": "0.816",
    "aithala_2026_vf267_moessbauer": "0.736",
    "aithala_2026_vf231_xanes": "0.726",
    "aithala_2026_vf231_moessbauer": "0.716",
    "aithala_2026_vf268_xanes": "0.65",
    "aithala_2026_vf268_moessbauer": "0.695",
    "aithala_2026_vf270_xanes": "0.685",
    "aithala_2026_vf270_moessbauer": "0.661",
    "aithala_2026_vf274_xanes": "0.637",
    "aithala_2026_vf274_moessbauer": "0.643",
    "aithala_2026_1800_30_xanes": "0.426",
    "aithala_2026_1900_30_xanes": "0.389",
    "aithala_2026_2000_30_xanes": "0.407",
    "aithala_2026_2100_30_xanes": "0.306",
    "aithala_2026_2000_10_xanes": "0.378",
    "aithala_2026_2000_120_xanes": "0.378",
}


def _aithala_fe_rows():
    migrator = Migrator()
    migrator._migrate_extract(AITHALA)
    migrator.finalize()
    prefix = "aithala-2026-magmatic-iron-redox-to-2100c::"
    return {
        oid[len(prefix):]: obs
        for oid, obs in migrator.result.observations.items()
        if oid[len(prefix):] in AITHALA_S1_FE3_OVER_FET
    }


def test_pin_aithala_fe3_over_fet_rows_have_no_closed_quantity() -> None:
    rows = _aithala_fe_rows()
    assert set(rows) == set(AITHALA_S1_FE3_OVER_FET)
    for oid, obs in rows.items():
        assert obs.identity.quantity.is_unknown, oid
        assert obs.identity.quantity.reason == "unsupported quantity 'Fe3+/FeT'", oid
        assert obs.value.kind is ValueKind.UNAVAILABLE, oid
        assert obs.evidence.class_.value is EvidenceClass.MEASURED_DIRECT, oid


def test_pin_declared_ratio_from_ferric_fraction_selects_nothing() -> None:
    sel = select_declared_source(
        Quantity.FE3_FE2_RATIO, None, {"quantity": "fe3_fe2_ratio", "Fe3_over_FeT": 0.25}
    )
    assert not sel.available
    assert sel.reason.startswith("no mapped fe3_fe2_ratio field selected")


def test_pin_declared_ratio_from_kress_wt_pct_selects_nothing() -> None:
    sel = select_declared_source(
        Quantity.FE3_FE2_RATIO,
        None,
        {"quantity": "fe3_fe2_ratio", "Fe2O3_wt_pct": "1.09", "FeO_wt_pct": "10.34"},
    )
    assert not sel.available
    assert sel.reason.startswith("no mapped fe3_fe2_ratio field selected")


def test_pin_declared_ratio_printed_directly_selects_nothing() -> None:
    sel = select_declared_source(
        Quantity.FE3_FE2_RATIO, None, {"quantity": "fe3_fe2_ratio", "fe3_fe2_ratio": 0.5}
    )
    assert not sel.available
