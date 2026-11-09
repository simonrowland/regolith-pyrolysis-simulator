"""Cache adapters for direct OpenIMCC bridge and redox evaluations."""

from __future__ import annotations

import importlib
from dataclasses import dataclass, fields, is_dataclass
from types import MappingProxyType
from typing import Any, Callable, Mapping

from simulator.melt_backend.openimcc_bridge import OpenImccBridgeResult


def _encode_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return {"$float_hex": value.hex()}
    if isinstance(value, tuple):
        return {"$tuple": [_encode_value(item) for item in value]}
    if isinstance(value, list):
        return {"$list": [_encode_value(item) for item in value]}
    if isinstance(value, Mapping):
        mapping_type = (
            "mappingproxy" if isinstance(value, MappingProxyType) else "dict"
        )
        return {
            "$mapping": mapping_type,
            "items": [
                [_encode_value(key), _encode_value(item)]
                for key, item in value.items()
            ],
        }
    raise TypeError(f"unsupported OpenIMCC cache value: {type(value).__name__}")


def _decode_value(value: Any) -> Any:
    if isinstance(value, list):
        return [_decode_value(item) for item in value]
    if not isinstance(value, Mapping):
        return value
    if set(value) == {"$float_hex"}:
        return float.fromhex(str(value["$float_hex"]))
    if set(value) == {"$tuple"}:
        return tuple(_decode_value(item) for item in value["$tuple"])
    if set(value) == {"$list"}:
        return [_decode_value(item) for item in value["$list"]]
    if set(value) == {"$mapping", "items"}:
        items = {
            _decode_value(key): _decode_value(item)
            for key, item in value["items"]
        }
        if value["$mapping"] == "mappingproxy":
            return MappingProxyType(items)
        if value["$mapping"] == "dict":
            return items
        raise ValueError("unknown OpenIMCC cache mapping type")
    return {key: _decode_value(item) for key, item in value.items()}


@dataclass(frozen=True)
class OpenImccEvaluationOutcome:
    """Bridge result or a stable refusal returned by an isolated evaluation."""

    result: OpenImccBridgeResult | None = None
    refusal_message: str | None = None

    def to_cache_payload(self) -> dict[str, Any]:
        if self.result is not None:
            return {
                "kind": "result",
                "result": self.result.to_cache_payload(),
            }
        if self.refusal_message is not None:
            return {"kind": "refusal", "message": self.refusal_message}
        raise ValueError("OpenIMCC evaluation outcome has no result")

    @classmethod
    def from_cache_payload(cls, payload: Mapping[str, Any]) -> OpenImccEvaluationOutcome:
        kind = payload.get("kind")
        if kind == "result":
            return cls(
                result=OpenImccBridgeResult.from_cache_payload(payload["result"])
            )
        if kind == "refusal":
            return cls(refusal_message=str(payload["message"]))
        raise ValueError("unknown OpenIMCC evaluation cache outcome")


@dataclass(frozen=True)
class OpenImccGasOutcome:
    """Gas-speciation pressures produced by one direct OpenIMCC call."""

    pressures: Mapping[str, Any]

    def to_cache_payload(self) -> dict[str, Any]:
        return {"pressures": _encode_value(dict(self.pressures))}

    @classmethod
    def from_cache_payload(cls, payload: Mapping[str, Any]) -> OpenImccGasOutcome:
        pressures = _decode_value(payload["pressures"])
        if not isinstance(pressures, Mapping):
            raise ValueError("OpenIMCC gas cache pressures must be a mapping")
        return cls(pressures=pressures)


@dataclass(frozen=True)
class OpenImccRedoxOutcome:
    """Exact redox result or typed refusal at the battery adapter boundary."""

    result: Any = None
    refusal_module: str | None = None
    refusal_name: str | None = None
    refusal_args: tuple[Any, ...] = ()

    def to_cache_payload(self) -> dict[str, Any]:
        if self.result is not None:
            if not is_dataclass(self.result):
                raise TypeError("OpenIMCC redox result must be a dataclass")
            return {
                "kind": "result",
                "fields": {
                    field.name: _encode_value(getattr(self.result, field.name))
                    for field in fields(self.result)
                },
            }
        if self.refusal_module and self.refusal_name:
            return {
                "kind": "refusal",
                "module": self.refusal_module,
                "name": self.refusal_name,
                "args": _encode_value(self.refusal_args),
            }
        raise ValueError("OpenIMCC redox outcome has no result")

    @classmethod
    def from_cache_payload(cls, payload: Mapping[str, Any]) -> OpenImccRedoxOutcome:
        kind = payload.get("kind")
        if kind == "result":
            from openimcc.redox import RedoxResult

            decoded = {
                str(name): _decode_value(value)
                for name, value in payload["fields"].items()
            }
            expected = {field.name for field in fields(RedoxResult)}
            if set(decoded) != expected:
                raise ValueError("OpenIMCC redox cache fields do not match RedoxResult")
            return cls(result=RedoxResult(**decoded))
        if kind == "refusal":
            module_name = str(payload["module"])
            if module_name != "openimcc" and not module_name.startswith("openimcc."):
                raise ValueError("OpenIMCC cache refusal has an invalid module")
            module = importlib.import_module(module_name)
            refusal_type = getattr(module, str(payload["name"]))
            import openimcc

            if not isinstance(refusal_type, type) or not issubclass(
                refusal_type, openimcc.ImccRefusal
            ):
                raise ValueError("OpenIMCC cache refusal type is not typed")
            return cls(
                refusal_module=module_name,
                refusal_name=refusal_type.__name__,
                refusal_args=_decode_value(payload["args"]),
            )
        raise ValueError("unknown OpenIMCC redox cache outcome")

    def refusal(self) -> BaseException:
        if not self.refusal_module or not self.refusal_name:
            raise ValueError("OpenIMCC redox outcome is not a refusal")
        module = importlib.import_module(self.refusal_module)
        refusal_type = getattr(module, self.refusal_name)
        return refusal_type(*self.refusal_args)


