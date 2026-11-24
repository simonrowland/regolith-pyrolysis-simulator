import ast
import inspect
from collections import Counter
from functools import partial, wraps
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


class _SignaturePropertyCallable:
    marker: Path | None = None

    def __call__(self, value):
        return value

    @property
    def __signature__(self):
        if self.marker is not None:
            self.marker.write_text("signature inspected", encoding="utf-8")
        return inspect.signature(self.__call__)


class _SignaturePropertyTarget:
    target = _SignaturePropertyCallable()


class _PartialTarget:
    target = partial(lambda value: value, 1)


class _WrappedTarget:
    @wraps(lambda value: value)
    def target(*args, **kwargs):
        return None


class _DynamicClassReceiver:
    marker: Path | None = None

    @property
    def __class__(self):
        if self.marker is not None:
            self.marker.write_text("dynamic class read", encoding="utf-8")
        return type(self)

    def emit(self, record):
        return record


_DYNAMIC_CLASS_RECEIVER = _DynamicClassReceiver()


def test_patch_double_violation_allowlist_only_shrinks() -> None:
    census = scan_tests(Path(__file__).parent)
    assert census.resolved + len(census.skipped) == census.scanned
    assert census.scanned > 0
    assert census.resolved > 0
    assert sum(count for _, count in census.skipped_by_reason) == len(census.skipped)
    print(f"UNRESOLVED-SKIPPED: {len(census.skipped)}")
    for reason, count in census.skipped_by_reason:
        print(f"SKIPPED-{reason.upper()}: {count}")
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
        ("x, **kwargs", "x, /, **kwargs", False),
        ("*, x", "x, /, **kwargs", False),
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
        ("x, **kwargs", "x, /, **kwargs", (), {"x": 1}, False),
        ("*, x", "x, /, **kwargs", (), {"x": 1}, False),
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
    assert census.resolved == 0
    assert census.skipped_by_reason == (("unsafe-type", 1),)
    assert not census.violations


def test_scan_resolves_safe_callable_through_importorskip_alias(tmp_path: Path) -> None:
    (tmp_path / "test_importorskip_safe_alias.py").write_text(
        "import pytest\n"
        "openimcc = pytest.importorskip('collections')\n"
        "def replacement(iterable=()): return None\n"
        "def test_alias(monkeypatch):\n"
        "    monkeypatch.setattr(openimcc.UserDict, 'fromkeys', replacement)\n",
        encoding="utf-8",
    )
    census = scan_tests(tmp_path)
    assert census.scanned == census.resolved == 1
    assert not census.skipped and len(census.violations) == 1


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


def test_scan_resolves_string_target_classmethod_binding(tmp_path: Path) -> None:
    (tmp_path / "test_string_classmethod.py").write_text(
        "from unittest.mock import patch\n"
        "def test_narrow_fromkeys():\n"
        "    patch('collections.UserDict.fromkeys', lambda value=None: None)\n",
        encoding="utf-8",
    )

    census = scan_tests(tmp_path)

    assert census.scanned == 1
    assert census.resolved == 1
    assert not census.skipped
    assert census.violations == (
        "test_string_classmethod.py::test_narrow_fromkeys::'collections.UserDict.fromkeys'",
    )


def test_scan_uses_the_same_classmethod_resolver_for_string_and_object_targets(
    tmp_path: Path,
) -> None:
    (tmp_path / "test_shared_classmethod_resolver.py").write_text(
        "from collections import UserDict\n"
        "from unittest.mock import patch\n"
        "def replacement(iterable=(), value=None): return None\n"
        "def test_string_target():\n"
        "    patch('collections.UserDict.fromkeys', replacement)\n"
        "def test_object_target():\n"
        "    patch.object(UserDict, 'fromkeys', replacement)\n",
        encoding="utf-8",
    )

    census = scan_tests(tmp_path)

    assert census.scanned == 2
    assert census.resolved == 2
    assert not census.skipped
    assert not census.violations


