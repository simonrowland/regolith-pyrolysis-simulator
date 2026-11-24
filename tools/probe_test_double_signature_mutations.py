#!/usr/bin/env python3
"""Run checker-level mutations for the signature compatibility rules."""

from __future__ import annotations

import ast
import inspect
import sys
import tempfile
import textwrap
import types
from pathlib import Path

from tools import test_double_signatures as checker


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


class _SideEffectFixture:
    output_path: Path

    def __init__(self):
        self.output_path.write_text("constructed", encoding="utf-8")

    def emit(self, record):
        return record


class _SignaturePropertyCallable:
    marker: Path

    def __call__(self, value):
        return value

    @property
    def __signature__(self):
        self.marker.write_text("signature inspected", encoding="utf-8")
        return inspect.signature(self.__call__)


class _SignaturePropertyTarget:
    target = _SignaturePropertyCallable()


def load_mutant(name: str, replacements: tuple[tuple[str, str], ...]):
    source = Path(checker.__file__).read_text(encoding="utf-8")
    for current, mutated in replacements:
        assert source.count(current) == 1, current
        source = source.replace(current, mutated, 1)
    module_name = f"_test_double_signature_mutant_{name}"
    module = types.ModuleType(module_name)
    module.__file__ = checker.__file__
    sys.modules[module_name] = module
    exec(compile(source, module.__file__, "exec"), module.__dict__)
    return module


def signature(module, arguments: str):
    node = ast.parse(f"lambda {arguments}: None", mode="eval").body
    result = module._replacement_signature(node)
    assert result is not None
    return result


def scan_fixture(module, filename: str, source: str):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / filename).write_text(textwrap.dedent(source), encoding="utf-8")
        return module.scan_tests(root)


def probe_positional_rule() -> None:
    production = signature(checker, "first, second, /")
    double = signature(checker, "first, /")
    assert not checker._compatible(production, double)
    mutant = load_mutant(
        "positional",
        ((
            "    if len(production_positional) > len(double_positional) and not _has_vararg(double, inspect.Parameter.VAR_POSITIONAL):\n"
            "        return False\n",
            "    if False:\n        return False\n",
        ),),
    )
    assert mutant._compatible(production, double)
    print("PASS mutation: positional-slot narrowing is detected")


def probe_keyword_rule() -> None:
    production = signature(checker, "value=42")
    double = signature(checker, "other=42")
    assert not checker._compatible(production, double)
    mutant = load_mutant(
        "keyword",
        ((
            "            if not _keyword_accepted(double, parameter.name):\n                return False\n",
            "            if False:\n                return False\n",
        ),),
    )
    assert mutant._compatible(production, double)
    print("PASS mutation: keyword-name narrowing is detected")


def probe_required_rule() -> None:
    production = signature(checker, "target=0, **kwargs")
    double = signature(checker, "target=0, *, root, **kwargs")
    assert not checker._compatible(production, double)
    mutant = load_mutant(
        "required",
        ((
            "    if not _required_parameters_compatible(production, double):\n        return False\n",
            "    if False:\n        return False\n",
        ),),
    )
    assert mutant._compatible(production, double)
    print("PASS mutation: a new required root parameter is detected")


def probe_required_positional_only_double() -> None:
    double = signature(checker, "x, /, **kwargs")
    cases = [
        (signature(checker, "x, **kwargs"), (), {"x": 1}),
        (signature(checker, "*, x"), (), {"x": 1}),
    ]
    for production, args, kwargs in cases:
        production.bind(*args, **kwargs)
        try:
            double.bind(*args, **kwargs)
        except TypeError:
            pass
        else:
            raise AssertionError("double unexpectedly accepts keyword-only production call")
        assert not checker._compatible(production, double)

    mutant = load_mutant(
        "positional_only_required",
        ((
            "        if parameter.kind is inspect.Parameter.POSITIONAL_ONLY:\n"
            "            if positional_index < production_positional_only_required:\n"
            "                continue\n"
            "            return False\n",
            "        if False:\n"
            "            return False\n",
        ),),
    )
    assert all(mutant._compatible(production, double) for production, _, _ in cases)
    print("PASS mutation: required positional-only doubles need production positional slots")


