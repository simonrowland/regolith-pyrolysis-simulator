"""write=False residue migrations match green a822c275f.

The temp-root setup copies tests/battery/test_migrate.py::_migrate_real_extract
(original extract text, one INDEX row, migrate(root, write=False)). Importing
that module would load the whole migration suite into the green subprocess.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
GREEN = "a822c275f29fe3e16fad41f4ccfe16606c01aa73"
SOURCES = (
    "ivanova-2018-spinel-rich-cai-evaporation",
    "kems-026-markova-1984",
    "kems-015-hashimoto-1983",
    "kems-012-sossi-2019",
    "kems-024-gibson-hubbard-1972",
    "kang-2007-sio2-activity-cas-low-silica",
)


def _dec(value: object) -> str | None:
    if value is None:
        return None
    number = value if isinstance(value, Decimal) else Decimal(str(value))
    return format(number.normalize(), "f")


def _raw_dec(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _pairs(rows: object, *, raw: bool) -> list[list[str | None]] | None:
    if rows is None:
        return None
    cast = _raw_dec if raw else _dec
    return [[cast(left), cast(right)] for left, right in rows]


def fingerprint(result) -> list[dict[str, object]]:
    rows = []
    for obs in result.observations.values():
        quantity = obs.identity.quantity
        if quantity.is_value:
            quantity_label = quantity.value.value
        else:
            quantity_label = f"{quantity.tag.value}:{quantity.reason}"
        value = obs.value
        numeric = {
            "kind": value.kind.value,
            "point": _dec(value.point),
            "series": _pairs(value.series, raw=False),
            "bound_operator": value.bound_operator,
            "bound_value": _dec(value.bound_value),
            "interval_low": _dec(value.interval_low),
            "interval_high": _dec(value.interval_high),
            "ordering": list(value.ordering) if value.ordering else None,
            "categorical": value.categorical,
            "relative_series": _pairs(value.relative_series, raw=False),
            "relative_normalization": value.relative_normalization,
            "expression_text": value.expression_text,
            "expression_parameters": [
                [name, _dec(amount)] for name, amount in value.expression_parameters
            ]
            if value.expression_parameters
            else None,
            "expression_domain": value.expression_domain,
            "unavailable_reason": value.unavailable_reason,
            "approximate": value.approximate,
        }
        text = {
            "point": _raw_dec(value.point),
            "series": _pairs(value.series, raw=True),
            "bound_value": _raw_dec(value.bound_value),
            "interval_low": _raw_dec(value.interval_low),
            "interval_high": _raw_dec(value.interval_high),
            "relative_series": _pairs(value.relative_series, raw=True),
            "expression_parameters": [
                [name, _raw_dec(amount)]
                for name, amount in value.expression_parameters
            ]
            if value.expression_parameters
            else None,
        }
        rows.append(
            {
                "id": obs.observation_id,
                "quantity": quantity_label,
                "species": obs.identity.species.formula,
                "value": numeric,
                "value_text": text,
                "admission": obs.admission.status.value,
                "admission_reason": obs.admission.reason,
            }
        )
    rows.sort(key=lambda row: str(row["id"]))
    return rows


def migrate_sources(root_base: Path) -> dict[str, list[dict[str, object]]]:
    from simulator.battery.migrate import migrate

    out: dict[str, list[dict[str, object]]] = {}
    for source_id in SOURCES:
        name = f"{source_id}.yaml"
        src = REPO / "data" / "literature" / "extracts" / name
        root = root_base / source_id
        extracts = root / "data" / "literature" / "extracts"
        extracts.mkdir(parents=True)
        (root / "data" / "literature" / "compilations").mkdir(parents=True)
        doc = yaml.safe_load(src.read_text(encoding="utf-8"))
        source = doc.get("source") if isinstance(doc.get("source"), dict) else {}
        index = {
            "schema_version": "literature_index.v1",
            "sources": [
                {
                    "source_id": source_id,
                    "citation": source.get("citation") or source_id,
                    "doi": source.get("doi"),
                }
            ],
        }
        (root / "data" / "literature" / "INDEX.yaml").write_text(
            yaml.safe_dump(index, sort_keys=False),
            encoding="utf-8",
        )
        (extracts / name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        out[source_id] = fingerprint(migrate(root, write=False))
    return out


def _install_green_migrate(path: str) -> None:
    from importlib.machinery import SourceFileLoader
    from importlib.util import spec_from_loader

    class Finder:
        def find_spec(self, fullname, search_path, target=None):
            if fullname != "simulator.battery.migrate":
                return None
            loader = SourceFileLoader(fullname, path)
            return spec_from_loader(fullname, loader)

    # The finder object must be an instance. Inserting the class makes
    # find_spec bind the module name to self, return None, and both sides
    # of the diff run the worktree migrator.
    sys.meta_path.insert(0, Finder())


def _rebind_green_paths() -> None:
    import simulator.battery.migrate as migrate

    migrate.REPO_ROOT = REPO
    migrate.LITERATURE = REPO / "data" / "literature"
    migrate.EXTRACTS_DIR = migrate.LITERATURE / "extracts"
    migrate.COMPILATIONS_DIR = migrate.LITERATURE / "compilations"
    migrate.INDEX_PATH = migrate.LITERATURE / "INDEX.yaml"
    migrate.WORKS_DIR = migrate.LITERATURE / "works"
    migrate.EXTRACTS_V2_DIR = migrate.LITERATURE / "extracts-v2"
    migrate.OBSERVATIONS_V2_DIR = migrate.LITERATURE / "observations-v2"
    migrate.BATTERY_DIR = REPO / "data" / "battery"
    migrate.LAB_PARAMETER_VOCAB_PATH = (
        migrate.LITERATURE / "lab_parameter_vocabulary.yaml"
    )


def _without_text(row: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in row.items() if key != "value_text"}


def _brief(row: dict[str, object]) -> str:
    value = row["value"]
    point = value.get("point") if isinstance(value, dict) else None
    kind = value.get("kind") if isinstance(value, dict) else None
    return (
        f"quantity={row['quantity']} species={row['species']} "
        f"kind={kind} point={point} admission={row['admission']} "
        f"reason={row['admission_reason']}"
    )


def diff_payloads(
    tip: dict[str, list[dict[str, object]]],
    green: dict[str, list[dict[str, object]]],
) -> dict[str, object]:
    report: dict[str, object] = {}
    real_total = 0
    text_total = 0
    first: str | None = None
    for source_id in SOURCES:
        tip_rows = {str(row["id"]): row for row in tip[source_id]}
        green_rows = {str(row["id"]): row for row in green[source_id]}
        only_tip = sorted(set(tip_rows) - set(green_rows))
        only_green = sorted(set(green_rows) - set(tip_rows))
        real = []
        text_only = []
        for obs_id in sorted(set(tip_rows) & set(green_rows)):
            left = tip_rows[obs_id]
            right = green_rows[obs_id]
            if _without_text(left) != _without_text(right):
                real.append(obs_id)
                if first is None:
                    first = (
                        f"{source_id} {obs_id}\n"
                        f"tip   {_brief(left)}\n"
                        f"green {_brief(right)}"
                    )
            elif left["value_text"] != right["value_text"]:
                text_only.append(obs_id)
        real_total += len(real) + len(only_tip) + len(only_green)
        text_total += len(text_only)
        if first is None and only_tip:
            missing = tip_rows[only_tip[0]]
            first = f"{source_id} only tip {only_tip[0]}\n{_brief(missing)}"
        elif first is None and only_green:
            missing = green_rows[only_green[0]]
            first = f"{source_id} only green {only_green[0]}\n{_brief(missing)}"
        report[source_id] = {
            "tip": len(tip_rows),
            "green": len(green_rows),
            "only_tip": only_tip[:8],
            "only_green": only_green[:8],
            "real": real[:8],
            "decimal_text_only": len(text_only),
        }
    report["real_total"] = real_total
    report["decimal_text_only_total"] = text_total
    report["first"] = first
    return report


def test_printed_residue_migrations_match_green_a822c275f(tmp_path: Path) -> None:
    green_src = tmp_path / "green-migrate.py"
    with green_src.open("w") as handle:
        subprocess.run(
            ["git", "show", f"{GREEN}:simulator/battery/migrate.py"],
            cwd=REPO,
            check=True,
            stdout=handle,
        )
    tip = migrate_sources(tmp_path / "tip")
    green_out = tmp_path / "green.json"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO)
    env["PARITY_GREEN_SRC"] = str(green_src)
    env["PARITY_OUT"] = str(green_out)
    env["PARITY_ROOT"] = str(tmp_path / "green")
    subprocess.run(
        [sys.executable, str(Path(__file__).resolve())],
        cwd=REPO,
        env=env,
        check=True,
    )
    green_payload = json.loads(green_out.read_text(encoding="utf-8"))
    loaded = Path(str(green_payload["loaded_migrate"])).resolve()
    assert loaded == green_src.resolve(), loaded
    report = diff_payloads(tip, green_payload["sources"])
    assert report["real_total"] == 0, report.get("first") or report
    assert report["decimal_text_only_total"] == 0, report


if __name__ == "__main__":
    green_src = Path(os.environ["PARITY_GREEN_SRC"]).resolve()
    _install_green_migrate(str(green_src))
    _rebind_green_paths()
    import simulator.battery.migrate as migrate

    loaded = Path(migrate.__file__).resolve()
    if loaded != green_src:
        raise SystemExit(f"green migrate was not loaded: {loaded}")
    payload = {
        "loaded_migrate": str(loaded),
        "sources": migrate_sources(Path(os.environ["PARITY_ROOT"])),
    }
    Path(os.environ["PARITY_OUT"]).write_text(json.dumps(payload), encoding="utf-8")
