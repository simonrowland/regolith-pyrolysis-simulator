#!/usr/bin/env python3
"""One-shot pilot migration: acquisition DRAFT blocks → literature extracts.

Sources:

* CEA sweep DRAFT → ``nasa-cea-thermo.yaml``
* vp-acquire-5 / vp-acquire-6 DRAFT YAML fences → per-source extract files

Reads research paths from the main worktree when the local clone lacks them
(docs-private research is often gitignored). Re-run is idempotent overwrite
of pilot extract files only.
"""

from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping

import yaml

ROOT = Path(__file__).resolve().parents[1]
EXTRACTS = ROOT / "data" / "literature" / "extracts"

# Prefer sibling main checkout for gitignored research drafts.
_CANDIDATE_MAINS = [
    ROOT,
    Path(
        "/Users/simonrowland/Library/CloudStorage/Dropbox/"
        "Starship Mission Design/Regolith Processing/"
        "regolith-pyrolysis-simulator"
    ),
]


def _find_research(*parts: str) -> Path | None:
    for base in _CANDIDATE_MAINS:
        p = base.joinpath(*parts)
        if p.is_file() or p.is_dir():
            return p
    return None


def _repo_relative(path: Path | str) -> str:
    """Normalize provenance to a repository-relative path (never machine-local)."""
    p = Path(path).resolve() if not isinstance(path, Path) else path.resolve()
    for base in _CANDIDATE_MAINS:
        try:
            return str(p.relative_to(base.resolve()))
        except ValueError:
            continue
    # Fall back: strip any absolute prefix that contains docs-private/
    s = str(p)
    marker = "docs-private/"
    if marker in s:
        return s[s.index(marker) :]
    return s


def _dump(path: Path, doc: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(dict(doc), sort_keys=False, allow_unicode=True, width=100),
        encoding="utf-8",
    )


def _yaml_fences(text: str) -> list[str]:
    return re.findall(r"```ya?ml\n(.*?)```", text, flags=re.S)


def _base_extract(
    source_id: str,
    *,
    citation: str,
    doi: str | None,
    url: str | None,
    year: int | None,
    method: str,
    date: str,
    worker: str,
    provenance_path: str,
    version: str | None = None,
) -> dict[str, Any]:
    source: dict[str, Any] = {"citation": citation}
    if doi:
        source["doi"] = doi
    if url:
        source["url"] = url
    if year is not None:
        source["year"] = year
    if version is not None:
        source["version"] = version
    return {
        "schema_version": "literature_extract.v1",
        "source_id": source_id,
        "source": source,
        "extraction": {
            "method": method,
            "date": date,
            "worker": worker,
            "provenance_path": _repo_relative(provenance_path),
        },
        "review_status": "draft",
        "species": {},
        "fidelity_samples": [],
    }


def _add_obs(
    doc: dict[str, Any],
    species_id: str,
    observation: Mapping[str, Any],
) -> None:
    block = doc["species"].setdefault(species_id, {"observations": []})
    # Drop null-only uncertainty artifacts (P1-F1 related NOT-FIXED nulls).
    obs = dict(observation)
    unc = obs.get("uncertainty")
    if isinstance(unc, Mapping):
        cleaned = {k: v for k, v in unc.items() if v is not None and v != ""}
        if cleaned:
            obs["uncertainty"] = cleaned
        else:
            obs.pop("uncertainty", None)
    elif unc is None or unc == "":
        obs.pop("uncertainty", None)
    # Dedupe by observation_id within the file (re-runs / multi-step merges).
    oid = obs.get("observation_id")
    if oid:
        for i, existing in enumerate(block["observations"]):
            if isinstance(existing, Mapping) and existing.get("observation_id") == oid:
                block["observations"][i] = obs
                return
    block["observations"].append(obs)


def _merge_species(dst: dict[str, Any], src: Mapping[str, Any]) -> None:
    """Merge species observations into dst, deduping by observation_id."""
    for sid, block in (src.get("species") or {}).items():
        if not isinstance(block, Mapping):
            continue
        for obs in block.get("observations") or []:
            if isinstance(obs, Mapping):
                _add_obs(dst, str(sid), obs)
    for sample in src.get("fidelity_samples") or []:
        samples = dst.setdefault("fidelity_samples", [])
        if sample not in samples:
            samples.append(sample)


def _add_fidelity(
    doc: dict[str, Any],
    *,
    path: str,
    value: Any,
    note: str,
    locator: Mapping[str, Any] | None = None,
) -> None:
    samples = doc.setdefault("fidelity_samples", [])
    sample: dict[str, Any] = {
        "path": path,
        "value": value,
        "note": note,
    }
    if locator:
        sample["locator"] = dict(locator)
    samples.append(sample)


def _corr_payload(corr: Mapping[str, Any]) -> dict[str, Any]:
    """Collect correlation payload: values map OR top-level numeric/structure keys.

    Null-hypothesis (P1-F3): migrator copied only ``corr["values"]``, so the 10/30
    DRAFT correlations that store coefficients/points/ΔH at the top level landed
    as ``values: {}``. Merge top-level payload keys so every DRAFT field is kept.
    """
    meta = {
        "id",
        "kind",
        "source",
        "locator",
        "valid_range_K",
        "phase",
        "standard_state",
        "values",
    }
    out: dict[str, Any] = {}
    raw_vals = corr.get("values")
    if isinstance(raw_vals, Mapping):
        out.update(raw_vals)
    elif raw_vals is not None:
        out["raw_values"] = raw_vals
    for k, v in corr.items():
        if k in meta:
            continue
        out[k] = v
    return out


def _kind_to_type(kind: str) -> str:
    k = (kind or "").lower()
    if "activity" in k or "henrian" in k or "speciation" in k:
        return "activity_coefficient"
    if "antoine" in k or "pressure_point" in k or "absolute_pressure" in k:
        return "psat_series"
    if "arrhenius" in k and "alpha" in k:
        return "alpha"
    return "gibbs_table"