def probe_collision_rule() -> None:
    production = signature(checker, "*args, **kwargs")
    double = signature(checker, "value=None, *args, **kwargs")
    assert not checker._compatible(production, double)
    identical = signature(checker, "target, *args, **kwargs")
    assert checker._compatible(identical, identical)
    optional_production = signature(checker, "x=None, **kwargs")
    renamed_double = signature(checker, "y=None, **kwargs")
    optional_production.bind(1, y=2)
    assert not checker._compatible(optional_production, renamed_double)
    prefix_production = signature(checker, "x, /, *args, y")
    prefix_double = signature(checker, "x, y=None, *args, **kwargs")
    prefix_production.bind(1, 2, y=3)
    assert not checker._compatible(prefix_production, prefix_double)
    fixed_production = signature(checker, "a=1, **kwargs")
    fixed_double = signature(checker, "root=None, /, value=None, **kwargs")
    fixed_production.bind(1, value=2)
    assert checker._compatible(fixed_production, fixed_double)
    mutant = load_mutant(
        "collision",
        ((
            "    return not _has_positional_keyword_collision(production, double)\n",
            "    return True\n",
        ),),
    )
    assert mutant._compatible(production, double)
    print("PASS mutation: positional/keyword collisions are detected")


def descriptor_fixture() -> str:
    return """
        from __main__ import _DescriptorFixture
        from random import Random
        def replacement(value): return value
        def no_args(): return None
        def test_static(monkeypatch):
            receiver = _DescriptorFixture()
            monkeypatch.setattr(receiver, 'static_call', replacement)
        def test_class(monkeypatch):
            receiver = _DescriptorFixture()
            monkeypatch.setattr(receiver, 'class_call', replacement)
        def test_python_bound(monkeypatch):
            receiver = _DescriptorFixture()
            monkeypatch.setattr(receiver, 'sleep', receiver.sleep)
        def test_random(monkeypatch):
            receiver = Random()
            monkeypatch.setattr(receiver, 'random', no_args)
    """


def probe_descriptor_rule() -> None:
    census = scan_fixture(checker, "test_descriptors.py", descriptor_fixture())
    assert census.scanned == census.resolved == 4
    assert not census.violations
    mutant = load_mutant(
        "descriptor",
        ((
            "        return _receiver_attribute_signature(receiver, target.elts[1].value)\n",
            "        return (\n"
            "            _class_attribute_signature(receiver.value, target.elts[1].value, False)\n"
            "            if inspect.isclass(receiver.value)\n"
            "            else _receiver_attribute_signature(receiver, target.elts[1].value)\n"
            "        )\n",
        ),),
    )
    mutated = scan_fixture(mutant, "test_descriptors.py", descriptor_fixture())
    assert mutated.resolved == 4
    assert len(mutated.violations) == 2
    assert any("test_python_bound" in item for item in mutated.violations)
    assert any("test_random" in item for item in mutated.violations)
    print("PASS mutation: descriptor binding uses the patched receiver")


def local_descriptor_fixture() -> str:
    return """
        from collections import UserDict
        class LocalDouble:
            @classmethod
            def fromkeys(cls, iterable=(), value=None): return None
            def emit(self, record): return record
        def test_local_classmethod(monkeypatch):
            receiver = UserDict()
            replacement = LocalDouble()
            monkeypatch.setattr(receiver, 'fromkeys', replacement.fromkeys)
        def test_local_method(monkeypatch):
            receiver = LocalDouble()
            monkeypatch.setattr(receiver, 'emit', receiver.emit)
    """


def probe_local_class_descriptors() -> None:
    census = scan_fixture(checker, "test_local_descriptors.py", local_descriptor_fixture())
    assert census.scanned == census.resolved == 2
    assert not census.skipped and not census.violations
    mutant = load_mutant(
        "local_descriptors",
        ((
            "    if isinstance(receiver.value, ast.ClassDef):\n"
            "        return _local_class_attribute_signature(receiver.value, name, receiver.is_instance)\n",
            "    if isinstance(receiver.value, ast.ClassDef):\n"
            "        return _SignatureResolution(None, \"unresolvable\", \"mutation\")\n",
        ),),
    )
    mutated = scan_fixture(mutant, "test_local_descriptors.py", local_descriptor_fixture())
    assert mutated.resolved == 0 and len(mutated.skipped) == 2
    print("PASS mutation: local classmethod and method descriptors resolve from AST")


