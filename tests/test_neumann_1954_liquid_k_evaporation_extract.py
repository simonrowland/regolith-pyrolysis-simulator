from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator


REPO_ROOT = Path(__file__).resolve().parents[1]
EXTRACT = (
    REPO_ROOT
    / "data/literature/extracts/neumann-1954-liquid-k-evaporation-coefficient.yaml"
)


def test_neumann_1954_table_2_rows_match_rendered_page_13() -> None:
    """Pin all five Table 2 rows to the page 13 render, not the garbled OCR exponents."""
    extract = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    points = extract["species"]["K"]["observations"][1]["values"]["points"]
    # Rendered p13, columns theta, p, 2a0, 2b0, t, A, B, l−2a0, alpha.
    assert len(points) == 5
    assert [
        (
            Decimal(str(point["T_C"])),
            point["p_mmHg_as_published"],
            Decimal(str(point["droplet_initial_diameter_2a0_cm"])),
            Decimal(str(point["droplet_initial_diameter_2b0_cm"])),
            point["duration"],
            point["A_cm_per_s"],
            point["B_cm3_per_s"],
            Decimal(str(point["l_minus_2a0_cm"])),
            Decimal(str(point["alpha"])),
        )
        for point in points
    ] == [
        (Decimal("66.6"), "1.20e-6", Decimal("0.15"), Decimal("0.12"), {"value": 200, "unit": "h"}, "2.55e-8", "1.6e-10", Decimal("0.55"), Decimal("0.89")),
        (Decimal("66.7"), "1.21e-6", Decimal("0.12"), Decimal("0.11"), {"value": 211, "unit": "h"}, "2.7e-8", "0.7e-10", Decimal("0.18"), Decimal("0.93")),
        (Decimal("104.5"), "2.66e-5", Decimal("0.12"), Decimal("0.10"), {"value": 180, "unit": "min"}, "0.93e-6", "2.7e-9", Decimal("0.38"), Decimal("1.01")),
        (Decimal("111.0"), "4.23e-5", Decimal("0.11"), Decimal("0.10"), {"value": 220, "unit": "min"}, "1.45e-6", "2.7e-9", Decimal("0.29"), Decimal("1.00")),
        (Decimal("119.2"), "7.4e-5", Decimal("0.12"), Decimal("0.10"), {"value": 190, "unit": "min"}, "2.40e-6", "6.0e-9", Decimal("0.38"), Decimal("0.95")),
    ]

    migrator = Migrator(REPO_ROOT)
    migrator._migrate_extract(EXTRACT)

    rows = [
        observation
        for observation in migrator.result.observations.values()
        if observation.source_id == "neumann-1954-liquid-k-evaporation-coefficient"
        and observation.locator is not None
        and str(observation.locator.table) == "2"
    ]
    # PDF p13, in printed row order: 66.6, 66.7, 104.5, 111.0, 119.2 °C.
    assert len(rows) == 5
    assert [row.value.point for row in rows] == [
        Decimal("0.89"),
        Decimal("0.93"),
        Decimal("1.01"),
        Decimal("1.00"),
        Decimal("0.95"),
    ]
    assert [row.identity.temperature_K.value for row in rows] == [
        Decimal("339.75"),
        Decimal("339.85"),
        Decimal("377.65"),
        Decimal("384.15"),
        Decimal("392.35"),
    ]
    assert [point["p_mmHg_as_published"] for point in points] == [
        "1.20e-6",
        "1.21e-6",
        "2.66e-5",
        "4.23e-5",
        "7.4e-5",
    ]
    assert all("total_pressure_Pa" not in row.point_conditions for row in rows)
    assert all(not row.identity.total_pressure_Pa.is_value for row in rows)
