from __future__ import annotations

from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/yan-2009-cr-ti-nb-v-slag-thermodynamics-review.yaml"


def test_review_values_are_attributed_context_and_migrate_without_fake_points() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    species = document["species"]
    assert all(not entry["observations"] for entry in species.values())
    context = {
        record["observation_id"]: record
        for entry in species.values()
        for record in entry["context"]
    }
    assert len(context) == 14

    expected = {
        "yan09_ref2_cr6_cr3_ratio_context": ("0.15–0.95", "Morita, Shibuya & Sano (1988) [2]"),
        "yan09_ref13_cr3_cr2_ratio_context": ("14–26", "Morita et al. (2005) [13]"),
        "yan09_ref19_tio15_gamma_context": ("2.3–8.6", "Morizane, Ozturk & Fruehan (1999) [19]"),
        "yan09_ref19_tio2_gamma_context": ("0.1–0.3", "Morizane, Ozturk & Fruehan (1999) [19]"),
        "yan09_ref17_ti3_ti4_ratio_context": ("7.9–91.2", "Ariyo, Gonzales & Holappa (2005) [17]"),
        "yan09_ref22_tio167_gamma_context": ("0.39–1.68", "Ariyo & Holappa (2002) [22]"),
        "yan09_ref21_tio2_gamma_context": ("0.6–3.5", "Jung & Fruehan (2001) [21]"),
        "yan09_ref21_tio15_gamma_context": ("54–132", "Jung & Fruehan (2001) [21]"),
        "yan09_ref28_nb_average_valence_context": ("4.8–5.0", "Tsukihashi et al. (1985) [28]"),
        "yan09_ref28_v_average_valence_context": ("2.2–4.8", "Tsukihashi et al. (1985) [28]"),
    }
    for observation_id, (printed_range, attribution) in expected.items():
        record = context[observation_id]
        assert record["type"] == "quoted_attributed"
        assert record["attribution"] == attribution
        assert printed_range in record["values"]["printed_values_as_published"]
        assert record["values"]["candidate_tokens"]
        assert record["values"]["schema_gap"]

    assert "7.04×10−6 Pa" in context["yan09_ref13_cr3_cr2_ratio_context"]["values"]["printed_values_as_published"]
    assert "4.1×10−7 Pa" in context["yan09_ref17_ti3_ti4_ratio_context"]["values"]["printed_values_as_published"]
    assert "1473–1623 K" in context["yan09_ref28_nb_average_valence_context"]["values"]["printed_values_as_published"]
    for entry in species.values():
        for record in entry["context"]:
            assert record["locator"]["page"] == record["locator"]["pdf_page_index"] + 1
            assert record["values"].get("printed_values_as_published")

    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert not migrator.result.observations
    assert not migrator.result.validation.hard_issues
