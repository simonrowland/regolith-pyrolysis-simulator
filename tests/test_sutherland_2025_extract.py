"""Source-specific guards for the Sutherland 2025 mare-simulant extract."""

from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import Quantity
from simulator.battery.migrate import Migrator, REPO_ROOT


def test_interval_mass_loss_stays_out_of_endpoint_observations(
    tmp_path: Path,
) -> None:
    source = (
        REPO_ROOT
        / "data/literature/extracts/sutherland-2025-mare-simulant-volatile-loss.yaml"
    )
    experiment_ids = {
        "eac-1a-tga-dsc-ega",
        "jsc-1a-tga-dsc-ega",
        "lms-1-tga-dsc-ega",
        "oprl2n-tga-dsc-ega",
        "oprl2nt-tga-dsc-ega",
        "tubs-m-tga-dsc-ega",
    }

    def mass_loss_count(extract: Path) -> int:
        migrator = Migrator(root=REPO_ROOT, index=None, aliases={})
        migrator._migrate_extract(extract)
        migrator.finalize()
        return sum(
            obs.identity.quantity.is_value
            and obs.identity.quantity.value is Quantity.MASS_LOSS_FRACTION
            and obs.experiment_id.rsplit("::", 1)[-1] in experiment_ids
            for obs in migrator.result.observations.values()
        )

    assert mass_loss_count(source) == 6

    document = yaml.safe_load(source.read_text(encoding="utf-8"))
    interval = document["species"]["EAC-1A"]["context"][0]["values"][
        "interval_losses"
    ][0]
    document["species"]["EAC-1A"]["observations"].append(
        {
            "observation_id": "sutherland_2025_eac_1a_interval_typed_again",
            "experiment": "eac-1a-tga-dsc-ega",
            "type": "mass_loss",
            "locator": interval["locator"],
            "regime": "tga",
            "units": "wt_percent",
            "values": {
                "quantity": "mass_loss_fraction",
                "method_class": "measured_direct",
                "points": [
                    {
                        "T_C": interval["upper_temperature_C"],
                        "mass_loss_wt_pct": interval["mass_loss_wt_pct"],
                        "locator": interval["locator"],
                    }
                ],
            },
        }
    )
    mutated = tmp_path / source.name
    mutated.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    with pytest.raises(AssertionError):
        assert mass_loss_count(mutated) == 6
