"""Source-owned declarations for narrowly scoped reader behaviour."""

from dataclasses import dataclass

from simulator.battery.enums import Phase


@dataclass(frozen=True)
class SourceRule:
    value: bool | Phase
    reason: str


SOURCE_DECLARATIONS: dict[str, dict[str, SourceRule]] = {
    "pankratz-1984-usbm-b677": {
        "ideal_gas_enthalpy_zero": SourceRule(
            True,
            "B677 ideal-gas tables define H298 against that gas when the record heading and zero cell say ideal gas",
        ),
    },
    "kems-015-hashimoto-1983": {
        "residue_component_phase": SourceRule(
            Phase.L,
            "Hashimoto Table 3 identifies the recovered spherule as the run-end silicate melt",
        ),
    },
    "kems-012-sossi-2019": {
        "residue_component_phase": SourceRule(
            Phase.GLASS,
            "Sossi Table 2 reports residue ppm in quenched glass",
        ),
    },
    "nist-janaf-4th": {
        "reaction_decimal_subscripts": SourceRule(
            True,
            "JANAF decimal subscripts are not dotted hydrate notation",
        ),
    },
}
