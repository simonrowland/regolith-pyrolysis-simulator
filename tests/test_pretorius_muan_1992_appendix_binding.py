"""Appendix A/C cells must sit on the printed sample, phase, and conditions.

The grid below is an independent transcription of the 600 dpi upright renders
of physical pages 10–13 (folio 1373–1376), cross-read against pdftotext where
the two agreed. It is not read from the extract's context tables.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/pretorius-muan-1992-chromium-oxide-activities.yaml"

def _block(page, samples, time, temperature, container, log_pO2, glass, metal, ses=None):
    rows = []
    for index, sample in enumerate(samples):
        conditions = (time[index], temperature[index], container[index], log_pO2[index])
        for phase, components in (("Glass", glass), ("Metal", metal), ("Ses", ses or {})):
            for component, cells in components.items():
                cell = cells[index]
                if cell is None:
                    continue
                value, sd = cell
                rows.append((page, sample, phase, component, value, sd, *conditions))
    return rows


PAGE_10_BLOCK_1 = _block(
    10,
    ["CCrS 1", "CCrS 5", "CCrS 7", "CCrS 8", "CCrS 11", "CCrS 12", "CCrS 13", "CCrS 14", "CCrS 15", "CCrS 17", "CCrS 18"],
    ["72", "48", "48", "48", "72", "50", "50", "60", "60", "65", "65"],
    ["1499", "1500", "1502", "1502", "1499", "1500", "1500", "1500", "1500", "1499", "1499"],
    ["Mo", "Pt", "SiO2", "Mo", "Pt", "Pt", "Pt", "Mo", "Mo", "Pt", "Pt"],
    ["-9.56"] * 11,
    {
        "SiO2": [("48.64", "0.21"), ("43.11", "0.11"), ("57.12", "0.43"), ("42.48", "0.66"), ("44.81", "0.57"), ("56.77", "0.14"), ("49.15", "0.41"), ("59.31", "0.2"), ("50.25", "0.25"), ("47.87", "0.05"), ("53.48", "0.06")],
        "CaO": [("44.87", "0.37"), ("53.78", "0.32"), ("30.26", "0.7"), ("51.34", "0.33"), ("49.34", "0.71"), ("40.27", "0.27"), ("45.57", "0.22"), ("29.01", "0.16"), ("40.50", "0.3"), ("45.05", "0.09"), ("38.36", "0.18")],
        "Cr2O3": [("6.96", "0.3"), ("1.90", "0.04"), ("12.02", "0.12"), ("4.37", "0.2"), ("4.72", "0.43"), ("3.20", "0.08"), ("5.52", "0.28"), ("11.63", "0.13"), ("8.78", "0.12"), ("6.74", "0.06"), ("8.29", "0.1")],
    },
    {
        "Pt": [None, ("93.39", "0.85"), None, None, ("94.95", "0.98"), ("93.96", "0.64"), None, None, None, ("93.84", "0.9"), ("93.40", "0.64")],
        "Cr": [None, ("6.27", "0.15"), None, None, ("5.49", "0.27"), ("7.57", "0.1"), None, None, None, ("7.80", "0.2"), ("7.18", "0.19")],
    },
)

PAGE_10_BLOCK_2 = _block(
    10,
    ["CCrS 23", "CCrS 1", "CCrS 1a", "CCrS 3", "CCrS 5", "CCrS 5", "CCrS 6", "CCrS 7", "CCrS 8", "CCrS 8"],
    ["48", "48", "48", "48", "48", "48", "48", "60", "60", "49"],
    ["1502", "1502", "1502", "1502", "1499", "1502", "1500", "1501", "1501", "1498"],
    ["Pt", "SiO2", "Pt", "Pt", "Pt", "Mo", "Mo", "SiO2", "Mo", "Pt"],
    ["-9.56", "-11.50", "-11.50", "-11.50", "-11.50", "-11.50", "-11.50", "-11.50", "-11.50", "-11.50"],
    {
        "SiO2": [("52.76", "0.13"), ("52.89", "0.13"), ("43.67", "0.16"), ("43.04", "0.15"), ("43.48", "0.21"), ("42.64", "0.13"), ("61.16", "0.37"), ("53.52", "0.28"), ("42.26", "0.13"), ("43.13", "0.17")],
        "CaO": [("36.28", "0.37"), ("16.75", "0.07"), ("43.38", "0.24"), ("42.88", "0.11"), ("55.14", "0.33"), ("51.62", "0.27"), ("31.66", "0.41"), ("17.14", "0.21"), ("47.52", "0.25"), ("52.95", "0.13")],
        "Cr2O3": [("8.92", "0.16"), ("30.99", "0.32"), ("12.59", "0.23"), ("12.70", "0.09"), ("0.67", "0.03"), ("5.91", "0.07"), ("7.17", "0.11"), ("30.31", "0.27"), ("10.24", "0.09"), ("3.25", "0.05")],
    },
    {
        "Pt": [("91.02", "0.62"), None, None, None, ("90.26", "0.9"), ("87.07", "0.63"), ("90.06", "0.75"), None, None, ("88.41", "0.8")],
        "Cr": [("7.57", "0.16"), None, None, None, ("8.94", "0.28"), ("14.66", "0.33"), ("11.87", "0.25"), None, None, ("13.44", "0.16")],
    },
)

PAGE_11 = _block(
    11,
    ["CCrS 14", "CCrS 16", "CCrS 18", "CCrS 20", "CCrS 21", "CCrS 26", "CCrS 27", "CCrS 28A", "CCrS 28B", "CCrS 28"],
    ["65", "49", "48", "48", "48", "48", "48", "48", "48", "48"],
    ["1502", "1498", "1502", "1501", "1501", "1498", "1500", "1500", "1500", "1500"],
    ["Mo", "Mo", "Mo", "Mo", "Mo", "Mo", "SiO2", "Mo", "Mo", "SiO2"],
    ["-11.50", "-11.50", "-11.50", "-12.50", "-12.50", "-12.50", "-12.50", "-12.50", "-12.50", "-12.50"],
    {
        "SiO2": [("49.29", "0.16"), ("45.39", "0.18"), ("52.79", "0.11"), ("42.55", "0.07"), ("41.02", "0.21"), ("41.98", "0.51"), ("47.37", "0.87"), ("48.06", "0.2"), ("44.95", "0.18"), ("45.54", "0.82")],
        "CaO": [("26.21", "0.26"), ("38.21", "0.24"), ("37.81", "0.21"), ("33.68", "0.3"), ("46.72", "0.25"), ("44.46", "0.29"), ("5.43", "0.36"), ("8.42", "0.13"), ("8.23", "0.12"), ("5.38", "0.07")],
        "Cr2O3": [("25.85", "0.23"), ("17.03", "0.07"), ("8.95", "0.16"), ("24.72", "0.29"), ("12.58", "0.1"), ("13.28", "0.29"), ("49.10", "0.35"), ("45.86", "0.32"), ("47.01", "0.23"), ("49.10", "0.23")],
    },
    {
        "Pt": [None, None, ("88.93", "0.77"), ("72.63", "0.69"), ("74.03", "0.83"), ("70.68", "0.89"), ("72.43", "0.41"), ("73.74", "0.44"), None, None],
        "Cr": [None, None, ("13.23", "0.29"), ("27.44", "0.29"), ("26.29", "0.3"), ("28.35", "0.14"), ("27.28", "0.31"), ("26.35", "0.18"), None, None],
    },
)

PAGE_12 = _block(
    12,
    ["CACrS 4", "CACrS 7", "CACrS 8", "CACrS 22", "CACrS 23", "CACrS 24", "CACrS 16", "CACrS 18", "CACrS 19", "CACrS 20"],
    ["48", "48", "48", "48", "48", "48", "52", "52", "52", "48"],
    ["1499", "1502", "1499", "1500", "1500", "1499", "1502", "1502", "1502", "1502"],
    ["Mo"] * 10,
    ["-11.50"] * 10,
    {
        "SiO2": [("54.24", "0.18"), ("56.96", "0.46"), ("47.02", "0.20"), ("45.94", "0.13"), ("39.01", "0.13"), ("42.75", "0.14"), ("42.87", "0.15"), ("39.05", "0.20"), ("36.05", "0.18"), ("49.16", "0.19")],
        "Al2O3": [("12.01", "0.07"), ("10.58", "0.10"), ("10.54", "0.04"), ("11.50", "0.07"), ("9.40", "0.08"), ("8.68", "0.13"), ("19.50", "0.15"), ("18.93", "0.13"), ("17.12", "0.24"), ("19.06", "0.13")],
        "CaO": [("13.32", "0.1"), ("12.69", "0.27"), ("25.75", "0.10"), ("26.00", "0.30"), ("42.02", "0.31"), ("35.26", "0.07"), ("22.23", "0.11"), ("30.10", "0.23"), ("35.96", "0.21"), ("9.79", "0.11")],
        "Cr2O3": [("20.63", "0.1"), ("20.79", "0.20"), ("17.24", "0.16"), ("17.22", "0.25"), ("9.97", "0.08"), ("13.09", "0.21"), ("13.97", "0.06"), ("10.69", "0.11"), ("9.16", "0.13"), ("20.50", "0.13")],
    },
    {
        "Pt": [("83.12", "0.79"), ("83.67", "0.38"), ("82.32", "0.89"), ("82.78", "0.72"), ("82.61", "0.49"), ("82.84", "0.7"), ("83.68", "0.42"), ("84.28", "0.48"), ("83.62", "0.73"), ("83.94", "0.9")],
        "Cr": [("16.44", "0.18"), ("17.00", "0.18"), ("16.40", "0.19"), ("16.40", "0.3"), ("16.55", "0.21"), ("16.28", "0.33"), ("16.08", "0.27"), ("16.03", "0.21"), ("17.04", "0.34"), ("16.99", "0.32")],
    },
    {
        "Al2O3": [("4.46", "0.04"), ("3.39", "0.09"), ("4.25", "0.03"), ("3.97", "0.03"), ("4.70", "0.06"), ("4.98", "0.13"), ("9.02", "0.23"), ("9.51", "0.13"), ("9.48", "0.16"), ("10.98", "0.32")],
        "Cr2O3": [("93.38", "0.55"), ("95.47", "0.5"), ("92.79", "0.54"), ("96.04", "0.8"), ("95.41", "0.25"), ("93.42", "0.31"), ("89.95", "0.77"), ("88.26", "0.42"), ("88.98", "0.5"), ("88.78", "0.42")],
    },
)

PAGE_13 = _block(
    13,
    ["CACrS 21", "CACrS 7", "CACrS 22", "CACrS 23", "CACrS 24", "CACrS 16", "CACrS 17", "CACrS 18", "CACrS 19", "CACrS 20"],
    ["48", "48", "48", "48", "48", "48", "48", "48", "48", "49"],
    ["1502", "1501", "1501", "1501", "1501", "1498", "1498", "1502", "1502", "1502"],
    ["Mo"] * 10,
    ["-11.50", "-12.58", "-12.58", "-12.58", "-12.58", "-12.58", "-12.58", "-12.58", "-12.58", "-12.58"],
    {
        "SiO2": [("53.43", "0.23"), ("45.85", "0.32"), ("42.85", "0.42"), ("38.68", "0.23"), ("39.09", "0.26"), ("36.87", "0.35"), ("37.41", "0.15"), ("35.18", "0.22"), ("33.96", "0.23"), ("40.38", "0.28")],
        "Al2O3": [("20.23", "0.16"), ("10.12", "0.21"), ("9.89", "0.17"), ("9.32", "0.19"), ("10.62", "0.17"), ("20.95", "0.16"), ("21.00", "0.27"), ("19.32", "0.13"), ("19.82", "0.11"), ("21.05", "0.2")],
        "CaO": [("4.01", "0.12"), ("14.21", "0.17"), ("21.42", "0.31"), ("40.61", "0.37"), ("31.25", "0.12"), ("19.46", "0.27"), ("18.14", "0.08"), ("27.21", "0.7"), ("35.65", "0.42"), ("10.90", "0.32")],
        "Cr2O3": [("21.96", "0.26"), ("30.11", "0.26"), ("26.11", "0.21"), ("12.10", "0.21"), ("18.80", "0.21"), ("23.17", "0.27"), ("23.14", "0.21"), ("17.95", "0.27"), ("9.96", "0.31"), ("28.10", "0.3")],
    },
    {
        "Pt": [("83.43", "0.88"), ("71.41", "0.65"), ("71.22", "0.93"), ("71.67", "0.98"), ("72.12", "0.76"), ("72.32", "0.95"), ("72.08", "0.89"), ("71.48", "0.47"), ("72.21", "0.66"), ("72.45", "0.43")],
        "Cr": [("16.98", "0.14"), ("28.24", "0.13"), ("28.10", "0.27"), ("28.22", "0.25"), ("28.05", "0.24"), ("27.64", "0.3"), ("27.36", "0.32"), ("27.38", "0.16"), ("27.10", "0.19"), ("27.28", "0.22")],
    },
    {
        "Al2O3": [("15.71", "0.41"), ("4.52", "0.49"), ("4.62", "0.34"), ("5.02", "0.37"), ("4.71", "0.62"), ("13.70", "0.65"), ("13.90", "0.61"), ("12.75", "0.23"), ("12.98", "0.34"), ("19.50", "0.56")],
        "Cr2O3": [("82.17", "0.77"), ("94.32", "0.93"), ("93.75", "0.87"), ("93.20", "0.83"), ("93.21", "0.88"), ("86.34", "0.98"), ("86.12", "0.89"), ("88.20", "0.39"), ("86.58", "0.56"), ("80.03", "0.45")],
    },
)

APPENDIX_GRID = PAGE_10_BLOCK_1 + PAGE_10_BLOCK_2 + PAGE_11 + PAGE_12 + PAGE_13


def _container(raw: object) -> str:
    text = str(raw).replace("$", "").replace("\\", "").replace("{", "").replace("}", "").replace("_", "").replace(" ", "")
    if text.lower() in {"sio2", "sio"}:
        return "SiO2"
    return text


def _decimal(raw: object) -> Decimal:
    return Decimal(str(raw).replace("−", "-").replace("–", "-"))


def _typed_rows(document):
    rows = []
    for species, block in document["species"].items():
        for obs in block.get("observations") or []:
            locator = obs.get("locator") or {}
            if locator.get("appendix") not in {"Appendix A", "Appendix C"}:
                continue
            values = obs["values"]
            point = obs["point_conditions"]
            rows.append({
                "species": species,
                "id": obs["observation_id"],
                "page": locator.get("page"),
                "sample": point["sample_id"]["state"]["value"],
                "phase": obs.get("phase_label_as_published"),
                "phase_token": obs.get("phase"),
                "phase_as_published": obs.get("phase_as_published"),
                "component": values["component_as_published"],
                "value": values["value"],
                "sd": (obs.get("uncertainty") or {}).get("as_published"),
                "T": str(obs.get("T_C")),
                "log": point["fO2_log"]["state"]["value"],
                "time": str(values.get("time_h_as_published")),
                "container": _container(values.get("container_as_published")),
            })
    return rows


def _key(page, sample, phase, component, value, sd, time, temperature, container, log_pO2):
    return (
        page,
        sample,
        phase,
        component,
        _decimal(value),
        _decimal(sd),
        str(time),
        str(temperature),
        _container(container),
        str(log_pO2),
    )


def test_every_appendix_cell_is_bound_to_its_printed_sample():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    typed = _typed_rows(document)
    expected = [_key(*row) for row in APPENDIX_GRID]
    found = [
        _key(row["page"], row["sample"], row["phase"], row["component"], row["value"], row["sd"], row["time"], row["T"], row["container"], row["log"])
        for row in typed
    ]
    assert sorted(found) == sorted(expected)

    by_component = {"SiO2": "SiO2", "CaO": "CaO", "Cr2O3": "Cr2O3", "Al2O3": "Al2O3", "Pt": "Pt", "Cr": "Cr"}
    for row in typed:
        assert row["species"] == by_component[row["component"]]
        if row["phase"] == "Glass":
            assert row["phase_token"] == "glass"
        elif row["phase"] == "Ses":
            assert row["phase_token"] == "cr"
        elif row["phase"] == "Metal":
            assert row["phase_token"] in (None, "")
            assert row["phase_as_published"] == "Metal"
        else:
            raise AssertionError(row["phase"])

    # 7.57 (0.16) is CCrS 23, not a second copy of a CCrS 5 cell. Page 10
    # still prints three different CCrS 5 metal-Cr cells.
    metal_cr = [
        row for row in typed
        if row["page"] == 10 and row["phase"] == "Metal" and row["component"] == "Cr" and row["sample"] == "CCrS 5"
    ]
    assert sorted((row["value"], row["sd"], row["T"], row["log"], row["container"]) for row in metal_cr) == [
        ("14.66", "0.33", "1502", "-11.50", "Mo"),
        ("6.27", "0.15", "1500", "-9.56", "Pt"),
        ("8.94", "0.28", "1499", "-11.50", "Pt"),
    ]
    ccrs23 = [
        row for row in typed
        if row["sample"] == "CCrS 23" and row["phase"] == "Metal" and row["component"] == "Cr"
    ]
    assert [(row["value"], row["sd"], row["T"], row["log"]) for row in ccrs23] == [
        ("7.57", "0.16", "1502", "-9.56"),
    ]


def test_scaled_activities_are_the_printed_decimal():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    activities = []
    for block in document["species"].values():
        for obs in block.get("observations") or []:
            values = obs.get("values") or {}
            if values.get("quantity") == "activity" and "activity" in values:
                activities.append(values["activity"])
                published = str(values.get("activity_as_published") or "")
                if "0.765" in published:
                    assert values["activity"] == "0.000765"
                elif "1.29" in published and "10^-3" in published:
                    assert values["activity"] == "0.00129"
                elif "19.9" in published:
                    assert values["activity"] == "0.0199"
                elif "5.11" in published:
                    assert values["activity"] == "0.0511"
    assert "0.0007650000000000001" not in activities
    assert "0.0012900000000000001" not in activities
    assert "0.019899999999999998" not in activities
    assert "0.051100000000000007" not in activities
