"""Independent expectations for OpenIMCC's shared redox extrapolation notice."""

from __future__ import annotations

from collections.abc import Iterable, Mapping


def expected_temperature_extrapolation(
    composition_mol: Mapping[str, float], temperature_K: float
) -> tuple[list[str], list[str]]:
    """Derive affected traces and extrapolated active rows from the pinned pack."""

    from openimcc.redox_pack import load_redox_pack
    from openimcc.trace_components import trace_component_rule

    redox_pack = load_redox_pack()
    active_parent_oxides = {
        str(parent["parent_oxide"]).removesuffix("(l)")
        for parent in redox_pack.parents
        if float(composition_mol.get(
            str(parent["parent_oxide"]).removesuffix("(l)"), 0.0
        )) > 0.0
    }
    active_rows = []
    for row in redox_pack.rows:
        if row.get("role") == "parent_reference":
            product = str(row["complex"]).removesuffix("(l)")
            active = product in active_parent_oxides
        else:
            active = all(
                float(amount) <= 0.0
                or str(parent).removesuffix("(l)") in active_parent_oxides
                for parent, amount in row.get("nu", {}).items()
            )
        if active:
            active_rows.append(row)

    extrapolated_rows = sorted(
        str(row["complex"])
        for row in active_rows
        if not float(row["T_domain_K"][0])
        <= temperature_K
        <= float(row["T_domain_K"][1])
    )
    # CrO(l)-effective spans 1673-1773 K and is outside its band at 1800,
    # 1950.1, and 2073.15 K. MnO rows span 1673-2000 K and are outside only
    # at 2073.15 K. The redox solve is coupled, so every present redox
    # associate trace is affected whenever any active row is extrapolated.
    affected_components = sorted(
        oxide
        for oxide, amount in composition_mol.items()
        if float(amount) > 0.0
        and (rule := trace_component_rule(oxide)) is not None
        and rule.activity_model == "redox_associates"
    ) if extrapolated_rows else []
    return affected_components, extrapolated_rows


def assert_temperature_extrapolation_notice(
    notices: Iterable[str],
    composition_mol: Mapping[str, float],
    temperature_K: float,
) -> None:
    """Compare notice labels to pack-derived components and active row domains."""

    expected_components, expected_rows = expected_temperature_extrapolation(
        composition_mol, temperature_K
    )
    assert expected_components, "composition must include redox-associate traces"
    assert expected_rows, "temperature must extrapolate an active redox row"
    matching = [
        value
        for value in notices
        if isinstance(value, str)
        and value.startswith(
            "openimcc_trace_effect:temperature_extrapolation:"
        )
    ]
    assert len(matching) == 1
    notice = matching[0]
    assert (
        f"affected_components={','.join(expected_components)}:"
        in notice
    )
    assert f"extrapolated_rows={','.join(expected_rows)}:" in notice