def _route_dominant_source(
    cid: str,
    citation: str,
    *,
    lh84: dict[str, Any],
    lh87: dict[str, Any],
    sf18: dict[str, Any],
    extras: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Route a dominant-missing correlation to the correct per-source extract.

    Null-hypothesis (P1-F3 misfile): default bucket dumped NIST/Stull/BR72 into
    LH84. Attribution must follow the DRAFT source citation, not the host file.
    """
    cit = citation or ""
    cid_u = cid.upper()
    if "LH84" in cid_u or "1984" in cit or "Lamoreaux & Hildenbrand" in cit:
        if "1987" in cit or "LH87" in cid_u:
            return lh87
        return lh84
    if "LH87" in cid_u or "1987" in cit or "Hildenbrand & Brewer" in cit:
        return lh87
    if "SF18" in cid_u or "Sossi" in cit or "Fegley" in cit:
        return sf18
    if "NIST" in cit or "Stull" in cit or "WebBook" in cit or "SRD 69" in cit:
        return extras.setdefault(
            "nist-webbook",
            _base_extract(
                "nist-webbook",
                citation="NIST Chemistry WebBook, SRD 69 (and Stull 1947 compilations cited therein)",
                doi=None,
                url="https://webbook.nist.gov/chemistry/",
                year=None,
                method="manual transcription from vp-acquire-5 dominant-missing-12 DRAFT",
                date="2026-07-30",
                worker="t508-store-gk",
                provenance_path="docs-private/research/2026-07-30-vp-acquire-5/dominant-missing-12.md",
            ),
        )
    if "Behrens" in cit or "BR72" in cid_u or "Rosenblatt" in cit:
        return extras.setdefault(
            "behrens-rosenblatt-1972",
            _base_extract(
                "behrens-rosenblatt-1972",
                citation="Behrens & Rosenblatt, J. Chem. Thermodyn. 4 (1972) 175–190",
                doi=None,
                url=None,
                year=1972,
                method="manual transcription from vp-acquire-5 dominant-missing-12 DRAFT",
                date="2026-07-30",
                worker="t508-store-gk",
                provenance_path="docs-private/research/2026-07-30-vp-acquire-5/dominant-missing-12.md",
            ),
        )
    if "Habermann" in cit or "Hab64" in cid_u:
        return extras.setdefault(
            "habermann-daane-1964",
            _base_extract(
                "habermann-daane-1964",
                citation="Habermann & Daane, J. Chem. Phys. 41 (1964) 2818–2827",
                doi=None,
                url=None,
                year=1964,
                method="manual transcription from vp-acquire-5 dominant-missing-12 DRAFT",
                date="2026-07-30",
                worker="t508-store-gk",
                provenance_path="docs-private/research/2026-07-30-vp-acquire-5/dominant-missing-12.md",
            ),
        )
    if "Berkowitz" in cit or "BIC57" in cid_u or "Chupka" in cit:
        return extras.setdefault(
            "berkowitz-chupka-inghram-1957",
            _base_extract(
                "berkowitz-chupka-inghram-1957",
                citation="Berkowitz, Chupka & Inghram, J. Chem. Phys. 27 (1957) 87–90",
                doi=None,
                url=None,
                year=1957,
                method="manual transcription from vp-acquire-5 dominant-missing-12 DRAFT",
                date="2026-07-30",
                worker="t508-store-gk",
                provenance_path="docs-private/research/2026-07-30-vp-acquire-5/dominant-missing-12.md",
            ),
        )
    if "Ames" in cit or "Ames67" in cid_u:
        return extras.setdefault(
            "ames-walsh-white-1967",
            _base_extract(
                "ames-walsh-white-1967",
                citation="Ames, Walsh & White, J. Phys. Chem. 71 (1967) 2707–2718",
                doi=None,
                url=None,
                year=1967,
                method="manual transcription from vp-acquire-5 dominant-missing-12 DRAFT",
                date="2026-07-30",
                worker="t508-store-gk",
                provenance_path="docs-private/research/2026-07-30-vp-acquire-5/dominant-missing-12.md",
            ),
        )
    if "Banchorn" in cit or "Ban86" in cid_u:
        return extras.setdefault(
            "banchor-matsui-naito-1986",
            _base_extract(
                "banchor-matsui-naito-1986",
                citation="Banchornheavakul, Matsui & Naito, J. Nucl. Sci. Technol. 23 (1986) 602–611",
                doi=None,
                url=None,
                year=1986,
                method="manual transcription from vp-acquire-5 dominant-missing-12 DRAFT",
                date="2026-07-30",
                worker="t508-store-gk",
                provenance_path="docs-private/research/2026-07-30-vp-acquire-5/dominant-missing-12.md",
            ),
        )
    if "LH" in cid_u:
        return lh84
    # Unattributed rows stay under LH84 only when the host tranche is LH;
    # otherwise create a misc bucket keyed by corr id prefix.
    return lh84


def ensure_fidelity_samples(doc: dict[str, Any]) -> None:
    """Ensure ≥1 fidelity sample for any extract that carries observations."""
    n_obs = 0
    first_path = None
    first_val = None
    first_loc = None
    for sid, block in (doc.get("species") or {}).items():
        if not isinstance(block, Mapping):
            continue
        for obs in block.get("observations") or []:
            if not isinstance(obs, Mapping):
                continue
            n_obs += 1
            if first_path is None:
                oid = obs.get("observation_id") or "obs"
                vals = obs.get("values")
                first_path = f"species.{sid}.observations[{oid}].values"
                first_val = vals
                first_loc = obs.get("locator")
    if n_obs == 0:
        return
    samples = doc.get("fidelity_samples")
    if isinstance(samples, list) and samples:
        return
    _add_fidelity(
        doc,
        path=str(first_path),
        value=first_val,
        note="auto fidelity sample from first observation payload (pilot migration)",
        locator=first_loc if isinstance(first_loc, Mapping) else None,
    )


# ---------------------------------------------------------------------------
# CEA
# ---------------------------------------------------------------------------


def migrate_cea() -> Path | None:
    draft_path = _find_research(
        "docs-private", "research", "2026-08-01-cea-sweep", "vp-cea-u0-hits-DRAFT.yaml"
    )
    if draft_path is None:
        print("WARN: CEA DRAFT not found; skip nasa-cea-thermo", file=sys.stderr)
        return None
    draft = yaml.safe_load(draft_path.read_text(encoding="utf-8"))
    families = draft.get("families") or {}
    doc = _base_extract(
        "nasa-cea-thermo",
        citation=(
            "McBride, B. J., Zehe, M. J. & Gordon, S., NASA Glenn Coefficients for "
            "Calculating Thermodynamic Properties of Individual Species, "
            "NASA TP-2002-211556; NASA CEA thermo.inp (Glenn coefficient database)."
        ),
        doi=None,
        url="https://www.grc.nasa.gov/www/CEAWeb/",
        year=2002,
        method="tools/vp_cea_ingest.py from thermo.inp; pilot migrate of DRAFT rows",
        date="2026-08-01",
        worker="t508-store-gk",
        provenance_path=_repo_relative(draft_path),
        version="thermo.inp@2026-08-01-cea-sweep",
    )
    # Cap pilot size: all families, but store thermo_record as gibbs_table values
    ag_ref = None
    for fam_key, fam in families.items():
        if not isinstance(fam, Mapping):
            continue
        phys = fam.get("physical_properties") or {}
        species_map = phys.get("species") or {}
        for sid, row in species_map.items():
            if not isinstance(row, Mapping):
                continue
            models = row.get("pressure_models") or []
            if not models:
                continue
            model = models[0]
            thermo = model.get("thermo_record") or {}
            cea_name = thermo.get("cea_name") or sid
            valid = model.get("valid_domain") or {}
            tmin = valid.get("T_min_K")
            tmax = valid.get("T_max_K")
            tr = [tmin, tmax] if tmin is not None and tmax is not None else None
            # Preserve source_ref_code verbatim (DRAFT may carry "g10/97").
            src_ref = thermo.get("source_ref_code")
            if str(sid) == "Ag":
                ag_ref = src_ref
            _add_obs(
                doc,
                str(sid),
                {
                    "observation_id": f"cea_{cea_name}_gibbs",
                    "type": "gibbs_table",
                    "locator": {
                        "source_path": "docs-private/research/2026-08-01-cea-sweep/thermo.inp",
                        "record": str(cea_name),
                        "note": (
                            "NASA CEA thermo.inp coefficient record; segments preserved "
                            "verbatim from vp-cea-u0-hits-DRAFT.yaml"
                        ),
                    },
                    "T_range_K": tr,
                    "phase": model.get("standard_state") or thermo.get("standard_state"),
                    "standard_state": (
                        f"CEA/JANAF P°={thermo.get('reference_pressure_Pa', 1.0e5)} Pa; "
                        f"phase_flag={thermo.get('phase_flag')}"
                    ),
                    "regime": "gas_standard_state_thermo",
                    "units": "NASA CEA polynomial (Cp/R, H/RT, S/R); delta_f_H in J/mol",
                    "uncertainty": {
                        "note": "Source evaluation uncertainties not restated in thermo.inp rows"
                    },
                    "values": {
                        "cea_name": cea_name,
                        "evaluator_family": thermo.get("evaluator_family"),
                        "formula": thermo.get("formula") or row.get("formula"),
                        "molecular_weight_g_per_mol": thermo.get(
                            "molecular_weight_g_per_mol"
                        ),
                        "delta_f_H_298_15_J_per_mol": thermo.get(
                            "delta_f_H_298_15_J_per_mol"
                        ),
                        "source_ref_code": src_ref,
                        "citation": thermo.get("citation"),
                        "reference_pressure_Pa": thermo.get("reference_pressure_Pa"),
                        "segments": thermo.get("segments"),
                    },
                },
            )
    if ag_ref is not None:
        _add_fidelity(
            doc,
            path="species.Ag.observations[cea_Ag_gibbs].values.source_ref_code",
            value=ag_ref,
            note="CEA Ag source_ref_code preserved verbatim from DRAFT",
            locator={"record": "Ag", "source_path": _repo_relative(draft_path)},
        )
    ensure_fidelity_samples(doc)
    out = EXTRACTS / "nasa-cea-thermo.yaml"
    _dump(out, doc)
    print(f"wrote {out} ({len(doc['species'])} species)")
    return out


# ---------------------------------------------------------------------------
# LH84 / LH87 from dominant-missing-12
# ---------------------------------------------------------------------------


def migrate_lh84_from_dominant_missing() -> list[Path]:
    path = _find_research(
        "docs-private",
        "research",
        "2026-07-30-vp-acquire-5",
        "dominant-missing-12.md",
    )
    if path is None:
        print("WARN: dominant-missing-12.md missing", file=sys.stderr)
        return []
    fences = _yaml_fences(path.read_text(encoding="utf-8"))
    if len(fences) < 2:
        print("WARN: expected DRAFT yaml fence in dominant-missing-12", file=sys.stderr)
        return []
    draft = yaml.safe_load(fences[1])
    root = draft.get("vp_dominant_missing_12_DRAFT") or draft
    rows = (root.get("rows") if isinstance(root, Mapping) else None) or {}

    # Bucket correlations by source family
    lh84 = _base_extract(
        "lamoreaux-hildenbrand-1984",
        citation=(
            "Lamoreaux, R. H. & Hildenbrand, D. L., High Temperature Vaporization "
            "Behavior of Oxides. I. Alkali Metal Binary Oxides, "
            "J. Phys. Chem. Ref. Data 13 (1984) 151–173"
        ),
        doi="10.1063/1.555706",
        url="https://doi.org/10.1063/1.555706",
        year=1984,
        method="manual transcription from vp-acquire-5 dominant-missing-12 DRAFT",
        date="2026-07-30",
        worker="t508-store-gk",
        provenance_path="docs-private/research/2026-07-30-vp-acquire-5/dominant-missing-12.md",
    )
    lh87 = _base_extract(
        "lamoreaux-hildenbrand-hildenbrand-1987",
        citation=(
            "Lamoreaux, R. H., Hildenbrand, D. L. & Brewer, L., High-Temperature "
            "Vaporization Behavior of Oxides II. Oxides of Be, Mg, Ca, Sr, Ba, B, "
            "Al, Ga, In, Tl, Si, Ge, Sn, Pb, Zn, Cd, and Hg, "
            "J. Phys. Chem. Ref. Data 16 (1987) 419–443"
        ),
        doi="10.1063/1.555799",
        url="https://doi.org/10.1063/1.555799",
        year=1987,
        method="manual transcription from vp-acquire-5 dominant-missing-12 DRAFT",
        date="2026-07-30",
        worker="t508-store-gk",
        provenance_path="docs-private/research/2026-07-30-vp-acquire-5/dominant-missing-12.md",
    )
    sf18 = _base_extract(
        "sossi-fegley-2018",
        citation=(
            "Sossi, P. A. & Fegley, B. Jr., Thermodynamics of Element Volatility "
            "and its Application to Planetary Processes, "
            "Reviews in Mineralogy and Geochemistry 84 (2018) 393–459"
        ),
        doi="10.2138/rmg.2018.84.11",
        url="https://doi.org/10.2138/rmg.2018.84.11",
        year=2018,
        method="manual transcription from vp-acquire-5 dominant-missing-12 DRAFT",
        date="2026-07-30",
        worker="t508-store-gk",
        provenance_path="docs-private/research/2026-07-30-vp-acquire-5/dominant-missing-12.md",
    )

    extras: dict[str, dict[str, Any]] = {}

    for row_key, row in rows.items():
        if not isinstance(row, Mapping):
            continue
        # Prefer U0-ish gas id from row key
        species_id = str(row_key).replace("_g", "")
        if species_id.endswith("(g)"):
            species_id = species_id[:-3]

        for corr in row.get("correlations") or []:
            if not isinstance(corr, Mapping):
                continue
            src = corr.get("source") or {}
            if not isinstance(src, Mapping):
                src = {}
            citation = str(src.get("citation") or "")
            locator_text = src.get("locator") or corr.get("id")
            values = _corr_payload(corr)
            kind = str(corr.get("kind") or "standard_reaction_thermochemistry")
            otype = _kind_to_type(kind)

            cid = str(corr.get("id") or "")
            target = _route_dominant_source(
                cid, citation, lh84=lh84, lh87=lh87, sf18=sf18, extras=extras
            )

            vr = corr.get("valid_range_K")
            obs_id = re.sub(r"[^A-Za-z0-9_]+", "_", cid) or f"{species_id}_{otype}"
            units = "kJ/mol and J/(mol·K) for formation; over_R in kK as published"
            if otype == "psat_series":
                units = str(
                    corr.get("source_form")
                    or values.get("source_form")
                    or "P as published (bar / Pa / mmHg per source_form)"
                )
            unc: dict[str, Any] = {}
            for uk in (
                "Delta_f_H_298_over_R_uncertainty_kK",
                "S_298_over_R_uncertainty",
                "mid_range_Delta_sub_H_uncertainty_kcal_mol",
                "third_law_Delta_sub_H_298_uncertainty_kcal_mol",
            ):
                if values.get(uk) is not None:
                    unc[uk] = values.get(uk)
            # Also surface citation-level uncertainty notes when present
            if values.get("warning"):
                unc["warning"] = values.get("warning")

            _add_obs(
                target,
                species_id,
                {
                    "observation_id": obs_id,
                    "type": otype,
                    "locator": {
                        "table": locator_text
                        if isinstance(locator_text, str) and "Table" in str(locator_text)
                        else None,
                        "note": str(locator_text) if locator_text is not None else citation[:240],
                        "record": cid,
                    },
                    "T_range_K": list(vr)
                    if isinstance(vr, (list, tuple)) and len(vr) == 2
                    else None,
                    "phase": corr.get("phase")
                    or corr.get("phase_branch")
                    or row.get("phase_gas")
                    or "gas",
                    "standard_state": (corr.get("standard_state") or {}).get("pressure")
                    if isinstance(corr.get("standard_state"), Mapping)
                    else corr.get("standard_state"),
                    "units": units,
                    "uncertainty": unc or None,
                    "values": values,
                },
            )
            # Pin one LH84 fidelity sample when coefficients present
            if target is lh84 and "coefficients" in values and not doc_has_fidelity(lh84):
                _add_fidelity(
                    lh84,
                    path=f"species.{species_id}.observations[{obs_id}].values.coefficients",
                    value=values.get("coefficients"),
                    note=f"DRAFT top-level coefficients retained for {cid}",
                    locator={"record": cid},
                )

        # Speciation notes from SF18 as qualitative activity/order observations
        note = row.get("speciation_note") or row.get("closure_anchor")
        if note and ("SF18" in str(note) or "SF18" in str(row.get("closure_anchor") or "")):
            _add_obs(
                sf18,
                species_id,
                {
                    "observation_id": f"sf18_speciation_{species_id}",
                    "type": "activity_coefficient",
                    "locator": {
                        "note": str(row.get("closure_anchor") or note)[:240],
                        "section": "SF18-T1 / speciation ordering",
                    },
                    "phase": "gas_over_oxide",
                    "units": "dimensionless ordering (not a numeric gamma)",
                    "values": {
                        "speciation_note": str(note)[:500],
                        "semantics": "bound_not_point_ordering",
                    },
                },
            )

    outs = []
    for d in (lh84, lh87, sf18, *extras.values()):
        if d["species"]:
            ensure_fidelity_samples(d)
            p = EXTRACTS / f"{d['source_id']}.yaml"
            # Merge into existing nist-webbook / other hand+migrated files
            if p.is_file() and d["source_id"] in {"nist-webbook"}:
                prev = yaml.safe_load(p.read_text(encoding="utf-8"))
                if isinstance(prev, Mapping) and isinstance(prev.get("species"), Mapping):
                    base = dict(prev)
                    base.setdefault("species", {})
                    _merge_species(base, d)
                    ensure_fidelity_samples(base)
                    d = base  # type: ignore[assignment]
            _dump(p, d)

            print(f"wrote {p} ({len(d['species'])} species)")
            outs.append(p)
    return outs


def doc_has_fidelity(doc: Mapping[str, Any]) -> bool:
    samples = doc.get("fidelity_samples")
    return isinstance(samples, list) and len(samples) > 0


# ---------------------------------------------------------------------------
# P-carriers DRAFT
# ---------------------------------------------------------------------------


def migrate_p_carriers() -> list[Path]:
    """Migrate P-carrier DRAFT rows under real species ids (not list-index keys).

    Null-hypothesis (P1-F2): recursive walk keyed species by the trailing path
    segment (``tabulated_delta_fG_kJ_mol[0]``), mistyped ΔfG as psat_series via
    case-sensitive ``"Delta_f" in str(vals)``, and wrote locator note ``'{}'``.
    """
    path = _find_research(
        "docs-private", "research", "2026-07-30-vp-acquire-5", "P-carriers.md"
    )
    if path is None:
        return []
    fences = _yaml_fences(path.read_text(encoding="utf-8"))
    if not fences:
        return []
    draft = yaml.safe_load(fences[0])
    root = draft.get("vp_p_carriers_DRAFT") or draft
    rows = (root.get("rows") if isinstance(root, Mapping) else None) or {}

    janaf = _base_extract(
        "janaf-4th",
        citation=(
            "Chase, M. W. Jr., NIST-JANAF Thermochemical Tables, 4th Edition, "
            "J. Phys. Chem. Ref. Data Monograph 9 (1998)"
        ),
        doi=None,
        url="https://janaf.nist.gov/",
        year=1998,
        method="manual transcription from vp-acquire-5 P-carriers DRAFT",
        date="2026-07-30",
        worker="t508-store-gk",
        provenance_path="docs-private/research/2026-07-30-vp-acquire-5/P-carriers.md",
        version="4th",
    )
    sf18 = _base_extract(
        "sossi-fegley-2018",
        citation=(
            "Sossi, P. A. & Fegley, B. Jr., Thermodynamics of Element Volatility "
            "and its Application to Planetary Processes, "
            "Reviews in Mineralogy and Geochemistry 84 (2018) 393–459"
        ),
        doi="10.2138/rmg.2018.84.11",
        url="https://doi.org/10.2138/rmg.2018.84.11",
        year=2018,
        method="manual transcription from vp-acquire-5 P-carriers DRAFT",
        date="2026-07-30",
        worker="t508-store-gk",
        provenance_path="docs-private/research/2026-07-30-vp-acquire-5/P-carriers.md",
    )

    po_table_sample = None
    for species_id, block in rows.items():
        if not isinstance(block, Mapping):
            continue
        sid = str(species_id)
        janaf_meta = block.get("janaf") if isinstance(block.get("janaf"), Mapping) else {}
        table_id = janaf_meta.get("table_id") if isinstance(janaf_meta, Mapping) else None
        janaf_src = (
            (janaf_meta.get("source") or {})
            if isinstance(janaf_meta, Mapping)
            else {}
        )
        janaf_citation = (
            janaf_src.get("citation")
            if isinstance(janaf_src, Mapping)
            else None
        ) or "Chase (1998) NIST-JANAF 4th ed."

        # 298 K JANAF anchors from the janaf: block when present
        if isinstance(janaf_meta, Mapping) and (
            janaf_meta.get("delta_fG_298_kJ_mol") is not None
            or janaf_meta.get("delta_fH_298_kJ_mol") is not None
        ):
            _add_obs(
                janaf,
                sid,
                {
                    "observation_id": f"janaf_{sid}_298_anchors",
                    "type": "gibbs_table",
                    "locator": {
                        "table": table_id,
                        "record": f"JANAF {table_id or sid}",
                        "note": str(janaf_citation)[:300],
                        "source_path": janaf_meta.get("url"),
                    },
                    "phase": block.get("phase") or "gas",
                    "standard_state": janaf_meta.get("standard_pressure"),
                    "units": "kJ/mol (ΔfH, ΔfG); J/(mol·K) (S)",
                    "uncertainty": {
                        k: janaf_meta[k]
                        for k in (
                            "uncertainty_flag",
                            "CODATA_delta_fH_uncertainty_kJ_mol",
                        )
                        if janaf_meta.get(k) is not None
                    }
                    or None,
                    "values": {
                        k: janaf_meta[k]
                        for k in janaf_meta
                        if k not in ("source", "url", "webbook_url")
                    },
                },
            )

        for corr in block.get("correlations") or []:
            if not isinstance(corr, Mapping):
                continue
            cid = str(corr.get("id") or f"{sid}_corr")
            kind = str(corr.get("kind") or "")
            kind_l = kind.lower()
            src = corr.get("source") if isinstance(corr.get("source"), Mapping) else {}
            citation = str((src or {}).get("citation") or janaf_citation)

            # Route SF18 / non-JANAF reaction rows out of the JANAF extract.
            is_janaf = (
                "janaf" in kind_l
                or "JANAF" in cid
                or "Chase" in citation
                or "tabulated_delta_fG" in corr
                or "tabulated_delta_fg" in {k.lower() for k in corr}
            )
            is_sf18 = "SF18" in cid or "Sossi" in citation or "Fegley" in citation
            target = sf18 if is_sf18 and not is_janaf else (janaf if is_janaf else sf18)
            if not is_janaf and not is_sf18:
                # Keep reaction windows as SF18-adjacent unless clearly JANAF
                target = janaf if "JANAF" in cid.upper() else sf18

            payload = _corr_payload(corr)
            # Case-insensitive ΔfG detection (P1-F2 mistype fix).
            payload_str = str(payload).lower()
            has_delta_f = (
                "delta_f" in payload_str
                or "delta_fg" in payload_str
                or "formation" in kind_l
                or "tabulated_delta_fg" in {k.lower() for k in corr}
            )
            if "antoine" in kind_l or "pressure" in kind_l and "point" in kind_l:
                otype = "psat_series"
                units = "Pa / bar as published"
            elif has_delta_f or "reaction" in kind_l or "formation" in kind_l:
                otype = "gibbs_table"
                units = "kJ/mol (ΔfG / formation tabulation as published)"
            else:
                otype = _kind_to_type(kind)
                units = "as published in P-carriers DRAFT"

            vr = corr.get("valid_range_K")
            loc_note = citation
            _add_obs(
                target,
                sid,
                {
                    "observation_id": re.sub(r"[^A-Za-z0-9_]+", "_", cid)[:80],
                    "type": otype,
                    "locator": {
                        "table": table_id if target is janaf else None,
                        "note": loc_note[:300],
                        "record": cid,
                        "source_path": janaf_meta.get("url")
                        if isinstance(janaf_meta, Mapping) and target is janaf
                        else None,
                    },
                    "T_range_K": list(vr)
                    if isinstance(vr, (list, tuple)) and len(vr) == 2
                    else None,
                    "phase": corr.get("phase_branch") or block.get("phase") or "gas",
                    "standard_state": corr.get("standard_pressure")
                    or (
                        janaf_meta.get("standard_pressure")
                        if isinstance(janaf_meta, Mapping)
                        else None
                    ),
                    "units": units,
                    "values": payload,
                },
            )
            if sid == "PO" and "tabulated_delta_fG_kJ_mol" in corr:
                series = corr.get("tabulated_delta_fG_kJ_mol")
                if isinstance(series, list) and series:
                    po_table_sample = series[0]

    if po_table_sample is not None:
        _add_fidelity(
            janaf,
            path="species.PO.observations[JANAF1998_PO_formation_tabulation].values.tabulated_delta_fG_kJ_mol[0]",
            value=po_table_sample,
            note="JANAF PO ΔfG grid point 0 retained under species PO (not list-index key)",
            locator={"table": "O-004", "record": "JANAF1998_PO_formation_tabulation"},
        )

    outs: list[Path] = []
    for doc in (janaf, sf18):
        if not doc["species"]:
            continue
        ensure_fidelity_samples(doc)
        p = EXTRACTS / f"{doc['source_id']}.yaml"
        # janaf-4th from P-carriers is the species-keyed rewrite: do NOT merge the
        # prior walk-shattered pseudo-species keys (P1-F2). Overwrite clean.
        if doc["source_id"] == "janaf-4th":
            # Drop any accidental non-species keys if present
            clean_species = {
                k: v
                for k, v in doc["species"].items()
                if "[" not in str(k) and "tabulated_" not in str(k)
            }
            doc["species"] = clean_species
            ensure_fidelity_samples(doc)
            _dump(p, doc)
            print(f"wrote {p} ({len(doc['species'])} species) [clean rewrite]")
            outs.append(p)
            continue
        if p.is_file():
            prev = yaml.safe_load(p.read_text(encoding="utf-8"))
            if isinstance(prev, Mapping) and isinstance(prev.get("species"), Mapping):
                base = dict(prev)
                base.setdefault("species", {})
                _merge_species(base, doc)
                ensure_fidelity_samples(base)
                doc = base  # type: ignore[assignment]
        _dump(p, doc)
        print(f"wrote {p} ({len(doc['species'])} species)")
        outs.append(p)
    return outs


# ---------------------------------------------------------------------------
# Chloride dimers (Datz 1961)
# ---------------------------------------------------------------------------


def migrate_chloride_datz() -> Path | None:
    path = _find_research(
        "docs-private",
        "research",
        "2026-07-30-vp-acquire-5",
        "chloride-dimers-and-gates.md",
    )
    if path is None:
        return None
    fences = _yaml_fences(path.read_text(encoding="utf-8"))
    if not fences:
        return None
    draft = yaml.safe_load(fences[0])
    node = draft.get("Na2Cl2_dissociation") or draft
    doc = _base_extract(
        "datz-and-smith-1961",
        citation=(
            "Datz, S. & Smith, W. T. Jr. (and related Datz 1961 tabulations) — "
            "Na2Cl2 dissociation equilibrium constants (Table II lineage) as "
            "transcribed in vp-acquire-5 chloride-dimers DRAFT"
        ),
        doi=None,
        url=None,
        year=1961,
        method="manual transcription from vp-acquire-5 chloride-dimers DRAFT",
        date="2026-07-30",
        worker="t508-store-gk",
        provenance_path="docs-private/research/2026-07-30-vp-acquire-5/chloride-dimers-and-gates.md",
    )
    for corr in (node.get("correlations") or []) if isinstance(node, Mapping) else []:
        if not isinstance(corr, Mapping):
            continue
        cid = str(corr.get("id") or "datz_corr")
        _add_obs(
            doc,
            "Na2Cl2",
            {
                "observation_id": re.sub(r"[^A-Za-z0-9_]+", "_", cid),
                "type": "gibbs_table",
                "locator": {
                    "table": "II" if "TableII" in cid or "Table II" in cid else None,
                    "note": f"Datz 1961 correlation {cid}",
                    "record": cid,
                },
                "phase": "gas",
                "standard_state": "Kd in mol/L as published",
                "units": "mol/L (Kd); log10 form as published",
                "values": {
                    "reaction": node.get("reaction"),
                    "form": corr.get("form"),
                    "acquisition_status": node.get("acquisition_status"),
                    **{
                        k: corr[k]
                        for k in corr
                        if k not in ("id", "form")
                    },
                },
            },
        )
        # Also record monomer side species for coverage
        _add_obs(
            doc,
            "NaCl",
            {
                "observation_id": re.sub(r"[^A-Za-z0-9_]+", "_", cid) + "_nacl_side",
                "type": "gibbs_table",
                "locator": {
                    "table": "II" if "TableII" in cid else None,
                    "note": f"NaCl monomer side of {cid}",
                    "record": cid,
                },
                "phase": "gas",
                "units": "mol/L (Kd); log10 form as published",
                "values": {
                    "reaction": node.get("reaction"),
                    "role": "monomer_product",
                    "form": corr.get("form"),
                    "semantics": "pointer_to_dimer_equilibrium",
                },
            },
        )

    # Competing observation retained (P1 cx: recorded_disagreement must not be dropped).
    # VALUE-PRECEDENCE: do not average; surface as a separate competing row.
    disagreements = (
        node.get("recorded_disagreement") if isinstance(node, Mapping) else None
    ) or []
    for drow in disagreements:
        if not isinstance(drow, Mapping):
            continue
        did = str(drow.get("id") or "recorded_disagreement")
        _add_obs(
            doc,
            "Na2Cl2",
            {
                "observation_id": re.sub(r"[^A-Za-z0-9_]+", "_", did),
                "type": "gibbs_table",
                "locator": {
                    "note": str(drow.get("note") or did)[:300],
                    "record": did,
                    "section": "recorded_disagreement",
                },
                "phase": "gas",
                "standard_state": "Kp / Shomate (JANAF/WebBook competing evaluation)",
                "units": "dimensionless Kp ratio note (not averaged with Datz)",
                "uncertainty": {
                    "note": str(drow.get("note") or "competing evaluation; do not average")
                },
                "values": {
                    "competitor_id": did,
                    "note": drow.get("note"),
                    "doi_monograph": drow.get("doi_monograph"),
                    "webbook_ids": drow.get("webbook_ids"),
                    "semantics": "competing_observation_do_not_average",
                    # Keep every DRAFT field under values (parity).
                    **{
                        k: drow[k]
                        for k in drow
                        if k not in ("id", "note", "doi_monograph", "webbook_ids")
                    },
                },
            },
        )

    if not doc["species"]:
        return None
    _add_fidelity(
        doc,
        path="species.Na2Cl2.observations[Datz1961_TableII_Kd].values.form",
        value=(
            (node.get("correlations") or [{}])[0].get("form")
            if isinstance(node, Mapping)
            else None
        ),
        note="Datz Table II Kd form retained; recorded_disagreement also migrated",
        locator={"table": "II", "record": "Datz1961_TableII_Kd"},
    )
    ensure_fidelity_samples(doc)
    out = EXTRACTS / "datz-and-smith-1961.yaml"
    _dump(out, doc)
    print(f"wrote {out} ({len(doc['species'])} species)")
    return out


# ---------------------------------------------------------------------------
# Alpha kinetics (acquire-6)
# ---------------------------------------------------------------------------


def migrate_alpha_kinetics() -> list[Path]:
    path = _find_research(
        "docs-private", "research", "2026-08-01-vp-acquire-6", "alpha-kinetics.md"
    )
    if path is None:
        return []
    fences = _yaml_fences(path.read_text(encoding="utf-8"))
    if not fences:
        return []
    draft = yaml.safe_load(fences[0])
    root = draft.get("vp_alpha_kinetics_DRAFT") or draft
    records = root.get("records") or []

    by_source: dict[str, dict[str, Any]] = {}

    def ensure(
        source_id: str,
        citation: str,
        doi: str | None,
        url: str | None,
        year: int | None,
    ) -> dict[str, Any]:
        if source_id not in by_source:
            by_source[source_id] = _base_extract(
                source_id,
                citation=citation,
                doi=doi,
                url=url,
                year=year,
                method="manual transcription from vp-acquire-6 alpha-kinetics DRAFT",
                date="2026-08-01",
                worker="t508-store-gk",
                provenance_path="docs-private/research/2026-08-01-vp-acquire-6/alpha-kinetics.md",
            )
        return by_source[source_id]

    for rec in records:
        if not isinstance(rec, Mapping):
            continue
        citation = str(rec.get("citation") or "UNKNOWN")
        doi_or_url = str(rec.get("doi_or_url") or "")
        doi = None
        url = None
        if "doi.org/" in doi_or_url:
            doi = doi_or_url.split("doi.org/")[-1]
            url = doi_or_url
        elif doi_or_url.startswith("10."):
            doi = doi_or_url
            url = f"https://doi.org/{doi}"
        elif doi_or_url:
            url = doi_or_url

        rid = str(rec.get("record_id") or "alpha_rec")
        # Map record_id prefix → source_id
        if rid.startswith("costa"):
            source_id = "costa-jacobson-2015"
            year = 2015
        elif rid.startswith("fedkin") or "fedkin" in rid:
            source_id = "fedkin-grossman-ghiorso-2006"
            year = 2006
        elif rid.startswith("sossi_2019") or "sossi_2019" in rid:
            source_id = "sossi-et-al-2019"
            year = 2019
        elif "richter" in rid:
            # DRAFT record_id is richter_2007_*; year 2007 GCA 71:5544 (P2-V2).
            source_id = "richter-et-al-2007"
            year = 2007
        elif "hashimoto" in rid:
            source_id = "hashimoto-1983"
            year = 1983
        elif "wetzel" in rid:
            source_id = "wetzel-gail-2013-sio-arrhenius"
            year = 2013
        else:
            # Stable slug from first author token
            slug = re.sub(r"[^a-z0-9]+", "-", rid.lower()).strip("-")[:60]
            source_id = slug or "alpha-unknown-source"
            year = None

        doc = ensure(source_id, citation, doi, url, year)
        species = str(rec.get("species") or "UNKNOWN")
        tr = rec.get("temperature_range_K")
        # Null-hypothesis (P1-F1): alpha_form was dropped → null alphas for
        # Wetzel/Richter Arrhenius records. Retain every DRAFT payload field.
        values: dict[str, Any] = {
            "alpha": rec.get("alpha_value"),
            "alpha_range": rec.get("alpha_range"),
            "gas_species": rec.get("gas_species"),
            "material": rec.get("material"),
        }
        if rec.get("alpha_form") is not None:
            values["alpha_form"] = rec.get("alpha_form")
        if rec.get("per_temperature"):
            values["per_temperature"] = rec.get("per_temperature")
        # Preserve remaining non-meta DRAFT keys under values for field-count parity.
        meta_keys = {
            "record_id",
            "species",
            "citation",
            "doi_or_url",
            "temperature_range_K",
            "phase",
            "standard_state_note",
            "regime",
            "uncertainty_note",
            "note",
            "alpha_value",
            "alpha_range",
            "alpha_form",
            "gas_species",
            "material",
            "per_temperature",
            "adoptable",
            "already_in_runtime",
            "already_in_sidecar",
            "certifies",
            "status",
        }
        for k, v in rec.items():
            if k in meta_keys:
                continue
            if k not in values and v is not None:
                values[f"draft_{k}"] = v

        equipment = None
        # Pilot: if draft had equipment, map it — most alpha DRAFT rows lack geometry;
        # leave equipment absent rather than invent. Equipment (when present) must
        # carry value+locator; do not invent apparatus numbers.

        unc_note = rec.get("uncertainty_note") or rec.get("note")
        # Also fold alpha_form uncertainty envelope into first-class uncertainty.
        unc: dict[str, Any] = {}
        if unc_note:
            unc["note"] = unc_note
        form = rec.get("alpha_form")
        if isinstance(form, Mapping):
            if form.get("uncertainty_envelope") is not None:
                unc["uncertainty_envelope"] = form.get("uncertainty_envelope")
            if form.get("E_uncertainty_J_per_mol") is not None:
                unc["E_uncertainty_J_per_mol"] = form.get("E_uncertainty_J_per_mol")
        if rec.get("alpha_range") is not None:
            unc["alpha_range"] = rec.get("alpha_range")

        obs: dict[str, Any] = {
            "observation_id": rid,
            "type": "alpha",
            "locator": {
                "note": citation[:240],
                "record": rid,
            },
            "T_range_K": list(tr) if isinstance(tr, (list, tuple)) and len(tr) == 2 else None,
            "phase": rec.get("phase"),
            "standard_state": rec.get("standard_state_note"),
            "regime": rec.get("regime"),
            "units": "dimensionless",
            "values": values,
        }
        if unc:
            obs["uncertainty"] = unc
        if equipment:
            obs["equipment"] = equipment
        _add_obs(doc, species, obs)
        if rec.get("alpha_form") is not None:
            _add_fidelity(
                doc,
                path=f"species.{species}.observations[{rid}].values.alpha_form",
                value=rec.get("alpha_form"),
                note="alpha_form retained verbatim from alpha-kinetics DRAFT (P1-F1 fix)",
                locator={"record": rid},
            )

        # Hashimoto free-evaporation rate points when present on Fedkin Fe table
        if rec.get("per_temperature") and source_id == "fedkin-grossman-ghiorso-2006":
            _add_obs(
                doc,
                species,
                {
                    "observation_id": rid + "_per_T_alpha_series",
                    "type": "rate_series",
                    "locator": {
                        "table": "3",
                        "note": "Fedkin 2006 Table 3 per-temperature alpha (from Hashimoto 1983)",
                        "record": rid,
                    },
                    "T_range_K": list(tr)
                    if isinstance(tr, (list, tuple)) and len(tr) == 2
                    else None,
                    "phase": rec.get("phase"),
                    "regime": rec.get("regime"),
                    "units": "dimensionless alpha vs T_K",
                    "values": {"series": rec.get("per_temperature")},
                },
            )

    outs = []
    for sid, doc in by_source.items():
        ensure_fidelity_samples(doc)
        p = EXTRACTS / f"{sid}.yaml"
        _dump(p, doc)
        print(f"wrote {p} ({len(doc['species'])} species)")
        outs.append(p)
    # Remove mislabeled 2011 Richter extract if we wrote the 2007 id (P2-V2).
    stale = EXTRACTS / "richter-et-al-2011.yaml"
    if stale.is_file() and (EXTRACTS / "richter-et-al-2007.yaml").is_file():
        stale.unlink()
        print(f"removed stale {stale.name} (renamed to richter-et-al-2007)")
    return outs


# ---------------------------------------------------------------------------
# Phase properties (transition points)
# ---------------------------------------------------------------------------


def migrate_phase_properties() -> list[Path]:
    path = _find_research(
        "docs-private", "research", "2026-08-01-vp-acquire-6", "phase-properties.md"
    )
    if path is None:
        return []
    fences = _yaml_fences(path.read_text(encoding="utf-8"))
    if not fences:
        return []
    # First fence is the main phase_properties_DRAFT
    draft = yaml.safe_load(fences[0])
    root = draft.get("phase_properties_DRAFT") or draft

    nist_path = EXTRACTS / "nist-webbook.yaml"
    if nist_path.is_file():
        nist = yaml.safe_load(nist_path.read_text(encoding="utf-8"))
        if not isinstance(nist, dict):
            nist = {}
        nist.setdefault("species", {})
        nist.setdefault("fidelity_samples", [])
    else:
        nist = _base_extract(
            "nist-webbook",
            citation="NIST Chemistry WebBook, SRD 69 (species thermochemistry pages)",
            doi=None,
            url="https://webbook.nist.gov/chemistry/",
            year=None,
            method="manual transcription from vp-acquire-6 phase-properties DRAFT",
            date="2026-08-01",
            worker="t508-store-gk",
            provenance_path="docs-private/research/2026-08-01-vp-acquire-6/phase-properties.md",
        )
    janaf_path = EXTRACTS / "janaf-4th.yaml"
    if janaf_path.is_file():
        janaf = yaml.safe_load(janaf_path.read_text(encoding="utf-8"))
        if not isinstance(janaf, dict):
            janaf = {}
        janaf.setdefault("species", {})
        janaf.setdefault("fidelity_samples", [])
    else:
        janaf = _base_extract(
            "janaf-4th",
            citation=(
                "Chase, M. W. Jr., NIST-JANAF Thermochemical Tables, 4th Edition, "
                "J. Phys. Chem. Ref. Data Monograph 9 (1998)"
            ),
            doi=None,
            url="https://janaf.nist.gov/",
            year=1998,
            method="manual transcription from vp-acquire-6 phase-properties DRAFT",
            date="2026-08-01",
            worker="t508-store-gk",
            provenance_path="docs-private/research/2026-08-01-vp-acquire-6/phase-properties.md",
            version="4th",
        )

    def absorb_phase_block(species_id: str, block: Mapping[str, Any], source_hint: str) -> None:
        target = janaf if "janaf" in source_hint.lower() else nist
        # melting / boiling
        for key, otype_prop in (
            ("melting_point_K", "melting_point"),
            ("boiling_point_K", "boiling_point"),
            ("triple_point_K", "triple_point"),
            ("T_b_K", "boiling_point"),
            ("T_m_K", "melting_point"),
        ):
            if key in block and block[key] is not None:
                _add_obs(
                    target,
                    species_id,
                    {
                        "observation_id": f"{species_id}_{key}",
                        "type": "transition_point",
                        "locator": {
                            "note": str(
                                block.get("source")
                                or block.get("citation")
                                or block.get(f"{key}_source")
                                or source_hint
                            )[:300],
                            "record": key,
                        },
                        "phase": block.get("phase"),
                        "units": "K",
                        "values": {
                            "property": otype_prop,
                            "T_K": block[key],
                        },
                    },
                )
        # nested phase_properties list
        for prop in block.get("phase_properties") or []:
            if not isinstance(prop, Mapping):
                continue
            pk = str(prop.get("property_kind") or prop.get("kind") or "transition")
            val = prop.get("value_K") or prop.get("T_K") or prop.get("value")
            if val is None:
                continue
            _add_obs(
                target,
                species_id,
                {
                    "observation_id": f"{species_id}_{pk}",
                    "type": "transition_point",
                    "locator": {
                        "note": str(prop.get("source") or prop.get("citation") or source_hint)[
                            :300
                        ],
                        "record": pk,
                    },
                    "units": prop.get("units") or "K",
                    "values": dict(prop),
                },
            )

    # Walk common draft layouts
    for section_key in (
        "species",
        "rows",
        "metals",
        "oxides",
        "foulants",
        "phase_rows",
    ):
        section = root.get(section_key) if isinstance(root, Mapping) else None
        if isinstance(section, Mapping):
            for sid, block in section.items():
                if isinstance(block, Mapping):
                    hint = str(
                        block.get("janaf_table")
                        or block.get("webbook")
                        or block.get("source")
                        or section_key
                    )
                    absorb_phase_block(str(sid), block, hint)

    # Also parse secondary fences that look like foulant_rows / volatile_static_rows
    for fence in fences[1:]:
        try:
            sub = yaml.safe_load(fence)
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(sub, Mapping):
            continue
        # foulant_rows: NaCl: {...}
        for top_k, top_v in sub.items():
            if not isinstance(top_v, Mapping):
                continue
            # if values look like species map
            sample = next(iter(top_v.values()), None) if top_v else None
            if isinstance(sample, Mapping) and (
                "formula" in sample or "phase_properties" in sample or "melting_point_K" in sample
            ):
                for sid, block in top_v.items():
                    if isinstance(block, Mapping):
                        hint = str(
                            block.get("janaf_table") or block.get("webbook") or top_k
                        )
                        absorb_phase_block(str(sid), block, hint)
            elif "formula" in top_v or "phase_properties" in top_v:
                absorb_phase_block(str(top_k), top_v, str(top_k))

    outs = []
    for doc in (nist, janaf):
        if doc.get("species"):
            ensure_fidelity_samples(doc)
            p = EXTRACTS / f"{doc['source_id']}.yaml"
            _dump(p, doc)
            print(f"wrote {p} ({len(doc['species'])} species)")
            outs.append(p)
    return outs


# ---------------------------------------------------------------------------
# Validation anchors → psat_series / pointer observations (REF sources)
# ---------------------------------------------------------------------------


def migrate_validation_anchors_sample() -> list[Path]:
    """Lift a few ADOPTABLE pure_Psat anchors into extract-shaped rows."""
    path = _find_research(
        "docs-private",
        "research",
        "2026-08-01-vp-acquire-6",
        "validation-anchors.md",
    )
    if path is None:
        return []
    text = path.read_text(encoding="utf-8")
    fences = _yaml_fences(text)
    # Combine list-like fences
    docs_by_source: dict[str, dict[str, Any]] = {}

    def ensure(source_id: str, citation: str) -> dict[str, Any]:
        if source_id not in docs_by_source:
            docs_by_source[source_id] = _base_extract(
                source_id,
                citation=citation,
                doi=None,
                url=None,
                year=None,
                method="manual transcription from vp-acquire-6 validation-anchors DRAFT",
                date="2026-08-01",
                worker="t508-store-gk",
                provenance_path="docs-private/research/2026-08-01-vp-acquire-6/validation-anchors.md",
            )
        return docs_by_source[source_id]

    for fence in fences:
        try:
            data = yaml.safe_load(fence)
        except Exception:  # noqa: BLE001
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, Mapping):
                continue
            sid = item.get("species_id")
            if not sid:
                continue
            anchor = item.get("independent_anchor") or {}
            if not isinstance(anchor, Mapping):
                continue
            if anchor.get("status") not in {
                "ADOPTABLE",
                "PARTIAL_NEED_PAGE",
                "adopted",
                "ADOPTED",
            }:
                # still allow if quantitative points present
                if "points" not in anchor and "value" not in anchor:
                    continue
            citation = str(
                anchor.get("citation")
                or item.get("current_fit_source")
                or "validation-anchors DRAFT"
            )
            # Source id from draft_validation_anchor_refs
            refs = item.get("draft_validation_anchor_refs") or anchor.get("refs") or []
            if isinstance(refs, str):
                refs = [refs]
            source_id = "validation-anchor-misc"
            if refs:
                ref0 = str(refs[0])
                if ref0.upper().startswith("JANAF"):
                    source_id = "janaf-4th"
                elif ref0.upper().startswith("REF-"):
                    source_id = re.sub(r"[^a-z0-9]+", "-", ref0.lower())
                else:
                    source_id = re.sub(r"[^a-z0-9]+", "-", ref0.lower())[:60]
            elif "JANAF" in citation.upper():
                source_id = "janaf-4th"
            elif "NIST" in citation.upper():
                source_id = "nist-webbook"

            doc = ensure(source_id, citation)
            quantity = str(anchor.get("quantity") or "pure_Psat")
            otype = "psat_series" if "Psat" in quantity or "psat" in quantity else "gibbs_table"
            if "transition" in quantity.lower():
                otype = "transition_point"
            values = {
                k: anchor[k]
                for k in anchor
                if k
                not in {
                    "status",
                    "citation",
                    "refs",
                }
            }
            if not values:
                values = {"quantity": quantity, "status": anchor.get("status")}
            _add_obs(
                doc,
                str(sid),
                {
                    "observation_id": f"anchor_{sid}_{quantity}",
                    "type": otype,
                    "locator": {
                        "note": citation[:300],
                        "record": str(refs[0]) if refs else str(sid),
                    },
                    "units": anchor.get("units") or "as published",
                    "values": values,
                },
            )

    outs = []
    for sid, doc in docs_by_source.items():
        if not doc["species"]:
            continue
        ensure_fidelity_samples(doc)
        p = EXTRACTS / f"{sid}.yaml"
        if p.is_file():
            prev = yaml.safe_load(p.read_text(encoding="utf-8"))
            if isinstance(prev, Mapping) and isinstance(prev.get("species"), Mapping):
                base = dict(prev)
                base.setdefault("species", {})
                _merge_species(base, doc)
                # Keep source metadata from prior when present
                ensure_fidelity_samples(base)
                doc = base  # type: ignore[assignment]
        _dump(p, doc)
        print(f"wrote {p} ({len(doc['species'])} species)")
        outs.append(p)
    return outs


def sanitize_extract_document(doc: dict[str, Any]) -> bool:
    """Dedupe observation_ids, drop pseudo-species keys, fix provenance. Returns dirty."""
    dirty = False
    extraction = doc.get("extraction")
    if isinstance(extraction, Mapping):
        prov = extraction.get("provenance_path")
        if isinstance(prov, str) and (prov.startswith("/") or prov.startswith("\\\\")):
            extraction = dict(extraction)
            extraction["provenance_path"] = _repo_relative(prov)
            doc["extraction"] = extraction
            dirty = True
    species = doc.get("species")
    if isinstance(species, Mapping):
        # Drop walk-shattered pseudo keys
        clean: dict[str, Any] = {}
        for sid, block in species.items():
            if "[" in str(sid) or str(sid).startswith("tabulated_"):
                dirty = True
                continue
            if not isinstance(block, Mapping):
                clean[str(sid)] = block
                continue
            obs_list = block.get("observations") or []
            if not isinstance(obs_list, list):
                clean[str(sid)] = block
                continue
            seen: dict[str, dict[str, Any]] = {}
            order: list[str] = []
            for obs in obs_list:
                if not isinstance(obs, Mapping):
                    continue
                oid = str(obs.get("observation_id") or "")
                if not oid:
                    # keep anonymous last
                    oid = f"__anon_{len(order)}"
                if oid not in seen:
                    order.append(oid)
                else:
                    dirty = True
                seen[oid] = dict(obs)
            new_block = dict(block)
            new_block["observations"] = [seen[oid] for oid in order]
            if len(new_block["observations"]) != len(obs_list):
                dirty = True
            clean[str(sid)] = new_block
        if set(clean.keys()) != set(species.keys()):
            dirty = True
        doc["species"] = clean
    before = list(doc.get("fidelity_samples") or [])
    ensure_fidelity_samples(doc)
    after = list(doc.get("fidelity_samples") or [])
    if after != before:
        dirty = True
    return dirty


def annotate_all_extracts_fidelity() -> int:
    """Post-pass: sanitize + fidelity samples for every extract."""
    n = 0
    for p in sorted(EXTRACTS.glob("*.yaml")):
        if p.name.startswith("_") or p.name.upper().startswith("SCHEMA"):
            continue
        try:
            doc = yaml.safe_load(p.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            print(f"WARN: skip {p.name}: {exc}", file=sys.stderr)
            continue
        if not isinstance(doc, dict):
            continue
        if sanitize_extract_document(doc):
            _dump(p, doc)
            n += 1
    return n


def main() -> int:
    EXTRACTS.mkdir(parents=True, exist_ok=True)
    # Clean rewrite targets that must not inherit shattered prior species keys.
    for stale_name in ("janaf-4th.yaml", "richter-et-al-2011.yaml"):
        stale = EXTRACTS / stale_name
        if stale.is_file() and stale_name == "janaf-4th.yaml":
            # Will be rewritten by migrate_p_carriers; remove pseudo-key pollution first.
            stale.unlink()
            print(f"cleared {stale.name} for clean rewrite")
        elif stale.is_file():
            stale.unlink()
            print(f"removed stale {stale.name}")
    written: list[Path] = []
    for fn in (
        migrate_cea,
        migrate_lh84_from_dominant_missing,
        migrate_p_carriers,
        migrate_chloride_datz,
        migrate_alpha_kinetics,
        migrate_phase_properties,
        migrate_validation_anchors_sample,
    ):
        result = fn()
        if result is None:
            continue
        if isinstance(result, list):
            written.extend(result)
        else:
            written.append(result)
    n_fid = annotate_all_extracts_fidelity()
    print(f"fidelity annotate pass touched {n_fid} file(s)")
    # Unique paths
    uniq = sorted({p.resolve() for p in written})
    print(f"pilot extracts: {len(uniq)}")
    for p in uniq:
        print(f"  {p.relative_to(ROOT)}")
    return 0 if uniq else 1


if __name__ == "__main__":
    raise SystemExit(main())
