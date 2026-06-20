from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from simulator.core import PyrolysisSimulator
from simulator.melt_backend.base import StubBackend
from simulator.state import CampaignPhase, MeltState


ROOT = Path(__file__).resolve().parents[1]


def _load_yaml(name: str) -> dict[str, Any]:
    return yaml.safe_load((ROOT / "data" / name).read_text())


def _make_sim(feedstock_id: str = "lunar_mare_low_ti") -> PyrolysisSimulator:
    setpoints = _load_yaml("setpoints.yaml")
    setpoints.setdefault("chemistry_kernel", {})["allow_fallback_vapor"] = True
    sim = PyrolysisSimulator(
        StubBackend(),
        setpoints,
        _load_yaml("feedstocks.yaml"),
        _load_yaml("vapor_pressures.yaml"),
    )
    sim.load_batch(feedstock_id, mass_kg=1000.0)
    return sim


def test_melt_fO2_log_exists_and_defaults_to_intrinsic_seed() -> None:
    melt = MeltState()

    assert hasattr(melt, "melt_fO2_log")
    assert melt.fO2_log == pytest.approx(-9.0)
    assert melt.melt_fO2_log == pytest.approx(-9.0)


def test_load_batch_seeds_melt_fO2_log_from_intrinsic_value() -> None:
    sim = _make_sim()
    intrinsic = sim._compute_intrinsic_melt_fO2()

    assert sim.melt.fO2_log == pytest.approx(intrinsic)
    assert sim.melt.melt_fO2_log == pytest.approx(intrinsic)


def test_start_campaign_mirrors_intrinsic_value_to_melt_fO2_log() -> None:
    sim = _make_sim()
    sim.melt.melt_fO2_log = 123.0

    sim.start_campaign(CampaignPhase.C0)
    intrinsic = sim._compute_intrinsic_melt_fO2()

    assert sim.melt.fO2_log == pytest.approx(intrinsic)
    assert sim.melt.melt_fO2_log == pytest.approx(intrinsic)


def test_step_mirrors_intrinsic_value_to_melt_fO2_log() -> None:
    sim = _make_sim()
    sim.start_campaign(CampaignPhase.C0)
    sim.melt.melt_fO2_log = 123.0

    sim.step()
    intrinsic = sim._compute_intrinsic_melt_fO2()

    assert sim.melt.fO2_log == pytest.approx(intrinsic)
    assert sim.melt.melt_fO2_log == pytest.approx(intrinsic)


def test_references_registry_carries_sso_r_r20_redox_citations() -> None:
    registry_path = ROOT / "docs" / "references" / "references.yaml"
    references = yaml.safe_load(registry_path.read_text(encoding="utf-8"))["references"]

    expected_notes = {
        "REF-001": "Kress91 ln(XFe2O3/XFeO) relation",
        "REF-035": "log10(fO2/bar) = 8.58 - 25050/T",
        "REF-036": "log10(fO2/bar) = -27215/T + 6.57",
        "REF-037": "graphite-CO-CO2 point formula",
        "REF-038": "IW-2 .. IW",
        "REF-039": "reduced vs terrestrial",
    }

    for ref_id, expected in expected_notes.items():
        assert ref_id in references
        assert expected in references[ref_id]["coefficient_note"]


def test_melt_fO2_log_is_inert_write_only_until_r21() -> None:
    """R2.0 contract + R2.1 tripwire: ``melt_fO2_log`` is write-only.

    It is mirrored from the intrinsic value at the three lifecycle sites and
    consumed by NO physics path (evaporation / equilibrium / vapor-pressure /
    MRE) until R2.1 wires it into ``a_FeO``. When R2.1 adds the first read,
    this test SHOULD fail -- update it then, deliberately, so the
    golden-neutral boundary crossing is explicit and reviewed.
    """
    import re

    # Match the bare attribute/field token, NOT the pre-existing method name
    # ``_resolve_intrinsic_melt_fO2_log`` (where it is preceded by a word char).
    token = re.compile(r"(?<!\w)melt_fO2_log")
    allowed_write = "self.melt.melt_fO2_log ="   # the 3 mirror assignments
    allowed_field = "melt_fO2_log:"              # the dataclass field declaration

    offenders: list[str] = []
    for src_dir in (ROOT / "simulator", ROOT / "engines"):
        for path in src_dir.rglob("*.py"):
            for lineno, line in enumerate(path.read_text().splitlines(), 1):
                if not token.search(line):
                    continue
                if allowed_write in line or allowed_field in line:
                    continue
                rel = path.relative_to(ROOT)
                offenders.append(f"{rel}:{lineno}: {line.strip()}")

    assert offenders == [], (
        "melt_fO2_log is read/used outside its write sites -- it must stay "
        "INERT until R2.1. If this is the R2.1 wiring, update this tripwire "
        "deliberately:\n" + "\n".join(offenders)
    )