def test_scan_binds_native_classmethod_for_class_and_instance_targets(tmp_path: Path) -> None:
    (tmp_path / "test_native_classmethod.py").write_text(
        "from builtins import dict as Dict\n"
        "from unittest.mock import patch\n"
        "def test_class_receiver():\n"
        "    patch.object(Dict, 'fromkeys', lambda iterable, value=None, /: None)\n"
        "def test_instance_receiver():\n"
        "    mapping = Dict()\n"
        "    patch.object(mapping, 'fromkeys', lambda iterable, value=None, /: None)\n",
        encoding="utf-8",
    )

    census = scan_tests(tmp_path)

    assert census.scanned == 2
    assert census.resolved == 2
    assert not census.skipped
    assert not census.violations


def test_scan_flags_narrow_native_dict_fromkeys_double(tmp_path: Path) -> None:
    (tmp_path / "test_narrow_native_classmethod.py").write_text(
        "from builtins import dict as Dict\n"
        "from unittest.mock import patch\n"
        "def replacement(iterable, /): return None\n"
        "def test_narrow():\n"
        "    patch.object(Dict, 'fromkeys', replacement)\n",
        encoding="utf-8",
    )
    census = scan_tests(tmp_path)
    assert census.scanned == census.resolved == 1
    assert len(census.violations) == 1


def test_scan_skips_wrapped_function_as_decorated(tmp_path: Path) -> None:
    (tmp_path / "test_wrapped_function.py").write_text(
        f"from {__name__} import _WrappedTarget\n"
        "def replacement(value): return value\n"
        "def test_wrapped(monkeypatch):\n"
        "    monkeypatch.setattr(_WrappedTarget, 'target', replacement)\n",
        encoding="utf-8",
    )
    census = scan_tests(tmp_path)
    assert census.scanned == 1 and census.resolved == 0
    assert census.skipped_by_reason == (("decorated", 1),)


def test_scan_never_uses_dynamic_instance_class_for_classification(tmp_path: Path, monkeypatch) -> None:
    marker = tmp_path / "dynamic-class-read"
    monkeypatch.setattr(_DynamicClassReceiver, "marker", marker)
    (tmp_path / "test_static_classification.py").write_text(
        f"from {__name__} import _DYNAMIC_CLASS_RECEIVER\n"
        "def replacement(mode='r', *args, **kwargs): return None\n"
        "def test_patch(monkeypatch):\n"
        "    monkeypatch.setattr(_DYNAMIC_CLASS_RECEIVER, 'emit', replacement)\n",
        encoding="utf-8",
    )
    census = scan_tests(tmp_path)
    assert census.scanned == census.resolved == 1
    assert not census.skipped and not census.violations
    assert not marker.exists()


def test_signature_metadata_mutation_probe_exposes_property_execution(tmp_path: Path, monkeypatch) -> None:
    import tools.test_double_signatures as checker

    marker = tmp_path / "signature-property-mutant-ran"
    monkeypatch.setattr(_SignaturePropertyCallable, "marker", marker)
    monkeypatch.setattr(checker, "_safe_signature_type", lambda value: True)
    (tmp_path / "test_signature_metadata_mutant.py").write_text(
        f"from {__name__} import _SignaturePropertyTarget\n"
        "def replacement(value): return value\n"
        "def test_metadata(monkeypatch):\n"
        "    monkeypatch.setattr(_SignaturePropertyTarget, 'target', replacement)\n",
        encoding="utf-8",
    )
    census = scan_tests(tmp_path)
    assert census.resolved == 1
    assert marker.exists()


def test_wrapped_function_mutation_probe_exposes_false_clean_resolution(tmp_path: Path, monkeypatch) -> None:
    import tools.test_double_signatures as checker

    monkeypatch.setattr(checker, "_safe_signature_type", lambda value: True)
    (tmp_path / "test_wrapped_function_mutant.py").write_text(
        f"from {__name__} import _WrappedTarget\n"
        "def replacement(*args, **kwargs): return None\n"
        "def test_wrapped(monkeypatch):\n"
        "    monkeypatch.setattr(_WrappedTarget, 'target', replacement)\n",
        encoding="utf-8",
    )
    census = scan_tests(tmp_path)
    assert census.scanned == census.resolved == 1
    assert not census.skipped and not census.violations