def probe_static_constructor_inspection() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        output = root / "constructor.log"
        _SideEffectFixture.output_path = output
        fixture = root / "test_side_effect.py"
        fixture.write_text(
            "from __main__ import _SideEffectFixture\n"
            "def test_emit(monkeypatch):\n"
            "    receiver = _SideEffectFixture()\n"
            "    monkeypatch.setattr(receiver, 'emit', lambda record: record)\n",
            encoding="utf-8",
        )
        census = checker.scan_tests(root)
        assert census.scanned == census.resolved == 1
        assert not output.exists()

        mutant = load_mutant(
            "constructor_execution",
            ((
                "            if value is not None:\n"
                "                return _Receiver(value, True)\n",
                "            if value is not None:\n"
                "                arguments = [ast.literal_eval(argument) for argument in constructor_call.args]\n"
                "                keywords = {keyword.arg: ast.literal_eval(keyword.value) for keyword in constructor_call.keywords}\n"
                "                return _Receiver(value(*arguments, **keywords), True)\n",
            ),),
        )
        mutated = mutant.scan_tests(root)
        assert mutated.scanned == mutated.resolved == 1
        assert output.exists()
    print("PASS mutation: static census leaves constructor side effects untouched")


def probe_single_predicate_owners() -> None:
    source = Path(checker.__file__).read_text(encoding="utf-8")
    assert source.count("def _keyword_accepted(") == 1
    assert "_keyword_is_production_accepted" not in source
    assert source.count("def _required_positional_only_prefix(") == 1
    assert source.count("_required_positional_only_prefix(production)") == 2

    production = signature(checker, "value=42")
    double = signature(checker, "value=42")
    assert checker._compatible(production, double)
    keyword_mutant = load_mutant(
        "single_keyword_owner",
        ((
            "def _keyword_accepted(signature: inspect.Signature, name: str) -> bool:\n"
            "    parameter = signature.parameters.get(name)\n"
            "    return (\n"
            "        parameter is not None\n"
            "        and parameter.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)\n"
            "    ) or _has_vararg(signature, inspect.Parameter.VAR_KEYWORD)\n",
            "def _keyword_accepted(signature: inspect.Signature, name: str) -> bool:\n"
            "    return False\n",
        ),),
    )
    assert not keyword_mutant._compatible(production, double)

    positional = signature(checker, "x, /, **kwargs")
    assert checker._compatible(positional, positional)
    prefix_mutant = load_mutant(
        "single_prefix_owner",
        ((
            "def _required_positional_only_prefix(signature: inspect.Signature) -> int:\n"
            "    count = 0\n"
            "    for parameter in signature.parameters.values():\n"
            "        if parameter.kind is inspect.Parameter.POSITIONAL_ONLY and parameter.default is inspect.Parameter.empty:\n"
            "            count += 1\n"
            "        elif parameter.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD):\n"
            "            break\n"
            "    return count\n",
            "def _required_positional_only_prefix(signature: inspect.Signature) -> int:\n"
            "    return 0\n",
        ),),
    )
    assert not prefix_mutant._compatible(positional, positional)
    print("PASS mutation: keyword acceptance and positional prefix each have one owner")


def probe_bound_method_narrowing() -> None:
    census = scan_fixture(
        checker,
        "test_bound_double.py",
        """
            from __main__ import _DescriptorFixture, _NarrowBoundFixture
            def test_narrow(monkeypatch):
                receiver = _DescriptorFixture()
                replacement = _NarrowBoundFixture()
                monkeypatch.setattr(receiver, 'sleep', replacement.sleep)
        """,
    )
    assert census.scanned == census.resolved == 1
    assert len(census.violations) == 1
    print("PASS probe: pure-Python bound method narrowing is reported")


def probe_opaque_builtin_is_skipped() -> None:
    census = scan_fixture(
        checker,
        "test_opaque_builtin.py",
        """
            import time
            def replacement(seconds): return None
            def test_sleep(monkeypatch):
                monkeypatch.setattr(time, 'sleep', replacement)
        """,
    )
    assert census.scanned == 1 and census.resolved == 0 and len(census.skipped) == 1
    print("PASS probe: builtin without a signature is counted as skipped")


