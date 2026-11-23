from pathlib import Path
import ast
import inspect

import pytest

from tools.test_double_signatures import _compatible, scan_tests


def test_all_patch_doubles_accept_production_signatures() -> None:
    census = scan_tests(Path(__file__).parent)
    assert census.resolved + len(census.skipped) == census.scanned
    assert census.scanned > 0
    assert census.resolved > 0
    assert not census.violations, "\n".join(census.violations)


@pytest.mark.parametrize(
    ("production", "double", "compatible"),
    [
        ("value=42", "other=42", False),
        ("value=42", "value", False),
        ("value=42", "value=42, **kwargs", True),
        ("first, second=2, *, flag=False", "first, second=2, *, flag=False", True),
    ],
)
def test_compatibility_covers_keyword_names_and_optional_omissions(
    production: str, double: str, compatible: bool
) -> None:
    production_node = ast.parse(f"lambda {production}: None", mode="eval").body
    double_node = ast.parse(f"lambda {double}: None", mode="eval").body
    from tools.test_double_signatures import _replacement_signature

    production_signature = _replacement_signature(production_node)
    double_signature = _replacement_signature(double_node)
    assert production_signature is not None
    assert double_signature is not None
    assert _compatible(production_signature, double_signature) is compatible


def test_scan_reports_unresolvable_discovered_candidate(tmp_path: Path) -> None:
    (tmp_path / "test_candidate.py").write_text(
        'from unittest.mock import patch\n'
        'def replacement(value): return value\n'
        'def test_candidate():\n'
        '    patch("not_a_real_module.call", replacement)\n',
        encoding="utf-8",
    )
    census = scan_tests(tmp_path)
    assert census.scanned == 1
    assert census.resolved == 0
    assert len(census.skipped) == 1
    assert "SKIPPED-UNRESOLVABLE" in census.skipped[0]


def test_scan_binds_methods_on_directly_constructed_receivers(tmp_path: Path) -> None:
    (tmp_path / "test_direct_method.py").write_text(
        "from pathlib import Path\n"
        "def replacement(mode='r', *args, **kwargs): return None\n"
        "def test_wide(monkeypatch):\n"
        "    backend = Path('.')\n"
        "    monkeypatch.setattr(backend, 'open', replacement)\n",
        encoding="utf-8",
    )
    (tmp_path / "test_narrow_method.py").write_text(
        "from pathlib import Path\n"
        "def replacement(other='r'): return None\n"
        "def test_narrow(monkeypatch):\n"
        "    backend = Path('.')\n"
        "    monkeypatch.setattr(backend, 'open', replacement)\n",
        encoding="utf-8",
    )
    census = scan_tests(tmp_path)
    assert census.scanned == 2
    assert census.resolved == 2
    assert len(census.violations) == 1
    assert "test_narrow_method.py" in census.violations[0]


def test_scan_resolves_a_bound_method_double(tmp_path: Path) -> None:
    (tmp_path / "test_bound_method.py").write_text(
        "import time\n"
        "class StepwiseDriver:\n"
        "    def sleep(self, seconds=0): return None\n"
        "driver = StepwiseDriver()\n"
        "def test_bound_sleep(monkeypatch):\n"
        "    monkeypatch.setattr(time, 'sleep', driver.sleep)\n",
        encoding="utf-8",
    )
    census = scan_tests(tmp_path)
    assert census.scanned == 1
    assert census.resolved == 1
    assert not census.skipped
    assert not census.violations
