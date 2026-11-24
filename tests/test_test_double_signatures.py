import ast
import inspect
import sys
from collections import Counter
from functools import partial, wraps
from pathlib import Path
from types import ModuleType

import pytest

from tools import test_double_signatures as checker
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


class _RenamedBoundFixture:
    def sleep(receiver, seconds=0):
        return seconds


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
    assert census.skipped_by_reason == (("nonstandard-metadata", 1),)


def test_scan_skips_function_with_custom_kwdefaults_without_running_hook(
    tmp_path: Path, monkeypatch
) -> None:
    reads: list[str] = []

    class Defaults(dict):
        def get(self, key, default=None):
            reads.append(key)
            return super().get(key, default)

    def emit(*, value=1):
        return value

    emit.__kwdefaults__ = Defaults(value=1)
    module = ModuleType("test_double_signature_custom_defaults")
    module.emit = emit
    monkeypatch.setitem(sys.modules, module.__name__, module)
    (tmp_path / "test_custom_defaults.py").write_text(
        "import test_double_signature_custom_defaults as api\n"
        "def test_patch(monkeypatch):\n"
        "    monkeypatch.setattr(api, 'emit', lambda *, value=1: None)\n",
        encoding="utf-8",
    )

    census = scan_tests(tmp_path)

    assert census.scanned == 1 and census.resolved == 0
    assert census.skipped_by_reason == (("nonstandard-metadata", 1),)
    assert not reads


def test_scan_skips_custom_metaclass_without_reading_class_dict(
    tmp_path: Path, monkeypatch
) -> None:
    reads: list[str] = []

    class Meta(type):
        def __getattribute__(cls, name):
            if name == "__dict__":
                reads.append(name)
            return super().__getattribute__(name)

    class Receiver(metaclass=Meta):
        def emit(self, value):
            return value

    module = ModuleType("test_double_signature_custom_metaclass")
    module.Receiver = Receiver
    monkeypatch.setitem(sys.modules, module.__name__, module)
    (tmp_path / "test_custom_metaclass.py").write_text(
        "from test_double_signature_custom_metaclass import Receiver\n"
        "def test_patch(monkeypatch):\n"
        "    monkeypatch.setattr(Receiver(), 'emit', lambda value: None)\n",
        encoding="utf-8",
    )

    census = scan_tests(tmp_path)

    assert census.scanned == 1 and census.resolved == 0
    assert census.skipped_by_reason == (("custom-metaclass", 1),)
    assert not reads


def test_string_and_object_targets_resolve_exported_instance_identically(
    tmp_path: Path, monkeypatch
) -> None:
    class Receiver:
        def emit(self, record):
            return record

    module = ModuleType("test_double_signature_exported_instance")
    module.receiver = Receiver()
    monkeypatch.setitem(sys.modules, module.__name__, module)
    (tmp_path / "test_instance_targets.py").write_text(
        "from test_double_signature_exported_instance import receiver\n"
        "from unittest.mock import patch\n"
        "def test_object_target():\n"
        "    patch.object(receiver, 'emit', lambda: None)\n"
        "def test_string_target():\n"
        "    patch('test_double_signature_exported_instance.receiver.emit', lambda: None)\n",
        encoding="utf-8",
    )

    census = scan_tests(tmp_path)

    assert census.scanned == census.resolved == 2
    assert not census.skipped
    assert len(census.violations) == 2
    assert {item.split("::")[1] for item in census.violations} == {
        "test_object_target",
        "test_string_target",
    }

    resolver = checker._receiver_and_attribute_for_path
    with monkeypatch.context() as mutation:
        def drop_exported_instance(dotted, modules, objects):
            if ".receiver.emit" in dotted:
                return None, "emit"
            return resolver(dotted, modules, objects)

        mutation.setattr(
            checker, "_receiver_and_attribute_for_path", drop_exported_instance
        )
        changed = scan_tests(tmp_path)
        assert changed.resolved == 1 and len(changed.skipped) == 1
        assert len(changed.violations) == 1


