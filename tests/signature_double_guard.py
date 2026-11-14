"""Signature checks shared by the test patch guard and its focused tests."""

from __future__ import annotations

import functools
import importlib
import inspect
import json
from functools import lru_cache
from pathlib import Path
from typing import Any


def _python_callable(value: Any) -> bool:
    while isinstance(value, (staticmethod, classmethod)):
        value = value.__func__
    while isinstance(value, functools.partial):
        value = value.func
    return inspect.isfunction(value) or inspect.ismethod(value)


@lru_cache(maxsize=4096)
def _cached_signature(value: Any, replacement: bool) -> inspect.Signature:
    if isinstance(value, staticmethod):
        value = value.__func__
    elif isinstance(value, classmethod):
        value = value.__func__
    if replacement:
        return inspect.signature(value, follow_wrapped=False)
    return inspect.signature(value)


def _signature(value: Any, *, replacement: bool = False) -> inspect.Signature:
    try:
        hash(value)
    except TypeError:
        return _cached_signature.__wrapped__(value, replacement)
    return _cached_signature(value, replacement)


def _is_signature_callable(value: Any) -> bool:
    if isinstance(value, classmethod):
        return callable(value.__func__)
    if isinstance(value, staticmethod):
        return callable(value.__func__)
    return callable(value)


def signature_shape(value: Any) -> str:
    """Return the binding-relevant part of a callable's actual signature."""
    signature = _signature(value, replacement=True)
    shape = [
        (
            parameter.kind.name,
            parameter.name,
            parameter.default is inspect.Parameter.empty,
        )
        for parameter in signature.parameters.values()
    ]
    return json.dumps(shape, separators=(",", ":"))


def signature_mismatch(
    production: Any,
    replacement: Any,
) -> tuple[str | None, bool]:
    """Check the replacement against all calls allowed by a Python callable."""
    if not _python_callable(production) or not _is_signature_callable(replacement):
        return None, False
    try:
        production_signature = _signature(production)
        replacement_signature = _signature(replacement, replacement=True)
        if isinstance(replacement, classmethod):
            replacement_signature = _without_receiver(replacement_signature)
    except (TypeError, ValueError):
        return None, True
    return signature_mismatch_for_signatures(
        production_signature, replacement_signature
    ), False


@lru_cache(maxsize=4096)
def _cached_signature_mismatch(
    production: inspect.Signature,
    replacement: inspect.Signature,
) -> str | None:
    return _compare_signatures(production, replacement)


def signature_mismatch_for_signatures(
    production: inspect.Signature,
    replacement: inspect.Signature,
) -> str | None:
    """Bind production call shapes directly to the replacement signature.

    Every declared positional count is checked with required names, all names,
    and each optional name. Variadic parameters add unbounded names, so their
    presence and collisions with replacement parameters are checked too.
    """
    try:
        hash(production)
        hash(replacement)
    except TypeError:
        return _compare_signatures(production, replacement)
    return _cached_signature_mismatch(production, replacement)