def _is_typed_bridge_refusal(error: RuntimeError) -> bool:
    prefix = "isolated OpenIMCC "
    message = str(error)
    if not message.startswith(prefix) or ":" not in message:
        return False
    refusal_name = message[len(prefix) :].split(":", 1)[0].rsplit(".", 1)[-1]
    if refusal_name in {"ImccNonconvergenceError", "RedoxNumericalError"}:
        return False
    import openimcc

    for module_name in ("openimcc", "openimcc.kernel", "openimcc.redox", "openimcc.gas"):
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        refusal_type = getattr(module, refusal_name, None)
        if isinstance(refusal_type, type) and issubclass(
            refusal_type, openimcc.ImccRefusal
        ):
            return True
    return False


def cached_openimcc_evaluate(
    cache: Any,
    *,
    composition_mol: Mapping[str, float] | None,
    temperature_K: float,
    timeout_s: float | None = None,
    pack: str | None = None,
    allow_extrapolation: bool = False,
    allow_out_of_envelope: bool = False,
    solve_fn: Callable[[], OpenImccBridgeResult],
) -> OpenImccBridgeResult:
    """Cache one direct bridge evaluation while retaining caller timeout policy."""
    from simulator.engine_cache import cached_solve

    request = {
        "call": "evaluate",
        "composition_mol": composition_mol,
        "temperature_K": temperature_K,
        "timeout_s": timeout_s,
        "pack": pack,
        "allow_extrapolation": allow_extrapolation,
        "allow_out_of_envelope": allow_out_of_envelope,
    }

    def solve_outcome() -> OpenImccEvaluationOutcome:
        try:
            return OpenImccEvaluationOutcome(result=solve_fn())
        except RuntimeError as exc:
            if not _is_typed_bridge_refusal(exc):
                raise
            return OpenImccEvaluationOutcome(refusal_message=str(exc))

    outcome = cached_solve(cache, "openimcc", request, solve_outcome)
    if outcome.refusal_message is not None:
        raise RuntimeError(outcome.refusal_message)
    assert outcome.result is not None
    return outcome.result


def cached_openimcc_redox(
    cache: Any,
    *,
    inventory: Mapping[str, float],
    temperature_K: float,
    mode: str,
    lambda_imposed: float | None = None,
    solve_fn: Callable[[], Any],
) -> Any:
    """Cache one direct redox solve, including stable OpenIMCC refusals."""
    from simulator.engine_cache import cached_solve

    request = {
        "call": "evaluate_redox",
        "inventory": inventory,
        "temperature_K": temperature_K,
        "mode": mode,
        "lambda_imposed": lambda_imposed,
    }

    def solve_outcome() -> OpenImccRedoxOutcome:
        try:
            return OpenImccRedoxOutcome(result=solve_fn())
        except Exception as exc:
            import openimcc

            if not isinstance(exc, openimcc.ImccRefusal):
                raise
            if type(exc).__name__ in {"ImccNonconvergenceError", "RedoxNumericalError"}:
                raise
            return OpenImccRedoxOutcome(
                refusal_module=type(exc).__module__,
                refusal_name=type(exc).__name__,
                refusal_args=tuple(exc.args),
            )

    outcome = cached_solve(cache, "openimcc", request, solve_outcome)
    if outcome.refusal_name is not None:
        raise outcome.refusal()
    return outcome.result


def cached_openimcc_evaluate_gas(
    cache: Any,
    *,
    parent_oxide_activities: Mapping[str, float],
    temperature_K: float,
    pressure_bar: float,
    gas_pack: Any,
    parent_oxides: tuple[str, ...],
    allow_extrapolation: bool,
    solve_fn: Callable[[], Mapping[str, Any]],
) -> Mapping[str, Any]:
    """Cache one gas-layer solve for OpenIMCC's selected default gas pack."""
    from simulator.engine_cache import cached_solve

    request = {
        "call": "evaluate_gas",
        "parent_oxide_activities": parent_oxide_activities,
        "temperature_K": temperature_K,
        "pressure_bar": pressure_bar,
        "gas_pack_selection": "default",
        "parent_oxides": parent_oxides,
        "allow_extrapolation": allow_extrapolation,
    }
    outcome = cached_solve(
        cache,
        "openimcc",
        request,
        lambda: OpenImccGasOutcome(pressures=dict(solve_fn())),
    )
    return outcome.pressures


__all__ = (
    "OpenImccEvaluationOutcome",
    "OpenImccGasOutcome",
    "OpenImccRedoxOutcome",
    "cached_openimcc_evaluate_gas",
    "cached_openimcc_evaluate",
    "cached_openimcc_redox",
)