def probe_signature_property_is_not_evaluated() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        marker = root / "signature-property-ran"
        _SignaturePropertyCallable.marker = marker
        fixture = root / "test_signature_property.py"
        fixture.write_text(
            "from __main__ import _SignaturePropertyTarget\n"
            "def replacement(value): return value\n"
            "def test_dynamic_signature(monkeypatch):\n"
            "    receiver = _SignaturePropertyTarget()\n"
            "    monkeypatch.setattr(receiver, 'target', replacement)\n",
            encoding="utf-8",
        )
        census = checker.scan_tests(root)
        assert census.scanned == 1 and census.resolved == 0
        assert census.skipped_by_reason == (("unsafe-type", 1),)
        assert not marker.exists()

        mutant = load_mutant(
            "unsafe_signature_type",
            ((
                "def _safe_signature_type(value) -> bool:\n"
                "    return type(value) in _SAFE_SIGNATURE_TYPES\n",
                "def _safe_signature_type(value) -> bool:\n"
                "    return True\n",
            ),),
        )
        mutated = mutant.scan_tests(root)
        assert mutated.scanned == mutated.resolved == 1
        assert marker.exists()
    print("PASS mutation: unsafe __signature__ properties remain untouched")


def probe_staticmethod_stays_unbound() -> None:
    fixture = """
        from __main__ import _DescriptorFixture
        def replacement(value): return value
        def test_static(monkeypatch):
            receiver = _DescriptorFixture()
            monkeypatch.setattr(receiver, 'static_call', replacement)
    """
    census = scan_fixture(checker, "test_staticmethod.py", fixture)
    assert census.scanned == census.resolved == 1
    assert not census.skipped and not census.violations

    mutant = load_mutant(
        "staticmethod_binding",
        ((
            "    if type(descriptor) is staticmethod:\n"
            "        return _signature_of(descriptor.__func__)",
            "    if type(descriptor) is staticmethod:\n"
            "        return _bind_descriptor_signature(_signature_of(descriptor.__func__), drop_first=True)",
        ),),
    )
    mutated = scan_fixture(mutant, "test_staticmethod.py", fixture)
    assert mutated.resolved == 1 and len(mutated.violations) == 1
    print("PASS mutation: staticmethod signatures remain unbound")


def probe_string_classmethod_binding() -> None:
    fixture = """
        from unittest.mock import patch
        def replacement(iterable=(), value=None): return None
        def test_fromkeys():
            patch('collections.UserDict.fromkeys', replacement)
    """
    census = scan_fixture(checker, "test_string_classmethod.py", fixture)
    assert census.scanned == census.resolved == 1
    assert not census.skipped and not census.violations

    mutant = load_mutant(
        "string_classmethod_binding",
        ((
            "    if type(descriptor) is classmethod:\n"
            "        return _bind_descriptor_signature(_signature_of(descriptor.__func__), drop_first=True)",
            "    if type(descriptor) is classmethod:\n"
            "        return _bind_descriptor_signature(_signature_of(descriptor.__func__), drop_first=False)",
        ),),
    )
    mutated = scan_fixture(mutant, "test_string_classmethod.py", fixture)
    assert mutated.resolved == 1 and len(mutated.violations) == 1
    print("PASS mutation: string targets use the classmethod binding resolver")


def probe_string_and_object_targets_share_resolver() -> None:
    fixture = """
        from collections import UserDict
        from unittest.mock import patch
        def replacement(iterable=(), value=None): return None
        def test_string_target():
            patch('collections.UserDict.fromkeys', replacement)
        def test_object_target():
            patch.object(UserDict, 'fromkeys', replacement)
    """
    census = scan_fixture(checker, "test_shared_classmethod_resolver.py", fixture)
    assert census.scanned == census.resolved == 2
    assert not census.skipped and not census.violations

    mutant = load_mutant(
        "separate_string_resolver",
        ((
            "        return _receiver_attribute_signature(receiver, name)\n"
            "    elif isinstance(target, ast.Tuple)",
            "        return _signature_of(_imported_object(target.value, {}, {}))\n"
            "    elif isinstance(target, ast.Tuple)",
        ),),
    )
    mutated = scan_fixture(mutant, "test_shared_classmethod_resolver.py", fixture)
    assert mutated.resolved == 1 and len(mutated.skipped) == 1
    assert mutated.skipped_by_reason == (("unsafe-type", 1),)
    print("PASS mutation: string and object targets share the same resolver")