def _compare_signatures(
    production: inspect.Signature,
    replacement: inspect.Signature,
) -> str | None:
    production_parameters = tuple(production.parameters.values())
    replacement_parameters = tuple(replacement.parameters.values())
    production_varargs = any(
        parameter.kind is inspect.Parameter.VAR_POSITIONAL
        for parameter in production_parameters
    )
    production_varkw = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in production_parameters
    )
    replacement_kinds = {parameter.kind for parameter in replacement_parameters}
    if production_varargs and inspect.Parameter.VAR_POSITIONAL not in replacement_kinds:
        return "production accepts *args but the double does not"
    if production_varkw and inspect.Parameter.VAR_KEYWORD not in replacement_kinds:
        return "production accepts **kwargs but the double does not"

    positional = tuple(
        parameter
        for parameter in production_parameters
        if parameter.kind
        in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    )
    required_positional_only = sum(
        parameter.kind is inspect.Parameter.POSITIONAL_ONLY
        and parameter.default is inspect.Parameter.empty
        for parameter in positional
    )
    named = tuple(
        parameter
        for parameter in production_parameters
        if parameter.kind
        in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    )
    marker = object()
    call_shapes: list[tuple[tuple[Any, ...], dict[str, Any], str]] = []
    seen_shapes: set[tuple[tuple[Any, ...], tuple[str, ...]]] = set()

    def add(args: tuple[Any, ...], kwargs: dict[str, Any], label: str) -> None:
        try:
            production.bind(*args, **kwargs)
        except TypeError:
            return
        shape = (args, tuple(sorted(kwargs)))
        if shape not in seen_shapes:
            seen_shapes.add(shape)
            call_shapes.append((args, kwargs, label))

    for count in range(required_positional_only, len(positional) + 1):
        args = (marker,) * count
        remaining = tuple(
            parameter
            for parameter in named
            if not (
                parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
                and parameter in positional[:count]
            )
        )
        required = tuple(
            parameter
            for parameter in remaining
            if parameter.default is inspect.Parameter.empty
        )
        optional = tuple(parameter for parameter in remaining if parameter not in required)
        required_kwargs = {parameter.name: marker for parameter in required}
        all_kwargs = {
            parameter.name: marker for parameter in (*required, *optional)
        }
        add(args, required_kwargs, f"{count} positional argument(s), required keywords")
        add(args, all_kwargs, f"{count} positional argument(s), all keywords")
        for parameter in optional:
            kwargs = {**required_kwargs, parameter.name: marker}
            add(
                args,
                kwargs,
                f"{count} positional argument(s), optional keyword {parameter.name!r}",
            )

    # If **kwargs accepts a replacement's positional name, passing that name
    # alongside the matching positional slot is a valid production call but a
    # duplicate binding for the replacement. Probe each such collision.
    replacement_positional = tuple(
        parameter
        for parameter in replacement_parameters
        if parameter.kind
        in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    )
    if production_varkw:
        for index, parameter in enumerate(replacement_positional):
            if parameter.kind is not inspect.Parameter.POSITIONAL_OR_KEYWORD:
                continue
            count = max(index + 1, required_positional_only)
            required_keywords = {
                item.name: marker
                for item in named
                if item.default is inspect.Parameter.empty
                and not (
                    item.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
                    and item in positional[:count]
                )
            }
            add(
                (marker,) * count,
                {**required_keywords, parameter.name: marker},
                f"variadic keyword collides with positional parameter {parameter.name!r}",
            )

    if production_varargs:
        # More positional arguments than production declares remain legal.
        for count in range(len(positional) + 1, len(positional) + len(replacement_positional) + 2):
            required_keywords = {
                parameter.name: marker
                for parameter in named
                if parameter.default is inspect.Parameter.empty
                and parameter.kind is inspect.Parameter.KEYWORD_ONLY
            }
            add(
                (marker,) * count,
                required_keywords,
                f"{count} positional argument(s) through *args",
            )

    for args, kwargs, label in call_shapes:
        try:
            replacement.bind(*args, **kwargs)
        except TypeError as error:
            return f"cannot bind production call ({label}): {error}"
    return None


def _production_callable(target: Any, name: str, value: Any) -> tuple[Any, bool]:
    """Resolve the existing descriptor as the patched caller currently sees it."""
    if isinstance(target, type):
        if isinstance(value, staticmethod):
            return value.__func__, False
        if isinstance(value, classmethod):
            return value.__get__(None, target), False
        if inspect.isfunction(value):
            # Class patches are called through instances; Python binds this
            # receiver before the test double receives user arguments.
            return value, True
        return value, False

    instance_values = getattr(target, "__dict__", {})
    if name in instance_values:
        return value, False
    if isinstance(value, staticmethod):
        return value.__func__, False
    if isinstance(value, classmethod):
        return value.__get__(target, type(target)), False
    if inspect.isfunction(value):
        return value.__get__(target, type(target)), False
    return value, False