def test_scan_skips_named_classmethod_replacement(tmp_path: Path) -> None:
    (tmp_path / "test_named_classmethod_replacement.py").write_text(
        "from collections import UserDict\n"
        "@classmethod\n"
        "def replacement(value): return value\n"
        "def test_patch(monkeypatch):\n"
        "    monkeypatch.setattr(UserDict, 'fromkeys', replacement)\n",
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


def test_mutation_probe_compatibility_rules_remain_active(monkeypatch) -> None:
    def signature(arguments: str) -> inspect.Signature:
        node = ast.parse(f"lambda {arguments}: None", mode="eval").body
        result = checker._replacement_signature(node)
        assert result is not None
        return result

    production = signature("first, second, /")
    narrow = signature("first, /")
    assert not _compatible(production, narrow)
    with monkeypatch.context() as mutation:
        mutation.setattr(checker, "_positional_parameters", lambda _signature: [])
        assert _compatible(production, narrow)

    production = signature("value=42")
    renamed = signature("other=42")
    assert not _compatible(production, renamed)
    accepts_keyword = checker._keyword_accepted
    with monkeypatch.context() as mutation:
        mutation.setattr(
            checker,
            "_keyword_accepted",
            lambda candidate, name: (
                candidate is renamed and name == "value"
            ) or accepts_keyword(candidate, name),
        )
        assert _compatible(production, renamed)

    production = signature("target=0, **kwargs")
    required = signature("target=0, *, root, **kwargs")
    assert not _compatible(production, required)
    with monkeypatch.context() as mutation:
        mutation.setattr(checker, "_required_parameters_compatible", lambda *_args: True)
        assert _compatible(production, required)

    positional_production = signature("x, **kwargs")
    positional_only_double = signature("x, /, **kwargs")
    assert not _compatible(positional_production, positional_only_double)
    with monkeypatch.context() as mutation:
        mutation.setattr(checker, "_required_parameters_compatible", lambda *_args: True)
        assert _compatible(positional_production, positional_only_double)

    production = signature("*args, **kwargs")
    collision = signature("value=None, *args, **kwargs")
    assert not _compatible(production, collision)
    with monkeypatch.context() as mutation:
        mutation.setattr(checker, "_has_positional_keyword_collision", lambda *_args: False)
        assert _compatible(production, collision)

    checker_source = Path(checker.__file__).read_text(encoding="utf-8")
    assert checker_source.count("def _keyword_accepted(") == 1
    assert "_keyword_is_production_accepted" not in checker_source
    assert checker_source.count("def _required_positional_only_prefix(") == 1
    assert checker_source.count("_required_positional_only_prefix(production)") == 2


def test_mutation_probe_descriptor_binding_and_local_resolution(
    tmp_path: Path, monkeypatch
) -> None:
    source = (
        "from collections import UserDict\n"
        "from builtins import dict as Dict\n"
        "from unittest.mock import patch\n"
        "def replacement(iterable=(), value=None): return None\n"
        "def native_replacement(iterable, value=None, /): return None\n"
        "class LocalDouble:\n"
        "    @classmethod\n"
        "    def fromkeys(cls, iterable=(), value=None): return None\n"
        "def test_native():\n"
        "    patch.object(UserDict, 'fromkeys', replacement)\n"
        "def test_string_classmethod():\n"
        "    patch('collections.UserDict.fromkeys', replacement)\n"
        "def test_native_classmethod():\n"
        "    patch.object(Dict, 'fromkeys', native_replacement)\n"
        "def test_local(monkeypatch):\n"
        "    mapping = UserDict()\n"
        "    double = LocalDouble()\n"
        "    monkeypatch.setattr(mapping, 'fromkeys', double.fromkeys)\n"
    )
    fixture = tmp_path / "test_descriptor_mutation.py"
    fixture.write_text(source, encoding="utf-8")
    baseline = scan_tests(tmp_path)
    assert baseline.resolved == 4 and not baseline.violations

    bind = checker._bind_descriptor_signature
    with monkeypatch.context() as mutation:
        mutation.setattr(
            checker,
            "_bind_descriptor_signature",
            lambda result, *, drop_first: bind(result, drop_first=False),
        )
        changed = scan_tests(tmp_path)
        assert changed.resolved == 4 and len(changed.violations) == 3

    local = checker._local_class_attribute_signature
    with monkeypatch.context() as mutation:
        mutation.setattr(
            checker,
            "_local_class_attribute_signature",
            lambda *_args: checker._SignatureResolution(None, "unresolvable", "mutated"),
        )
        changed = scan_tests(tmp_path)
        assert changed.resolved == 3 and len(changed.skipped) == 1
    assert local is checker._local_class_attribute_signature


def test_mutation_probe_static_class_and_bound_method_binding(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / "test_binding_mutation.py").write_text(
        f"from {__name__} import _DescriptorFixture, _RenamedBoundFixture\n"
        "from unittest.mock import patch\n"
        "def replacement(value): return value\n"
        "def test_static(monkeypatch):\n"
        "    receiver = _DescriptorFixture()\n"
        "    monkeypatch.setattr(receiver, 'static_call', replacement)\n"
        "def test_classed():\n"
        "    patch.object(_DescriptorFixture, 'class_call', replacement)\n"
        "def test_bound(monkeypatch):\n"
        "    receiver = _DescriptorFixture()\n"
        "    double = _RenamedBoundFixture()\n"
        "    monkeypatch.setattr(receiver, 'sleep', double.sleep)\n",
        encoding="utf-8",
    )
    baseline = scan_tests(tmp_path)
    assert baseline.resolved == 3 and not baseline.violations

    class_attribute = checker._class_attribute_signature
    with monkeypatch.context() as mutation:
        def bind_static(class_object, name, is_instance):
            descriptor = inspect.getattr_static(class_object, name)
            if type(descriptor) is staticmethod:
                return checker._bind_descriptor_signature(
                    checker._signature_of(descriptor.__func__), drop_first=True
                )
            return class_attribute(class_object, name, is_instance)

        mutation.setattr(checker, "_class_attribute_signature", bind_static)
        changed = scan_tests(tmp_path)
        assert changed.resolved == 3 and len(changed.violations) == 1

    bind = checker._bind_descriptor_signature
    with monkeypatch.context() as mutation:
        mutation.setattr(
            checker,
            "_bind_descriptor_signature",
            lambda result, *, drop_first: bind(result, drop_first=False),
        )
        changed = scan_tests(tmp_path)
        assert changed.resolved == 3 and len(changed.violations) == 2


def test_mutation_probe_opaque_builtin_remains_skipped(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "test_opaque_mutation.py").write_text(
        "import time\n"
        "def replacement(seconds): return None\n"
        "def test_sleep(monkeypatch):\n"
        "    monkeypatch.setattr(time, 'sleep', replacement)\n",
        encoding="utf-8",
    )
    baseline = scan_tests(tmp_path)
    assert baseline.resolved == 0 and len(baseline.skipped) == 1

    signature_of = checker._signature_of
    with monkeypatch.context() as mutation:
        def pretend_inspectable(value):
            if value is __import__("time").sleep:
                return checker._SignatureResolution(inspect.Signature())
            return signature_of(value)

        mutation.setattr(checker, "_signature_of", pretend_inspectable)
        changed = scan_tests(tmp_path)
        assert changed.resolved == 1


def test_mutation_probe_named_and_class_attribute_decorators_differ(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / "test_decorator_mutation.py").write_text(
        "from collections import UserDict\n"
        "@classmethod\n"
        "def replacement(value): return value\n"
        "class LocalDouble:\n"
        "    @classmethod\n"
        "    def fromkeys(cls, iterable=(), value=None): return None\n"
        "def test_named(monkeypatch):\n"
        "    monkeypatch.setattr(UserDict, 'fromkeys', replacement)\n"
        "def test_class_attribute(monkeypatch):\n"
        "    mapping = UserDict()\n"
        "    double = LocalDouble()\n"
        "    monkeypatch.setattr(mapping, 'fromkeys', double.fromkeys)\n",
        encoding="utf-8",
    )
    baseline = scan_tests(tmp_path)
    assert baseline.resolved == 1 and baseline.skipped_by_reason == (("decorated", 1),)

    replacement_resolver = checker._replacement_signature_for_site
    with monkeypatch.context() as mutation:
        def ignore_named_decorators(node, site, tree, parents, modules, objects):
            definition = checker._callable_definition(node, site.call, parents, tree)
            if isinstance(definition, (ast.FunctionDef, ast.AsyncFunctionDef)):
                signature = checker._replacement_signature(definition)
                return checker._SignatureResolution(signature)
            return replacement_resolver(node, site, tree, parents, modules, objects)

        mutation.setattr(
            checker, "_replacement_signature_for_site", ignore_named_decorators
        )
        changed = scan_tests(tmp_path)
        assert changed.resolved == 2 and not changed.skipped


def test_mutation_probe_importorskip_alias_is_preserved(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "test_alias_mutation.py").write_text(
        "import pytest\n"
        "module_alias = pytest.importorskip('collections')\n"
        "def replacement(iterable=(), /, **kwargs): return None\n"
        "def test_patch(monkeypatch):\n"
        "    monkeypatch.setattr(module_alias, 'Counter', replacement)\n",
        encoding="utf-8",
    )
    baseline = scan_tests(tmp_path)
    assert baseline.skipped_by_reason == (("unsafe-type", 1),)

    aliases = checker._aliases
    with monkeypatch.context() as mutation:
        def without_importorskip(tree):
            modules, objects = aliases(tree)
            modules.pop("module_alias", None)
            return modules, objects

        mutation.setattr(checker, "_aliases", without_importorskip)
        changed = scan_tests(tmp_path)
        assert changed.skipped_by_reason == (("unresolvable", 1),)


def test_mutation_probe_static_constructor_scan_has_no_side_effect(
    tmp_path: Path, monkeypatch
) -> None:
    marker = tmp_path / "constructor-ran"

    class SideEffect:
        def __init__(self):
            marker.write_text("ran", encoding="utf-8")

        def emit(self, record):
            return record

    module = ModuleType("test_double_signature_side_effect")
    module.SideEffect = SideEffect
    monkeypatch.setitem(sys.modules, module.__name__, module)
    (tmp_path / "test_constructor_mutation.py").write_text(
        "from test_double_signature_side_effect import SideEffect\n"
        "def test_patch(monkeypatch):\n"
        "    receiver = SideEffect()\n"
        "    monkeypatch.setattr(receiver, 'emit', lambda record: record)\n",
        encoding="utf-8",
    )
    assert scan_tests(tmp_path).resolved == 1
    assert not marker.exists()

    receiver = checker._receiver
    with monkeypatch.context() as mutation:
        def instantiate_constructor(node, at, parents, modules, objects):
            constructor_call = (
                checker._assigned_constructor_call(node.id, at, parents)
                if isinstance(node, ast.Name)
                else node if isinstance(node, ast.Call) else None
            )
            if (
                constructor_call is not None
                and isinstance(constructor_call.func, ast.Name)
                and constructor_call.func.id == "SideEffect"
            ):
                return checker._Receiver(SideEffect(), True)
            return receiver(node, at, parents, modules, objects)

        mutation.setattr(checker, "_receiver", instantiate_constructor)
        changed = scan_tests(tmp_path)
        assert changed.resolved == 1
        assert marker.exists()
