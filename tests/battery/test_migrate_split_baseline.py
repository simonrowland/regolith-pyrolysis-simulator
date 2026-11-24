"""Migration pins: representative source rows and byte-exact whole-store output.

The representative pins exercise individual source shapes through
``migrate(..., write=False)`` without ``observation_from_plain`` or
``load_migrated_store``. The whole-store pin runs the complete migration and
compares the writer's complete output map against the checked-in derived store.
"""

from __future__ import annotations

import base64
import json
import re
import zlib
from pathlib import Path
from unittest.mock import patch

import yaml

from simulator.battery.migrate import (
    Migrator,
    REPO_ROOT,
    _store_output_file_bytes,
    _store_regeneration_differences,
    migrate,
    to_plain,
    write_outputs,
)


_EXTRACT_ROWS = (
    ("kems-022-demaria-1971.yaml", "demaria_1971_al2o_psat_table4"),
    ("kems-022-demaria-1971.yaml", "demaria_1971_o2_psat_table1_alkali_range"),
    ("kems-042-plante-1979.yaml", "plante1979_table2_k2o_s1115_000_1259K"),
    ("kems-042-plante-1979.yaml", "plante1979_table2_s1214_r037_quoted"),
    ("cardiff-2007-vacuum-pyrolysis-gsfc.yaml", "cardiff_2007_table1_fresnel_pyrolysis_log"),
    ("cardiff-2007-vacuum-pyrolysis-gsfc.yaml", "cardiff_2007_fig6_hsc_1e-1_Torr_figure_only"),
    ("cardiff-2007-vacuum-pyrolysis-gsfc.yaml", "cardiff_2007_fig7_hsc_1e-7_Torr_figure_only"),
    (
        "kems-026-markova-1984.yaml",
        "markova_1984_table2_residual_melt_VI_lherzolite",
        "::mass_loss_fraction::T=2156.15:h=e9110f31a14b",
    ),
)
_COMPILATION = (
    "data/literature/compilations/robie-hemingway-fisher-1978-usgs-b1452/"
    "records/robie-hemingway-fisher-1978-usgs-b1452-0046-phase-04.json"
)
_FAST_SAFE_YAML_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