def test_scan_skips_local_method_with_an_unknown_decorator(tmp_path: Path) -> None:
    (tmp_path / "test_decorated_classmethod.py").write_text(
        "from collections import UserDict\n"
        "def narrow(function):\n"
        "    return lambda cls, value=None: None\n"
        "class Double:\n"
        "    @classmethod\n"
        "    @narrow\n"
        "    def fromkeys(cls, iterable=(), value=None): return None\n"
        "def test_decorated_fromkeys(monkeypatch):\n"
        "    mapping = UserDict()\n"
        "    double = Double()\n"
        "    monkeypatch.setattr(mapping, 'fromkeys', double.fromkeys)\n",
        encoding="utf-8",
    )

    census = scan_tests(tmp_path)

    assert census.scanned == 1
    assert census.resolved == 0
    assert census.skipped_by_reason == (("decorated", 1),)
    assert "SKIPPED-DECORATED" in census.skipped[0]
    assert not census.violations


def test_scan_does_not_evaluate_callable_signature_property(tmp_path: Path) -> None:
    marker = tmp_path / "signature-property-ran"
    _SignaturePropertyCallable.marker = marker
    (tmp_path / "test_signature_property.py").write_text(
        f"from {__name__} import _SignaturePropertyTarget\n"
        "def replacement(value): return value\n"
        "def test_dynamic_signature(monkeypatch):\n"
        "    receiver = _SignaturePropertyTarget()\n"
        "    monkeypatch.setattr(receiver, 'target', replacement)\n",
        encoding="utf-8",
    )

    census = scan_tests(tmp_path)
    _SignaturePropertyCallable.marker = None

    assert census.scanned == 1
    assert census.resolved == 0
    assert census.skipped_by_reason == (("unsafe-type", 1),)
    assert not marker.exists()
    assert not census.violations


def test_scan_skips_functools_partial_wrappers(tmp_path: Path) -> None:
    (tmp_path / "test_partial_wrapper.py").write_text(
        f"from {__name__} import _PartialTarget\n"
        "def test_partial(monkeypatch):\n"
        "    receiver = _PartialTarget()\n"
        "    monkeypatch.setattr(receiver, 'target', lambda: None)\n",
        encoding="utf-8",
    )

    census = scan_tests(tmp_path)

    assert census.scanned == 1
    assert census.resolved == 0
    assert census.skipped_by_reason == (("unsafe-type", 1),)
    assert not census.violations


def test_scan_resolves_local_descriptors_without_running_constructors(tmp_path: Path) -> None:
    side_effect = tmp_path / "constructor.log"
    (tmp_path / "test_local_descriptors.py").write_text(
        "from collections import UserDict\n"
        "from pathlib import Path\n"
        "class LocalDouble:\n"
        "    @classmethod\n"
        "    def fromkeys(cls, iterable=(), value=None): return None\n"
        "    def emit(self, record): return None\n"
        "    def __init__(self):\n"
        f"        Path({str(side_effect)!r}).write_text('ran', encoding='utf-8')\n"
        "def test_local_classmethod(monkeypatch):\n"
        "    mapping = UserDict()\n"
        "    double = LocalDouble()\n"
        "    monkeypatch.setattr(mapping, 'fromkeys', double.fromkeys)\n"
        "def test_local_method(monkeypatch):\n"
        "    receiver = LocalDouble()\n"
        "    monkeypatch.setattr(receiver, 'emit', receiver.emit)\n"
        "def test_constructor_side_effect(monkeypatch):\n"
        "    receiver = LocalDouble()\n"
        "    monkeypatch.setattr(receiver, 'emit', lambda record: None)\n",
        encoding="utf-8",
    )

    census = scan_tests(tmp_path)

    assert census.scanned == 3
    assert census.resolved == 3
    assert not census.skipped
    assert not census.violations
    assert not side_effect.exists()
