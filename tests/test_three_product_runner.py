"""E6c: tests for the three-product runner CLI.

Pins:
1. ``run()`` programmatic entry point returns the expanded
   classification dict while preserving the 5 canonical buckets.
2. ``main()`` CLI entry produces markdown OR json output to file
   per ``--format`` arg.
3. ``--hours 0`` produces a well-formed report on a never-stepped
   sim (the unstepped baseline).
4. JSON output is round-trippable + carries metadata.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from simulator.three_product_runner import (
    SUPPORTED_FORMATS,
    main,
    run,
)


def test_run_returns_classification_dict():
    """The programmatic ``run()`` entry point returns the same
    expanded dict shape that ``classify_products`` produces."""
    result = run(
        feedstock_id="lunar_mare_low_ti",
        campaign="C2A",
        hours=2,
    )
    canonical_buckets = {
        'metals_plus_O2',
        'pure_silica_glass',
        'industrial_mixed_glass',
        'refractory_ceramic_rump',
        'unclassified',
    }
    additive_buckets = {
        'ingots_metals',
        'oxygen',
        'glass',
        'captured_volatiles',
    }
    assert canonical_buckets <= result.keys()
    assert set(result.keys()) == canonical_buckets | additive_buckets


def test_run_with_zero_hours_returns_well_formed_baseline():
    """``--hours 0`` builds the sim but doesn't step it. The
    classifier still returns a valid canonical dict (likely with empty
    metals_plus_O2 + zero stage_3 capture)."""
    result = run(
        feedstock_id="lunar_mare_low_ti",
        campaign="C2A",
        hours=0,
    )
    assert result['metals_plus_O2']['class_total_kg'] >= 0.0
    assert result['pure_silica_glass']['class_total_kg'] >= 0.0


def test_cli_writes_markdown_report_to_file(tmp_path):
    """End-to-end: invoke ``main()`` directly with markdown output
    path; verify the report file exists + carries the expected
    header structure."""
    output = tmp_path / "report.md"
    rc = main([
        "--feedstock", "lunar_mare_low_ti",
        "--campaign", "C2A",
        "--hours", "2",
        "--output", str(output),
        "--format", "markdown",
    ])
    assert rc == 0
    assert output.exists()
    body = output.read_text()
    assert "Three-Product-Class Report" in body
    assert "Metals + O₂" in body
    assert "Pure silica glass" in body
    assert "Refractory ceramic rump" in body


def test_cli_writes_json_report_to_file(tmp_path):
    """JSON output is round-trippable via json.loads and carries
    the metadata header (feedstock_id, campaign)."""
    output = tmp_path / "report.json"
    rc = main([
        "--feedstock", "lunar_mare_low_ti",
        "--campaign", "C2A",
        "--hours", "2",
        "--output", str(output),
        "--format", "json",
    ])
    assert rc == 0
    assert output.exists()
    payload = json.loads(output.read_text())
    assert payload["feedstock_id"] == "lunar_mare_low_ti"
    assert payload["campaign"] == "C2A"
    assert "classification" in payload
    assert "metals_plus_O2" in payload["classification"]


def test_supported_formats_only_includes_markdown_and_json():
    """Pin the documented format options so a future addition
    surfaces here as a deliberate update, not a silent drift."""
    assert SUPPORTED_FORMATS == ('markdown', 'json')


def test_cli_via_subprocess_invocation(tmp_path):
    """End-to-end via the canonical ``python -m`` invocation
    pattern. Confirms the module is importable + the entry point
    works from a clean subprocess."""
    output = tmp_path / "report.md"
    result = subprocess.run(
        [
            sys.executable,
            "-m", "simulator.three_product_runner",
            "--feedstock", "lunar_mare_low_ti",
            "--campaign", "C2A",
            "--hours", "1",
            "--output", str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"runner failed: stdout={result.stdout} stderr={result.stderr}"
    )
    assert output.exists()
    body = output.read_text()
    assert "lunar_mare_low_ti" in body


def test_cli_invalid_format_raises():
    """Argparse rejects unsupported --format values; the runner
    returns a non-zero exit code."""
    with pytest.raises(SystemExit) as exc_info:
        main([
            "--feedstock", "lunar_mare_low_ti",
            "--campaign", "C2A",
            "--hours", "1",
            "--format", "yaml",   # not supported
        ])
    assert exc_info.value.code != 0


def test_run_default_early_tap_mode_zeroes_mixed_glass():
    """The runner's default ``run()`` call (no ``early_tap_mode``)
    must zero the mixed-glass bucket — even a mid-C2A run with
    1000 kg in ``process.cleaned_melt`` is NOT product output
    (evening-4commits review P2 #2)."""
    result = run(
        feedstock_id="lunar_mare_low_ti",
        campaign="C2A",
        hours=4,  # mid-C2A; cleaned_melt is still in the crucible
    )
    assert (
        result['industrial_mixed_glass']['mixed_melt_residual_kg']
        == 0.0
    )
    assert (
        result['industrial_mixed_glass']['early_tap_mode'] is False
    )


def test_run_early_tap_mode_surfaces_cleaned_melt(tmp_path):
    """With ``--early-tap``, the runner surfaces the
    ``cleaned_melt`` residual as the mixed-glass product. End-to-end
    CLI invocation."""
    output = tmp_path / "report.json"
    import json
    rc = main([
        "--feedstock", "lunar_mare_low_ti",
        "--campaign", "C2A",
        "--hours", "4",
        "--output", str(output),
        "--format", "json",
        "--early-tap",
    ])
    assert rc == 0
    payload = json.loads(output.read_text())
    mixed = payload["classification"]["industrial_mixed_glass"]
    assert mixed["early_tap_mode"] is True
    # cleaned_melt should be non-zero at this point in a real run.
    assert mixed["mixed_melt_residual_kg"] > 0.0


def test_run_with_missing_data_dir_falls_back_to_default():
    """When ``data_dir`` is omitted, the runner uses the project's
    default ``data/`` directory automatically. Pin this so a future
    refactor doesn't accidentally require the kwarg."""
    result = run(
        feedstock_id="lunar_mare_low_ti",
        campaign="C2A",
        hours=1,
    )
    assert isinstance(result, dict)