def _replacement_callable(target: Any, value: Any) -> tuple[Any, bool]:
    """Resolve binding that the replacement will receive after installation."""
    if not isinstance(target, type):
        # MonkeyPatch stores an instance replacement in the instance dictionary;
        # functions there are not descriptors and receive no implicit self.
        return value, False
    if isinstance(value, staticmethod):
        return value.__func__, False
    if isinstance(value, classmethod):
        return value.__get__(None, target), False
    if inspect.isfunction(value):
        return value, True
    return value, False


def patch_signature_mismatch(
    target: Any,
    name: str,
    replacement: Any,
) -> tuple[Any, str | None, bool]:
    """Return production callable, mismatch reason, and signature-skip flag."""
    raw_production = inspect.getattr_static(target, name)
    production, bind_production_receiver = _production_callable(
        target, name, raw_production
    )
    effective_replacement, bind_replacement_receiver = _replacement_callable(
        target, replacement
    )
    if not _python_callable(production) or not _is_signature_callable(effective_replacement):
        return production, None, False
    try:
        production_signature = _signature(production)
        replacement_signature = _signature(
            effective_replacement, replacement=True
        )
        if bind_production_receiver:
            production_signature = _without_receiver(production_signature)
        if bind_replacement_receiver:
            replacement_signature = _without_receiver(replacement_signature)
    except (TypeError, ValueError):
        return production, None, True
    mismatch = signature_mismatch_for_signatures(
        production_signature, replacement_signature
    )
    return production, mismatch, False


def check_patch_signature(
    target: Any,
    name: str,
    replacement: Any,
    nodeid: str,
    allowed: dict[str, str],
    collected: dict[str, str] | None = None,
    fail_unlisted: bool = True,
) -> bool:
    production, mismatch, skipped = patch_signature_mismatch(
        target, name, replacement
    )
    if skipped or mismatch is None:
        return skipped
    key = allowlist_key_for_node(nodeid, production, replacement)
    if collected is not None:
        collected[key] = mismatch
    if fail_unlisted and key not in allowed:
        raise AssertionError(
            f"test double for {key} is incompatible with the production signature: "
            f"{mismatch}"
        )
    return False


def _without_receiver(signature: inspect.Signature) -> inspect.Signature:
    parameters = tuple(signature.parameters.values())
    return signature.replace(parameters=parameters[1:])


def signature_target_name(value: Any) -> str:
    while isinstance(value, (staticmethod, classmethod)):
        value = value.__func__
    while isinstance(value, functools.partial):
        value = value.func
    if inspect.ismethod(value):
        value = value.__func__
    return f"{value.__module__}.{value.__qualname__}"


def allowlist_key_for_node(nodeid: str, production: Any, replacement: Any) -> str:
    base_nodeid = nodeid.split("[", 1)[0]
    return "::".join(
        (base_nodeid, signature_target_name(production), signature_shape(replacement))
    )


def read_allowlist(path: Path) -> dict[str, str]:
    entries: dict[str, str] = {}
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, reason = line.partition(" # ")
        if not separator or not reason.strip():
            raise AssertionError(
                f"{path}:{line_number} needs `path::test::target::signature # reason`"
            )
        if key in entries:
            raise AssertionError(f"{path}:{line_number} repeats {key}")
        entries[key] = reason.strip()
    return entries


def resolve_import_path(path: str) -> Any:
    """Import the longest module prefix, then resolve its remaining attributes."""
    parts = path.split(".")
    for split in range(len(parts), 0, -1):
        try:
            value: Any = importlib.import_module(".".join(parts[:split]))
        except ModuleNotFoundError as error:
            if error.name != ".".join(parts[:split]):
                raise
            continue
        for part in parts[split:]:
            value = getattr(value, part)
        return value
    raise ModuleNotFoundError(path)


def resolve_target_string(path: str) -> tuple[Any, str]:
    target_path, separator, attribute = path.rpartition(".")
    if not separator:
        raise LookupError(f"not a dotted monkeypatch target: {path}")
    return resolve_import_path(target_path), attribute
