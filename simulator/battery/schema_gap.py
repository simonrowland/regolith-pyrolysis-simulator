"""SC-475 schema-gap refusal rules for declared source observations."""

from collections.abc import Mapping
from typing import Any

from simulator.battery.records import State


SC475_SCHEMA_GAP_SOURCE_IDS = frozenset(
    {
        "shornikov-2008-mgo-aluminosilicate-melts",
        "shornikov-2011-cao-mgo-alumina-melts",
        "shornikov-2014-sodium-germanate-melts",
        "shornikov-2017-spinel-thermodynamics",
        "shornikova-2019-perovskite-thermodynamics",
        "zaitsev-2006-magnesium-silicate-thermodynamics",
        "beyer-1980-potassium-metasilicate-disilicate-ri8410",
        "bennington-brown-1982-synthetic-acmite-ri8621",
        "bennington-brown-beyer-1984-aegirine-ri8912",
    }
)
_SC475_POINT_GAP_CONTEXT = "_sc475_declared_schema_gap"


def declared_schema_gaps_for_extract(document: Mapping[str, Any]) -> dict[str, str]:
    return {
        str(gap["observation_id"]): str(gap["reason"])
        for gap in (document.get("schema_gaps") or ())
        if isinstance(gap, Mapping)
        and isinstance(gap.get("observation_id"), str)
        and isinstance(gap.get("reason"), str)
    }


def attach_extract_declarations(
    context: dict[str, Any], document: Mapping[str, Any]
) -> dict[str, Any]:
    context["declared_schema_gaps"] = declared_schema_gaps_for_extract(document)
    return context


def declared_schema_gap(payload: Mapping[str, Any] | None) -> str | None:
    if not isinstance(payload, Mapping):
        return None
    reason = payload.get("schema_gap")
    return reason.strip() if isinstance(reason, str) and reason.strip() else None


def sc475_declared_schema_gap(
    source_id: str,
    observation_id: str,
    payload: Mapping[str, Any] | None,
    declarations: Mapping[str, str] | None,
) -> str | None:
    if source_id not in SC475_SCHEMA_GAP_SOURCE_IDS or declarations is None:
        return None
    declared = declarations.get(observation_id)
    if not isinstance(declared, str) or not declared.strip():
        return None
    return declared_schema_gap(payload)


def map_quantity(
    mapper: Any,
    source_id: str,
    observation_id: str,
    payload: Mapping[str, Any],
    context: Mapping[str, Any],
    observation: Mapping[str, Any],
) -> tuple[State, str | None, str | None]:
    obs_type = observation.get("type")
    obs_type = obs_type if isinstance(obs_type, str) else None
    schema_gap = sc475_declared_schema_gap(
        source_id,
        observation_id,
        payload,
        context.get("declared_schema_gaps"),
    )
    quantity, reason = mapper(
        obs_type,
        payload,
        units=observation.get("units"),
        row=observation,
    )
    quantity, reason = apply_sc475_quantity_gap(quantity, reason, schema_gap)
    return quantity, reason, schema_gap


def point_schema_gap_context(
    quantity_token: object,
    parent_values: object,
    selection_payload: Mapping[str, Any],
) -> tuple[Mapping[str, Any], State | None]:
    """Carry a parent's declared gap to its point only when the reason matches."""
    schema_gap_reason = (
        parent_values.get(_SC475_POINT_GAP_CONTEXT)
        if isinstance(parent_values, Mapping)
        else None
    )
    if (
        quantity_token is None
        and isinstance(parent_values, Mapping)
        and schema_gap_reason is not None
        and declared_schema_gap(parent_values) == schema_gap_reason
        and "schema_gap" not in selection_payload
    ):
        selection_payload = {**selection_payload, "schema_gap": schema_gap_reason}
    applies = (
        quantity_token is None
        and schema_gap_reason is not None
        and declared_schema_gap(selection_payload) == schema_gap_reason
    )
    return selection_payload, State.unknown(schema_gap_reason) if applies else None


def point_value(
    selector: Any,
    quantity_token: object,
    units: str,
    parent_values: object,
    selection_payload: Mapping[str, Any] | None,
) -> Any:
    if selection_payload is None:
        return selector(quantity_token, units, None)
    selection_payload, gap_quantity = point_schema_gap_context(
        quantity_token, parent_values, selection_payload
    )
    selection_quantity = gap_quantity or quantity_token
    return selector(selection_quantity, units, selection_payload)


def point_context(values: Mapping[str, Any], reason: str | None, transform: Any = None):
    if transform is not None:
        values = transform(values)
    if reason is None:
        return values
    return {**values, _SC475_POINT_GAP_CONTEXT: reason}


def declared_gap_unknown_quantity(
    declared: object, payload: Mapping[str, Any] | None
) -> State | None:
    reason = declared_schema_gap(payload)
    if reason is not None and isinstance(declared, State) and declared.reason == reason:
        return State.unknown(reason)
    return None


def gap_selection(
    selector: Any,
    declared: object,
    payload: Mapping[str, Any],
    condition_ranges: Any,
    unused: Any,
) -> Any:
    quantity = declared_gap_unknown_quantity(declared, payload)
    if quantity is None:
        return None
    return selector(
        quantity.reason,
        condition_ranges=condition_ranges,
        unused_ancillary=unused,
    )


def rejects_admitted_schema_gap(
    quantity_token: object,
    schema_gap_reason: str | None,
    value_kind: str,
    admission_status: str,
) -> bool:
    return (
        quantity_token is None
        and schema_gap_reason is not None
        and value_kind == "unavailable"
        and admission_status == "admitted"
    )


def admission(
    admission: Any,
    quantity_token: object,
    schema_gap_reason: str | None,
    value_kind: str,
    admission_status: str,
    admission_for: Any,
    extraction: Any,
    locator: Any,
) -> Any:
    if not rejects_admitted_schema_gap(
        quantity_token, schema_gap_reason, value_kind, admission_status
    ):
        return admission
    return admission_for(
        "typed_refusal",
        superseded_by=None,
        extraction=extraction,
        locator=locator,
        refusal_reason=schema_gap_reason,
    )


def apply_sc475_quantity_gap(
    quantity: State, reason: str | None, schema_gap: str | None
) -> tuple[State, str | None]:
    if schema_gap is None or reason is None:
        return quantity, reason
    if reason in {
        "missing quantity",
        "source does not state a closed quantity",
        "row type is a curation label, not a statement of the observable",
    } or reason.startswith("unsupported quantity "):
        return State.unknown(schema_gap), schema_gap
    return quantity, reason
