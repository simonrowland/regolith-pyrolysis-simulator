import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "populate_reduced_real_cache.py"


def _load_driver():
    spec = importlib.util.spec_from_file_location(
        "populate_reduced_real_cache",
        SCRIPT_PATH,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_profile_recipe_defaults_use_seed_campaigns_and_run_additives():
    driver = _load_driver()
    profile = driver._load_yaml(
        REPO_ROOT / "data" / "optimize_profiles" / "mars_basalt.yaml"
    )

    assert driver._profile_campaigns(profile) == ("C0", "C2B")
    assert driver._profile_additives(profile) == {"C": 30.0}


def test_cli_additive_overrides_profile_additive():
    driver = _load_driver()
    profile = driver._load_yaml(
        REPO_ROOT / "data" / "optimize_profiles" / "mars_basalt.yaml"
    )
    args = driver._parse_args(
        [
            "--profile",
            "data/optimize_profiles/mars_basalt.yaml",
            "--additive",
            "C=31.5",
            "--additive",
            "Na=2.0",
        ]
    )

    additives = driver._feedstock_additives(
        "mars_basalt",
        loaded_profile=profile,
        cli_additives=driver._cli_additives(args.additives),
    )

    assert additives == {"C": 31.5, "Na": 2.0}


def test_other_feedstock_uses_own_profile_additives_not_loaded_profile():
    driver = _load_driver()
    profile = driver._load_yaml(
        REPO_ROOT / "data" / "optimize_profiles" / "mars_basalt.yaml"
    )

    assert (
        driver._feedstock_additives(
            "mars_basalt",
            loaded_profile=profile,
            cli_additives={},
        )
        == driver._profile_additives(profile)
        == {"C": 30.0}
    )
    assert (
        driver._feedstock_additives(
            "lunar_mare_low_ti",
            loaded_profile=profile,
            cli_additives={},
        )
        == {}
    )


def test_start_session_passes_profile_additives_to_load_batch(tmp_path):
    driver = _load_driver()
    profile = driver._load_yaml(
        REPO_ROOT / "data" / "optimize_profiles" / "mars_basalt.yaml"
    )

    driver._start_session(
        feedstock="mars_basalt",
        campaign="C0",
        backend_name="stub",
        mass_kg=1000.0,
        additives_kg=driver._profile_additives(profile),
        store=driver.PT0DeterminismStore(
            "capture",
            db_path=tmp_path / "cache.db",
        ),
        allow_internal_analytical_equilibrium=True,
    )


def test_profile_c0b_campaign_key_is_accepted_by_session(tmp_path):
    driver = _load_driver()

    driver._start_session(
        feedstock="lunar_pkt_kreep_average",
        campaign="C0b_p_cleanup",
        backend_name="stub",
        mass_kg=1000.0,
        additives_kg={},
        store=driver.PT0DeterminismStore(
            "capture",
            db_path=tmp_path / "cache.db",
        ),
        allow_internal_analytical_equilibrium=True,
    )