def _one_extract_row(tmp_path: Path, filename: str, observation_id: str):
    source_path = REPO_ROOT / "data/literature" / "extracts" / filename
    document = yaml.load(
        source_path.read_text(encoding="utf-8"), Loader=_FAST_SAFE_YAML_LOADER
    )
    matches = []
    species = {}
    for formula, payload in document["species"].items():
        observations = payload.get("observations", [])
        selected = [row for row in observations if row.get("observation_id") == observation_id]
        if selected:
            matches.extend(selected)
            species[formula] = {**payload, "observations": selected}
    assert len(matches) == 1, observation_id
    document["species"] = species

    root = tmp_path / observation_id
    literature = root / "data" / "literature"
    extracts = literature / "extracts"
    extracts.mkdir(parents=True)
    (literature / "compilations").mkdir()
    (extracts / filename).write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    source = document.get("source") or {}
    (literature / "INDEX.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "literature_index.v1",
                "sources": [{
                    "source_id": document["source_id"],
                    "citation": source.get("citation") or document["source_id"],
                    "doi": source.get("doi"),
                }],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return migrate(root, write=False), f"{document['source_id']}::{observation_id}"


def _one_compilation_table(tmp_path: Path):
    source_path = REPO_ROOT / _COMPILATION
    root = tmp_path / "b1452"
    records = root / "data" / "literature" / "compilations" / "b1452" / "records"
    records.mkdir(parents=True)
    (records / source_path.name).write_bytes(source_path.read_bytes())
    migrator = Migrator(root, index={})
    migrator.migrate_compilations(records)
    result = migrator.result
    observation_id = sorted(result.observations)[0]
    return result, observation_id


def _state_output(state: object, axis: str) -> object:
    plain = to_plain(state)
    if isinstance(plain, dict) and plain.get("tag") == "not_applicable":
        return {"tag": "not_applicable"}
    if (
        axis == "composition"
        and isinstance(plain, dict)
        and plain.get("tag") == "value"
        and isinstance(plain.get("value"), dict)
    ):
        composition = plain["value"]
        plain["value"] = {
            key: composition.get(key)
            for key in (
                "amount_basis",
                "basis",
                "components",
                "proxy_flag",
                "proxy_source",
                "analysis_selection_rule",
                "input_provenance",
            )
        }
    return plain


def _pinned_output(result, observation_id: str) -> dict[str, object]:
    observation = result.observations[observation_id]
    queued = [entry for entry in result.queue if entry.observation_id == observation_id]
    identity = {
        key: _state_output(value, key)
        for key, value in to_plain(observation.identity).items()
    }

    point_conditions = (
        {
            key: {
                "state": _state_output(value.state, key),
                "locator": to_plain(value.locator),
                **({"inference": to_plain(value.inference)} if value.inference else {}),
            }
            for key, value in sorted(observation.point_conditions.items())
        }
        if observation.point_conditions is not None
        else None
    )
    return {
        "identity": identity,
        "point_conditions": point_conditions,
        "evidence_class": to_plain(observation.evidence.class_),
        "admission_status": observation.admission.status.value,
        "locator": to_plain(observation.locator),
        "queue": {
            "queued": bool(queued),
            "why": [entry.why for entry in queued] if queued else ["no queue entry"],
        },
    }


def _observation_rows(output: bytes) -> dict[str, dict[str, object]]:
    document = yaml.load(output, Loader=_FAST_SAFE_YAML_LOADER)
    if not isinstance(document, dict) or not isinstance(document.get("observations"), list):
        return {}
    return {
        str(row["observation_id"]): row
        for row in document["observations"]
        if isinstance(row, dict) and row.get("observation_id") is not None
    }


def _field_differences(path: str, stored: object, regenerated: object) -> list[str]:
    if isinstance(stored, dict) and isinstance(regenerated, dict):
        differences = []
        for key in sorted(stored.keys() | regenerated.keys(), key=str):
            if key not in stored or key not in regenerated:
                old = stored[key] if key in stored else "<absent>"
                new = regenerated[key] if key in regenerated else "<absent>"
                differences.append(
                    f"{path}.{key}: stored={json.dumps(old, sort_keys=True, default=str)}; "
                    f"regenerated={json.dumps(new, sort_keys=True, default=str)}"
                )
                continue
            differences.extend(_field_differences(
                f"{path}.{key}", stored[key], regenerated[key],
            ))
        return differences
    if isinstance(stored, list) and isinstance(regenerated, list):
        if len(stored) != len(regenerated):
            return [
                f"{path}: stored={json.dumps(stored, sort_keys=True, default=str)}; "
                f"regenerated={json.dumps(regenerated, sort_keys=True, default=str)}"
            ]
        differences = []
        for index, (old, new) in enumerate(zip(stored, regenerated)):
            differences.extend(_field_differences(f"{path}[{index}]", old, new))
        return differences
    if stored == regenerated:
        return []
    return [
        f"{path}: stored={json.dumps(stored, sort_keys=True, default=str)}; "
        f"regenerated={json.dumps(regenerated, sort_keys=True, default=str)}"
    ]


def _compact_difference_paths(differences: list[str], prefix: str) -> str:
    fields = [difference.split(": stored=", 1)[0] for difference in differences]
    indexed: dict[str, list[int]] = {}
    compact = []
    marker = f"{prefix}.experiments["
    for field in fields:
        if not field.startswith(marker):
            compact.append(field.removeprefix(f"{prefix}."))
            continue
        closing = field.find("]", len(marker))
        if closing == -1:
            compact.append(field.removeprefix(f"{prefix}."))
            continue
        try:
            index = int(field[len(marker):closing])
        except ValueError:
            compact.append(field.removeprefix(f"{prefix}."))
            continue
        suffix = field[closing + 1:]
        indexed.setdefault(suffix, []).append(index)
    for suffix, indices in sorted(indexed.items()):
        ordered = sorted(set(indices))
        ranges = []
        start = previous = ordered[0]
        for index in ordered[1:]:
            if index == previous + 1:
                previous = index
                continue
            ranges.append(str(start) if start == previous else f"{start}-{previous}")
            start = previous = index
        ranges.append(str(start) if start == previous else f"{start}-{previous}")
        compact.append(f"experiments[{','.join(ranges)}]{suffix}")
    return ", ".join(compact)


def _compact_observation_ids(observation_ids: list[str]) -> list[dict[str, object]]:
    remaining = set(observation_ids)
    compact = []
    while remaining:
        templates: dict[str, list[tuple[int, str]]] = {}
        for observation_id in sorted(remaining):
            for match in re.finditer(r"\d+", observation_id):
                template = (
                    observation_id[:match.start()]
                    + "{n}"
                    + observation_id[match.end():]
                )
                templates.setdefault(template, []).append((int(match.group()), observation_id))
        candidates = {
            template: members
            for template, members in templates.items()
            if len({observation_id for _, observation_id in members}) > 1
        }
        if not candidates:
            break
        template, members = max(
            candidates.items(),
            key=lambda item: len({observation_id for _, observation_id in item[1]}),
        )
        numbers = sorted({number for number, _ in members})
        ranges = []
        start = previous = numbers[0]
        for number in numbers[1:]:
            if number == previous + 1:
                previous = number
                continue
            ranges.append(str(start) if start == previous else f"{start}-{previous}")
            start = previous = number
        ranges.append(str(start) if start == previous else f"{start}-{previous}")
        compact.append({"template": template, "numbers": ranges})
        remaining.difference_update(observation_id for _, observation_id in members)
    if remaining:
        compact.append({"ids": sorted(remaining)})
    return compact


def _store_difference_details(
    stored: dict[str, bytes], regenerated: dict[str, bytes], paths: list[str]
) -> str:
    details = {
        "changed_store_paths": len(paths),
        "observations": {},
        "work": [],
        "other": [],
    }
    for path in paths:
        stored_bytes = stored.get(path)
        regenerated_bytes = regenerated.get(path)
        if stored_bytes is None or regenerated_bytes is None:
            present_bytes = stored_bytes if stored_bytes is not None else regenerated_bytes
            details["other"].append(
                f"{path}: {'missing from store' if stored_bytes is None else 'unexpected in store'}"
            )
            if present_bytes is not None:
                for observation_id, row in sorted(_observation_rows(present_bytes).items()):
                    field = "row missing from store" if stored_bytes is None else "unexpected row"
                    details["observations"].setdefault(path, {}).setdefault(field, []).append(
                        observation_id
                    )
                details["observations"][path] = {
                    field: _compact_observation_ids(observation_ids)
                    for field, observation_ids in sorted(details["observations"][path].items())
                }
            continue
        if path.endswith(".md"):
            details["other"].append(
                f"{path}: bytes differ ({len(stored_bytes)} stored, "
                f"{len(regenerated_bytes)} regenerated)"
            )
            continue
        if path.startswith("data/literature/works/"):
            stored_work = yaml.load(stored_bytes, Loader=_FAST_SAFE_YAML_LOADER)
            regenerated_work = yaml.load(regenerated_bytes, Loader=_FAST_SAFE_YAML_LOADER)
            differences = _field_differences("work", stored_work, regenerated_work)
            work = stored_work.get("work") if isinstance(stored_work, dict) else None
            if not isinstance(work, dict) and isinstance(regenerated_work, dict):
                work = regenerated_work.get("work")
            source_ids = work.get("source_ids", []) if isinstance(work, dict) else []
            work_id = work.get("work_id") if isinstance(work, dict) else None
            source_label = (
                ",".join(map(str, source_ids))
                if source_ids
                else f"work_id={work_id}"
            )
            details["work"].append({
                "path": path,
                "source": source_label,
                "fields": _compact_difference_paths(differences, "work")
                if differences
                else "bytes differ without a YAML field difference",
            })
            continue
        stored_rows = _observation_rows(stored_bytes)
        regenerated_rows = _observation_rows(regenerated_bytes)
        changed_rows = [
            observation_id
            for observation_id in sorted(stored_rows.keys() | regenerated_rows.keys())
            if stored_rows.get(observation_id) != regenerated_rows.get(observation_id)
        ]
        if changed_rows:
            source_fields = details["observations"].setdefault(path, {})
            for observation_id in changed_rows:
                differences = _field_differences(
                    "observation",
                    stored_rows.get(observation_id),
                    regenerated_rows.get(observation_id),
                )
                source_id = Path(path).stem
                short_id = observation_id.removeprefix(f"{source_id}::")
                fields = differences or ["bytes differ without a YAML field difference"]
                for field in fields:
                    source_fields.setdefault(field, []).append(short_id)
            details["observations"][path] = {
                field: _compact_observation_ids(observation_ids)
                for field, observation_ids in sorted(source_fields.items())
            }
        else:
            details["other"].append(
                f"{path}: bytes differ ({len(stored_bytes)} stored, "
                f"{len(regenerated_bytes)} regenerated); no observation-row difference"
            )
    raw = json.dumps(details, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload = base64.b64encode(zlib.compress(raw, level=9)).decode("ascii")
    return f"store_delta_zlib_base64={payload}"


def test_representative_migration_rows_match_literal_baseline(tmp_path: Path) -> None:
    actual = {}
    for row in _EXTRACT_ROWS:
        filename, observation_id, *child_suffix = row
        result, parent_id = _one_extract_row(tmp_path, filename, observation_id)
        candidates = sorted(
            oid for oid in result.observations
            if oid == parent_id or oid.startswith(f"{parent_id}::")
        )
        assert candidates, parent_id
        migrated_id = (
            parent_id + child_suffix[0]
            if child_suffix
            else candidates[0]
        )
        assert migrated_id in candidates, migrated_id
        actual[migrated_id] = _pinned_output(result, migrated_id)
    result, observation_id = _one_compilation_table(tmp_path)
    actual[observation_id] = _pinned_output(result, observation_id)

    # Replaced with literal typed output from the first Studio run.
    expected = json.loads(r'''
{
  "cardiff-2007-vacuum-pyrolysis-gsfc::cardiff_2007_fig6_hsc_1e-1_Torr_figure_only": {
    "admission_status": "admitted",
    "evidence_class": {
      "tag": "value",
      "value": "model_derived"
    },
    "identity": {
      "quantity": {
        "reason": "unsupported quantity 'equilibrium_composition'",
        "tag": "unknown"
      },
      "species": {
        "charge": {
          "tag": "value",
          "value": 0
        },
        "formula": "MLS-1A",
        "phase": {
          "reason": "phase string 'MLS-1A_HSC_equilibrium' is not in the closed automatic map",
          "tag": "unknown"
        },
        "polymorph": {
          "reason": "phase unknown; polymorph unresolved",
          "rejection_kind": "omitted",
          "tag": "unknown"
        }
      },
      "temperature_K": {
        "reason": "source T_range_K [273.15, 3273.15]; no midpoint invented",
        "tag": "unknown"
      }
    },
    "locator": {
      "figure": "6",
      "note": "Figure 6 Equilibrium composition of MLS-1A at 10^{-1} Torr",
      "pdf_page_index": 6
    },
    "point_conditions": null,
    "queue": {
      "queued": true,
      "why": [
        "unsupported quantity 'equilibrium_composition'",
        "phase string 'MLS-1A_HSC_equilibrium' is not in the closed automatic map",
        "source T_range_K [273.15, 3273.15]; no midpoint invented",
        "regime 'HSC_gibbs_minimization_model' is not a closed method token (kems_effusion alone is insufficient)",
        "derived evidence class with unstated ancestry; queued for page-grounding",
        "derived evidence class with unstated derivation; queued for page-grounding"
      ]
    }
  },
  "cardiff-2007-vacuum-pyrolysis-gsfc::cardiff_2007_fig7_hsc_1e-7_Torr_figure_only": {
    "admission_status": "admitted",
    "evidence_class": {
      "tag": "value",
      "value": "model_derived"
    },
    "identity": {
      "quantity": {
        "reason": "unsupported quantity 'equilibrium_composition'",
        "tag": "unknown"
      },
      "species": {
        "charge": {
          "tag": "value",
          "value": 0
        },
        "formula": "MLS-1A",
        "phase": {
          "reason": "phase string 'MLS-1A_HSC_equilibrium' is not in the closed automatic map",
          "tag": "unknown"
        },
        "polymorph": {
          "reason": "phase unknown; polymorph unresolved",
          "rejection_kind": "omitted",
          "tag": "unknown"
        }
      },
      "temperature_K": {
        "reason": "source T_range_K [273.15, 3273.15]; no midpoint invented",
        "tag": "unknown"
      }
    },
    "locator": {
      "figure": "7",
      "note": "Figure 7 Equilibrium composition of MLS-1A at 10^{-7} Torr",
      "pdf_page_index": 6
    },
    "point_conditions": null,
    "queue": {
      "queued": true,
      "why": [
        "unsupported quantity 'equilibrium_composition'",
        "phase string 'MLS-1A_HSC_equilibrium' is not in the closed automatic map",
        "source T_range_K [273.15, 3273.15]; no midpoint invented",
        "regime 'HSC_gibbs_minimization_model' is not a closed method token (kems_effusion alone is insufficient)",
        "derived evidence class with unstated ancestry; queued for page-grounding",
        "derived evidence class with unstated derivation; queued for page-grounding"
      ]
    }
  },
  "cardiff-2007-vacuum-pyrolysis-gsfc::cardiff_2007_table1_fresnel_pyrolysis_log::T=1345.15:row=mgsio3:h=c69dcb1e3dc7": {
    "admission_status": "admitted",
    "evidence_class": {
      "tag": "value",
      "value": "measured_direct"
    },
    "identity": {
      "composition": {
        "reason": "no composition mapped from source",
        "tag": "unknown"
      },
      "exposure": {
        "reason": "no exposure mapped from source",
        "tag": "unknown"
      },
      "fO2_Pa": {
        "reason": "no fO2_Pa mapped from source",
        "tag": "unknown"
      },
      "formation_elements": {
        "tag": "not_applicable"
      },
      "per": {
        "reason": "no per mapped from source",
        "tag": "unknown"
      },
      "quantity": {
        "tag": "value",
        "value": "mass_loss_fraction"
      },
      "reaction": {
        "tag": "not_applicable"
      },
      "reference_state": {
        "tag": "not_applicable"
      },
      "reservoir": {
        "reason": "no reservoir mapped from source",
        "tag": "unknown"
      },
      "sample_mass_kg": {
        "reason": "no sample_mass_kg mapped from source",
        "tag": "unknown"
      },
      "species": {
        "charge": {
          "tag": "value",
          "value": 0
        },
        "formula": "MgSiO3",
        "phase": {
          "reason": "phase string 'fresnel_lens_vacuum_pyrolysis_mixed_simulants' is not in the closed automatic map",
          "tag": "unknown"
        },
        "polymorph": {
          "reason": "phase unknown; polymorph unresolved",
          "rejection_kind": "omitted",
          "tag": "unknown"
        }
      },
      "standard_pressure_Pa": {
        "tag": "not_applicable"
      },
      "subtype": {
        "tag": "not_applicable"
      },
      "sweep_gas": {
        "reason": "no sweep_gas mapped from source",
        "tag": "unknown"
      },
      "temperature_K": {
        "tag": "value",
        "value": "1345.15"
      },
      "total_pressure_Pa": {
        "reason": "no total_pressure_Pa mapped from source",
        "tag": "unknown"
      },
      "wall": {
        "tag": "not_applicable"
      }
    },
    "locator": {
      "note": "TABLE 1. Results of 12 tests with the prototype system from 2005",
      "pdf_page_index": 3,
      "table": "1"
    },
    "point_conditions": {
      "temperature_K": {
        "inference": {
          "inputs": [
            "T_K = T_C + 273.15",
            "original_unit=C"
          ],
          "output_unit": "K",
          "parameters": [
            [
              "original",
              {
                "locator": {
                  "note": "TABLE 1. Results of 12 tests with the prototype system from 2005",
                  "pdf_page_index": 3,
                  "table": "1"
                },
                "state": {
                  "tag": "value",
                  "value": "1072"
                }
              }
            ],
            [
              "factor",
              {
                "locator": {
                  "note": "TABLE 1. Results of 12 tests with the prototype system from 2005",
                  "pdf_page_index": 3,
                  "table": "1"
                },
                "state": {
                  "tag": "value",
                  "value": "273.15"
                }
              }
            ]
          ],
          "references": [],
          "relation": "celsius_to_kelvin"
        },
        "locator": {
          "note": "TABLE 1. Results of 12 tests with the prototype system from 2005",
          "pdf_page_index": 3,
          "table": "1"
        },
        "state": {
          "tag": "value",
          "value": "1345.15"
        }
      },
      "total_pressure_Pa": {
        "inference": {
          "inputs": [
            "P_Pa = P_Torr × 101325 / 760",
            "original_unit=Torr",
            "as_published=0.23 Torr",
            "printed=P_test_Torr"
          ],
          "output_unit": "Pa",
          "parameters": [
            [
              "original",
              {
                "locator": {
                  "note": "TABLE 1. Results of 12 tests with the prototype system from 2005",
                  "pdf_page_index": 3,
                  "table": "1"
                },
                "state": {
                  "tag": "value",
                  "value": "0.23"
                }
              }
            ],
            [
              "factor",
              {
                "locator": {
                  "note": "TABLE 1. Results of 12 tests with the prototype system from 2005",
                  "pdf_page_index": 3,
                  "table": "1"
                },
                "state": {
                  "tag": "value",
                  "value": "133.3223684210526315789473684"
                }
              }
            ]
          ],
          "references": [],
          "relation": "Torr_to_Pa"
        },
        "locator": {
          "note": "TABLE 1. Results of 12 tests with the prototype system from 2005; printed_cell=[\"0.23 Torr\", \"Torr\"]",
          "pdf_page_index": 3,
          "table": "1"
        },
        "state": {
          "tag": "value",
          "value": "30.66414473684210526315789473"
        }
      }
    },
    "queue": {
      "queued": true,
      "why": [
        "unsupported quantity 'vacuum_pyrolysis_experiment_summary'",
        "phase string 'fresnel_lens_vacuum_pyrolysis_mixed_simulants' is not in the closed automatic map",
        "source T_range_K [821.15, 2140.15]; no midpoint invented"
      ]
    }
  },
  "kems-022-demaria-1971::demaria_1971_al2o_psat_table4::T=2060.0:row=12022:h=24f36e6113b1": {
    "admission_status": "admitted",
    "evidence_class": {
      "tag": "value",
      "value": "measured_direct"
    },
    "identity": {
      "composition": {
        "tag": "value",
        "value": {
          "amount_basis": "mole_fraction",
          "analysis_selection_rule": "use the first reported value for each major oxide in the held Apollo 12 whole-sample bulk analysis; do not average methods. This is the starting bulk composition, not a per-step residue composition. Above about 2000 K DeMaria's stepwise KEMS measures a depleted residue, and no step-specific residue composition is printed",
          "basis": "sample_catalog_proxy",
          "components": [
            [
              "SiO2",
              "0.4370300404472189490300406296"
            ],
            [
              "Al2O3",
              "0.04992363851472767186779541665"
            ],
            [
              "FeO",
              "0.186631857820954309618158003"
            ],
            [
              "MgO",
              "0.1832826449540607504498940327"
            ],
            [
              "CaO",
              "0.09962773800078939244754301791"
            ],
            [
              "Na2O",
              "0.00230361043344815193559034437"
            ],
            [
              "K2O",
              "0.0004019960173842589283052064581"
            ],
            [
              "TiO2",
              "0.03808571527364136642564305475"
            ],
            [
              "MnO",
              "0.002712758537775149297030294361"
            ]
          ],
          "proxy_flag": "composition_from_sample_catalog",
          "proxy_source": "Smales et al. 1971, Tables 1–3, pp. 1254–1255; source observation smales_1971_apollo_12022_bulk_composition_tables1_2_3. Selection rule: use the first reported value for each major oxide in the held Apollo 12 whole-sample bulk analysis; do not average methods. This is the starting bulk composition, not a per-step residue composition. Above about 2000 K DeMaria's stepwise KEMS measures a depleted residue, and no step-specific residue composition is printed.",
          "input_provenance": null
        }
      },
      "exposure": {
        "tag": "not_applicable"
      },
      "fO2_Pa": {
        "reason": "no fO2_Pa mapped from source",
        "tag": "unknown"
      },
      "formation_elements": {
        "tag": "not_applicable"
      },
      "per": {
        "tag": "not_applicable"
      },
      "quantity": {
        "tag": "value",
        "value": "p_partial"
      },
      "reaction": {
        "reason": "source reaction/equilibrium not grounded",
        "tag": "unknown"
      },
      "reference_state": {
        "tag": "value",
        "value": {
          "component_basis": "Al2O",
          "convention": "raoultian_pure_endmember",
          "endmember": {
            "charge": {
              "tag": "value",
              "value": 0
            },
            "formula": "Al2O",
            "phase": {
              "tag": "value",
              "value": "g"
            },
            "polymorph": {
              "reason": "not crystal",
              "tag": "not_applicable"
            }
          },
          "reference_pressure_bar": "1"
        }
      },
      "reservoir": {
        "reason": "source condensed reservoir not grounded",
        "tag": "unknown"
      },
      "sample_mass_kg": {
        "tag": "not_applicable"
      },
      "species": {
        "charge": {
          "tag": "value",
          "value": 0
        },
        "formula": "Al2O",
        "phase": {
          "tag": "value",
          "value": "g"
        },
        "polymorph": {
          "reason": "not crystal",
          "tag": "not_applicable"
        }
      },
      "standard_pressure_Pa": {
        "tag": "not_applicable"
      },
      "subtype": {
        "tag": "not_applicable"
      },
      "sweep_gas": {
        "tag": "not_applicable"
      },
      "temperature_K": {
        "tag": "value",
        "value": "2060"
      },
      "total_pressure_Pa": {
        "reason": "in_cell_total_pressure_not_derivable",
        "tag": "unknown"
      },
      "wall": {
        "tag": "not_applicable"
      }
    },
    "locator": {
      "line_range": "284-286",
      "paragraph": "Partial pressures of AlO and Al2O over samples 12022 and 12065",
      "source_path": "text/kems-022-demaria-1971/mineru/source/vlm/source.md",
      "table": "4"
    },
    "point_conditions": {
      "composition": {
        "inference": {
          "inputs": [
            "x_i = (w_i / M_i) / Σ_j (w_j / M_j)",
            "original_unit=wt_pct"
          ],
          "output_unit": "mole_fraction",
          "parameters": [
            [
              "original_SiO2_wt_pct",
              {
                "locator": {
                  "line_range": "284-286",
                  "paragraph": "Partial pressures of AlO and Al2O over samples 12022 and 12065",
                  "source_path": "text/kems-022-demaria-1971/mineru/source/vlm/source.md",
                  "table": "4"
                },
                "state": {
                  "tag": "value",
                  "value": "42.3"
                }
              }
            ],
            [
              "M_SiO2_g_mol",
              {
                "locator": {
                  "line_range": "284-286",
                  "paragraph": "Partial pressures of AlO and Al2O over samples 12022 and 12065",
                  "source_path": "text/kems-022-demaria-1971/mineru/source/vlm/source.md",
                  "table": "4"
                },
                "state": {
                  "tag": "value",
                  "value": "60.083"
                }
              }
            ],
            [
              "original_Al2O3_wt_pct",
              {
                "locator": {
                  "line_range": "284-286",
                  "paragraph": "Partial pressures of AlO and Al2O over samples 12022 and 12065",
                  "source_path": "text/kems-022-demaria-1971/mineru/source/vlm/source.md",
                  "table": "4"
                },
                "state": {
                  "tag": "value",
                  "value": "8.2"
                }
              }
            ],
            [
              "M_Al2O3_g_mol",
              {
                "locator": {
                  "line_range": "284-286",
                  "paragraph": "Partial pressures of AlO and Al2O over samples 12022 and 12065",
                  "source_path": "text/kems-022-demaria-1971/mineru/source/vlm/source.md",
                  "table": "4"
                },
                "state": {
                  "tag": "value",
                  "value": "101.960077"
                }
              }
            ],
            [
              "original_FeO_wt_pct",
              {
                "locator": {
                  "line_range": "284-286",
                  "paragraph": "Partial pressures of AlO and Al2O over samples 12022 and 12065",
                  "source_path": "text/kems-022-demaria-1971/mineru/source/vlm/source.md",
                  "table": "4"
                },
                "state": {
                  "tag": "value",
                  "value": "21.6"
                }
              }
            ],
            [
              "M_FeO_g_mol",
              {
                "locator": {
                  "line_range": "284-286",
                  "paragraph": "Partial pressures of AlO and Al2O over samples 12022 and 12065",
                  "source_path": "text/kems-022-demaria-1971/mineru/source/vlm/source.md",
                  "table": "4"
                },
                "state": {
                  "tag": "value",
                  "value": "71.844"
                }
              }
            ],
            [
              "original_MgO_wt_pct",
              {
                "locator": {
                  "line_range": "284-286",
                  "paragraph": "Partial pressures of AlO and Al2O over samples 12022 and 12065",
                  "source_path": "text/kems-022-demaria-1971/mineru/source/vlm/source.md",
                  "table": "4"
                },
                "state": {
                  "tag": "value",
                  "value": "11.9"
                }
              }
            ],
            [
              "M_MgO_g_mol",
              {
                "locator": {
                  "line_range": "284-286",
                  "paragraph": "Partial pressures of AlO and Al2O over samples 12022 and 12065",
                  "source_path": "text/kems-022-demaria-1971/mineru/source/vlm/source.md",
                  "table": "4"
                },
                "state": {
                  "tag": "value",
                  "value": "40.304"
                }
              }
            ],
            [
              "original_CaO_wt_pct",
              {
                "locator": {
                  "line_range": "284-286",
                  "paragraph": "Partial pressures of AlO and Al2O over samples 12022 and 12065",
                  "source_path": "text/kems-022-demaria-1971/mineru/source/vlm/source.md",
                  "table": "4"
                },
                "state": {
                  "tag": "value",
                  "value": "9"
                }
              }
            ],
            [
              "M_CaO_g_mol",
              {
                "locator": {
                  "line_range": "284-286",
                  "paragraph": "Partial pressures of AlO and Al2O over samples 12022 and 12065",
                  "source_path": "text/kems-022-demaria-1971/mineru/source/vlm/source.md",
                  "table": "4"
                },
                "state": {
                  "tag": "value",
                  "value": "56.077000000000005"
                }
              }
            ],
            [
              "original_Na2O_wt_pct",
              {
                "locator": {
                  "line_range": "284-286",
                  "paragraph": "Partial pressures of AlO and Al2O over samples 12022 and 12065",
                  "source_path": "text/kems-022-demaria-1971/mineru/source/vlm/source.md",
                  "table": "4"
                },
                "state": {
                  "tag": "value",
                  "value": "0.23"
                }
              }
            ],
            [
              "M_Na2O_g_mol",
              {
                "locator": {
                  "line_range": "284-286",
                  "paragraph": "Partial pressures of AlO and Al2O over samples 12022 and 12065",
                  "source_path": "text/kems-022-demaria-1971/mineru/source/vlm/source.md",
                  "table": "4"
                },
                "state": {
                  "tag": "value",
                  "value": "61.978538560000004"
                }
              }
            ],
            [
              "original_K2O_wt_pct",
              {
                "locator": {
                  "line_range": "284-286",
                  "paragraph": "Partial pressures of AlO and Al2O over samples 12022 and 12065",
                  "source_path": "text/kems-022-demaria-1971/mineru/source/vlm/source.md",
                  "table": "4"
                },
                "state": {
                  "tag": "value",
                  "value": "0.061"
                }
              }
            ],
            [
              "M_K2O_g_mol",
              {
                "locator": {
                  "line_range": "284-286",
                  "paragraph": "Partial pressures of AlO and Al2O over samples 12022 and 12065",
                  "source_path": "text/kems-022-demaria-1971/mineru/source/vlm/source.md",
                  "table": "4"
                },
                "state": {
                  "tag": "value",
                  "value": "94.1956"
                }
              }
            ],
            [
              "original_TiO2_wt_pct",
              {
                "locator": {
                  "line_range": "284-286",
                  "paragraph": "Partial pressures of AlO and Al2O over samples 12022 and 12065",
                  "source_path": "text/kems-022-demaria-1971/mineru/source/vlm/source.md",
                  "table": "4"
                },
                "state": {
                  "tag": "value",
                  "value": "4.9"
                }
              }
            ],
            [
              "M_TiO2_g_mol",
              {
                "locator": {
                  "line_range": "284-286",
                  "paragraph": "Partial pressures of AlO and Al2O over samples 12022 and 12065",
                  "source_path": "text/kems-022-demaria-1971/mineru/source/vlm/source.md",
                  "table": "4"
                },
                "state": {
                  "tag": "value",
                  "value": "79.865"
                }
              }
            ],
            [
              "original_MnO_wt_pct",
              {
                "locator": {
                  "line_range": "284-286",
                  "paragraph": "Partial pressures of AlO and Al2O over samples 12022 and 12065",
                  "source_path": "text/kems-022-demaria-1971/mineru/source/vlm/source.md",
                  "table": "4"
                },
                "state": {
                  "tag": "value",
                  "value": "0.31"
                }
              }
            ],
            [
              "M_MnO_g_mol",
              {
                "locator": {
                  "line_range": "284-286",
                  "paragraph": "Partial pressures of AlO and Al2O over samples 12022 and 12065",
                  "source_path": "text/kems-022-demaria-1971/mineru/source/vlm/source.md",
                  "table": "4"
                },
                "state": {
                  "tag": "value",
                  "value": "70.937044"
                }
              }
            ]
          ],
          "references": [],
          "relation": "wt_pct_to_mole_fraction"
        },
        "locator": {
          "line_range": "284-286",
          "paragraph": "Partial pressures of AlO and Al2O over samples 12022 and 12065",
          "source_path": "text/kems-022-demaria-1971/mineru/source/vlm/source.md",
          "table": "4"
        },
        "state": {
          "tag": "value",
          "value": {
            "amount_basis": "mole_fraction",
            "analysis_selection_rule": "use the first reported value for each major oxide in the held Apollo 12 whole-sample bulk analysis; do not average methods. This is the starting bulk composition, not a per-step residue composition. Above about 2000 K DeMaria's stepwise KEMS measures a depleted residue, and no step-specific residue composition is printed",
            "basis": "sample_catalog_proxy",
            "components": [
              [
                "SiO2",
                "0.4370300404472189490300406296"
              ],
              [
                "Al2O3",
                "0.04992363851472767186779541665"
              ],
              [
                "FeO",
                "0.186631857820954309618158003"
              ],
              [
                "MgO",
                "0.1832826449540607504498940327"
              ],
              [
                "CaO",
                "0.09962773800078939244754301791"
              ],
              [
                "Na2O",
                "0.00230361043344815193559034437"
              ],
              [
                "K2O",
                "0.0004019960173842589283052064581"
              ],
              [
                "TiO2",
                "0.03808571527364136642564305475"
              ],
              [
                "MnO",
                "0.002712758537775149297030294361"
              ]
            ],
            "proxy_flag": "composition_from_sample_catalog",
            "proxy_source": "Smales et al. 1971, Tables 1–3, pp. 1254–1255; source observation smales_1971_apollo_12022_bulk_composition_tables1_2_3. Selection rule: use the first reported value for each major oxide in the held Apollo 12 whole-sample bulk analysis; do not average methods. This is the starting bulk composition, not a per-step residue composition. Above about 2000 K DeMaria's stepwise KEMS measures a depleted residue, and no step-specific residue composition is printed.",
            "input_provenance": null
          }
        }
      },
      "temperature_K": {
        "locator": {
          "line_range": "284-286",
          "paragraph": "Partial pressures of AlO and Al2O over samples 12022 and 12065",
          "source_path": "text/kems-022-demaria-1971/mineru/source/vlm/source.md",
          "table": "4"
        },
        "state": {
          "tag": "value",
          "value": "2060"
        }
      }
    },
    "queue": {
      "queued": true,
      "why": [
        "source T_range_K [2060.0, 2500.0]; no midpoint invented",
        "locator source_path 'text/kems-022-demaria-1971/mineru/source/vlm/source.md' has no matching INDEX asset"
      ]
    }
  },
  "kems-022-demaria-1971::demaria_1971_o2_psat_table1_alkali_range::T=1396.0:row=12022:h=3dd0a0a3319e": {
    "admission_status": "admitted",
    "evidence_class": {
      "tag": "value",
      "value": "measured_direct"
    },
    "identity": {
      "composition": {
        "reason": "no composition mapped from source",
        "tag": "unknown"
      },
      "exposure": {
        "tag": "not_applicable"
      },
      "fO2_Pa": {
        "reason": "no fO2_Pa mapped from source",
        "tag": "unknown"
      },
      "formation_elements": {
        "tag": "not_applicable"
      },
      "per": {
        "tag": "not_applicable"
      },
      "quantity": {
        "tag": "value",
        "value": "p_partial"
      },
      "reaction": {
        "reason": "source reaction/equilibrium not grounded",
        "tag": "unknown"
      },
      "reference_state": {
        "tag": "value",
        "value": {
          "component_basis": "O2",
          "convention": "raoultian_pure_endmember",
          "endmember": {
            "charge": {
              "tag": "value",
              "value": 0
            },
            "formula": "O2",
            "phase": {
              "tag": "value",
              "value": "g"
            },
            "polymorph": {
              "reason": "not crystal",
              "tag": "not_applicable"
            }
          },
          "reference_pressure_bar": "1"
        }
      },
      "reservoir": {
        "tag": "not_applicable"
      },
      "sample_mass_kg": {
        "tag": "not_applicable"
      },
      "species": {
        "charge": {
          "tag": "value",
          "value": 0
        },
        "formula": "O2",
        "phase": {
          "tag": "value",
          "value": "g"
        },
        "polymorph": {
          "reason": "not crystal",
          "tag": "not_applicable"
        }
      },
      "standard_pressure_Pa": {
        "tag": "not_applicable"
      },
      "subtype": {
        "tag": "not_applicable"
      },
      "sweep_gas": {
        "tag": "not_applicable"
      },
      "temperature_K": {
        "tag": "value",
        "value": "1396"
      },
      "total_pressure_Pa": {
        "reason": "in_cell_total_pressure_not_derivable",
        "tag": "unknown"
      },
      "wall": {
        "tag": "not_applicable"
      }
    },
    "locator": {
      "line_range": "106-108",
      "paragraph": "Partial pressures of O2 corresponding approximately to alkali metal vaporization",
      "source_path": "text/kems-022-demaria-1971/mineru/source/vlm/source.md",
      "table": "1"
    },
    "point_conditions": {
      "temperature_K": {
        "locator": {
          "line_range": "106-108",
          "paragraph": "Partial pressures of O2 corresponding approximately to alkali metal vaporization",
          "source_path": "text/kems-022-demaria-1971/mineru/source/vlm/source.md",
          "table": "1"
        },
        "state": {
          "tag": "value",
          "value": "1396"
        }
      }
    },
    "queue": {
      "queued": true,
      "why": [
        "source T_range_K [1396.0, 1499.0]; no midpoint invented",
        "locator source_path 'text/kems-022-demaria-1971/mineru/source/vlm/source.md' has no matching INDEX asset"
      ]
    }
  },
  "kems-026-markova-1984::markova_1984_table2_residual_melt_VI_lherzolite::mass_loss_fraction::T=2156.15:h=e9110f31a14b": {
    "admission_status": "admitted",
    "evidence_class": {
      "tag": "value",
      "value": "measured_direct"
    },
    "identity": {
      "composition": {
        "tag": "value",
        "value": {
          "amount_basis": "mole_fraction",
          "basis": "printed_oxides",
          "components": [
            [
              "SiO2",
              "0.3821173839269460708474485548"
            ],
            [
              "Al2O3",
              "0.02332561240102551057000802657"
            ],
            [
              "FeO",
              "0.05836653496472179772464472096"
            ],
            [
              "MgO",
              "0.4715509074964332951841453868"
            ],
            [
              "CaO",
              "0.06463956121087332567375331089"
            ]
          ],
          "proxy_flag": null,
          "proxy_source": null,
          "analysis_selection_rule": null,
          "input_provenance": null
        }
      },
      "exposure": {
        "reason": "no exposure mapped from source",
        "tag": "unknown"
      },
      "fO2_Pa": {
        "reason": "no fO2_Pa mapped from source",
        "tag": "unknown"
      },
      "formation_elements": {
        "tag": "not_applicable"
      },
      "per": {
        "reason": "no per mapped from source",
        "tag": "unknown"
      },
      "quantity": {
        "tag": "value",
        "value": "mass_loss_fraction"
      },
      "reaction": {
        "tag": "not_applicable"
      },
      "reference_state": {
        "tag": "not_applicable"
      },
      "reservoir": {
        "reason": "no reservoir mapped from source",
        "tag": "unknown"
      },
      "sample_mass_kg": {
        "reason": "no sample_mass_kg mapped from source",
        "tag": "unknown"
      },
      "species": {
        "charge": {
          "tag": "value",
          "value": 0
        },
        "formula": "unknown",
        "phase": {
          "tag": "value",
          "value": "l"
        },
        "polymorph": {
          "reason": "not crystal",
          "tag": "not_applicable"
        }
      },
      "standard_pressure_Pa": {
        "tag": "not_applicable"
      },
      "subtype": {
        "tag": "not_applicable"
      },
      "sweep_gas": {
        "reason": "no sweep_gas mapped from source",
        "tag": "unknown"
      },
      "temperature_K": {
        "tag": "value",
        "value": "2156.15"
      },
      "total_pressure_Pa": {
        "reason": "no total_pressure_Pa mapped from source",
        "tag": "unknown"
      },
      "wall": {
        "tag": "not_applicable"
      }
    },
    "locator": {
      "note": "Table 2 left panel (composition VI), T=1883 C; oxide wt% and mass-loss row",
      "pdf_page_index": 1,
      "published_page": 510,
      "table": "2"
    },
    "point_conditions": {
      "composition": {
        "locator": {
          "note": "Table 2 left panel (composition VI), T=1883 C; oxide wt% and mass-loss row",
          "pdf_page_index": 1,
          "published_page": 510,
          "table": "2"
        },
        "state": {
          "reason": "mass-percent composition is a partial analysis: kept sum=91.43 wt% is below 95 wt%",
          "tag": "unknown"
        }
      },
      "duration_h": {
        "locator": {
          "note": "15 minutes for each printed temperature step.",
          "pdf_page_index": 0,
          "published_page": 509,
          "section": "experimental method"
        },
        "state": {
          "tag": "value",
          "value": "0.25"
        }
      },
      "fO2_Pa": {
        "locator": {
          "pdf_page_index": 0,
          "published_page": 509,
          "section": "experimental method"
        },
        "state": {
          "reason": "No point p(O2), fO2, buffer, or gas mix is printed; p(O2) is listed as a result, not an imposed condition.",
          "tag": "unknown"
        }
      },
      "pressure_kind": {
        "locator": {
          "pdf_page_index": 0,
          "published_page": 509,
          "section": "experimental method"
        },
        "state": {
          "reason": "No vacuum, Knudsen-cell interior pressure, or carrier-gas pressure condition is stated.",
          "tag": "unknown"
        }
      },
      "printed_composition": {
        "locator": {
          "note": "Table 2 left panel (composition VI), T=1883 C; oxide wt% and mass-loss row",
          "pdf_page_index": 1,
          "published_page": 510,
          "table": "2"
        },
        "state": {
          "tag": "value",
          "value": {
            "Al2O3": "13.09",
            "CaO": "16.34",
            "FeO": "1.79",
            "MgO": "46.61",
            "SiO2": "13.6"
          }
        }
      },
      "starting_composition": {
        "locator": {
          "pdf_page_index": 1,
          "published_page": 510,
          "table": "2"
        },
        "state": {
          "tag": "value",
          "value": {
            "Al2O3": "4.56",
            "CaO": "6.95",
            "FeO": "8.04",
            "MgO": "36.44",
            "SiO2": "44.02"
          }
        }
      },
      "temperature_K": {
        "inference": {
          "inputs": [
            "T_K = T_C + 273.15",
            "original_unit=C"
          ],
          "output_unit": "K",
          "parameters": [
            [
              "original",
              {
                "locator": {
                  "note": "Table 2 left panel (composition VI), T=1883 C; oxide wt% and mass-loss row",
                  "pdf_page_index": 1,
                  "published_page": 510,
                  "table": "2"
                },
                "state": {
                  "tag": "value",
                  "value": "1883"
                }
              }
            ],
            [
              "factor",
              {
                "locator": {
                  "note": "Table 2 left panel (composition VI), T=1883 C; oxide wt% and mass-loss row",
                  "pdf_page_index": 1,
                  "published_page": 510,
                  "table": "2"
                },
                "state": {
                  "tag": "value",
                  "value": "273.15"
                }
              }
            ]
          ],
          "references": [],
          "relation": "celsius_to_kelvin"
        },
        "locator": {
          "note": "Table 2 left panel (composition VI), T=1883 C; oxide wt% and mass-loss row",
          "pdf_page_index": 1,
          "published_page": 510,
          "table": "2"
        },
        "state": {
          "tag": "value",
          "value": "2156.15"
        }
      }
    },
    "queue": {
      "queued": false,
      "why": [
        "no queue entry"
      ]
    }
  },
  "kems-042-plante-1979::plante1979_table2_k2o_s1115_000_1259K": {
    "admission_status": "admitted",
    "evidence_class": {
      "tag": "value",
      "value": "measured_reduced"
    },
    "identity": {
      "composition": {
        "tag": "value",
        "value": {
          "amount_basis": "mole_fraction",
          "basis": "printed_oxides",
          "components": [
            [
              "K2O",
              "0.2503139289921102343627073385"
            ],
            [
              "SiO2",
              "0.7496860710078897656372926616"
            ]
          ],
          "proxy_flag": null,
          "proxy_source": null,
          "analysis_selection_rule": null,
          "input_provenance": null
        }
      },
      "exposure": {
        "tag": "not_applicable"
      },
      "fO2_Pa": {
        "reason": "no fO2_Pa mapped from source",
        "tag": "unknown"
      },
      "formation_elements": {
        "tag": "not_applicable"
      },
      "per": {
        "tag": "not_applicable"
      },
      "quantity": {
        "tag": "value",
        "value": "p_partial"
      },
      "reaction": {
        "tag": "value",
        "value": {
          "terms": [
            {
              "coefficient": "2",
              "species": {
                "charge": {
                  "tag": "value",
                  "value": 0
                },
                "formula": "K",
                "phase": {
                  "tag": "value",
                  "value": "g"
                },
                "polymorph": {
                  "reason": "not crystal",
                  "tag": "not_applicable"
                }
              }
            },
            {
              "coefficient": "1/2",
              "species": {
                "charge": {
                  "tag": "value",
                  "value": 0
                },
                "formula": "O2",
                "phase": {
                  "tag": "value",
                  "value": "g"
                },
                "polymorph": {
                  "reason": "not crystal",
                  "tag": "not_applicable"
                }
              }
            },
            {
              "coefficient": "-1",
              "species": {
                "charge": {
                  "tag": "value",
                  "value": 0
                },
                "formula": "K2O",
                "phase": {
                  "tag": "value",
                  "value": "l"
                },
                "polymorph": {
                  "reason": "not crystal",
                  "tag": "not_applicable"
                }
              }
            }
          ]
        }
      },
      "reference_state": {
        "tag": "value",
        "value": {
          "component_basis": "K",
          "convention": "raoultian_pure_endmember",
          "endmember": {
            "charge": {
              "tag": "value",
              "value": 0
            },
            "formula": "K",
            "phase": {
              "tag": "value",
              "value": "g"
            },
            "polymorph": {
              "reason": "not crystal",
              "tag": "not_applicable"
            }
          },
          "reference_pressure_bar": "1"
        }
      },
      "reservoir": {
        "tag": "value",
        "value": {
          "charge": {
            "tag": "value",
            "value": 0
          },
          "formula": "K2O",
          "phase": {
            "tag": "value",
            "value": "l"
          },
          "polymorph": {
            "reason": "not crystal",
            "tag": "not_applicable"
          }
        }
      },
      "sample_mass_kg": {
        "tag": "not_applicable"
      },
      "species": {
        "charge": {
          "tag": "value",
          "value": 0
        },
        "formula": "K",
        "phase": {
          "tag": "value",
          "value": "g"
        },
        "polymorph": {
          "reason": "not crystal",
          "tag": "not_applicable"
        }
      },
      "standard_pressure_Pa": {
        "tag": "not_applicable"
      },
      "subtype": {
        "tag": "not_applicable"
      },
      "sweep_gas": {
        "tag": "not_applicable"
      },
      "temperature_K": {
        "tag": "value",
        "value": "1259"
      },
      "total_pressure_Pa": {
        "tag": "value",
        "value": "0.0124244715"
      },
      "wall": {
        "tag": "not_applicable"
      }
    },
    "locator": {
      "note": "Series 1115 T=1259 K, 34.36 wt% K2O, P_K=1.000e-07 atm as printed",
      "pdf_page_index": 300,
      "published_page": 276,
      "table": "2"
    },
    "point_conditions": {
      "composition": {
        "inference": {
          "inputs": [
            "x_i = (w_i / M_i) / Σ_j (w_j / M_j)",
            "original_unit=wt_pct"
          ],
          "output_unit": "mole_fraction",
          "parameters": [
            [
              "original_K2O_wt_pct",
              {
                "locator": {
                  "note": "Series 1115 T=1259 K, 34.36 wt% K2O, P_K=1.000e-07 atm as printed",
                  "pdf_page_index": 300,
                  "published_page": 276,
                  "table": "2"
                },
                "state": {
                  "tag": "value",
                  "value": "34.36"
                }
              }
            ],
            [
              "M_K2O_g_mol",
              {
                "locator": {
                  "note": "Series 1115 T=1259 K, 34.36 wt% K2O, P_K=1.000e-07 atm as printed",
                  "pdf_page_index": 300,
                  "published_page": 276,
                  "table": "2"
                },
                "state": {
                  "tag": "value",
                  "value": "94.1956"
                }
              }
            ],
            [
              "original_SiO2_wt_pct",
              {
                "locator": {
                  "note": "Series 1115 T=1259 K, 34.36 wt% K2O, P_K=1.000e-07 atm as printed",
                  "pdf_page_index": 300,
                  "published_page": 276,
                  "table": "2"
                },
                "state": {
                  "tag": "value",
                  "value": "65.64"
                }
              }
            ],
            [
              "M_SiO2_g_mol",
              {
                "locator": {
                  "note": "Series 1115 T=1259 K, 34.36 wt% K2O, P_K=1.000e-07 atm as printed",
                  "pdf_page_index": 300,
                  "published_page": 276,
                  "table": "2"
                },
                "state": {
                  "tag": "value",
                  "value": "60.083"
                }
              }
            ]
          ],
          "references": [],
          "relation": "wt_pct_to_mole_fraction"
        },
        "locator": {
          "note": "Series 1115 T=1259 K, 34.36 wt% K2O, P_K=1.000e-07 atm as printed",
          "pdf_page_index": 300,
          "published_page": 276,
          "table": "2"
        },
        "state": {
          "tag": "value",
          "value": {
            "amount_basis": "mole_fraction",
            "basis": "printed_oxides",
            "components": [
              [
                "K2O",
                "0.2503139289921102343627073385"
              ],
              [
                "SiO2",
                "0.7496860710078897656372926616"
              ]
            ],
            "proxy_flag": null,
            "proxy_source": null,
            "analysis_selection_rule": null,
            "input_provenance": null
          }
        }
      },
      "fO2_Pa": {
        "inference": {
          "inputs": [
            "values.po2_over_pK_as_published",
            "values.P_K_atm_as_published",
            "values.oxygen_partial_pressure",
            "assumption=congruent_vaporization"
          ],
          "output_unit": "Pa",
          "parameters": [
            [
              "po2_over_pK_as_published",
              {
                "locator": {
                  "note": "Series 1115 T=1259 K, 34.36 wt% K2O, P_K=1.000e-07 atm as printed",
                  "pdf_page_index": 300,
                  "published_page": 276,
                  "table": "2"
                },
                "state": {
                  "tag": "value",
                  "value": "0.226"
                }
              }
            ],
            [
              "P_K_atm_as_published",
              {
                "locator": {
                  "note": "Series 1115 T=1259 K, 34.36 wt% K2O, P_K=1.000e-07 atm as printed",
                  "pdf_page_index": 300,
                  "published_page": 276,
                  "table": "2"
                },
                "state": {
                  "tag": "value",
                  "value": "0.0000001"
                }
              }
            ],
            [
              "M_O2_g_mol",
              {
                "locator": {
                  "equation": "7",
                  "published_page": 280
                },
                "state": {
                  "tag": "value",
                  "value": "32"
                }
              }
            ],
            [
              "M_K_g_mol",
              {
                "locator": {
                  "equation": "7",
                  "published_page": 280
                },
                "state": {
                  "tag": "value",
                  "value": "39.1"
                }
              }
            ],
            [
              "oxygen_partial_pressure",
              {
                "locator": {
                  "equation": "7",
                  "published_page": 280
                },
                "state": {
                  "tag": "value",
                  "value": "0.0000000226"
                }
              }
            ],
            [
              "original",
              {
                "locator": {
                  "equation": "7",
                  "published_page": 280
                },
                "state": {
                  "tag": "value",
                  "value": "0.0000000226"
                }
              }
            ],
            [
              "factor",
              {
                "locator": {
                  "equation": "7",
                  "published_page": 280
                },
                "state": {
                  "tag": "value",
                  "value": "101325"
                }
              }
            ]
          ],
          "references": [],
          "relation": "congruent_vaporization_P_O2_atm=po2_over_pK_as_published*P_K_atm_as_published;P_O2/P_K=(1/4)*sqrt(M_O2/M_K)=0.2262;Plante_printed_ratio=0.226;atm_to_Pa"
        },
        "locator": {
          "equation": "7",
          "note": "DERIVED condition: congruent vaporization P_O2=0.226 P_K; not a measurement",
          "published_page": 280
        },
        "state": {
          "tag": "value",
          "value": {
            "approximate": false,
            "kind": "point",
            "point": "0.002289945"
          }
        }
      },
      "temperature_K": {
        "locator": {
          "note": "Series 1115 T=1259 K, 34.36 wt% K2O, P_K=1.000e-07 atm as printed",
          "pdf_page_index": 300,
          "published_page": 276,
          "table": "2"
        },
        "state": {
          "tag": "value",
          "value": "1259"
        }
      },
      "total_pressure_Pa": {
        "locator": {
          "note": "Series 1115 T=1259 K, 34.36 wt% K2O, P_K=1.000e-07 atm as printed",
          "pdf_page_index": 300,
          "published_page": 276,
          "table": "2"
        },
        "state": {
          "tag": "value",
          "value": "0.0124244715"
        }
      }
    },
    "queue": {
      "queued": false,
      "why": [
        "no queue entry"
      ]
    }
  },
  "kems-042-plante-1979::plante1979_table2_s1214_r037_quoted": {
    "admission_status": "admitted",
    "evidence_class": {
      "tag": "value",
      "value": "measured_reduced"
    },
    "identity": {
      "composition": {
        "tag": "value",
        "value": {
          "amount_basis": "mole_fraction",
          "basis": "printed_oxides",
          "components": [
            [
              "K2O",
              "0.04420099031668051797116203944"
            ],
            [
              "SiO2",
              "0.9557990096833194820288379604"
            ]
          ],
          "proxy_flag": null,
          "proxy_source": null,
          "analysis_selection_rule": null,
          "input_provenance": null
        }
      },
      "exposure": {
        "tag": "not_applicable"
      },
      "fO2_Pa": {
        "reason": "no fO2_Pa mapped from source",
        "tag": "unknown"
      },
      "formation_elements": {
        "tag": "not_applicable"
      },
      "per": {
        "tag": "not_applicable"
      },
      "quantity": {
        "tag": "value",
        "value": "p_partial"
      },
      "reaction": {
        "reason": "source reaction/equilibrium not grounded",
        "tag": "unknown"
      },
      "reference_state": {
        "reason": "source gas standard state not grounded",
        "tag": "unknown"
      },
      "reservoir": {
        "reason": "source condensed reservoir not grounded",
        "tag": "unknown"
      },
      "sample_mass_kg": {
        "tag": "not_applicable"
      },
      "species": {
        "charge": {
          "tag": "value",
          "value": 0
        },
        "formula": "K",
        "phase": {
          "tag": "value",
          "value": "g"
        },
        "polymorph": {
          "reason": "not crystal",
          "tag": "not_applicable"
        }
      },
      "standard_pressure_Pa": {
        "tag": "not_applicable"
      },
      "subtype": {
        "tag": "not_applicable"
      },
      "sweep_gas": {
        "tag": "not_applicable"
      },
      "temperature_K": {
        "tag": "value",
        "value": "1335"
      },
      "total_pressure_Pa": {
        "tag": "value",
        "value": "0.012548716215"
      },
      "wall": {
        "tag": "not_applicable"
      }
    },
    "locator": {
      "note": "Series 1214, row 37; columns T,K | Wt % K2O | P_K(atm).",
      "published_page": 278,
      "table": "2"
    },
    "point_conditions": {
      "composition": {
        "inference": {
          "inputs": [
            "x_i = (w_i / M_i) / Σ_j (w_j / M_j)",
            "original_unit=wt_pct",
            "SiO2 = declared K2O-SiO2 binary complement (100 - K2O wt%)"
          ],
          "output_unit": "mole_fraction",
          "parameters": [
            [
              "original_K2O_wt_pct",
              {
                "locator": {
                  "note": "Series 1214, row 37; columns T,K | Wt % K2O | P_K(atm).",
                  "published_page": 278,
                  "table": "2"
                },
                "state": {
                  "tag": "value",
                  "value": "6.76"
                }
              }
            ],
            [
              "M_K2O_g_mol",
              {
                "locator": {
                  "note": "Series 1214, row 37; columns T,K | Wt % K2O | P_K(atm).",
                  "published_page": 278,
                  "table": "2"
                },
                "state": {
                  "tag": "value",
                  "value": "94.1956"
                }
              }
            ],
            [
              "original_SiO2_wt_pct",
              {
                "locator": {
                  "note": "Series 1214, row 37; columns T,K | Wt % K2O | P_K(atm).",
                  "published_page": 278,
                  "table": "2"
                },
                "state": {
                  "tag": "value",
                  "value": "93.24"
                }
              }
            ],
            [
              "M_SiO2_g_mol",
              {
                "locator": {
                  "note": "Series 1214, row 37; columns T,K | Wt % K2O | P_K(atm).",
                  "published_page": 278,
                  "table": "2"
                },
                "state": {
                  "tag": "value",
                  "value": "60.083"
                }
              }
            ]
          ],
          "references": [],
          "relation": "SiO2_wt_pct=100-K2O_wt_pct;wt_pct_to_mole_fraction"
        },
        "locator": {
          "note": "Series 1214, row 37; columns T,K | Wt % K2O | P_K(atm).",
          "published_page": 278,
          "table": "2"
        },
        "state": {
          "tag": "value",
          "value": {
            "amount_basis": "mole_fraction",
            "basis": "printed_oxides",
            "components": [
              [
                "K2O",
                "0.04420099031668051797116203944"
              ],
              [
                "SiO2",
                "0.9557990096833194820288379604"
              ]
            ],
            "proxy_flag": null,
            "proxy_source": null,
            "analysis_selection_rule": null,
            "input_provenance": null
          }
        }
      },
      "fO2_Pa": {
        "inference": {
          "inputs": [
            "values.po2_over_pK_as_published",
            "values.P_K_atm_as_published",
            "values.oxygen_partial_pressure",
            "assumption=congruent_vaporization"
          ],
          "output_unit": "Pa",
          "parameters": [
            [
              "po2_over_pK_as_published",
              {
                "locator": {
                  "note": "Series 1214, row 37; columns T,K | Wt % K2O | P_K(atm).",
                  "published_page": 278,
                  "table": "2"
                },
                "state": {
                  "tag": "value",
                  "value": "0.226"
                }
              }
            ],
            [
              "P_K_atm_as_published",
              {
                "locator": {
                  "note": "Series 1214, row 37; columns T,K | Wt % K2O | P_K(atm).",
                  "published_page": 278,
                  "table": "2"
                },
                "state": {
                  "tag": "value",
                  "value": "0.000000101"
                }
              }
            ],
            [
              "M_O2_g_mol",
              {
                "locator": {
                  "equation": "7",
                  "published_page": 280
                },
                "state": {
                  "tag": "value",
                  "value": "32"
                }
              }
            ],
            [
              "M_K_g_mol",
              {
                "locator": {
                  "equation": "7",
                  "published_page": 280
                },
                "state": {
                  "tag": "value",
                  "value": "39.1"
                }
              }
            ],
            [
              "oxygen_partial_pressure",
              {
                "locator": {
                  "equation": "7",
                  "published_page": 280
                },
                "state": {
                  "tag": "value",
                  "value": "0.000000022826"
                }
              }
            ],
            [
              "original",
              {
                "locator": {
                  "equation": "7",
                  "published_page": 280
                },
                "state": {
                  "tag": "value",
                  "value": "0.000000022826"
                }
              }
            ],
            [
              "factor",
              {
                "locator": {
                  "equation": "7",
                  "published_page": 280
                },
                "state": {
                  "tag": "value",
                  "value": "101325"
                }
              }
            ]
          ],
          "references": [],
          "relation": "congruent_vaporization_P_O2_atm=po2_over_pK_as_published*P_K_atm_as_published;P_O2/P_K=(1/4)*sqrt(M_O2/M_K)=0.2262;Plante_printed_ratio=0.226;atm_to_Pa"
        },
        "locator": {
          "equation": "7",
          "note": "DERIVED condition: congruent vaporization P_O2=0.226 P_K; not a measurement",
          "published_page": 280
        },
        "state": {
          "tag": "value",
          "value": {
            "approximate": false,
            "kind": "point",
            "point": "0.00231284445"
          }
        }
      },
      "printed_composition": {
        "locator": {
          "note": "Series 1214, row 37; columns T,K | Wt % K2O | P_K(atm).",
          "published_page": 278,
          "table": "2"
        },
        "state": {
          "tag": "value",
          "value": {
            "K2O": "6.76",
            "SiO2": "93.24"
          }
        }
      },
      "temperature_K": {
        "locator": {
          "note": "Series 1214, row 37; columns T,K | Wt % K2O | P_K(atm).",
          "published_page": 278,
          "table": "2"
        },
        "state": {
          "tag": "value",
          "value": "1335"
        }
      },
      "total_pressure_Pa": {
        "locator": {
          "note": "Series 1214, row 37; columns T,K | Wt % K2O | P_K(atm).",
          "published_page": 278,
          "table": "2"
        },
        "state": {
          "tag": "value",
          "value": "0.012548716215"
        }
      }
    },
    "queue": {
      "queued": false,
      "why": [
        "no queue entry"
      ]
    }
  },
  "robie-hemingway-fisher-1978-usgs-b1452:robie-hemingway-fisher-1978-usgs-b1452-0046-phase-04:S:shared:T=1517:col=entropy:v=d630877923:poly=reference": {
    "admission_status": "admitted",
    "evidence_class": {
      "tag": "value",
      "value": "compilation_assessed"
    },
    "identity": {
      "composition": {
        "tag": "not_applicable"
      },
      "exposure": {
        "tag": "not_applicable"
      },
      "fO2_Pa": {
        "tag": "not_applicable"
      },
      "formation_elements": {
        "tag": "not_applicable"
      },
      "per": {
        "tag": "value",
        "value": "mol_species"
      },
      "quantity": {
        "tag": "value",
        "value": "S"
      },
      "reaction": {
        "tag": "not_applicable"
      },
      "reference_state": {
        "tag": "not_applicable"
      },
      "reservoir": {
        "tag": "not_applicable"
      },
      "sample_mass_kg": {
        "tag": "not_applicable"
      },
      "species": {
        "charge": {
          "tag": "value",
          "value": 0
        },
        "formula": "Mn",
        "phase": {
          "tag": "value",
          "value": "cr"
        },
        "polymorph": {
          "tag": "value",
          "value": "reference"
        }
      },
      "standard_pressure_Pa": {
        "tag": "value",
        "value": "100000"
      },
      "subtype": {
        "tag": "not_applicable"
      },
      "sweep_gas": {
        "tag": "not_applicable"
      },
      "temperature_K": {
        "tag": "value",
        "value": "1517"
      },
      "total_pressure_Pa": {
        "tag": "not_applicable"
      },
      "wall": {
        "tag": "not_applicable"
      }
    },
    "locator": {
      "note": "as_published='91.86'; unit='J/mol·K' quoted from PDF page 36 (printed p. 30) unit row; captured units_as_published ignored; standard_pressure=1 bar from title (298.15 K and 1 bar (10^5 pascals) pressure)",
      "pdf_page_index": 78,
      "published_page": 72,
      "record": "robie-hemingway-fisher-1978-usgs-b1452-0046-phase-04",
      "source_path": "data/literature/compilations/robie-hemingway-fisher-1978-usgs-b1452/records/robie-hemingway-fisher-1978-usgs-b1452-0046-phase-04.json",
      "table": "robie-hemingway-fisher-1978-usgs-b1452-0046-phase-04"
    },
    "point_conditions": null,
    "queue": {
      "queued": false,
      "why": [
        "no queue entry"
      ]
    }
  }
}
    ''')
    differences = _field_differences("observations", expected, actual)
    assert not differences, "\n".join(differences)


def _assert_complete_store_regeneration_matches_checked_in_bytes() -> None:
    result = migrate(REPO_ROOT, write=False)
    stored = _store_output_file_bytes(REPO_ROOT)
    regenerated: dict[str, bytes] = {}

    def capture_writer_output(result, root: Path) -> None:
        write_outputs(result, root)
        regenerated.update(_store_output_file_bytes(root))

    with patch(
        "simulator.battery.migrate.write_outputs",
        side_effect=capture_writer_output,
    ):
        helper_differences = _store_regeneration_differences(REPO_ROOT, result)

    byte_differences = sorted(
        path
        for path in stored.keys() | regenerated.keys()
        if stored.get(path) != regenerated.get(path)
    )
    assert helper_differences == byte_differences
    assert byte_differences == [], _store_difference_details(
        stored, regenerated, byte_differences
    )

    report = regenerated["data/battery/migration-report.md"].decode("utf-8")
    headline = {
        "rows in": sum(count.rows_in for count in result.source_counts.values()),
        "records out (observations)": len(result.observations),
        "works": len(result.works),
        "experiments": len(result.experiments),
        "queue size": len(result.queue),
        "hard issues": len(result.validation.hard_issues),
    }
    for label, count in headline.items():
        assert f"{label}: {count}" in report
