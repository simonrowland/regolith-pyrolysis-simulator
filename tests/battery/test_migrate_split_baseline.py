"""Golden M0 migration pins; complementary to test_migrate.py's 137-row digest.

These pins cover a few representative source shapes before the migrate.py
codec, composition, and identity moves. Each run migrates one extract row or
one compilation table through the existing migration entry points. They call
``migrate(..., write=False)`` without ``observation_from_plain`` or
``load_migrated_store``, select one child per input, and the compilation helper
stops before ``Migrator.finalize``. This is not whole-store coverage or a
complete proof for M1-M3.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator, REPO_ROOT, migrate, to_plain


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


def _one_extract_row(tmp_path: Path, filename: str, observation_id: str):
    source_path = REPO_ROOT / "data/literature" / "extracts" / filename
    document = yaml.safe_load(source_path.read_text(encoding="utf-8"))
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
        value = plain["value"]
        plain["value"] = {
            key: value[key]
            for key in (
                "amount_basis",
                "basis",
                "components",
                "proxy_flag",
                "proxy_source",
                "analysis_selection_rule",
                "input_provenance",
            )
            if key in value
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
          "proxy_flag": "composition_from_sample_catalog"
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
            "proxy_flag": "composition_from_sample_catalog"
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
          ]
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
            ]
          }
        }
      },
      "fO2_Pa": {
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
          ]
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
            ]
          }
        }
      },
      "fO2_Pa": {
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
        "locator": {
          "note": "TABLE 1. Results of 12 tests with the prototype system from 2005",
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
    expected[
        "kems-026-markova-1984::markova_1984_table2_residual_melt_VI_lherzolite"
        "::mass_loss_fraction::T=2156.15:h=e9110f31a14b"
    ] = {"STUDIO_VALUE_REQUIRED": True}
    assert actual == expected, json.dumps(actual, sort_keys=True, separators=(",", ":"), default=str)