def probe_native_classmethod_binding_on_class_receiver() -> None:
    fixture = """
        from builtins import dict as Dict
        from unittest.mock import patch
        def replacement(iterable, value=None, /): return None
        def test_fromkeys():
            patch.object(Dict, 'fromkeys', replacement)
    """
    census = scan_fixture(checker, "test_native_classmethod.py", fixture)
    assert census.scanned == census.resolved == 1
    assert not census.skipped and not census.violations

    mutant = load_mutant(
        "native_classmethod_binding",
        ((
            "    if type(descriptor) is types.ClassMethodDescriptorType:\n"
            "        return _bind_descriptor_signature(_signature_of(descriptor), drop_first=True)",
            "    if type(descriptor) is types.ClassMethodDescriptorType:\n"
            "        return _bind_descriptor_signature(_signature_of(descriptor), drop_first=False)",
        ),),
    )
    mutated = scan_fixture(mutant, "test_native_classmethod.py", fixture)
    assert mutated.resolved == 1 and len(mutated.violations) == 1
    print("PASS mutation: native classmethod descriptors bind on class receivers")


def probe_decorated_local_method_is_skipped() -> None:
    fixture = """
        from collections import UserDict
        def narrow(function): return lambda cls, value=None: None
        class Double:
            @classmethod
            @narrow
            def fromkeys(cls, iterable=(), value=None): return None
        def test_decorated(monkeypatch):
            mapping = UserDict()
            double = Double()
            monkeypatch.setattr(mapping, 'fromkeys', double.fromkeys)
    """
    census = scan_fixture(checker, "test_decorated_classmethod.py", fixture)
    assert census.scanned == 1 and census.resolved == 0
    assert census.skipped_by_reason == (("decorated", 1),)

    mutant = load_mutant(
        "ignore_local_decorator",
        ((
            "    if any(decorator not in {\"classmethod\", \"staticmethod\"} for decorator in decorators):\n"
            "        return _SignatureResolution(None, \"decorated\", \"local method has a decorator that can change its callable\")",
            "    if False:\n"
            "        return _SignatureResolution(None, \"decorated\", \"local method has a decorator that can change its callable\")",
        ),),
    )
    mutated = scan_fixture(mutant, "test_decorated_classmethod.py", fixture)
    assert mutated.resolved == 1 and not mutated.skipped and not mutated.violations
    print("PASS mutation: decorated local methods cannot pass from their declaration")


def probe_importorskip_alias() -> None:
    census = scan_fixture(
        checker,
        "test_importorskip_alias.py",
        """
            import pytest
            module_alias = pytest.importorskip('collections')
            def replacement(iterable=(), /, **kwargs): return None
            def test_patch(monkeypatch):
                monkeypatch.setattr(module_alias, 'Counter', replacement)
        """,
    )
    assert census.scanned == 1 and census.resolved == 0
    assert census.skipped_by_reason == (("unsafe-type", 1),)
    assert not census.violations
    mutant = load_mutant(
        "importorskip_alias",
        ((
            "                        modules[target.id] = value.args[0].value\n",
            "                        pass\n",
        ),),
    )
    mutated = scan_fixture(
        mutant,
        "test_importorskip_alias.py",
        """
            import pytest
            module_alias = pytest.importorskip('collections')
            def replacement(iterable=(), /, **kwargs): return None
            def test_patch(monkeypatch):
                monkeypatch.setattr(module_alias, 'Counter', replacement)
        """,
    )
    assert mutated.resolved == 0
    assert mutated.skipped_by_reason == (("unresolvable", 1),)
    print("PASS mutation: pytest.importorskip module aliases resolve")


if __name__ == "__main__":
    probe_positional_rule()
    probe_keyword_rule()
    probe_required_rule()
    probe_required_positional_only_double()
    probe_collision_rule()
    probe_descriptor_rule()
    probe_local_class_descriptors()
    probe_static_constructor_inspection()
    probe_single_predicate_owners()
    probe_bound_method_narrowing()
    probe_opaque_builtin_is_skipped()
    probe_signature_property_is_not_evaluated()
    probe_staticmethod_stays_unbound()
    probe_string_classmethod_binding()
    probe_string_and_object_targets_share_resolver()
    probe_native_classmethod_binding_on_class_receiver()
    probe_decorated_local_method_is_skipped()
    probe_importorskip_alias()
