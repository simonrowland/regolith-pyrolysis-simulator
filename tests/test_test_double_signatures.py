import ast
from collections import Counter
from pathlib import Path

import pytest

from tools.test_double_signatures import _compatible, scan_tests


ALLOWLIST = Path(__file__).parents[1] / "tools" / "test_double_signature_allowlist.txt"


class _DescriptorFixture:
    @staticmethod
    def static_call(value):
        return value

    @classmethod
    def class_call(cls, value):
        return cls, value

    def sleep(self, seconds=0):
        return seconds


class _NarrowBoundFixture:
    def sleep(self):
        return None


def test_patch_double_violation_allowlist_only_shrinks() -> None:
    census = scan_tests(Path(__file__).parent)
    assert census.resolved + len(census.skipped) == census.scanned
    assert census.scanned > 0
    assert census.resolved > 0
    print(f"UNRESOLVED-SKIPPED: {len(census.skipped)}")
    allowed = Counter(
        line.strip()
        for line in ALLOWLIST.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    actual = Counter(census.violations)
    assert actual == allowed, (
        f"unlisted violation occurrences: {sorted((actual - allowed).items())}\n"
        f"stale allowlist occurrences: {sorted((allowed - actual).items())}\n"
        f"unresolved skipped: {len(census.skipped)}"
    )


@pytest.mark.parametrize(
    ("production", "double", "compatible"),
    [
        ("value=42", "other=42", False),
        ("value=42", "value", False),
        ("value=42", "value=42, **kwargs", True),
        ("first, second=2, *, flag=False", "first, second=2, *, flag=False", True),
        ("*args, **kwargs", "value=None, *args, **kwargs", False),
        ("target, *args, **kwargs", "target, *args, **kwargs", True),
        ("target, *args, **kwargs", "target, *, root, **kwargs", False),
        ("first, second=2", "first, *args, **kwargs", True),
        ("a=1, **kwargs", "root=None, /, value=None, **kwargs", True),
        ("x=None, **kwargs", "y=None, **kwargs", False),
        ("x, /, *args, y", "x, y=None, *args, **kwargs", False),
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


@pytest.mark.parametrize(
    ("production", "double", "args", "kwargs", "compatible"),
    [
        ("*args, **kwargs", "value=None, *args, **kwargs", (1,), {"value": 2}, False),
        ("x=None, **kwargs", "y=None, **kwargs", (1,), {"y": 2}, False),
        ("x, /, *args, y", "x, y=None, *args, **kwargs", (1, 2), {"y": 3}, False),
        ("target, *args, **kwargs", "target, *args, **kwargs", (1, 2), {"other": 3}, True),
        ("a=1, **kwargs", "root=None, /, value=None, **kwargs", (1,), {"value": 2}, True),
        ("x, /, *args, **kwargs", "x, *args, **kwargs", (1, 2), {"x": 3}, False),
    ],
)
def test_parameter_comparison_matches_python_call_binding(
    production: str,
    double: str,
    args: tuple[object, ...],
    kwargs: dict[str, object],
    compatible: bool,
) -> None:
    from tools.test_double_signatures import _replacement_signature

    production_node = ast.parse(f"lambda {production}: None", mode="eval").body
    double_node = ast.parse(f"lambda {double}: None", mode="eval").body
    production_signature = _replacement_signature(production_node)
    double_signature = _replacement_signature(double_node)
    assert production_signature is not None and double_signature is not None
    production_signature.bind(*args, **kwargs)
    if compatible:
        double_signature.bind(*args, **kwargs)
    else:
        with pytest.raises(TypeError):
            double_signature.bind(*args, **kwargs)
    assert _compatible(production_signature, double_signature) is compatible


def test_required_root_parameter_is_rejected() -> None:
    from tools.test_double_signatures import _replacement_signature

    production = _replacement_signature(
        ast.parse("lambda target=0, **kwargs: None", mode="eval").body
    )
    double = _replacement_signature(
        ast.parse("lambda target=0, *, root, **kwargs: None", mode="eval").body
    )
    assert production is not None and double is not None
    assert not _compatible(production, double)


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


def test_scan_preserves_pytest_importorskip_module_alias(tmp_path: Path) -> None:
    (tmp_path / "test_importorskip_alias.py").write_text(
        "import pytest\n"
        "openimcc = pytest.importorskip('collections')\n"
        "def replacement(iterable=(), /, **kwargs): return None\n"
        "def test_alias(monkeypatch):\n"
        "    monkeypatch.setattr(openimcc, 'Counter', replacement)\n",
        encoding="utf-8",
    )
    census = scan_tests(tmp_path)
    assert census.scanned == 1
    assert census.resolved == 1
    assert not census.skipped
    assert not census.violations


def test_scan_resolves_a_bound_method_double(tmp_path: Path) -> None:
    (tmp_path / "test_bound_method.py").write_text(
        f"from {__name__} import _DescriptorFixture, _NarrowBoundFixture\n"
        "def test_bound_sleep(monkeypatch):\n"
        "    driver = _DescriptorFixture()\n"
        "    monkeypatch.setattr(driver, 'sleep', driver.sleep)\n"
        "def test_narrow_bound_sleep(monkeypatch):\n"
        "    driver = _DescriptorFixture()\n"
        "    narrow = _NarrowBoundFixture()\n"
        "    monkeypatch.setattr(driver, 'sleep', narrow.sleep)\n",
        encoding="utf-8",
    )
    census = scan_tests(tmp_path)
    assert census.scanned == 2
    assert census.resolved == 2
    assert not census.skipped
    assert len(census.violations) == 1
    assert "test_narrow_bound_sleep" in census.violations[0]


def test_scan_reads_method_descriptors_as_accessed_on_receiver(tmp_path: Path) -> None:
    (tmp_path / "test_descriptors.py").write_text(
        f"from {__name__} import _DescriptorFixture\n"
        "from random import Random\n"
        "def replacement(value): return value\n"
        "def no_args(): return None\n"
        "def test_static(monkeypatch):\n"
        "    receiver = _DescriptorFixture()\n"
        "    monkeypatch.setattr(receiver, 'static_call', replacement)\n"
        "def test_class(monkeypatch):\n"
        "    receiver = _DescriptorFixture()\n"
        "    monkeypatch.setattr(receiver, 'class_call', replacement)\n"
        "def test_native(monkeypatch):\n"
        "    receiver = Random()\n"
        "    monkeypatch.setattr(receiver, 'random', no_args)\n",
        encoding="utf-8",
    )
    census = scan_tests(tmp_path)
    assert census.scanned == 3
    assert census.resolved == 3
    assert not census.skipped
    assert not census.violations


def test_builtin_without_signature_is_counted_as_skipped(tmp_path: Path) -> None:
    (tmp_path / "test_opaque_builtin.py").write_text(
        "import time\n"
        "def replacement(seconds): return None\n"
        "def test_sleep(monkeypatch):\n"
        "    monkeypatch.setattr(time, 'sleep', replacement)\n",
        encoding="utf-8",
    )
    census = scan_tests(tmp_path)
    assert census.scanned == 1
    assert census.resolved == 0
    assert len(census.skipped) == 1
    assert not census.violations


def test_scan_keeps_bound_classmethod_parameters(tmp_path: Path) -> None:
    (tmp_path / "test_bound_classmethod.py").write_text(
        "from collections import UserDict\n"
        "def replacement(value=None): return None\n"
        "def test_bound_fromkeys(monkeypatch):\n"
        "    mapping = UserDict()\n"
        "    monkeypatch.setattr(mapping, 'fromkeys', replacement)\n",
        encoding="utf-8",
    )
    census = scan_tests(tmp_path)
    assert census.scanned == 1
    assert census.resolved == 1
    assert len(census.violations) == 1
    assert census.violations[0].startswith(
        "test_bound_classmethod.py::test_bound_fromkeys::"
    )
