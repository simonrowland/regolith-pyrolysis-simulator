from __future__ import annotations

from copy import deepcopy
import csv
import functools
import hashlib
import importlib.util
import math
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import zlib

import pytest
import yaml

from engines.builtin.vapor_pressure import BuiltinVaporPressureProvider
from simulator.chemistry.kernel import ChemistryIntent, IntentRequest
from simulator.chemistry.kernel.dto import ProviderAccountView
from simulator.condensation import (
    CondensationModel,
    _species_has_antoine_data,
    _try_antoine_psat_pa,
)
from simulator.config import load_config_bundle
from simulator.core import PyrolysisSimulator
from simulator.melt_backend.base import InternalAnalyticalBackend
from simulator.state import CondensationTrain, EvaporationFlux, MeltState
from simulator.volatile_properties import _catalog as _volatile_runtime_catalog
from simulator.vapour_rail.catalog import (
    RUNTIME_THERMO_EVALUATOR_FAMILIES,
    CatalogCompileError,
    OUT_OF_RANGE_STATUS,
    bound_effective_fit_evidence,
    compile_vapour_rail_catalog,
    validate_species_catalog,
)
from simulator.vapour_rail.channels import CHANNEL_O2
from tests.git_history import require_git_history


DATA_DIR = Path(__file__).resolve().parents[1] / "data"
CATALOG_PIN_DIR = (
    Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "catalog-pins"
)
T609_BASE_CATALOG_FIXTURE = CATALOG_PIN_DIR / "vapor_pressures.cf4a499d.yaml"
T609_LANDING_CATALOG_FIXTURE = CATALOG_PIN_DIR / "vapor_pressures.c4a22134.yaml"
T622_LANDING_CATALOG_FIXTURE = CATALOG_PIN_DIR / "vapor_pressures.3a36e9bb.yaml"
PINNED_CATALOG_FIXTURES = (
    ("cf4a499dff2beee6741f1b4da6fa43b61b6ecaa2", T609_BASE_CATALOG_FIXTURE),
    ("c4a2213422eb30b6f2f38b68281d9662fc35926d", T609_LANDING_CATALOG_FIXTURE),
    ("3a36e9bb6ff79a6a3f51ca969d3a2d41c4e800a9", T622_LANDING_CATALOG_FIXTURE),
)
CATALOG_PIN_SHA256SUMS = CATALOG_PIN_DIR / "SHA256SUMS"
# The pin identities live HERE, in test code, not only in SHA256SUMS. A
# manifest file next to the fixtures is a self-checksum: an edit that touches a
# fixture and rewrites its manifest line together would pass a manifest-only
# gate (review-proved). Baking the digests here makes that coupled edit a
# test-file change, which is what a reviewer sees.
PINNED_CATALOG_FIXTURE_SHA256 = {
    "vapor_pressures.cf4a499d.yaml": (
        "9d066f72eb7194be9dd226907ba24715f2d1bf270c591b7954243a3edd7e7c9d"
    ),
    "vapor_pressures.c4a22134.yaml": (
        "020eb16f67d9850692dd0f1d753123ca24ce9658c0c448603a861479b4221ca3"
    ),
    "vapor_pressures.3a36e9bb.yaml": (
        "fefcd5fa2f3f1ba60c3255504dc33c7e710dbb1f0a60fedff433ef14c7826f5d"
    ),
}
COLLISION_GASES = {
    "Al2O3_gas",
    "CaO_gas",
    "CoO_gas",
    "Cr2O3_gas",
    "Fe2O3_gas",
    "FeO_gas",
    "FeO_association_gas",
    "K2O_gas",
    "MgO_gas",
    "MnO_gas",
    "Na2O_gas",
    "NiO_gas",
    "P2O5_gas",
    "SiO2_gas",
    "TiO2_gas",
}
ACTIVE_COLLISION_GASES = {
    "Al2O3_gas",
    "CaO_gas",
    "CoO_gas",
    "FeO_association_gas",
    "K2O_gas",
    "MgO_gas",
    "MnO_gas",
    "Na2O_gas",
    "NiO_gas",
    "SiO2_gas",
    "TiO2_gas",
}
CARRIER_ONLY = {
    "CH4_NH3_HCN",
    "CO_CH4_propellant",
    "CO_CO2",
    "Fe_Ni_alloy",
    "NH3_HCN",
    "NaCl_KCl_salts",
    "REE_oxides",
    "carbonate_salts",
    "generic_carbonaceous_hydrocarbon",
    "generic_carbonaceous_organic",
    "metallic_FeNi",
    "organic_tar",
}


def _yaml(name: str) -> dict:
    return yaml.safe_load((DATA_DIR / name).read_text())


def _live_vapour_species_ids() -> tuple[str, ...]:
    payload = yaml.safe_load((DATA_DIR / "vapor_pressures.yaml").read_text())
    return tuple(
        str(species_id)
        for family in payload["families"].values()
        for species_id in family["physical_properties"]["species"]
    )


def _load_t609_proof():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "prove_t609_cross_revision_additivity.py"
    )
    spec = importlib.util.spec_from_file_location(
        "prove_t609_cross_revision_additivity", path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _family_species_ids(text: str) -> set[str]:
    payload = yaml.safe_load(text)
    ids: set[str] = set()
    for family in payload.get("families", {}).values():
        species = (family.get("physical_properties") or {}).get("species") or {}
        ids.update(str(species_id) for species_id in species)
    return ids


def _load_catalog_pin_sha256sums(
    path: Path = CATALOG_PIN_SHA256SUMS,
) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for raw_line in path.read_text(encoding="ascii").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        digest, name = line.split(None, 1)
        if name.startswith("*"):
            name = name[1:]
        mapping[name] = digest.lower()
    return mapping


def _git_show_catalog_at_revision(root: Path, revision: str) -> bytes | None:
    """Return ``git show <rev>:data/vapor_pressures.yaml``, or None if unresolvable.

    Missing git executable (FileNotFoundError) and a checkout that cannot
    resolve the pin are the same typed skip: this per-pin provenance check
    does not run. Fixture existence/digest is a separate always-on test.
    """

    try:
        probe = subprocess.run(
            ["git", "rev-parse", "--verify", f"{revision}^{{commit}}"],
            cwd=root,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return None
    if probe.returncode != 0:
        return None
    try:
        shown = subprocess.run(
            ["git", "show", f"{revision}:data/vapor_pressures.yaml"],
            cwd=root,
            capture_output=True,
        )
    except FileNotFoundError:
        return None
    if shown.returncode != 0:
        # The commit resolved but its catalog blob cannot be shown: that is a
        # broken checkout or a wrong path, not an unresolvable pin. Fail loud
        # rather than fold it into the typed skip (review).
        raise AssertionError(
            f"git resolved {revision} but could not show its catalog: "
            f"{shown.stderr.decode('utf-8', 'replace').strip()}"
        )
    return shown.stdout


def test_t609_cross_revision_additivity_evidence_is_reproducible() -> None:
    root = Path(__file__).resolve().parents[1]
    require_git_history(
        root,
        "cf4a499dff2beee6741f1b4da6fa43b61b6ecaa2",
        "c4a2213422eb30b6f2f38b68281d9662fc35926d",
    )
    evidence_path = (
        root
        / "validation-data"
        / "pin-evidence"
        / "t609_additivity_2026-08-11.yaml"
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "prove_t609_cross_revision_additivity.py"),
            "--check",
            "--output",
            str(evidence_path),
        ],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    assert (
        "PASS: 227 pre-existing species, 2556 exact grid cases" in completed.stdout
    )

    evidence = yaml.safe_load(evidence_path.read_text(encoding="utf-8"))
    assert evidence["result"] == "pass"
    assert evidence["method"]["baseline_revision"] == (
        "cf4a499dff2beee6741f1b4da6fa43b61b6ecaa2"
    )
    assert evidence["method"]["candidate_revision"] == (
        "c4a2213422eb30b6f2f38b68281d9662fc35926d"
    )
    assert evidence["method"]["compiler_common_mode"] is False
    assert evidence["species_delta"] == {
        "baseline_compiled_species": 227,
        "candidate_compiled_species": 229,
        "additions": ["FeO_association_gas", "NiO_gas"],
        "removals": [],
    }
    equivalence = evidence["preexisting_equivalence"]
    assert equivalence["compiled_species_compared"] == 227
    assert equivalence["evaluator_species_compared"] == 213
    assert equivalence["evaluation_cases_compared"] == 2556
    assert equivalence["baseline_compiled_dataclasses_sha256"] == equivalence[
        "candidate_compiled_dataclasses_sha256"
    ]
    assert equivalence["baseline_evaluation_grid_sha256"] == equivalence[
        "candidate_evaluation_grid_sha256"
    ]
    assert evidence["t583_coverage"]["total_t583_compositions_covered"] == 251


def test_t609_additivity_pin_is_the_t609_landing_not_the_live_catalog() -> None:
    """t-622 already added MnO/CoO. Widening EXPECTED_ADDITIONS would hide that.

    Choice: commit the historic ``data/vapor_pressures.yaml`` snapshots as
    fixtures under ``tests/fixtures/catalog-pins/`` and read those files
    here. Studio CI rsync copies have no ``.git``, and ``git show`` of
    cf4a499d / c4a22134 is also fragile on shallow clones. The pin is then
    self-contained. A missing fixture is a failure of the always-on SHA-256
    digest test (git-independent). ``git show`` is developer-only provenance
    and is skipped per pin when that revision does not resolve.
    """

    root = Path(__file__).resolve().parents[1]
    proof = _load_t609_proof()
    assert proof.EXPECTED_ADDITIONS == ("FeO_association_gas", "NiO_gas")
    assert proof.BASE_REVISION == PINNED_CATALOG_FIXTURES[0][0]
    assert proof.CANDIDATE_REVISION == PINNED_CATALOG_FIXTURES[1][0]
    assert T609_BASE_CATALOG_FIXTURE.is_file()
    assert T609_LANDING_CATALOG_FIXTURE.is_file()

    base_ids = _family_species_ids(
        T609_BASE_CATALOG_FIXTURE.read_text(encoding="utf-8")
    )
    t609_ids = _family_species_ids(
        T609_LANDING_CATALOG_FIXTURE.read_text(encoding="utf-8")
    )
    live_ids = _family_species_ids(
        (root / "data" / "vapor_pressures.yaml").read_text(encoding="utf-8")
    )
    assert sorted(t609_ids - base_ids) == list(proof.EXPECTED_ADDITIONS)
    later = live_ids - t609_ids
    assert {"CoO_gas", "MnO_gas"} <= later


def test_pinned_catalog_fixtures_match_sha256_manifest() -> None:
    """Always-on pin identity: fixtures exist and match SHA256SUMS.

    Studio CI rsync copies have no ``.git`` and may lack a git executable.
    This test does not call git. A missing fixture or a digest mismatch is
    a failure. ``git show`` comparison is a separate, developer-only
    per-pin provenance check.
    """

    assert CATALOG_PIN_SHA256SUMS.is_file(), (
        f"missing catalog pin digest manifest {CATALOG_PIN_SHA256SUMS}"
    )
    manifest = _load_catalog_pin_sha256sums()
    names = {fixture.name for _, fixture in PINNED_CATALOG_FIXTURES}
    assert set(PINNED_CATALOG_FIXTURE_SHA256) == names, (
        "PINNED_CATALOG_FIXTURE_SHA256 must name exactly the three pins"
    )
    # The manifest is convenience for `shasum -c`; the test constants are the
    # authority. A manifest rewritten alongside a fixture edit fails here.
    assert manifest == PINNED_CATALOG_FIXTURE_SHA256, (
        "SHA256SUMS disagrees with the digests pinned in this test: "
        f"{manifest} vs {PINNED_CATALOG_FIXTURE_SHA256}"
    )
    for _, fixture in PINNED_CATALOG_FIXTURES:
        assert fixture.is_file(), f"missing catalog pin fixture {fixture}"
        digest = hashlib.sha256(fixture.read_bytes()).hexdigest()
        assert digest == PINNED_CATALOG_FIXTURE_SHA256[fixture.name], (
            f"catalog pin fixture digest mismatch: {fixture.name} "
            f"got {digest} expected {PINNED_CATALOG_FIXTURE_SHA256[fixture.name]}"
        )


@pytest.mark.parametrize(
    ("revision", "fixture"),
    PINNED_CATALOG_FIXTURES,
    ids=("cf4a499d", "c4a22134", "3a36e9bb"),
)
def test_pinned_catalog_fixtures_match_git_when_revision_resolves(
    revision: str,
    fixture: Path,
) -> None:
    """Developer-only provenance: fixture bytes == ``git show`` when resolvable.

    An unresolved pin (missing git executable, no ``.git``, or a shallow
    clone that does not contain the revision) is a typed skip of *this*
    per-pin check. It is never folded into a pass because another pin
    resolved. A missing fixture is a failure of the always-on digest test,
    not a skip of identity.
    """

    root = Path(__file__).resolve().parents[1]
    shown = _git_show_catalog_at_revision(root, revision)
    if shown is None:
        pytest.skip(
            "pinned catalog revision not resolvable in this checkout "
            f"(no git executable, no .git, or shallow clone): {revision}"
        )
    assert fixture.is_file(), f"missing catalog pin fixture {fixture}"
    assert fixture.read_bytes() == shown, (
        f"catalog pin fixture drifted from git {revision}: {fixture}"
    )


def test_t609_additivity_proof_ignores_a_further_catalog_addition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A later lawful Extra_gas in the live tree must not change t-609's delta."""

    root = Path(__file__).resolve().parents[1]
    proof = _load_t609_proof()
    baseline_ids = ("Preexisting_A", "Preexisting_B")
    t609_ids = (*baseline_ids, *proof.EXPECTED_ADDITIONS)
    live_ids = (*t609_ids, "MnO_gas", "CoO_gas", "Extra_gas")
    worker_roots: list[str] = []

    def _snapshot(ids: tuple[str, ...]) -> dict:
        compiled = {species_id: {"id": species_id} for species_id in ids}
        evaluations = {species_id: [] for species_id in ids}
        return {
            "catalog_sha256": "0" * 64,
            "compiler_sha256": "1" * 64,
            "compiled_species": compiled,
            "evaluations": evaluations,
            "counts": {
                "compiled_species": len(ids),
                "evaluator_species": 0,
                "evaluation_cases": 0,
                "exceptions": 0,
            },
            "t583": {
                "status_only_species": [f"status_{index}" for index in range(151)],
                "status_only_ledger_pair_compositions": 250,
                "existing_executable_species": ["H2S"],
                "existing_executable_ledger_pair_compositions": 1,
                "total_compositions": 251,
            },
        }

    def fake_run(command: list[str], *, cwd: Path) -> SimpleNamespace:
        if command[:2] == ["git", "rev-parse"]:
            spec = command[2].replace("^{commit}", "")
            resolved = {
                proof.BASE_REVISION: proof.BASE_REVISION,
                proof.CANDIDATE_REVISION: proof.CANDIDATE_REVISION,
                "HEAD": "0" * 40,
            }[spec]
            return SimpleNamespace(stdout=resolved + "\n")
        raise AssertionError(f"unexpected command {command} in {cwd}")

    def fake_archive(_repo_root: Path, revision: str, dest_root: Path) -> None:
        dest_root.mkdir(parents=True, exist_ok=True)
        (dest_root / "revision").write_text(revision, encoding="utf-8")

    def fake_worker(
        import_root: Path,
        _snapshot_path: Path,
        *,
        catalog_path: Path | None = None,
    ) -> dict:
        worker_roots.append(import_root.name)
        if catalog_path is not None:
            raise AssertionError("t-609 archive path must not use a catalog blob")
        if import_root.resolve() == root.resolve():
            return _snapshot(live_ids)
        if import_root.name == "baseline":
            return _snapshot(baseline_ids)
        if import_root.name == "candidate":
            return _snapshot(t609_ids)
        raise AssertionError(f"unexpected worker root {import_root}")

    monkeypatch.setattr(proof, "_run", fake_run)
    monkeypatch.setattr(proof, "_archive_revision", fake_archive)
    monkeypatch.setattr(proof, "_run_worker", fake_worker)

    with pytest.raises(proof.ProofFailure, match="refusing unpinned live-catalog"):
        proof._generate_evidence(root)

    evidence = proof._generate_evidence(
        root,
        candidate_revision=proof.CANDIDATE_REVISION,
        candidate_materialization="revision_archive",
    )
    assert evidence["species_delta"]["additions"] == list(proof.EXPECTED_ADDITIONS)
    assert evidence["species_delta"]["removals"] == []
    assert worker_roots == ["baseline", "candidate"]
    assert "Extra_gas" not in evidence["added_species_digests"]


def test_t622_cross_revision_additivity_evidence_is_reproducible() -> None:
    root = Path(__file__).resolve().parents[1]
    require_git_history(
        root,
        "97969c434cb679d149756cbfd119e40220763d7a",
        "3a36e9bb6ff79a6a3f51ca969d3a2d41c4e800a9",
    )
    evidence_path = (
        root
        / "validation-data"
        / "pin-evidence"
        / "t622_additivity_2026-08-12.yaml"
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "prove_t622_cross_revision_additivity.py"),
            "--check",
            "--output",
            str(evidence_path),
        ],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    evidence = yaml.safe_load(evidence_path.read_text(encoding="utf-8"))
    equivalence = evidence["preexisting_equivalence"]
    assert (
        f"PASS: {equivalence['compiled_species_compared']} pre-existing species, "
        f"{equivalence['evaluation_cases_compared']} exact grid cases"
        in completed.stdout
    )
    assert evidence["result"] == "pass"
    assert evidence["method"]["baseline_revision"] == (
        "97969c434cb679d149756cbfd119e40220763d7a"
    )
    assert evidence["method"]["compiler_common_mode"] is False
    assert evidence["species_delta"] == {
        "baseline_compiled_species": 229,
        "candidate_compiled_species": 231,
        "additions": ["CoO_gas", "MnO_gas"],
        "removals": [],
    }
    assert equivalence["compiled_dataclasses_exact"] is True
    assert equivalence["evaluation_results_and_exceptions_exact"] is True
    assert equivalence["baseline_compiled_dataclasses_sha256"] == equivalence[
        "candidate_compiled_dataclasses_sha256"
    ]
    assert equivalence["baseline_evaluation_grid_sha256"] == equivalence[
        "candidate_evaluation_grid_sha256"
    ]


def test_require_git_history_skips_only_missing_history(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from tests import git_history

    def completed(
        returncode: int = 0,
        stderr: str = "",
        stdout: str = "true\n",
    ) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess([], returncode, stdout, stderr)

    def run_results(results):
        remaining = iter(results)

        def fake_run(command, **kwargs):
            result = next(remaining)
            if kwargs.get("check") and result.returncode:
                raise subprocess.CalledProcessError(
                    result.returncode, command, stderr=result.stderr
                )
            return result

        return fake_run

    monkeypatch.setattr(
        git_history.subprocess,
        "run",
        run_results((completed(), completed(stdout="commit\n"))),
    )
    try:
        require_git_history(tmp_path, "reachable")
    except pytest.skip.Exception as exc:
        pytest.fail(f"reachable history was unexpectedly skipped: {exc}")

    monkeypatch.setattr(
        git_history.subprocess,
        "run",
        run_results((completed(128, "fatal: not a git repository"),)),
    )
    with pytest.raises(pytest.skip.Exception, match="missing_git_history"):
        require_git_history(tmp_path)

    monkeypatch.setattr(
        git_history.subprocess,
        "run",
        run_results(
            (
                completed(),
                completed(128, "fatal: unknown revision"),
                completed(stdout=""),
            )
        ),
    )
    with pytest.raises(pytest.skip.Exception, match="missing_git_history"):
        require_git_history(tmp_path, "unreachable")

    monkeypatch.setattr(
        git_history.subprocess,
        "run",
        run_results(
            (
                completed(),
                completed(
                    128,
                    "error: short object ID deadbee is ambiguous\n"
                    "fatal: Needed a single revision\n",
                ),
                completed(stdout="deadbeef00000000000000000000000000000000\n"),
            )
        ),
    )
    with pytest.raises(subprocess.CalledProcessError) as error:
        require_git_history(tmp_path, "deadbee")
    assert "ambiguous" in (error.value.stderr or "")

    def other_error(*_args, **_kwargs):
        raise subprocess.CalledProcessError(
            2, ["git"], stderr="fatal: permission denied"
        )

    monkeypatch.setattr(git_history.subprocess, "run", other_error)
    try:
        require_git_history(tmp_path)
    except pytest.skip.Exception as exc:
        pytest.fail(f"unrelated Git error was unexpectedly skipped: {exc}")
    except subprocess.CalledProcessError as raised:
        assert raised.stderr == "fatal: permission denied"
    else:
        pytest.fail("unrelated Git error should propagate")


def _init_history_repo(root: Path) -> Path:
    root.mkdir()
    subprocess.run(
        ["git", "-C", str(root), "init", "-q"],
        capture_output=True,
        check=True,
    )
    return root


def _history_git(root: Path, *args: str, input: bytes | None = None) -> bytes:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        input=input,
        capture_output=True,
        check=True,
    ).stdout


def _write_ambiguous_history_objects(root: Path) -> str:
    tree = _history_git(root, "mktree", input=b"").decode().strip()
    seen: dict[str, tuple[str, bytes]] = {}
    for counter in range(100_000):
        body = (
            f"tree {tree}\n"
            "author History test <history@example.invalid> 0 +0000\n"
            f"committer History test <history@example.invalid> {counter} +0000\n"
            f"\nambiguous history object {counter}\n"
        ).encode("utf-8")
        raw = f"commit {len(body)}\0".encode("ascii") + body
        object_id = hashlib.sha1(raw).hexdigest()
        prefix = object_id[:7]
        previous = seen.get(prefix)
        if previous is not None:
            for candidate_id, candidate_raw in (previous, (object_id, raw)):
                object_path = (
                    root / ".git" / "objects" / candidate_id[:2] / candidate_id[2:]
                )
                object_path.parent.mkdir(parents=True, exist_ok=True)
                object_path.write_bytes(zlib.compress(candidate_raw))
            return prefix
        seen[prefix] = object_id, raw
    pytest.fail("could not generate an ambiguous seven-character Git prefix")


def test_require_git_history_skips_absent_object_in_tmp_repo(tmp_path: Path) -> None:
    repo = _init_history_repo(tmp_path / "missing-history")

    with pytest.raises(pytest.skip.Exception, match="missing_git_history"):
        require_git_history(repo, "f" * 40)


def test_require_git_history_rejects_blob_object_in_tmp_repo(tmp_path: Path) -> None:
    repo = _init_history_repo(tmp_path / "blob-history")
    blob = _history_git(repo, "hash-object", "-w", "--stdin", input=b"not a commit\n")
    blob_id = blob.decode().strip()

    with pytest.raises(ValueError, match="not a commit"):
        require_git_history(repo, blob_id)


def test_require_git_history_rejects_ambiguous_ack_prefix_in_tmp_repo(
    tmp_path: Path,
) -> None:
    repo = _init_history_repo(tmp_path / "ambiguous-history")
    ack_dir = repo / "scripts" / "store_neutral_acks"
    ack_dir.mkdir(parents=True)
    prefix = _write_ambiguous_history_objects(repo)
    ack = ack_dir / f"{prefix}.txt"
    ack.write_text("ambiguous prefix test receipt\n", encoding="utf-8")

    with pytest.raises(subprocess.CalledProcessError):
        require_git_history(repo, ack.stem)


# Pseudo-block Antoine law evaluate() runs for the Fe zero-activity admission.
# engines.antoine._antoine_log10_pressure: log10(P/Pa) = A - B / (T + C)
# A=9.408547, B=23312.312336, C=1000, T=1750, T+C=2750.
# log10(P) = 9.408547 - 23312.312336/2750 = 0.9313425141818197
# P = 10 ** 0.9313425141818197 = 8.537731922030234 Pa
# This is an activity-independent effective pressure over basalt, not pure-Fe P_sat.
# At 1800 K the same law is 10 ** (9.408547 - 23312.312336/2800) = 12.098211309846041 Pa.
_FE_PSEUDO_LAW_1750_PA = 8.537731922030234
_FE_PSEUDO_LAW_1800_PA = 12.098211309846041
_FE_DISAGREEMENT_ANCHOR = (
    "validation-data/pin-evidence/vaporock_engine_crosscheck_2026-08-08.yaml"
)
_DIAGNOSTIC_AUTHORITY = "diagnostic/extrapolated"
_DIAGNOSTIC_LAW_PROVENANCE = "backsolved from VapoRock, no coefficients fitted"
_DIAGNOSTIC_EVALUATOR_FORM = "log10(P) = A - B / (T + C)"
_DIAGNOSTIC_CLOSER = "t-1723"
# Synthetic diagnostic Antoine in the generative grid. Same form.
# A=10, B=10000, C=0, T=1100. log10(P) = 10 - 10000/1100 = 0.9090909090909083
# P = 8.111308307896856 Pa. Comparators are 1 Pa and 0.1 Pa.
_SYNTHETIC_DIAGNOSTIC_1100_PA = 8.111308307896856


@functools.lru_cache(maxsize=1)
def _fe_crosscheck_row() -> dict:
    path = (
        Path(__file__).resolve().parents[1]
        / "validation-data"
        / "pin-evidence"
        / "vaporock_engine_crosscheck_2026-08-08.yaml"
    )
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    return payload["pin_rows"]["Fe"]


def _assert_diagnostic_disclosure(
    notice: dict | None,
    *,
    executed_species_id: str,
    anchor: str,
    temperature_K: float,
    rail_pressure_Pa: float,
    vaporock_pressure_Pa: float,
    law_pressure_Pa: float,
) -> None:
    assert notice is not None
    assert notice["authority"] == _DIAGNOSTIC_AUTHORITY
    assert notice["law_provenance"] == _DIAGNOSTIC_LAW_PROVENANCE
    assert notice["provenance_status"] == "diagnostic_only"
    assert notice["evaluator_form"] == _DIAGNOSTIC_EVALUATOR_FORM
    assert notice["closes_with"] == _DIAGNOSTIC_CLOSER
    assert notice["executed_species_id"] == executed_species_id
    assert notice["anchor"] == anchor
    assert notice["anchor_role"] == "disagreement_not_source"
    assert notice["temperature_K"] == temperature_K
    assert notice["law_pressure_Pa"] == pytest.approx(
        law_pressure_Pa, rel=1e-12, abs=0.0
    )
    comparators = notice["comparators"]
    assert comparators["rail_pressure_Pa"] == rail_pressure_Pa
    assert comparators["vaporock_pressure_Pa"] == vaporock_pressure_Pa
    assert comparators["gap_dex_law_vs_rail"] == pytest.approx(
        math.log10(law_pressure_Pa / rail_pressure_Pa), rel=0, abs=1e-12
    )
    assert comparators["gap_dex_law_vs_vaporock"] == pytest.approx(
        math.log10(law_pressure_Pa / vaporock_pressure_Pa), rel=0, abs=1e-12
    )


def _assert_fe_pseudo_law_disclosure(notice: dict | None) -> None:
    """Flag carries the anchor gap. Comparators are the crosscheck row.

    Recomputed from that file, not from the catalog coefficients:
    rail 0.6585350200427053 Pa, gap 1.1127636390496998 dex;
    VapoRock 0.3004593572618527 Pa, gap 1.4535567803078235 dex.
    The file residual of rail against VapoRock is 0.3407931412581238 dex.
    """

    row = _fe_crosscheck_row()
    rail = float(row["rail_pressure_Pa"])
    vaporock = float(row["vaporock_pressure_Pa"])
    assert float(row["temperature_K"]) == 1750.0
    assert float(row["signed_delta_dex"]) == pytest.approx(
        math.log10(rail / vaporock), rel=0, abs=1e-12
    )
    assert math.log10(_FE_PSEUDO_LAW_1750_PA / rail) == pytest.approx(
        1.1127636390496998, rel=0, abs=1e-12
    )
    assert math.log10(_FE_PSEUDO_LAW_1750_PA / vaporock) == pytest.approx(
        1.4535567803078235, rel=0, abs=1e-12
    )
    _assert_diagnostic_disclosure(
        notice,
        executed_species_id="Fe",
        anchor=_FE_DISAGREEMENT_ANCHOR,
        temperature_K=1750.0,
        rail_pressure_Pa=rail,
        vaporock_pressure_Pa=vaporock,
        law_pressure_Pa=_FE_PSEUDO_LAW_1750_PA,
    )


def _assert_admission_notice_for_record(evaluator, evaluation, record) -> None:
    evaluation_notice = getattr(evaluation, "diagnostic_admission_notice", None)
    evaluator_notice = getattr(evaluator, "diagnostic_admission_notice", None)
    if len(record) == 7 and record[6][0] == "diagnostic_admission":
        assert record[0] == "Fe"
        _assert_fe_pseudo_law_disclosure(evaluation_notice)
        _assert_fe_pseudo_law_disclosure(evaluator_notice)
        return
    assert evaluation_notice is None
    assert evaluator_notice is None


def test_t622_frozen_fe_law_compiles_without_rewriting_the_fixture() -> None:
    fixture_bytes = T622_LANDING_CATALOG_FIXTURE.read_bytes()
    assert (
        hashlib.sha256(fixture_bytes).hexdigest()
        == PINNED_CATALOG_FIXTURE_SHA256[T622_LANDING_CATALOG_FIXTURE.name]
    )
    historic_payload = yaml.safe_load(fixture_bytes)
    with pytest.raises(
        CatalogCompileError,
        match="effective-pressure reference fit requires",
    ):
        compile_vapour_rail_catalog(historic_payload, emit_u0_request_rules=False)
    fits = bound_effective_fit_evidence(_yaml("vapor_pressures.yaml"))
    historic = compile_vapour_rail_catalog(
        historic_payload,
        emit_u0_request_rules=False,
        bound_effective_fits=fits,
    )
    live = compile_vapour_rail_catalog(
        _yaml("vapor_pressures.yaml"), emit_u0_request_rules=False
    )
    for species_id, kwargs in (
        ("Fe", {}),
        ("FeO_association_gas", {"pO2_bar": 1.0}),
    ):
        historic_evaluator = historic.species[species_id].evaluator
        live_evaluator = live.species[species_id].evaluator
        assert historic_evaluator is not None and live_evaluator is not None
        historic_evaluation = historic_evaluator.evaluate(1800.0, **kwargs)
        live_evaluation = live_evaluator.evaluate(1800.0, **kwargs)
        assert historic_evaluation.pressure_pa == live_evaluation.pressure_pa
        if species_id == "Fe":
            assert historic_evaluation.pressure_pa == pytest.approx(
                _FE_PSEUDO_LAW_1800_PA, rel=1e-12, abs=0.0
            )
            for pinned_evaluator in (historic_evaluator, live_evaluator):
                pinned = pinned_evaluator.evaluate(1750.0)
                assert pinned.pressure_pa == pytest.approx(
                    _FE_PSEUDO_LAW_1750_PA, rel=1e-12, abs=0.0
                )
                _assert_fe_pseudo_law_disclosure(
                    getattr(pinned, "diagnostic_admission_notice", None)
                )
                _assert_fe_pseudo_law_disclosure(
                    getattr(pinned_evaluator, "diagnostic_admission_notice", None)
                )
        else:
            _assert_fe_pseudo_law_disclosure(
                getattr(historic_evaluation, "diagnostic_admission_notice", None)
            )
            _assert_fe_pseudo_law_disclosure(
                getattr(live_evaluation, "diagnostic_admission_notice", None)
            )
    assert historic.species["Fe"].validation_anchor_refs == ()
    assert historic.species["FeO_association_gas"].validation_anchor_refs == ()

    drifted = yaml.safe_load(fixture_bytes)
    drifted["families"]["metals_fe_family"]["physical_properties"]["species"]["Fe"][
        "pressure_models"
    ][0]["coefficients"]["B"] += 1.0
    with pytest.raises(
        CatalogCompileError,
        match="effective-pressure reference fit requires",
    ):
        compile_vapour_rail_catalog(
            drifted,
            emit_u0_request_rules=False,
            bound_effective_fits=fits,
        )
    assert hashlib.sha256(T622_LANDING_CATALOG_FIXTURE.read_bytes()).hexdigest() == (
        PINNED_CATALOG_FIXTURE_SHA256[T622_LANDING_CATALOG_FIXTURE.name]
    )


def test_t622_oxidative_window_envelope_evidence_is_reproducible(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[1]
    output = tmp_path / "oxidative-window-envelope.csv"
    completed = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "prove_t622_oxidative_window.py"),
            "--output",
            str(output),
        ],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "38520 element-grid evaluations summarized in 120 rows" in completed.stdout
    with output.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 120
    assert {row["fO2_grid_points"] for row in rows} == {"321"}
    candidates = [row for row in rows if row["element"] in {"Mn", "Co"}]
    assert all(row["candidate_carrier"] in {"MnO_gas", "CoO_gas"} for row in candidates)
    assert all(
        "WINDOWS-UNKNOWABLE-UNTIL-MODELED" not in row["window_state_counts"]
        for row in candidates
    )
    co_rows = [row for row in candidates if row["element"] == "Co"]
    assert all(
        row["element_coverage_status"] == "incomplete_atomic_Co_not_compiled"
        for row in co_rows
    )
    assert all(not row["candidate_nominal_fraction_min"] for row in co_rows)
    assert all(
        not row["nominal_modeled_element_pressure_min_Pa"]
        and not row["nominal_modeled_element_pressure_max_Pa"]
        and not row["lower_modeled_element_pressure_min_Pa"]
        and not row["upper_modeled_element_pressure_max_Pa"]
        for row in co_rows
    )


def test_t622_mno_coo_are_congruent_diagnostic_composites() -> None:
    payload = _yaml("vapor_pressures.yaml")
    catalog = compile_vapour_rail_catalog(payload, emit_u0_request_rules=False)
    expected = {
        "MnO_gas": ("oxide_vapors_mno_family", (1519.0, 2273.15), 0.0010798340273847744),
        "CoO_gas": ("oxide_vapors_coo_family", (1400.0, 2000.0), 0.03810104876926756),
    }
    for species_id, (family_id, domain, expected_pressure) in expected.items():
        compiled = catalog.species[species_id]
        row = payload["families"][family_id]["physical_properties"]["species"][
            species_id
        ]
        evaluator = compiled.evaluator
        assert evaluator is not None
        assert evaluator.evaluator_family == "shomate"
        assert compiled.valid_temperature_K == domain
        assert evaluator.activity_exponent == pytest.approx(1.0)
        assert evaluator.pO2_exponent == pytest.approx(0.0)
        assert evaluator.o2_channel_term is None
        assert row["runtime_disposition"] == "status_bearing_non_authoritative"
        assert row["retain_analytical_pressure_channel"] is True
        assert row["flux_dormant"] is True
        assert row["validation"]["certification_ceiling"] == "never"
        assert row["gas_thermo_fit_domain_K"] == [1000.0, 3000.0]

        p_unit = evaluator.evaluate(1800.0, source_activity=1.0).pressure_pa
        p_half = evaluator.evaluate(1800.0, source_activity=0.5).pressure_pa
        p_other_o2 = evaluator.evaluate(
            1800.0, source_activity=1.0, pO2_bar=1.0e-12
        ).pressure_pa
        assert p_unit == pytest.approx(expected_pressure, rel=2.0e-12)
        assert p_half == pytest.approx(0.5 * p_unit, rel=1.0e-12)
        assert p_other_o2 == pytest.approx(p_unit, rel=0.0, abs=0.0)


def test_t609_feo_nio_exponents_are_stoichiometry_derived() -> None:
    payload = _yaml("vapor_pressures.yaml")
    rows = {
        species_id: payload["families"][family_id]["physical_properties"][
            "species"
        ][species_id]
        for species_id, family_id in {
            "FeO_association_gas": "oxide_vapors_feo_association_family",
            "NiO_gas": "oxide_vapors_nio_family",
        }.items()
    }
    for row in rows.values():
        assert "pO2_exponent" not in row
        assert "pO2_exponent" not in row["pressure_models"][0]

    catalog = compile_vapour_rail_catalog(payload, emit_u0_request_rules=False)
    feo = catalog.species["FeO_association_gas"].evaluator
    nio = catalog.species["NiO_gas"].evaluator
    assert feo is not None and nio is not None
    assert feo.pO2_exponent == pytest.approx(0.5)
    assert nio.pO2_exponent == pytest.approx(0.0)

    term = feo.o2_channel_term
    assert term is not None
    assert term.input_id == CHANNEL_O2
    assert term.signed_nu == pytest.approx(-0.5)
    assert term.target_nu == pytest.approx(1.0)
    assert term.derived_exponent == pytest.approx(0.5)
    assert nio.o2_channel_term is None
    assert catalog.species["FeO_gas"].family_id == "t583_status_feo_gas_family"


def test_t609_feo_association_slope_and_1800k_ratio() -> None:
    catalog = compile_vapour_rail_catalog(
        _yaml("vapor_pressures.yaml"), emit_u0_request_rules=False
    )
    fe = catalog.species["Fe"].evaluator
    feo = catalog.species["FeO_association_gas"].evaluator
    assert fe is not None and feo is not None

    p_fe = fe.evaluate(1800.0).pressure_pa
    p_feo_1bar = feo.evaluate(1800.0, pO2_bar=1.0).pressure_pa
    p_feo_001bar = feo.evaluate(1800.0, pO2_bar=0.01).pressure_pa
    assert p_feo_1bar / p_fe == pytest.approx(217.3144, rel=2.0e-5)
    assert p_feo_1bar / p_feo_001bar == pytest.approx(10.0, rel=1.0e-12)


def test_t609_positive_reaction_scaling_leaves_feo_exponent_unchanged() -> None:
    payload = _yaml("vapor_pressures.yaml")
    row = payload["families"]["oxide_vapors_feo_association_family"][
        "physical_properties"
    ]["species"]["FeO_association_gas"]
    model = row["pressure_models"][0]
    for reaction in (row["source_reactions"][0], model["gas_exchange_reaction"]):
        for side in ("reactants", "products"):
            for participant in reaction[side]:
                participant["stoichiometry"] *= 7.0

    evaluator = compile_vapour_rail_catalog(
        payload, emit_u0_request_rules=False
    ).species["FeO_association_gas"].evaluator
    assert evaluator is not None
    assert evaluator.pO2_exponent == pytest.approx(0.5)
    assert evaluator.o2_channel_term is not None
    assert evaluator.o2_channel_term.signed_nu == pytest.approx(-0.5)


def test_t609_nio_congruent_limit_is_activity_linear_and_po2_neutral() -> None:
    evaluator = compile_vapour_rail_catalog(
        _yaml("vapor_pressures.yaml"), emit_u0_request_rules=False
    ).species["NiO_gas"].evaluator
    assert evaluator is not None
    p_unit = evaluator.evaluate(1800.0, source_activity=1.0).pressure_pa
    p_half = evaluator.evaluate(1800.0, source_activity=0.5).pressure_pa
    p_other_o2 = evaluator.evaluate(
        1800.0, source_activity=1.0, pO2_bar=1.0e-8
    ).pressure_pa
    assert p_unit == pytest.approx(0.04120438, rel=2.0e-6)
    assert p_half == pytest.approx(0.5 * p_unit, rel=1.0e-12)
    assert p_other_o2 == pytest.approx(p_unit, rel=0.0, abs=0.0)


@pytest.mark.parametrize(
    "family_id",
    (
        "oxide_vapors_feo_association_family",
        "oxide_vapors_nio_family",
        "oxide_vapors_mno_family",
        "oxide_vapors_coo_family",
    ),
)
def test_monoxide_unknown_declared_alpha_authority_fails_closed(family_id: str) -> None:
    payload = _yaml("vapor_pressures.yaml")
    alpha = payload["families"][family_id]["vaporisation_coefficients"][
        "evaporation_alpha"
    ]
    alpha["status"] = "invented_authoritative_measurement"

    with pytest.raises(CatalogCompileError, match="unsupported authority class"):
        compile_vapour_rail_catalog(payload, emit_u0_request_rules=False)


def _reaction_fixture() -> dict:
    return {
        "schema_version": 2,
        "families": {
            "potassium_test_family": {
                "physical_properties": {
                    "species": {
                        "K": {
                            "formula": "K",
                            "source_reactions": [
                                {
                                    "id": "ko0_5_to_k",
                                    "reactants": [
                                        {"formula": "KO0.5", "stoichiometry": 1.0}
                                    ],
                                    "products": [
                                        {"formula": "K", "stoichiometry": 1.0},
                                        {"formula": "O2", "stoichiometry": 0.25},
                                    ],
                                    "activity_input": {
                                        "component_id": "KO0.5",
                                        "standard_state": {
                                            "convention": "raoultian_pure_endmember",
                                            "phase": "liquid",
                                            "reference_pressure_bar": 1.0,
                                            "component_basis": "raoultian_pure_endmember",
                                        },
                                        "activity_model": "provider_reported_thermodynamic_activity",
                                        "allow_henrian_upper_bound": False,
                                        "compound_bearing": False,
                                        "require_assemblage_match": False,
                                    },
                                }
                            ],
                            "pressure_models": [
                                {
                                    "evaluator_family": "standard_reaction_term",
                                    "fit_target": "standard_reaction_term",
                                    "pressure_kind": "equilibrium_partial_pressure",
                                    "species_basis": "monomer",
                                    "valid_domain": {"temperature_K": [1000.0, 1200.0]},
                                    "source_reaction_id": "ko0_5_to_k",
                                    "activity_semantics": "source_reaction_activity",
                                    "reference_pressure_model": {
                                        "evaluator_family": "tabulated_equilibrium",
                                        "points": [
                                            {"temperature_K": 1000.0, "pressure_Pa": 1.0},
                                            {"temperature_K": 1200.0, "pressure_Pa": 100.0},
                                        ],
                                    },
                                    "activity_exponent": 1.0,
                                    "pO2_exponent": -0.25,
                                    "pO2_reference_bar": 1.0,
                                    "oxygen_fugacity_channel": "intrinsic_melt",
                                }
                            ],
                            "validation": {
                                "status": "pending_validation",
                                "anchor_refs": [],
                            },
                            "parent_oxide": "K2O",
                            "oxide_activity_exponent": 1.0,
                            "pO2_exponent": -0.25,
                            "pO2_reference_bar": 1.0,
                            "molar_mass_g_mol": 39.0983,
                        }
                    }
                },
                "fiat_routing": {
                    "plant_bin": None,
                    "engineering_capture_policy": "temperature_threshold",
                    "products_and_coproducts": [],
                    "process_or_terminal_destination": "process.condensation_train",
                    "condensation_reference_at_1mbar_C": 420.0,
                },
                "vaporisation_coefficients": {
                    "evaporation_alpha": {"value": 0.13},
                    "alpha_domain_and_uncertainty": {},
                    "extrapolation_policy": "conservative_slope_continuation",
                    "out_of_range_status": OUT_OF_RANGE_STATUS,
                    "acquisition_flag": "acquire:test:K",
                },
                "code_metadata": {
                    "formula_id": "K",
                    "source_account": "process.cleaned_melt",
                    "request_rule": "source_inventory_present",
                    "solve_group_id": "potassium_test_family",
                    "compatibility_projection": "metals",
                    "canonical_aliases": [],
                    "hot_train_applicability": "applicable",
                },
            }
        },
    }


def _effective_pressure_fit_fixture() -> dict:
    payload = _reaction_fixture()
    row = payload["families"]["potassium_test_family"][
        "physical_properties"
    ]["species"]["K"]
    row["source_reactions"] = []
    row["pressure_models"][0] = {
        "evaluator_family": "antoine",
        "fit_target": "pseudo_psat_backsolved_from_vaporock",
        "pressure_kind": "equilibrium_partial_pressure",
        "activity_semantics": "effective_pressure_reference_fit",
        "activity_exponent": 0.0,
        "species_basis": "monomer",
        "valid_domain": {"temperature_K": [1000.0, 1200.0]},
        "provenance": [
            {
                "source_id": "fit-grid-source",
                "observation_id": "fit-grid",
            }
        ],
        "coefficients": {"A": 10.0, "B": 10000.0, "C": 0.0},
    }
    return payload


@pytest.mark.parametrize(
    "availability",
    [
        {"status": "unavailable_pending_acquisition"},
        "unknown_pending_state",
    ],
)
def test_pressure_model_availability_is_a_fail_closed_scalar_enum(
    availability,
) -> None:
    payload = _reaction_fixture()
    model = payload["families"]["potassium_test_family"]["physical_properties"][
        "species"
    ]["K"]["pressure_models"][0]
    model["availability"] = availability

    with pytest.raises(CatalogCompileError, match="availability must be"):
        compile_vapour_rail_catalog(payload)


def test_pressure_model_availability_omitted_or_explicitly_unavailable() -> None:
    available = compile_vapour_rail_catalog(_reaction_fixture())
    assert available.species["K"].evaluator is not None

    payload = _reaction_fixture()
    model = payload["families"]["potassium_test_family"]["physical_properties"][
        "species"
    ]["K"]["pressure_models"][0]
    model["availability"] = "unavailable_pending_acquisition"
    unavailable = compile_vapour_rail_catalog(payload)
    assert unavailable.species["K"].evaluator is None


@pytest.mark.parametrize(
    "missing",
    [
        "model_provenance",
        "migration_provenance",
        "fit_provenance",
        "fit_residual",
        "anchor_refs",
        "composition_mode",
    ],
)
def test_effective_pressure_reference_fit_requires_fit_evidence(missing: str) -> None:
    payload = _effective_pressure_fit_fixture()
    row = payload["families"]["potassium_test_family"][
        "physical_properties"
    ]["species"]["K"]
    row["backsolve"] = {
        "residual_dex": 0.12,
        "provenance": {"generated_by": "fit.py", "fit_objective": "minimax"},
    }
    row["validation"]["anchor_refs"] = ["validation:fit-grid"]
    if missing == "model_provenance":
        row["pressure_models"][0].pop("provenance")
    elif missing == "migration_provenance":
        row["pressure_models"][0]["provenance"] = [
            {"source": "schema_v1_in_place_migration"}
        ]
    elif missing == "fit_provenance":
        row["backsolve"].pop("provenance")
    elif missing == "fit_residual":
        row["backsolve"].pop("residual_dex")
    elif missing == "anchor_refs":
        row["validation"]["anchor_refs"] = []
    else:
        row["pressure_models"][0].pop("provenance")
        row.pop("backsolve")
        row["validation"]["anchor_refs"] = []
        row["pressure_models"][0][
            "composition_mode"
        ] = "gas_association_from_effective_base"

    with pytest.raises(
        CatalogCompileError,
        match="effective-pressure reference fit requires",
    ):
        compile_vapour_rail_catalog(payload)


def test_effective_pressure_reference_fit_compiles_with_fit_evidence() -> None:
    payload = _effective_pressure_fit_fixture()
    row = payload["families"]["potassium_test_family"][
        "physical_properties"
    ]["species"]["K"]
    row["backsolve"] = {
        "residual_dex": 0.12,
        "provenance": {"generated_by": "fit.py", "fit_objective": "minimax"},
    }
    row["validation"]["anchor_refs"] = ["validation:fit-grid"]

    compiled = compile_vapour_rail_catalog(payload)

    assert compiled.species["K"].evaluator is not None


def test_gas_association_base_must_match_evidenced_effective_fit() -> None:
    payload = _yaml("vapor_pressures.yaml")
    compiled = compile_vapour_rail_catalog(payload)
    assert compiled.species["FeO_association_gas"].evaluator is not None

    model = payload["families"]["oxide_vapors_feo_association_family"][
        "physical_properties"
    ]["species"]["FeO_association_gas"]["pressure_models"][0]
    model["base_reference_pressure_model"]["coefficients"]["A"] += 0.1

    with pytest.raises(
        CatalogCompileError,
        match="effective-pressure reference fit requires",
    ):
        compile_vapour_rail_catalog(payload)


_EVIDENCED_BASE_COEFFICIENTS = {"A": 9.408547, "B": 23312.312336, "C": 1000.0}
_ZERO_ACTIVITY_FAMILIES = (
    "antoine",
    "tabulated_equilibrium",
    "standard_reaction_term",
    "nasa_cea_7",
    "nasa_cea_9",
    "shomate",
)
_ZERO_ACTIVITY_MODES = (
    None,
    "source_reaction_activity",
    "gas_association_from_effective_base",
    "not_a_composition_mode",
)
_ZERO_ACTIVITY_EVIDENCE = (
    "none",
    "migration_only",
    "full",
    "diagnostic_unfitted",
    "diagnostic_token",
    "diagnostic_sibling",
)
_ZERO_ACTIVITY_BASE_LINKS = ("absent", "matching", "mismatched")
_COMPILABLE_ZERO_ACTIVITY_FAMILIES = frozenset(
    {"antoine", "tabulated_equilibrium", "standard_reaction_term"}
)


def _set_fit_evidence(row: dict, model: dict, kind: str) -> None:
    if kind == "none":
        model.pop("provenance", None)
        row.pop("backsolve", None)
        row["validation"]["anchor_refs"] = []
        return
    if kind == "migration_only":
        model["provenance"] = [{"source": "schema_v1_in_place_migration"}]
        model.pop("anchor_disagreement", None)
    elif kind == "full":
        model["provenance"] = [
            {
                "source_id": "fit-grid-source",
                "observation_id": "fit-grid",
            }
        ]
        model.pop("anchor_disagreement", None)
    elif kind == "diagnostic_unfitted":
        model["provenance"] = [
            {
                "source_id": "pseudo_psat_backsolved_from_vaporock",
                "observation_id": "fit-grid",
                "status": "diagnostic_only",
                "law_provenance": _DIAGNOSTIC_LAW_PROVENANCE,
            }
        ]
        model["anchor_disagreement"] = {
            "role": "disagreement_not_source",
            "anchor": "validation:disagreement-not-source",
            "temperature_K": 1100.0,
            "rail_pressure_Pa": 1.0,
            "vaporock_pressure_Pa": 0.1,
        }
    elif kind == "diagnostic_token":
        # Status plus a real observation id, residual, and anchors. That is
        # not the unfitted statement and must not certify.
        model["provenance"] = [
            {
                "source_id": "fit-grid-source",
                "observation_id": "fit-grid",
                "status": "diagnostic_only",
            }
        ]
        model.pop("anchor_disagreement", None)
    elif kind == "diagnostic_sibling":
        # A fitted citation beside diagnostic_only must not launder it.
        model["provenance"] = [
            {
                "source_id": "pseudo_psat_backsolved_from_vaporock",
                "observation_id": "fit-grid",
                "status": "diagnostic_only",
                "law_provenance": _DIAGNOSTIC_LAW_PROVENANCE,
            },
            {
                "source_id": "fit-grid-source",
                "observation_id": "fit-grid",
            },
        ]
        model["anchor_disagreement"] = {
            "role": "disagreement_not_source",
            "anchor": "validation:disagreement-not-source",
            "temperature_K": 1100.0,
            "rail_pressure_Pa": 1.0,
            "vaporock_pressure_Pa": 0.1,
        }
    else:
        raise AssertionError(kind)
    row["backsolve"] = {
        "residual_dex": 0.12,
        "provenance": {"generated_by": "fit.py", "fit_objective": "minimax"},
    }
    row["validation"]["anchor_refs"] = ["validation:fit-grid"]


def _rename_family_species(payload: dict, species_id: str) -> dict:
    family = payload["families"].pop("potassium_test_family")
    family["code_metadata"]["formula_id"] = species_id
    family["code_metadata"]["solve_group_id"] = f"{species_id}_family"
    row = family["physical_properties"]["species"].pop("K")
    row["formula"] = species_id
    family["physical_properties"]["species"][species_id] = row
    payload["families"][f"{species_id}_family"] = family
    return row


def _zero_activity_melt_payload(
    *,
    family: str,
    mode: str | None,
    evidence: str,
    base_link: str,
    child_id: str = "K",
) -> dict:
    """One cleaned-melt activity exponent of zero, plus an evidenced Antoine base.

    The child coefficients stay unrelated to the base. ``base_link`` only
    changes the unused or embedded base block.
    """

    payload = _effective_pressure_fit_fixture()
    base_row = _rename_family_species(payload, "FeBase")
    base_model = base_row["pressure_models"][0]
    base_model["coefficients"] = dict(_EVIDENCED_BASE_COEFFICIENTS)
    base_model["activity_exponent"] = 0.0
    _set_fit_evidence(base_row, base_model, "full")

    if family == "standard_reaction_term":
        child_payload = _reaction_fixture()
    else:
        child_payload = _effective_pressure_fit_fixture()
    child_row = _rename_family_species(child_payload, child_id)
    child_model = child_row["pressure_models"][0]
    child_model["activity_exponent"] = 0.0
    child_model["activity_semantics"] = "effective_pressure_reference_fit"
    child_model["pressure_kind"] = "equilibrium_partial_pressure"
    if family == "tabulated_equilibrium":
        child_model["evaluator_family"] = "tabulated_equilibrium"
        child_model.pop("coefficients", None)
        child_model["points"] = [
            {"temperature_K": 1000.0, "pressure_Pa": 1.0},
            {"temperature_K": 1200.0, "pressure_Pa": 10.0},
        ]
    elif family != "standard_reaction_term":
        child_model["evaluator_family"] = family
        if family == "antoine":
            child_model["coefficients"] = {"A": 10.0, "B": 10000.0, "C": 0.0}
    _set_fit_evidence(child_row, child_model, evidence)
    if mode is not None:
        child_model["composition_mode"] = mode
    if base_link != "absent":
        coefficients = dict(_EVIDENCED_BASE_COEFFICIENTS)
        if base_link == "mismatched":
            coefficients["A"] += 1.0
        child_model["base_vapor_formula"] = "FeBase"
        child_model["base_reference_pressure_model"] = {
            "evaluator_family": "antoine",
            "coefficients": coefficients,
        }
    payload["families"].update(child_payload["families"])
    return payload


def _assert_zero_activity_admission(
    *,
    family: str,
    mode: str | None,
    evidence: str,
    base_link: str,
    child_id: str = "K",
) -> None:
    payload = _zero_activity_melt_payload(
        family=family,
        mode=mode,
        evidence=evidence,
        base_link=base_link,
        child_id=child_id,
    )
    compilable = family in _COMPILABLE_ZERO_ACTIVITY_FAMILIES
    admitted_unflagged = compilable and evidence == "full"
    admitted_flagged = (
        compilable and evidence == "diagnostic_unfitted" and family == "antoine"
    )
    try:
        compiled = compile_vapour_rail_catalog(payload)
    except CatalogCompileError as exc:
        if admitted_unflagged or admitted_flagged:
            pytest.fail(f"evidenced {family} law was refused: {exc}")
        if compilable:
            assert "effective-pressure reference fit requires" in str(exc)
        return
    assert admitted_unflagged or admitted_flagged, (
        f"{family} mode={mode!r} evidence={evidence} base={base_link} "
        f"child={child_id} compiled without evidence on the executable law"
    )
    evaluator = compiled.species[child_id].evaluator
    assert evaluator is not None
    assert evaluator.activity_exponent == 0.0
    assert evaluator.reference_model.physical_composite_ood is False
    assert getattr(evaluator.reference_model, "_association_base", None) is None
    evaluation = evaluator.evaluate(1100.0, pO2_bar=1.0)
    pressure = evaluation.pressure_pa
    notice = getattr(evaluation, "diagnostic_admission_notice", None)
    if admitted_unflagged:
        assert notice is None
        assert getattr(evaluator, "diagnostic_admission_notice", None) is None
        if family == "antoine":
            # log10(P/Pa) = 10 - 10000/1100 = 0.9090909090909083
            assert pressure == pytest.approx(
                _SYNTHETIC_DIAGNOSTIC_1100_PA, rel=1e-12, abs=0.0
            )
        else:
            assert pressure > 0.0
        return
    assert pressure == pytest.approx(
        _SYNTHETIC_DIAGNOSTIC_1100_PA, rel=1e-12, abs=0.0
    )
    _assert_diagnostic_disclosure(
        notice,
        executed_species_id=child_id,
        anchor="validation:disagreement-not-source",
        temperature_K=1100.0,
        rail_pressure_Pa=1.0,
        vaporock_pressure_Pa=0.1,
        law_pressure_Pa=_SYNTHETIC_DIAGNOSTIC_1100_PA,
    )
    _assert_diagnostic_disclosure(
        getattr(evaluator, "diagnostic_admission_notice", None),
        executed_species_id=child_id,
        anchor="validation:disagreement-not-source",
        temperature_K=1100.0,
        rail_pressure_Pa=1.0,
        vaporock_pressure_Pa=0.1,
        law_pressure_Pa=_SYNTHETIC_DIAGNOSTIC_1100_PA,
    )


@pytest.mark.parametrize("family", _ZERO_ACTIVITY_FAMILIES)
@pytest.mark.parametrize("mode", _ZERO_ACTIVITY_MODES)
@pytest.mark.parametrize("evidence", _ZERO_ACTIVITY_EVIDENCE)
@pytest.mark.parametrize("base_link", _ZERO_ACTIVITY_BASE_LINKS)
def test_zero_activity_melt_admits_only_an_evidenced_executable_law(
    family: str,
    mode: str | None,
    evidence: str,
    base_link: str,
) -> None:
    _assert_zero_activity_admission(
        family=family,
        mode=mode,
        evidence=evidence,
        base_link=base_link,
    )


@pytest.mark.parametrize("species_id", _live_vapour_species_ids())
def test_association_token_does_not_evidence_any_live_species(
    species_id: str,
) -> None:
    _assert_zero_activity_admission(
        family="antoine",
        mode="gas_association_from_effective_base",
        evidence="none",
        base_link="matching",
        child_id=species_id,
    )


def _unevidenced_law_copy(
    species_id: str,
    context: dict[str, object],
    **overrides: object,
) -> dict:
    """Cleaned-melt Antoine with no fit evidence, copying one live fit context.

    ``overrides`` changes a single identity field. The coefficients are the
    live law unless A, B, or C is overridden.
    """

    payload = _effective_pressure_fit_fixture()
    row = _rename_family_species(payload, species_id)
    row["formula"] = overrides.get("formula", context["formula"])
    row["parent_oxide"] = overrides.get("parent_oxide", context["parent_oxide"])
    row.pop("backsolve", None)
    row["validation"]["anchor_refs"] = []
    model = row["pressure_models"][0]
    model.pop("provenance", None)
    model["evaluator_family"] = "antoine"
    model["activity_semantics"] = "effective_pressure_reference_fit"
    model["activity_exponent"] = 0.0
    model["pressure_kind"] = "equilibrium_partial_pressure"
    model["species_basis"] = overrides.get("species_basis", context["species_basis"])
    model["coefficients"] = {
        "A": overrides.get("A", context["A"]),
        "B": overrides.get("B", context["B"]),
        "C": overrides.get("C", context["C"]),
    }
    model["valid_domain"] = {
        "temperature_K": [
            overrides.get("low", context["low"]),
            overrides.get("high", context["high"]),
        ]
    }
    family = payload["families"][f"{species_id}_family"]
    family["code_metadata"]["source_account"] = overrides.get(
        "source_account", context["source_account"]
    )
    return payload


def _oracle_provenance_cites_observations(item: dict) -> bool:
    """True when a provenance item cites real observation ids.

    Independent of the compiler. A singular ``observation_id`` is one
    citation. ``observation_ids`` counts only as a nonempty sequence of
    nonblank strings. ``null``, ``""``, ``true``, and a bare string are not
    citations. A present field that is malformed voids the item, so a valid
    sibling cannot launder it.
    """

    has_singular = "observation_id" in item
    has_plural = "observation_ids" in item
    if not has_singular and not has_plural:
        return False
    if has_singular:
        raw = item.get("observation_id")
        if not isinstance(raw, str) or not raw.strip():
            return False
    if has_plural:
        raw_ids = item.get("observation_ids")
        if isinstance(raw_ids, (str, bytes)) or not isinstance(raw_ids, (list, tuple)):
            return False
        if len(raw_ids) == 0:
            return False
        if any(not isinstance(obs, str) or not obs.strip() for obs in raw_ids):
            return False
    return True


def _oracle_stored_oxygen_law(
    model: dict,
) -> tuple[float, float, str | None] | None:
    """Oxygen multiplier the evaluator stores. Independent of the compiler.

    Omitted exponent is 0 and stores no channel. A non-zero exponent needs a
    legal channel. None means the declaration cannot compile. This is not a
    call into the historic matcher, so a matcher that drops the multiplier
    cannot satisfy the grid by construction.
    """

    raw_exponent = model.get("pO2_exponent", None)
    if raw_exponent is None or raw_exponent == "":
        exponent = 0.0
    elif isinstance(raw_exponent, bool) or not isinstance(
        raw_exponent, (int, float)
    ):
        return None
    else:
        exponent = float(raw_exponent)
        if not math.isfinite(exponent):
            return None
    if "pO2_reference_bar" not in model:
        reference = 1.0
    else:
        raw_reference = model.get("pO2_reference_bar")
        if isinstance(raw_reference, bool) or not isinstance(
            raw_reference, (int, float)
        ):
            return None
        reference = float(raw_reference)
        if not math.isfinite(reference) or reference <= 0.0:
            return None
    if exponent == 0.0:
        return (0.0, reference, None)
    raw_channel = model.get("oxygen_fugacity_channel")
    if not isinstance(raw_channel, str) or not raw_channel.strip():
        return None
    channel = raw_channel.strip()
    if channel not in {"intrinsic_melt", "transport_headspace"}:
        return None
    return (exponent, reference, channel)


@functools.lru_cache(maxsize=1)
def _live_bound_effective_fit_contexts() -> tuple[dict[str, object], ...]:
    """Antoine effective fits whose own row carries the evidence fields.

    This scan is the test oracle. It does not call the compiler's historic
    matcher, so a matcher that ignores species or the oxygen multiplier
    cannot satisfy the test by construction.
    """

    payload = _yaml("vapor_pressures.yaml")
    found: list[dict[str, object]] = []
    for family in payload["families"].values():
        account = family["code_metadata"]["source_account"]
        for species_id, row in family["physical_properties"]["species"].items():
            models = row.get("pressure_models") or []
            if len(models) != 1 or not isinstance(models[0], dict):
                continue
            model = models[0]
            if model.get("evaluator_family") != "antoine":
                continue
            if model.get("activity_semantics") != "effective_pressure_reference_fit":
                continue
            if model.get("activity_exponent", 0.0) not in (0, 0.0, None, ""):
                continue
            if model.get("pressure_kind") != "equilibrium_partial_pressure":
                continue
            provenance = model.get("provenance") or []
            has_model_provenance = any(
                isinstance(item, dict)
                and str(item.get("source_id") or "").strip()
                and _oracle_provenance_cites_observations(item)
                for item in provenance
            )
            backsolve = row.get("backsolve") if isinstance(row.get("backsolve"), dict) else {}
            back_provenance = (
                backsolve.get("provenance")
                if isinstance(backsolve.get("provenance"), dict)
                else {}
            )
            try:
                residual = float(backsolve.get("residual_dex"))
            except (TypeError, ValueError):
                residual = float("nan")
            anchors = (row.get("validation") or {}).get("anchor_refs") or []
            if not (
                has_model_provenance
                and str(back_provenance.get("generated_by") or "").strip()
                and str(back_provenance.get("fit_objective") or "").strip()
                and math.isfinite(residual)
                and residual >= 0.0
                and anchors
            ):
                continue
            coefficients = model["coefficients"]
            low, high = model["valid_domain"]["temperature_K"]
            found.append(
                {
                    "species_id": str(species_id),
                    "formula": row["formula"],
                    "parent_oxide": row["parent_oxide"],
                    "source_account": account,
                    "species_basis": model["species_basis"],
                    "A": float(coefficients["A"]),
                    "B": float(coefficients["B"]),
                    "C": float(coefficients["C"]),
                    "low": float(low),
                    "high": float(high),
                    "oxygen_law": _oracle_stored_oxygen_law(model),
                }
            )
    return tuple(found)


# Hand literals for the pseudo-block law evaluate() runs. The crosscheck file
# is disagreement evidence at 1750 K, not the source of these coefficients.
# pO2_exponent is omitted, so the stored multiplier is 0 and pO2 does not
# scale P. Derivations are next to _FE_PSEUDO_LAW_1750_PA.
_CITED_EFFECTIVE_FIT_PRESSURE_PA = {
    ("Fe", 1750.0): _FE_PSEUDO_LAW_1750_PA,
    ("Fe", 1800.0): _FE_PSEUDO_LAW_1800_PA,
}


def _cited_effective_fit_pressure_pa(species_id: str, temperature_K: float) -> float:
    pressure = _CITED_EFFECTIVE_FIT_PRESSURE_PA.get((species_id, temperature_K))
    assert pressure is not None, (
        f"no cited pressure pin for {species_id} at {temperature_K} K"
    )
    return pressure


def _shifted_fit_context_value(field: str, context: dict[str, object]) -> object:
    current = context[field]
    if field in {"formula", "parent_oxide", "species_basis"}:
        return f"{current}_other"
    if field in {"A", "B", "C", "low", "high"}:
        return float(current) + 1.0
    raise AssertionError(field)


@pytest.mark.parametrize("species_id", _live_vapour_species_ids())
def test_copied_effective_fit_law_is_evidence_only_for_its_species(
    species_id: str,
) -> None:
    """Invariant: Fe's coefficients do not evidence any other cleaned-melt species.

    Every live species is given the evidenced fit's formula, parent oxide,
    species basis, and Antoine law, and no provenance, residual, or anchors.
    Admission is allowed only for the species the live evidence names.
    """

    contexts = _live_bound_effective_fit_contexts()
    assert contexts
    assert any(item["species_id"] == "Fe" for item in contexts)
    fits = bound_effective_fit_evidence(_yaml("vapor_pressures.yaml"))
    for context in contexts:
        payload = _unevidenced_law_copy(species_id, context)
        if species_id != context["species_id"]:
            with pytest.raises(
                CatalogCompileError,
                match="effective-pressure reference fit requires",
            ):
                compile_vapour_rail_catalog(payload, bound_effective_fits=fits)
            continue
        compiled = compile_vapour_rail_catalog(payload, bound_effective_fits=fits)
        assert compiled.species[species_id].validation_anchor_refs == ()
        evaluator = compiled.species[species_id].evaluator
        assert evaluator is not None
        assert evaluator.activity_exponent == 0.0
        temperature_K = 1800.0
        if not (float(context["low"]) <= temperature_K <= float(context["high"])):
            temperature_K = 0.5 * (float(context["low"]) + float(context["high"]))
        expected_pa = _cited_effective_fit_pressure_pa(
            str(context["species_id"]), temperature_K
        )
        evaluation = evaluator.evaluate(temperature_K)
        assert evaluation.pressure_pa == pytest.approx(
            expected_pa, rel=1e-12, abs=0.0
        )
        record = next(item for item in fits if item[0] == context["species_id"])
        _assert_admission_notice_for_record(evaluator, evaluation, record)
        if (
            context["species_id"] == "Fe"
            and float(context["low"]) <= 1750.0 <= float(context["high"])
        ):
            pinned = evaluator.evaluate(1750.0)
            assert pinned.pressure_pa == pytest.approx(
                _cited_effective_fit_pressure_pa("Fe", 1750.0),
                rel=1e-12,
                abs=0.0,
            )
            _assert_fe_pseudo_law_disclosure(
                getattr(pinned, "diagnostic_admission_notice", None)
            )


_FIT_CONTEXT_FIELDS = (
    "formula",
    "parent_oxide",
    "species_basis",
    "A",
    "B",
    "C",
    "low",
    "high",
)


@pytest.mark.parametrize("field", _FIT_CONTEXT_FIELDS)
def test_historic_reuse_rejects_each_changed_fit_context_field(field: str) -> None:
    """One changed identity field drops historic reuse of the evidenced law."""

    contexts = _live_bound_effective_fit_contexts()
    assert contexts
    fits = bound_effective_fit_evidence(_yaml("vapor_pressures.yaml"))
    for context in contexts:
        payload = _unevidenced_law_copy(
            str(context["species_id"]),
            context,
            **{field: _shifted_fit_context_value(field, context)},
        )
        with pytest.raises(
            CatalogCompileError,
            match="effective-pressure reference fit requires",
        ):
            compile_vapour_rail_catalog(payload, bound_effective_fits=fits)


# Omitted (None) is a distinct declaration from an explicit zero or 1.
# Exponents cover zero, signed zero, the reviewer value 1, and both signs
# of a fractional multiplier. References cover the default, the explicit
# 1 bar the reviewer adds, and 1e-9 bar, which would cancel that exponent
# at the reviewer's single pressure point. Channels cover both legal planes
# and one illegal name.
_OXYGEN_EXPONENTS = (None, 0, float("-0.0"), 1, -1, 0.5, -0.5)
_OXYGEN_REFERENCES = (None, 1, 1.0e-9)
_OXYGEN_CHANNELS = (
    None,
    "transport_headspace",
    "intrinsic_melt",
    "not_a_channel",
)


@pytest.mark.parametrize("exponent", _OXYGEN_EXPONENTS)
@pytest.mark.parametrize("reference", _OXYGEN_REFERENCES)
@pytest.mark.parametrize("channel", _OXYGEN_CHANNELS)
def test_historic_reuse_binds_every_oxygen_executable_law(
    exponent: object,
    reference: object,
    channel: object,
) -> None:
    """Invariant: historic reuse is the pressure evaluate() computes.

    A cleaned-melt activity exponent of 0 is admitted only when the Antoine
    curve and the stored oxygen multiplier are the evidenced law for that
    same species. The multiplier is pO2_exponent, pO2_reference_bar, and
    oxygen_fugacity_channel. Coefficients alone are not that law.

    The reviewer bypass is one cell: Fe's identity and coefficients, no
    evidence fields, plus pO2_exponent 1, pO2_reference_bar 1, and
    oxygen_fugacity_channel transport_headspace. evaluate() would then
    multiply the evidenced curve by pO2/p_ref. At pO2 = 1e-9 bar that
    factor is 1e-9. The cell must be refused.
    """

    contexts = _live_bound_effective_fit_contexts()
    assert contexts
    assert any(
        item["species_id"] == "Fe" and item["oxygen_law"] == (0.0, 1.0, None)
        for item in contexts
    )
    fits = bound_effective_fit_evidence(_yaml("vapor_pressures.yaml"))
    for context in contexts:
        species_id = str(context["species_id"])
        payload = _unevidenced_law_copy(species_id, context)
        model = payload["families"][f"{species_id}_family"][
            "physical_properties"
        ]["species"][species_id]["pressure_models"][0]
        if exponent is not None:
            model["pO2_exponent"] = exponent
        if reference is not None:
            model["pO2_reference_bar"] = reference
        if channel is not None:
            model["oxygen_fugacity_channel"] = channel
        expected = _oracle_stored_oxygen_law(model)
        live = context["oxygen_law"]
        reviewer_bypass = (
            exponent == 1
            and reference == 1
            and channel == "transport_headspace"
        )
        if expected is not None and expected == live:
            assert not reviewer_bypass
            compiled = compile_vapour_rail_catalog(
                payload, bound_effective_fits=fits
            )
            evaluator = compiled.species[species_id].evaluator
            assert evaluator is not None
            assert evaluator.activity_exponent == 0.0
            assert (
                float(evaluator.pO2_exponent),
                float(evaluator.pO2_reference_bar),
                evaluator.oxygen_fugacity_channel,
            ) == live
            temperature_K = 1800.0
            if not (
                float(context["low"])
                <= temperature_K
                <= float(context["high"])
            ):
                temperature_K = 0.5 * (
                    float(context["low"]) + float(context["high"])
                )
            # The cited Fe law stores exponent 0, so every pO2 returns the
            # same pressure. A non-zero cited exponent needs its own literals.
            live_exponent, _live_reference, _live_channel = live
            assert live_exponent == 0.0
            expected_pa = _cited_effective_fit_pressure_pa(species_id, temperature_K)
            evaluation = evaluator.evaluate(temperature_K)
            assert evaluation.pressure_pa == pytest.approx(
                expected_pa, rel=1e-12, abs=0.0
            )
            record = next(item for item in fits if item[0] == species_id)
            _assert_admission_notice_for_record(evaluator, evaluation, record)
            assert evaluator.evaluate(
                temperature_K, pO2_bar=1.0
            ).pressure_pa == pytest.approx(expected_pa, rel=1e-12, abs=0.0)
            assert evaluator.evaluate(
                temperature_K, pO2_bar=1.0e-9
            ).pressure_pa == pytest.approx(expected_pa, rel=1e-12, abs=0.0)
            continue
        with pytest.raises(CatalogCompileError) as exc_info:
            compile_vapour_rail_catalog(payload, bound_effective_fits=fits)
        if expected is None:
            continue
        assert "effective-pressure reference fit requires" in str(exc_info.value)
        if reviewer_bypass:
            # (pO2/p_ref)**1 at 1e-9 bar is 1e-9. Admission must stop first.
            assert expected == (1.0, 1.0, "transport_headspace")
            assert live != expected


def test_unevidenced_fit_follows_the_request_not_a_warm_cache() -> None:
    """A cached admission must not survive removal of the request's evidence.

    The unevidenced Fe law is refused when the compile request carries no
    bound fits. Supplying the live catalog's fits admits it. Compiling that
    same payload again without the fits misses the cache and refuses.
    """

    contexts = _live_bound_effective_fit_contexts()
    fe = next(item for item in contexts if item["species_id"] == "Fe")
    payload = _unevidenced_law_copy("Fe", fe)
    with pytest.raises(
        CatalogCompileError,
        match="effective-pressure reference fit requires",
    ):
        compile_vapour_rail_catalog(payload, emit_u0_request_rules=False)
    fits = bound_effective_fit_evidence(_yaml("vapor_pressures.yaml"))
    admitted = compile_vapour_rail_catalog(
        payload,
        emit_u0_request_rules=False,
        bound_effective_fits=fits,
    )
    evaluator = admitted.species["Fe"].evaluator
    assert evaluator is not None
    evaluation = evaluator.evaluate(1800.0)
    assert evaluation.pressure_pa == pytest.approx(
        _cited_effective_fit_pressure_pa("Fe", 1800.0), rel=1e-12, abs=0.0
    )
    record = next(item for item in fits if item[0] == "Fe")
    _assert_admission_notice_for_record(evaluator, evaluation, record)
    pinned = evaluator.evaluate(1750.0)
    assert pinned.pressure_pa == pytest.approx(
        _FE_PSEUDO_LAW_1750_PA, rel=1e-12, abs=0.0
    )
    _assert_fe_pseudo_law_disclosure(
        getattr(pinned, "diagnostic_admission_notice", None)
    )
    with pytest.raises(
        CatalogCompileError,
        match="effective-pressure reference fit requires",
    ):
        compile_vapour_rail_catalog(payload, emit_u0_request_rules=False)


def test_live_zero_activity_melt_species_match_the_evidence_invariant() -> None:
    payload = _yaml("vapor_pressures.yaml")
    catalog = compile_vapour_rail_catalog(payload, emit_u0_request_rules=False)
    checked: list[str] = []
    for species_id, compiled in catalog.species.items():
        evaluator = compiled.evaluator
        if evaluator is None or evaluator.activity_exponent != 0.0:
            continue
        if compiled.code_metadata.source_account != "process.cleaned_melt":
            continue
        checked.append(species_id)
        executes_association = (
            evaluator.evaluator_family in RUNTIME_THERMO_EVALUATOR_FAMILIES
            and evaluator.reference_model.physical_composite_ood is True
            and not evaluator.reference_model.coefficients
        )
        if executes_association:
            assert species_id == "FeO_association_gas"
            association_model = payload["families"][compiled.family_id][
                "physical_properties"
            ]["species"][species_id]["pressure_models"][0]
            embedded = association_model["base_reference_pressure_model"]
            assert embedded["coefficients"]["A"] == pytest.approx(9.408547)
            assert embedded["coefficients"]["B"] == pytest.approx(23312.312336)
            assert embedded["coefficients"]["C"] == pytest.approx(1000.0)
            # The closure calls log10_pressure() and does not apply a base
            # oxygen multiplier. Live admission is the identity multiplier only.
            base_model = payload["families"]["metals_fe_family"][
                "physical_properties"
            ]["species"]["Fe"]["pressure_models"][0]
            assert _oracle_stored_oxygen_law(base_model) == (0.0, 1.0, None)
            assert _oracle_stored_oxygen_law(embedded) == (0.0, 1.0, None)
            assert base_model["provenance"][0]["status"] == "diagnostic_only"
            assert (
                base_model["provenance"][0]["law_provenance"]
                == _DIAGNOSTIC_LAW_PROVENANCE
            )
            assert (
                base_model["provenance"][0]["source_id"]
                == "pseudo_psat_backsolved_from_vaporock"
            )
            for pO2_bar, expected_pa in _CITED_FEO_ASSOCIATION_PRESSURE_PA.items():
                evaluation = evaluator.evaluate(1800.0, pO2_bar=pO2_bar)
                assert evaluation.pressure_pa == pytest.approx(
                    expected_pa, rel=1e-12, abs=0.0
                )
                _assert_fe_pseudo_law_disclosure(
                    getattr(evaluation, "diagnostic_admission_notice", None)
                )
            _assert_fe_pseudo_law_disclosure(
                getattr(evaluator, "diagnostic_admission_notice", None)
            )
            continue
        row = payload["families"][compiled.family_id]["physical_properties"][
            "species"
        ][species_id]
        model = row["pressure_models"][0]
        notice = getattr(evaluator, "diagnostic_admission_notice", None)
        if notice is None:
            assert model["provenance"][0].get("status") != "diagnostic_only"
            assert model["provenance"][0]["source_id"]
            assert row["backsolve"]["residual_dex"] >= 0.0
            assert row["backsolve"]["provenance"]["generated_by"]
            assert row["validation"]["anchor_refs"]
            continue
        assert species_id == "Fe"
        assert (
            model["provenance"][0]["source_id"]
            == "pseudo_psat_backsolved_from_vaporock"
        )
        assert model["provenance"][0]["source_id"] != model["anchor_disagreement"]["anchor"]
        assert model["anchor_disagreement"]["anchor"] == _FE_DISAGREEMENT_ANCHOR
        assert model["anchor_disagreement"]["role"] == "disagreement_not_source"
        assert row["validation"]["anchor_refs"] == [_FE_DISAGREEMENT_ANCHOR]
        assert model["provenance"][0]["status"] == "diagnostic_only"
        assert model["provenance"][0]["law_provenance"] == _DIAGNOSTIC_LAW_PROVENANCE
        for temperature_K, expected_pa in (
            (1750.0, _FE_PSEUDO_LAW_1750_PA),
            (1800.0, _FE_PSEUDO_LAW_1800_PA),
        ):
            evaluation = evaluator.evaluate(temperature_K)
            assert evaluation.pressure_pa == pytest.approx(
                expected_pa, rel=1e-12, abs=0.0
            )
            _assert_fe_pseudo_law_disclosure(
                getattr(evaluation, "diagnostic_admission_notice", None)
            )
        _assert_fe_pseudo_law_disclosure(notice)
    assert {"Fe", "FeO_association_gas"} <= set(checked)
    assert (
        getattr(catalog.species["Fe"].evaluator, "diagnostic_admission_notice", None)
        is not None
    )


def test_diagnostic_admission_reaches_every_public_carrier() -> None:
    """Invariant: a diagnostic admission is on every public pressure carrier.

    Every live species whose evaluator carries diagnostic_admission_notice
    is resolved as a batch answer. Every such species the oxide provider
    publishes keeps the same notice on its provenance. The notice carries
    authority, provenance, the disagreement anchor, both gaps, and t-1723.
    The metal compiled-reference rail publishes pressure_pa only; a
    diagnostic species must not sit on that rail.
    """

    from simulator.vapour_rail.batch import (
        FLUX_ACTIVATION_EPOCH_RG_MANIFEST,
        FluxActivationContext,
        PressureUpperBound,
        PressureValue,
    )
    from simulator.vapour_rail.instrumentation import serialize_vapour_answer

    payload = _yaml("vapor_pressures.yaml")
    provider = BuiltinVaporPressureProvider(payload)
    catalog = provider._vapour_rail_catalog
    assert catalog is not None
    noticed: list[str] = []
    oxide_noticed: list[str] = []
    parents: dict[str, float] = {}
    for species_id, compiled in sorted(catalog.species.items()):
        evaluator = compiled.evaluator
        if evaluator is None:
            continue
        if getattr(evaluator, "diagnostic_admission_notice", None) is None:
            continue
        noticed.append(species_id)
        row = payload["families"][compiled.family_id]["physical_properties"][
            "species"
        ][species_id]
        parent = row.get("parent_oxide")
        if isinstance(parent, str) and parent.strip():
            parents[parent.strip()] = 1.0
        if compiled.code_metadata.compatibility_projection == "oxide_vapors":
            oxide_noticed.append(species_id)
        elif compiled.code_metadata.compatibility_projection == "metals":
            # equilibrium.py and the metal rail keep only pressure_pa from
            # prefer_compiled_reference_pressure_model. That publisher does
            # not copy this notice. Fail until it calls
            # apply_public_carrier_notices.
            assert not row.get("prefer_compiled_reference_pressure_model"), (
                species_id
            )
    assert "Fe" in noticed
    assert "FeO_association_gas" in oxide_noticed
    assert parents

    batch = catalog.resolve_batch(
        {"process.cleaned_melt": dict(parents)},
        state={
            "temperature_K": 1750.0,
            "process_phase": "hot_train",
            "stage": "evaporation",
            "fO2_bar": 1.0e-9,
            "source_reaction_fO2_bar": 1.0e-9,
        },
        flux_activation_context=FluxActivationContext(
            epoch=FLUX_ACTIVATION_EPOCH_RG_MANIFEST
        ),
    )
    for species_id in noticed:
        compiled = catalog.species[species_id]
        evaluator = compiled.evaluator
        assert evaluator is not None
        kwargs: dict[str, float] = {}
        if evaluator.pO2_exponent or evaluator.oxygen_fugacity_channel:
            kwargs["pO2_bar"] = 1.0e-9
        evaluation = evaluator.evaluate(1750.0, **kwargs)
        evaluation_notice = getattr(evaluation, "diagnostic_admission_notice", None)
        answer = batch.channel(species_id)
        assert answer.refusal_code is None, (species_id, answer.extra)
        assert isinstance(answer.pressure, (PressureValue, PressureUpperBound))
        assert answer.pressure.pa > 0.0
        if species_id == "Fe":
            # Hand literal for log10(P/Pa) = A - B/(T+C) at 1750 K.
            # A=9.408547, B=23312.312336, C=1000, T+C=2750.
            # log10(P) = 9.408547 - 23312.312336/2750
            # P = 8.537731922030234 Pa. Not recomputed from the catalog.
            assert isinstance(answer.pressure, PressureValue)
            assert answer.pressure.pa == pytest.approx(
                _FE_PSEUDO_LAW_1750_PA, rel=1e-12, abs=0.0
            )
        public = serialize_vapour_answer(answer)
        public_notices = public["extra"]["diagnostic_admission_notice"]
        assert public_notices == [evaluation_notice]
        public_notice = public_notices[0]
        assert public_notice["authority"] == _DIAGNOSTIC_AUTHORITY
        assert public_notice["law_provenance"] == _DIAGNOSTIC_LAW_PROVENANCE
        assert public_notice["closes_with"] == _DIAGNOSTIC_CLOSER
        assert public_notice["evaluator_form"] == _DIAGNOSTIC_EVALUATOR_FORM
        assert "gap_dex_law_vs_rail" in public_notice["comparators"]
        assert "gap_dex_law_vs_vaporock" in public_notice["comparators"]
        if public_notice["executed_species_id"] == "Fe":
            _assert_fe_pseudo_law_disclosure(public_notice)

    provider_result = provider.dispatch(
        IntentRequest(
            intent=ChemistryIntent.VAPOR_PRESSURE,
            account_view=ProviderAccountView(
                accounts={"process.cleaned_melt": dict(parents)},
                species_formula_registry={},
            ),
            temperature_C=1600.0,
            pressure_bar=1e-6,
            control_inputs={"pO2_bar": 1.0e-9},
        )
    )
    assert provider_result.status == "ok"
    provenance_by_species = provider_result.diagnostic[
        "vapor_pressure_numerator_provenance"
    ]
    for species_id in oxide_noticed:
        evaluator = catalog.species[species_id].evaluator
        assert evaluator is not None
        expected = getattr(evaluator, "diagnostic_admission_notice", None)
        provenance = provenance_by_species[species_id]
        assert provenance["diagnostic_admission_notice"] == [expected]
        if expected["executed_species_id"] == "Fe":
            _assert_fe_pseudo_law_disclosure(
                provenance["diagnostic_admission_notice"][0]
            )
    # The metal Fe pressure is the pure-component rail, not this law.
    metal_fe = provenance_by_species.get("Fe")
    if metal_fe is not None:
        assert "diagnostic_admission_notice" not in metal_fe


def _diagnostic_only_carrier_notice() -> dict:
    """A carrier notice with no sibling extrapolation_notice.

    Comparators are the Fe pseudo-law anchor gaps. The literal pressure is
    the hand evaluation at 1750 K, not a catalog read.
    """

    return {
        "authority": _DIAGNOSTIC_AUTHORITY,
        "reason": (
            "effective_pressure_reference_fit would legalize a cleaned-melt "
            "activity exponent of 0 without a certified executed fit"
        ),
        "law_provenance": _DIAGNOSTIC_LAW_PROVENANCE,
        "provenance_status": "diagnostic_only",
        "evaluator_form": _DIAGNOSTIC_EVALUATOR_FORM,
        "closes_with": _DIAGNOSTIC_CLOSER,
        "executed_species_id": "Fe",
        "anchor": _FE_DISAGREEMENT_ANCHOR,
        "anchor_role": "disagreement_not_source",
        "temperature_K": 1750.0,
        "law_pressure_Pa": _FE_PSEUDO_LAW_1750_PA,
        "comparators": {
            "rail_pressure_Pa": 0.6585350200427053,
            "gap_dex_law_vs_rail": 1.1127636390496998,
            "vaporock_pressure_Pa": 0.3004593572618527,
            "gap_dex_law_vs_vaporock": 1.4535567803078235,
        },
    }


def _assert_published_diagnostic_notice(notice: object) -> None:
    from collections.abc import Mapping

    assert isinstance(notice, (list, tuple)), type(notice)
    # Coating merge freezes the snapshot, so a notice body may be a
    # mapping proxy rather than a dict. The fields are the publication.
    items = [item for item in notice if isinstance(item, Mapping)]
    assert items
    canonical = [
        item
        for item in items
        if item.get("closes_with") == _DIAGNOSTIC_CLOSER
        and item.get("executed_species_id") == "Fe"
        and abs(
            float(item["comparators"]["gap_dex_law_vs_rail"])
            - 1.1127636390496998
        )
        < 1e-9
    ]
    assert canonical, items
    notice = canonical[0]
    assert notice["authority"] == _DIAGNOSTIC_AUTHORITY
    assert notice["law_provenance"] == _DIAGNOSTIC_LAW_PROVENANCE
    assert notice["provenance_status"] == "diagnostic_only"
    assert notice["evaluator_form"] == _DIAGNOSTIC_EVALUATOR_FORM
    assert notice["closes_with"] == _DIAGNOSTIC_CLOSER
    assert notice["executed_species_id"] == "Fe"
    comparators = notice["comparators"]
    assert "gap_dex_law_vs_rail" in comparators
    assert "gap_dex_law_vs_vaporock" in comparators
    assert comparators["gap_dex_law_vs_rail"] == pytest.approx(
        1.1127636390496998, rel=0, abs=1e-12
    )
    assert comparators["gap_dex_law_vs_vaporock"] == pytest.approx(
        1.4535567803078235, rel=0, abs=1e-12
    )


def _published_diagnostic_notice(surface: str) -> dict:
    """Feed one carrier whose only notice is the diagnostic admission.

    Each arm calls the production entry point. The carrier has no
    extrapolation_notice. Deleting that surface's collector call drops
    the provenance, the comparator gaps, and the t-1723 closer.
    """

    notice = _diagnostic_only_carrier_notice()
    extra = {"diagnostic_admission_notice": deepcopy(notice)}
    assert list(extra) == ["diagnostic_admission_notice"]

    if surface == "batch_request_answer":
        from simulator.vapour_rail.batch import (
            FLUX_ACTIVATION_EPOCH_RG_MANIFEST,
            FluxActivationContext,
            PressureValue,
        )
        from simulator.vapour_rail.instrumentation import (
            public_carrier_notices,
            serialize_vapour_answer,
        )

        # Live Fe evaluate() attaches only diagnostic_admission_notice.
        # 1750 K is inside 1400-1900 K and the oxygen exponent is 0, so
        # the floor slot on the evaluation is None and the collector
        # omits it. Resolution must copy the evaluator notice.
        provider = BuiltinVaporPressureProvider(_yaml("vapor_pressures.yaml"))
        catalog = provider._vapour_rail_catalog
        assert catalog is not None
        evaluator = catalog.species["Fe"].evaluator
        assert evaluator is not None
        evaluation = evaluator.evaluate(1750.0)
        assert list(public_carrier_notices(evaluation)) == [
            "diagnostic_admission_notice"
        ]
        batch = catalog.resolve_batch(
            {"process.cleaned_melt": {"FeO": 1.0}},
            {
                "temperature_K": 1750.0,
                "process_phase": "hot_train",
                "stage": "evaporation",
                "fO2_bar": 1.0e-9,
                "source_reaction_fO2_bar": 1.0e-9,
            },
            flux_activation_context=FluxActivationContext(
                epoch=FLUX_ACTIVATION_EPOCH_RG_MANIFEST
            ),
        )
        answer = batch.channel("Fe")
        assert answer.refusal_code is None
        assert isinstance(answer.pressure, PressureValue)
        assert answer.pressure.pa == pytest.approx(
            _FE_PSEUDO_LAW_1750_PA, rel=1e-12, abs=0.0
        )
        public = serialize_vapour_answer(answer)
        assert "extrapolation_notice" not in public["extra"]
        return public["extra"]["diagnostic_admission_notice"]

    if surface == "source_provenance":
        from simulator.vapour_rail.batch import (
            FLUX_ACTIVATION_EPOCH_RG_MANIFEST,
            FluxActivationContext,
            PressureValue,
        )
        from simulator.vapour_rail.instrumentation import serialize_vapour_answer
        from tests.test_vapour_batch_request import _u0_stub

        # Evidenced Antoine: the evaluator carries no diagnostic notice.
        # The only notice is on source provenance. Resolution must copy it.
        payload = _effective_pressure_fit_fixture()
        row = payload["families"]["potassium_test_family"][
            "physical_properties"
        ]["species"]["K"]
        model = row["pressure_models"][0]
        _set_fit_evidence(row, model, "full")
        catalog = compile_vapour_rail_catalog(
            payload, u0_manifest=_u0_stub("K")
        )
        evaluator = catalog.species["K"].evaluator
        assert evaluator is not None
        assert getattr(evaluator, "diagnostic_admission_notice", None) is None
        batch = catalog.resolve_batch(
            {"process.cleaned_melt": {"K2O": 1.0}},
            {
                "temperature_K": 1100.0,
                "process_phase": "hot_train",
                "source_reaction_activity_provenance": {
                    "K": {"diagnostic_admission_notice": deepcopy(notice)},
                },
            },
            flux_activation_context=FluxActivationContext(
                epoch=FLUX_ACTIVATION_EPOCH_RG_MANIFEST
            ),
        )
        answer = batch.channel("K")
        assert answer.refusal_code is None
        assert isinstance(answer.pressure, PressureValue)
        # log10(P/Pa) = 10 - 10000/1100. Same coefficients as the fixture.
        assert answer.pressure.pa == pytest.approx(
            _SYNTHETIC_DIAGNOSTIC_1100_PA, rel=1e-12, abs=0.0
        )
        public = serialize_vapour_answer(answer)
        assert "extrapolation_notice" not in public["extra"]
        return public["extra"]["diagnostic_admission_notice"]

    if surface == "oxide_provenance":
        from simulator.vapour_rail.instrumentation import public_carrier_notices

        # Live FeO association at 1600 C (1873.15 K), inside 1400-1900 K.
        # pO2 1e-9 bar is not the dissociation floor and the oxygen
        # exponent is positive, so the evaluation's only notice is the
        # diagnostic admission. Provider dispatch must copy it.
        provider = BuiltinVaporPressureProvider(_yaml("vapor_pressures.yaml"))
        catalog = provider._vapour_rail_catalog
        assert catalog is not None
        evaluator = catalog.species["FeO_association_gas"].evaluator
        assert evaluator is not None
        evaluation = evaluator.evaluate(1873.15, pO2_bar=1.0e-9)
        assert list(public_carrier_notices(evaluation)) == [
            "diagnostic_admission_notice"
        ]
        result = provider.dispatch(
            IntentRequest(
                intent=ChemistryIntent.VAPOR_PRESSURE,
                account_view=ProviderAccountView(
                    accounts={"process.cleaned_melt": {"FeO": 1.0}},
                    species_formula_registry={},
                ),
                temperature_C=1600.0,
                pressure_bar=1e-6,
                control_inputs={"pO2_bar": 1.0e-9},
            )
        )
        assert result.status == "ok"
        provenance = result.diagnostic["vapor_pressure_numerator_provenance"][
            "FeO_association_gas"
        ]
        assert provenance["P_eq_Pa"] > 0.0
        assert "extrapolation_notice" not in provenance
        return provenance["diagnostic_admission_notice"]

    if surface == "diagnostics_summary":
        from simulator.diagnostics import pressure_coating_pareto_diagnostic

        sim = SimpleNamespace(
            condensation_model=SimpleNamespace(
                last_sticking_alpha_provenance_notice={
                    "vapour_carrier_authority_by_species": {
                        "Fe": {"extra": deepcopy(extra)},
                    }
                }
            )
        )
        diagnostic = pressure_coating_pareto_diagnostic(
            sim, target_species=("Fe",)
        )
        assert diagnostic["status"] == "unavailable"
        entry = diagnostic["by_species"]["Fe"]
        assert "vapour_pressure_extrapolation_notice" not in entry
        return entry["diagnostic_admission_notice"]

    if surface == "pareto_table":
        from simulator.diagnostics import pressure_coating_pareto_diagnostic

        knudsen = {
            "gas_temperature_C": 1000.0,
            "carrier_gas": "N2",
            "overhead_pressure_mbar": 10.0,
            "pipe_diameter_m": 0.12,
            "knudsen_number": 0.001,
            "regime": "free_molecular",
            "segments": [
                {
                    "name": "main",
                    "characteristic_length_m": 0.12,
                    "knudsen_number": 0.001,
                    "regime": "viscous",
                },
                {
                    "name": "narrow",
                    "characteristic_length_m": 0.01,
                    "knudsen_number": 0.12,
                    "regime": "free_molecular",
                },
            ],
        }
        sim = SimpleNamespace(
            condensation_model=SimpleNamespace(
                last_knudsen_regime_diagnostic=knudsen,
                gas_temperature_C=1000.0,
                carrier_gas="N2",
                last_sticking_alpha_provenance_notice={
                    "vapour_carrier_authority_by_species": {
                        "Fe": {"extra": deepcopy(extra)},
                    }
                },
            ),
            _last_evaporation_flux_diagnostic={
                "evaporation_series_resistance": {
                    "Fe": {
                        "P_eq_Pa": _FE_PSEUDO_LAW_1750_PA,
                        "P_bulk_Pa": 0.0,
                        "alpha_intrinsic": 0.02,
                        "transport_length_m": 0.12,
                    }
                }
            },
        )
        diagnostic = pressure_coating_pareto_diagnostic(
            sim,
            per_hour=[{"wall_deposit_delta_kg": {"wall": {"Fe": 1.0}}}],
            target_species=("Fe",),
        )
        entry = diagnostic["by_species"]["Fe"]
        assert entry["P_eq_Pa"] == pytest.approx(
            _FE_PSEUDO_LAW_1750_PA, rel=1e-12, abs=0.0
        )
        assert "vapour_pressure_extrapolation_notice" not in entry
        return entry["diagnostic_admission_notice"]

    if surface == "runner_summary":
        from simulator.core import CampaignPhase
        from simulator.runner import build_per_hour_summary
        from simulator.state import HourSnapshot

        snapshot = HourSnapshot(campaign=CampaignPhase.C2A)
        sim = SimpleNamespace(
            campaign_mgr=SimpleNamespace(last_pO2_enforcement=None),
            product_ledger=lambda: {},
            record=SimpleNamespace(snapshots=[snapshot]),
            _last_vapour_batch_report={
                "n_requested": 1,
                "n_flux_active": 1,
                "n_refused": 0,
                "solve_bundle_ids": {},
                "metadata": {},
                "channels_by_species": {
                    "Fe": {
                        "pressure": {
                            "kind": "value",
                            "pa": _FE_PSEUDO_LAW_1750_PA,
                        },
                        "extra": deepcopy(extra),
                    }
                },
            },
        )
        summary = build_per_hour_summary(
            sim, snapshot, include_fe_redox_split=False
        )
        metadata = summary["vapour_batch_summary"]["metadata"]
        assert "extrapolation_notices_by_species" not in metadata
        return metadata["carrier_notices_by_species"]["Fe"][
            "diagnostic_admission_notice"
        ]

    if surface == "coating_merge":
        from simulator.coating_lifespan import (
            FoulingTerminalSnapshot,
            merge_run_snapshot,
        )

        authority = {
            "vapour_carrier_authority_by_species": {
                "Fe": {
                    "species_id": "Fe",
                    "pressure": {"kind": "value", "pa": _FE_PSEUDO_LAW_1750_PA},
                    "flux": {"kind": "eligible"},
                    "verdict_status": "status_bearing_non_authoritative",
                    "certification_ceiling": "never",
                    "validation_status": "modeled-PENDING",
                    "is_union_flux_eligible": True,
                    "is_flux_active": True,
                    "extra": deepcopy(extra),
                }
            },
            "alpha_s_provenance_by_species": {
                "Fe": {
                    "hot_wall": {
                        "segment": "hot_wall",
                        "species": "Fe",
                        "alpha_s": 0.02,
                        "citation_status": "CITED",
                        "status": "sourced",
                        "output_status": "sourced_with_surface_proxy",
                    }
                }
            },
        }
        def _export(payload_notice: dict, kg: float) -> FoulingTerminalSnapshot:
            body = deepcopy(authority)
            body["vapour_carrier_authority_by_species"]["Fe"]["extra"] = {
                "diagnostic_admission_notice": payload_notice,
            }
            return FoulingTerminalSnapshot.from_trace(
                SimpleNamespace(
                    wall_deposit_by_segment_species_kg={("hot_wall", "Fe"): kg},
                    wall_deposit_sticking_authority=body,
                )
            )

        # Two snapshots, distinct comparator gaps. A merge that keeps one
        # payload per key drops the second provenance and its gaps.
        other = deepcopy(notice)
        other["comparators"] = dict(other["comparators"])
        other["comparators"]["gap_dex_law_vs_rail"] = 2.25
        other["comparators"]["gap_dex_law_vs_vaporock"] = 3.5
        merged, _net = merge_run_snapshot(
            _export(deepcopy(notice), 0.1),
            _export(other, 0.1),
        )
        carrier = merged.wall_deposit_sticking_authority[
            "vapour_carrier_authority_by_species"
        ]["Fe"]
        assert carrier["pressure"]["pa"] == pytest.approx(
            _FE_PSEUDO_LAW_1750_PA, rel=1e-12, abs=0.0
        )
        assert "extrapolation_notice" not in carrier["extra"]
        plural = carrier["extra"]["diagnostic_admission_notice"]
        assert isinstance(plural, (list, tuple))
        assert len(plural) == 2
        from simulator.diagnostics import pressure_coating_pareto_diagnostic

        published = pressure_coating_pareto_diagnostic(
            SimpleNamespace(
                condensation_model=SimpleNamespace(
                    last_sticking_alpha_provenance_notice=(
                        merged.wall_deposit_sticking_authority
                    ),
                )
            ),
            target_species=("Fe",),
        )
        pareto_notices = published["by_species"]["Fe"][
            "diagnostic_admission_notice"
        ]
        assert len(pareto_notices) == 2
        assert sorted(
            item["comparators"]["gap_dex_law_vs_rail"] for item in pareto_notices
        ) == pytest.approx([1.1127636390496998, 2.25], rel=0, abs=1e-12)
        rail_gaps = sorted(
            item["comparators"]["gap_dex_law_vs_rail"] for item in plural
        )
        vaporock_gaps = sorted(
            item["comparators"]["gap_dex_law_vs_vaporock"] for item in plural
        )
        assert rail_gaps[0] == pytest.approx(
            1.1127636390496998, rel=0, abs=1e-12
        )
        assert rail_gaps[1] == pytest.approx(2.25, rel=0, abs=1e-12)
        assert vaporock_gaps[0] == pytest.approx(
            1.4535567803078235, rel=0, abs=1e-12
        )
        assert vaporock_gaps[1] == pytest.approx(3.5, rel=0, abs=1e-12)
        for item in plural:
            assert item["law_provenance"] == _DIAGNOSTIC_LAW_PROVENANCE
            assert item["closes_with"] == _DIAGNOSTIC_CLOSER
            assert item["authority"] == _DIAGNOSTIC_AUTHORITY
        return carrier["extra"]["diagnostic_admission_notice"]

    if surface == "typed_pressure_shadow":
        import math

        from simulator.vapour_rail.activity import (
            ActivityVerdictKind,
            SourceReactionActivity,
            StandardStateIdentity,
        )
        from tests.test_vapour_batch_request import _minimal_family, _u0_stub

        # In-band oxygen (1e-8 bar, certified band 1e-30..100) so the oxygen
        # receipt contributes no extrapolation_notice. The only carrier notice
        # is the diagnostic admission set on the evaluator.
        payload = _minimal_family("K", validation_status="pending_validation")
        catalog = compile_vapour_rail_catalog(
            payload, u0_manifest=_u0_stub("K")
        )
        evaluator = catalog.species["K"].evaluator
        assert evaluator is not None
        object.__setattr__(
            evaluator, "diagnostic_admission_notice", deepcopy(notice)
        )
        standard = StandardStateIdentity(
            convention="raoultian_pure_endmember",
            phase="liquid",
            reference_pressure_bar=1.0,
            component_basis="raoultian_pure_endmember",
        )
        activity = SourceReactionActivity(
            component_id="KO0.5",
            value=0.25,
            ln_value=math.log(0.25),
            verdict=ActivityVerdictKind.STATUS_BEARING_VALUE,
            bound_direction=None,
            reason="diagnostic-only-carrier",
            standard_state=standard,
            phase_assemblage_ref="test:liquid_melt",
            chemical_potential_ref="test:mu",
            state_fingerprint="state:test",
            solve_group_id=None,
            provider="test",
            authority=False,
            target_standard_state=standard,
        )
        result = evaluator.evaluate_typed_shadow(
            1500.0,
            activity=activity,
            pO2_bar=1.0e-8,
            expected_component_id="KO0.5",
            expected_standard_state=standard,
            expected_state_fingerprint="state:test",
        )
        assert math.isfinite(result["ln_pressure_Pa"])
        assert "extrapolation_notice" not in result
        return result["diagnostic_admission_notice"]

    raise AssertionError(f"unknown public surface {surface}")


@pytest.mark.parametrize(
    "surface",
    [
        "batch_request_answer",
        "source_provenance",
        "oxide_provenance",
        "diagnostics_summary",
        "pareto_table",
        "runner_summary",
        "coating_merge",
        "typed_pressure_shadow",
    ],
)
def test_public_surface_publishes_diagnostic_only_carrier_notice(surface: str) -> None:
    """Invariant: every public pressure surface carries every carrier notice.

    The carrier extra holds only diagnostic_admission_notice. A surface that
    selects extrapolation_notice by name drops the provenance, the comparator
    gaps, and the t-1723 closer.
    """

    _assert_published_diagnostic_notice(_published_diagnostic_notice(surface))


@pytest.mark.parametrize(
    "carrier",
    ["Fe", "FeO_association_gas", "K"],
)
@pytest.mark.parametrize("malformed", [None, "bare-token", 1, True])
def test_malformed_provenance_entry_refuses_compile(
    carrier: str,
    malformed: object,
) -> None:
    """A non-mapping provenance entry refuses the whole list.

    Invariant: every compiled pressure model is refused when any provenance
    entry is not a mapping. The branches are the direct Fe law (diagnostic
    citation plus a fitted sibling), the live FeO association (Fe's base
    evidence stays valid; the association list itself is poisoned), and an
    activity-dependent model that admission does not read. Skipping the bad
    entry would keep the citation and admit the row.
    """

    if carrier == "K":
        payload = _reaction_fixture()
        model = payload["families"]["potassium_test_family"][
            "physical_properties"
        ]["species"]["K"]["pressure_models"][0]
        model["provenance"] = [
            {"source_id": "fit-grid-source", "observation_id": "fit-grid"},
            malformed,
        ]
    else:
        payload = _fe_feo_payload()
        fe_model = payload["families"]["metals_fe_family"][
            "physical_properties"
        ]["species"]["Fe"]["pressure_models"][0]
        assert fe_model["provenance"][0]["status"] == "diagnostic_only"
        assert (
            fe_model["provenance"][0]["law_provenance"]
            == _DIAGNOSTIC_LAW_PROVENANCE
        )
        if carrier == "Fe":
            model = fe_model
            model["provenance"] = [
                *model["provenance"],
                {"source_id": "fitted-citation", "observation_id": "fit-grid"},
                malformed,
            ]
        else:
            model = payload["families"]["oxide_vapors_feo_association_family"][
                "physical_properties"
            ]["species"]["FeO_association_gas"]["pressure_models"][0]
            model["provenance"] = [*model["provenance"], malformed]
    index = len(model["provenance"]) - 1
    with pytest.raises(
        CatalogCompileError,
        match=(
            rf"{carrier}: provenance\[{index}\] is not a mapping; "
            "malformed provenance is a compile refusal"
        ),
    ):
        compile_vapour_rail_catalog(payload, emit_u0_request_rules=False)


# Oxygen multiplier the association closure evaluates. log10_pressure() takes
# temperature only: omitted exponent is 0, omitted reference is 1 bar, and
# exponent 0 stores no channel. This is not read from the compiler.
_ASSOCIATION_EXECUTED_BASE_OXYGEN = (0.0, 1.0, None)
# FeO_association_gas at 1800 K when that identity law is the base.
# data/vapor_pressures.yaml: Fe(g) + 0.5 O2(g) -> FeO(g), so the outer
# exponent is +0.5. Off the 1 bar reference the closure already baked in:
#   P(pO2) = P(1 bar) * (pO2 / 1 bar) ** 0.5
#   P(1e-9 bar) = 2629.115392552568 * 10 ** (-4.5) = 0.08313992871873806
#   P(0.01 bar) = 2629.115392552568 * 10 ** (-1) = 262.91153925525686
# P(1 bar) is the landed CEA composition of the cited Fe curve
# (12.098211309846041 Pa). These expectations are not recomputed from A, B, C.
_CITED_FEO_ASSOCIATION_PRESSURE_PA = {
    1.0: 2629.115392552568,
    1.0e-9: 0.08313992871873806,
    0.01: 262.91153925525686,
}
_CITATION_CASES = (
    ("singular", {"observation_id": "fit-grid"}, True),
    ("singular-blank", {"observation_id": ""}, False),
    ("singular-whitespace", {"observation_id": "   "}, False),
    ("singular-null", {"observation_id": None}, False),
    ("singular-bool", {"observation_id": True}, False),
    ("plural", {"observation_ids": ["fit-grid"]}, True),
    ("plural-two", {"observation_ids": ["fit-grid", "other"]}, True),
    ("plural-null", {"observation_ids": [None]}, False),
    ("plural-blank", {"observation_ids": [""]}, False),
    ("plural-whitespace", {"observation_ids": ["   "]}, False),
    ("plural-bool", {"observation_ids": True}, False),
    ("plural-string", {"observation_ids": "fit-grid"}, False),
    ("plural-empty", {"observation_ids": []}, False),
    ("plural-mixed-blank", {"observation_ids": ["fit-grid", ""]}, False),
    ("plural-mixed-null", {"observation_ids": [None, "fit-grid"]}, False),
    (
        "both-valid",
        {"observation_id": "fit-grid", "observation_ids": ["other"]},
        True,
    ),
    (
        "singular-hides-bad-plural",
        {"observation_id": "fit-grid", "observation_ids": [None]},
        False,
    ),
    ("absent", {}, False),
)


def _citation_provenance(fields: dict) -> dict:
    item = {"source_id": "fit-grid-source"}
    item.update(fields)
    return item


def _zero_activity_law_cases() -> list[dict]:
    """Every citation shape and every stored oxygen law the gate must judge.

    The oracle below is the invariant. It does not call the compiler, so a
    gate that ignores oxygen or treats any truthy observation_ids as a
    citation cannot satisfy the grid by construction.
    """

    cases: list[dict] = []
    for label, fields, admitted in _CITATION_CASES:
        cases.append(
            {
                "id": f"direct-citation-{label}",
                "kind": "direct-citation",
                "fields": fields,
                "admitted": admitted,
                "evidence_message": not admitted,
            }
        )
        cases.append(
            {
                "id": f"association-citation-{label}",
                "kind": "association-citation",
                "fields": fields,
                "admitted": admitted,
                "evidence_message": not admitted,
            }
        )
    for exponent in _OXYGEN_EXPONENTS:
        for reference in _OXYGEN_REFERENCES:
            for channel in _OXYGEN_CHANNELS:
                for target in ("base", "captured"):
                    cases.append(
                        {
                            "id": (
                                f"association-{target}"
                                f"-e={exponent!r}-r={reference!r}-c={channel!r}"
                            ),
                            "kind": "association-oxygen",
                            "target": target,
                            "exponent": exponent,
                            "reference": reference,
                            "channel": channel,
                            "own_evidence": False,
                        }
                    )
    # Own-row provenance on the association must not bypass the base law.
    for exponent, reference, channel, own_evidence in (
        (None, None, None, True),
        (1, 1, "transport_headspace", True),
    ):
        cases.append(
            {
                "id": (
                    f"association-own-evidence"
                    f"-e={exponent!r}-r={reference!r}-c={channel!r}"
                ),
                "kind": "association-oxygen",
                "target": "base",
                "exponent": exponent,
                "reference": reference,
                "channel": channel,
                "own_evidence": own_evidence,
            }
        )
    return cases


def _apply_oracle_oxygen(
    model: dict,
    *,
    exponent: object,
    reference: object,
    channel: object,
) -> None:
    if exponent is None:
        model.pop("pO2_exponent", None)
    else:
        model["pO2_exponent"] = exponent
    if reference is None:
        model.pop("pO2_reference_bar", None)
    else:
        model["pO2_reference_bar"] = reference
    if channel is None:
        model.pop("oxygen_fugacity_channel", None)
    else:
        model["oxygen_fugacity_channel"] = channel


@functools.lru_cache(maxsize=1)
def _fe_feo_payload_cached() -> tuple:
    payload = _yaml("vapor_pressures.yaml")
    return (
        payload["schema_version"],
        payload["families"]["metals_fe_family"],
        payload["families"]["oxide_vapors_feo_association_family"],
    )


def _fe_feo_payload() -> dict:
    schema_version, fe_family, feo_family = _fe_feo_payload_cached()
    return {
        "schema_version": schema_version,
        "families": {
            "metals_fe_family": deepcopy(fe_family),
            "oxide_vapors_feo_association_family": deepcopy(feo_family),
        },
    }


def _association_case_expects_admission(case: dict, payload: dict) -> bool:
    fe_model = payload["families"]["metals_fe_family"]["physical_properties"][
        "species"
    ]["Fe"]["pressure_models"][0]
    captured = payload["families"]["oxide_vapors_feo_association_family"][
        "physical_properties"
    ]["species"]["FeO_association_gas"]["pressure_models"][0][
        "base_reference_pressure_model"
    ]
    if case["kind"] == "association-citation":
        return _oracle_provenance_cites_observations(fe_model["provenance"][0])
    base_law = _oracle_stored_oxygen_law(fe_model)
    captured_law = _oracle_stored_oxygen_law(captured)
    return (
        base_law == _ASSOCIATION_EXECUTED_BASE_OXYGEN
        and captured_law == _ASSOCIATION_EXECUTED_BASE_OXYGEN
    )


def _assert_cited_executed_law(case: dict) -> None:
    """Invariant: activity exponent 0 is admitted only for a cited executed law.

    A cleaned-melt evaluator with activity exponent 0 compiles only when the
    pressure ``evaluate()`` computes is a complete evidenced law. Citations
    are a nonempty sequence of nonblank observation ids. A gas association
    executes its captured base by calling ``log10_pressure()``, which applies
    no oxygen multiplier, so the evidenced base and the captured reference
    must both be that identity law. Own-row provenance on the association
    does not replace the base law.
    """

    if case["kind"] == "direct-citation":
        payload = _effective_pressure_fit_fixture()
        row = payload["families"]["potassium_test_family"]["physical_properties"][
            "species"
        ]["K"]
        row["backsolve"] = {
            "residual_dex": 0.12,
            "provenance": {"generated_by": "fit.py", "fit_objective": "minimax"},
        }
        row["validation"]["anchor_refs"] = ["validation:fit-grid"]
        row["pressure_models"][0]["provenance"] = [
            _citation_provenance(case["fields"])
        ]
        admitted = _oracle_provenance_cites_observations(
            row["pressure_models"][0]["provenance"][0]
        )
        assert admitted is case["admitted"]
        try:
            compiled = compile_vapour_rail_catalog(payload)
        except CatalogCompileError as exc:
            assert not admitted, f"refused a cited law: {exc}"
            assert "effective-pressure reference fit requires" in str(exc)
            return
        assert admitted, "direct zero-activity melt compiled without a citation"
        evaluator = compiled.species["K"].evaluator
        assert evaluator is not None
        assert evaluator.activity_exponent == 0.0
        evaluation = evaluator.evaluate(1100.0)
        assert evaluation.pressure_pa == pytest.approx(
            _SYNTHETIC_DIAGNOSTIC_1100_PA, rel=1e-12, abs=0.0
        )
        assert getattr(evaluation, "diagnostic_admission_notice", None) is None
        assert getattr(evaluator, "diagnostic_admission_notice", None) is None
        return

    payload = _fe_feo_payload()
    fe_row = payload["families"]["metals_fe_family"]["physical_properties"][
        "species"
    ]["Fe"]
    fe_model = fe_row["pressure_models"][0]
    association_row = payload["families"]["oxide_vapors_feo_association_family"][
        "physical_properties"
    ]["species"]["FeO_association_gas"]
    association_model = association_row["pressure_models"][0]
    captured = association_model["base_reference_pressure_model"]
    if case["kind"] == "association-citation":
        fe_model["provenance"] = [_citation_provenance(case["fields"])]
    else:
        _apply_oracle_oxygen(
            fe_model if case["target"] == "base" else captured,
            exponent=case["exponent"],
            reference=case["reference"],
            channel=case["channel"],
        )
        if case["own_evidence"]:
            association_row["backsolve"] = {
                "residual_dex": 0.12,
                "provenance": {
                    "generated_by": "fit.py",
                    "fit_objective": "minimax",
                },
            }
            association_row["validation"]["anchor_refs"] = ["validation:fit-grid"]
    admitted = _association_case_expects_admission(case, payload)
    if case["kind"] == "association-oxygen":
        base_law = _oracle_stored_oxygen_law(fe_model)
        captured_law = _oracle_stored_oxygen_law(captured)
        evidence_message = base_law is not None and captured_law is not None
    else:
        evidence_message = True
    reviewer_base_bypass = (
        case["kind"] == "association-oxygen"
        and case["target"] == "base"
        and case["exponent"] == 1
        and case["reference"] == 1
        and case["channel"] == "transport_headspace"
    )
    try:
        compiled = compile_vapour_rail_catalog(
            payload, emit_u0_request_rules=False
        )
    except CatalogCompileError as exc:
        assert not admitted, f"refused the executed identity law: {exc}"
        if evidence_message and not admitted:
            assert "effective-pressure reference fit requires" in str(exc)
        if reviewer_base_bypass:
            assert "effective-pressure reference fit requires" in str(exc)
        return
    assert admitted, (
        f"{case['id']} compiled a zero-activity law evaluate() does not cite "
        "and execute"
    )
    evaluator = compiled.species["FeO_association_gas"].evaluator
    assert evaluator is not None
    assert evaluator.activity_exponent == 0.0
    if case["kind"] == "association-citation":
        assert getattr(evaluator, "diagnostic_admission_notice", None) is None
        fe_evaluator = compiled.species["Fe"].evaluator
        assert fe_evaluator is not None
        assert getattr(fe_evaluator, "diagnostic_admission_notice", None) is None
    else:
        _assert_fe_pseudo_law_disclosure(
            getattr(evaluator, "diagnostic_admission_notice", None)
        )
    for pO2_bar, expected_pa in _CITED_FEO_ASSOCIATION_PRESSURE_PA.items():
        evaluation = evaluator.evaluate(1800.0, pO2_bar=pO2_bar)
        assert evaluation.pressure_pa == pytest.approx(
            expected_pa, rel=1e-12, abs=0.0
        )
        if case["kind"] == "association-citation":
            assert getattr(evaluation, "diagnostic_admission_notice", None) is None
        else:
            _assert_fe_pseudo_law_disclosure(
                getattr(evaluation, "diagnostic_admission_notice", None)
            )


@pytest.mark.parametrize(
    "case",
    _zero_activity_law_cases(),
    ids=lambda case: case["id"],
)
def test_zero_activity_melt_admits_only_a_cited_executed_law(case: dict) -> None:
    _assert_cited_executed_law(case)


def test_policy_identity_cannot_compile_as_executable_alpha() -> None:
    payload = _reaction_fixture()
    alpha = payload["families"]["potassium_test_family"][
        "vaporisation_coefficients"
    ]["evaporation_alpha"]
    alpha["value"] = {"type": "policy", "status": "no_data"}

    with pytest.raises(CatalogCompileError, match="correlation form"):
        compile_vapour_rail_catalog(payload)


@pytest.mark.parametrize(
    ("field", "hostile_value"),
    [
        ("A", True),
        ("B", False),
        ("valid_range_K", [True, 2000.0]),
        ("valid_range_K", [300.0, False]),
        ("uncertainty_envelope", [True, 1.0]),
        ("uncertainty_envelope", [0.0, False]),
    ],
)
def test_arrhenius_alpha_rejects_nested_boolean_values(
    field,
    hostile_value,
) -> None:
    payload = _reaction_fixture()
    alpha = payload["families"]["potassium_test_family"][
        "vaporisation_coefficients"
    ]["evaporation_alpha"]
    alpha["value"] = {
        "form": "arrhenius",
        "A": 0.5,
        "B": 1000.0,
        "valid_range_K": [300.0, 2000.0],
        "uncertainty_envelope": [0.1, 0.9],
        "cite": "hostile type regression",
        "status": "CITED",
    }
    alpha["value"][field] = hostile_value

    with pytest.raises(CatalogCompileError, match="not boolean"):
        compile_vapour_rail_catalog(payload)


def test_production_schema_compiles_exact_four_strata_and_legacy_projection() -> None:
    payload = _yaml("vapor_pressures.yaml")
    assert payload["schema_version"] == 2
    assert payload["families"]
    for family in payload["families"].values():
        assert set(family) == {
            "physical_properties",
            "fiat_routing",
            "vaporisation_coefficients",
            "code_metadata",
        }

    catalog = compile_vapour_rail_catalog(payload)
    # VR-7 adds dormant acquisition families; VR-8 adds monatomic O (dormant).
    # Combined catalog is larger than either chunk alone.
    assert len(catalog.species) >= 16
    assert "O" in catalog.species
    assert catalog.species["O"].evaluator is None
    assert catalog.species["O"].code_metadata.hot_train_applicability == (
        "not_applicable"
    )
    legacy = catalog.legacy_view()
    assert len(legacy["metals"]) == 10
    # The oxide, phosphorus, and MC-4 systematic-carrier chunks intentionally
    # activate twenty carriers through the pre-RG compatibility seam.
    # 2026-08-05 MC-4 wave-1 integration: the union of wave A (Al/C/Ca/Cl/
    # Cr/Fe/H) and wave B (K/Mg/N/Na/P/S/Si) carriers projects 30 oxide-vapor
    # rows through the pre-RG compatibility seam. t-609 adds the status-bearing
    # FeO association and NiO channels without activating t-583's FeO audit row;
    # t-622 adds the diagnostic MnO/CoO channels.
    assert len(legacy["oxide_vapors"]) == 33
    # 2026-08-05 MC-4 integration: the A+B union projects 24 foulant rows
    # (wave A adds the CaCl2/Cl-family foulant carriers; wave B the chloride
    # dimers) — one more than either wave alone pinned.
    assert len(legacy["foulant_vapor"]) == 24
    # MC-4b adds ten exact CEA-composed K/Mg/Na/P/Si carriers through the
    # same oxide-vapor compatibility seam. Each is independently listed;
    # the parent ledger still performs one grouped reservoir debit.
    # MC-4b activates five previously catalog-only Stage-0 overhead carriers:
    # N2, NH3, SO2, and the K/Na salt dimers. MgCl2 remains NEEDS-BASE because
    # no runtime Stage-0 MgCl2 reservoir exists. (The B-side ==8 foulant pin
    # was superseded by the A+B union count asserted above.)
    assert set(legacy["metals"]) == {
        "Na",
        "K",
        "Mg",
        "Fe",
        "Ca",
        "Al",
        "Si",
        "Ti",
        "Cr",
        "Mn",
    }
    assert set(legacy["oxide_vapors"]) == {
        "SiO",
        "CrO2",
        "TiO",
        "TiO2_gas",
        "CaO_gas",
        "CoO_gas",
        "AlO",
        "Al2O",
        "Al2",
        "Al2O2",
        "Al2O3_gas",
        "AlO2",
        "Ca2",
        "CrO",
        "CrO3",
        "FeO_association_gas",
        "PO",
        "PO2",
        "P2",
        "P4",
        "P4O6",
        "P4O10",
        "K2",
        "K2O_gas",
        "Mg2",
        "MgO_gas",
        "MnO_gas",
        "Na2",
        "Na2O_gas",
        "NiO_gas",
        "Si2",
        "Si3",
        "SiO2_gas",
    }
    # 2026-08-05 MC-4 integration: wave A deliberately projects all Stage-0
    # overhead species (volatiles/organics included) through the foulant_vapor
    # compatibility group; the curated salt-only membership B pinned is
    # superseded by the A+B union view.
    assert set(legacy["foulant_vapor"]) == {
        "C2H5OH",
        "C2H6",
        "CH3COOH",
        "CH4",
        "CO",
        "CO2",
        "COS",
        "CS2",
        "CaCl2",
        "Cl2",
        "H2O",
        "H2S",
        "HCHO",
        "HCN",
        "HCl",
        "HNCO",
        "K2Cl2",
        "KCl",
        "N2",
        "NH3",
        "Na2Cl2",
        "NaCl",
        "NaF",
        "SO2",
    }
    # (duplicate B-side membership pin superseded by the union set above)
    assert legacy["metals"]["K"]["antoine"]["A"] == pytest.approx(10.641294)
    # Activity-corrected schema-v2 models must not leak into the legacy
    # projection that still feeds the pre-RG equilibrium-backend seam.
    assert {
        species_id: legacy["metals"][species_id]["antoine"]
        for species_id in ("Ca", "Al", "Ti", "Cr", "Mn")
    } == {
        "Ca": {"A": 11.238, "B": 9520, "C": 0},
        "Al": {"A": 11.553, "B": 17340, "C": 0},
        "Ti": {"A": 11.65, "B": 23200, "C": 0},
        "Cr": {"A": 11.42, "B": 20730, "C": 0},
        "Mn": {"A": 11.183, "B": 14740, "C": 0},
    }
    sodium = catalog.species["Na"]
    assert sodium.fiat_routing.process_or_terminal_destination == (
        "process.condensation_train"
    )
    assert sodium.vaporisation_coefficients.extrapolation_policy == (
        "conservative_slope_continuation"
    )
    assert sodium.code_metadata.formula_id == "Na"
    assert sodium.code_metadata.source_account == "process.cleaned_melt"

    bundle = load_config_bundle(DATA_DIR)
    assert bundle.vapor_pressures == legacy
    assert bundle.vapor_pressures.catalog_payload == payload


def test_production_p_carriers_share_parent_activity_and_never_sparsify() -> None:
    payload = _yaml("vapor_pressures.yaml")
    provider = BuiltinVaporPressureProvider(payload)
    request = IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(
            accounts={
                "process.cleaned_melt": {"P2O5": 1.0, "SiO2": 99.0}
            },
            species_formula_registry={},
        ),
        temperature_C=1200.0,
        pressure_bar=0.009,
        fO2_log=-9.0,
        control_inputs={
            "pO2_bar": 0.009,
            "intrinsic_fO2_log": -9.0,
            "process_phase": "stage0",
        },
    )

    result = provider.dispatch(request)
    diagnostic = result.diagnostic or {}
    carriers = {"PO", "PO2", "P2", "P4", "P4O6", "P4O10"}
    pressures = diagnostic["vapor_pressures_Pa"]
    activities = diagnostic["activities"]
    assert carriers <= set(pressures)
    assert carriers <= set(activities)

    catalog = compile_vapour_rail_catalog(
        payload, emit_u0_request_rules=False
    )
    intrinsic_fO2_bar = diagnostic["source_reaction_fO2_bar"]
    for species in carriers:
        evaluator = catalog.evaluator_for(species)
        expected = evaluator.evaluate(
            1473.15,
            source_activity=activities[species],
            pO2_bar=intrinsic_fO2_bar,
        ).pressure_pa
        assert pressures[species] == pytest.approx(expected, rel=1.0e-12)
        provenance = diagnostic["vapor_pressure_numerator_provenance"][species]
        assert provenance["equivalent_parent_oxide_activity"] == pytest.approx(
            activities[species]
        )
        assert provenance["melt_oxide_activity_authority_status"] == (
            "out_of_gamma_domain_status_bearing_non_authoritative"
        )


@pytest.mark.parametrize(
    "mutation,match",
    [
        ("malformed_antoine", "compatibility_antoine.A"),
        ("missing_antoine_key", "must contain exactly A, B, C"),
        ("extra_antoine_key", "must contain exactly A, B, C"),
        ("nonfinite_antoine", "compatibility_antoine.B must be a finite number"),
        ("boolean_antoine", "compatibility_antoine.A must be a finite number"),
        ("reversed_range", "compatibility_valid_range_K must be increasing"),
        ("malformed_range_shape", r"must be \[low, high\]"),
        ("nonfinite_range", r"compatibility_valid_range_K\[1\] must be finite"),
        ("boolean_range", "compatibility_valid_range_K.*must be numeric, not boolean"),
    ],
)
def test_legacy_compatibility_projection_rejects_malformed_flux_inputs(
    mutation: str,
    match: str,
) -> None:
    payload = _yaml("vapor_pressures.yaml")
    ca_model = payload["families"]["metals_ca_family"]["physical_properties"][
        "species"
    ]["Ca"]["pressure_models"][0]
    if mutation == "malformed_antoine":
        ca_model["compatibility_antoine"]["A"] = "bogus"
    elif mutation == "missing_antoine_key":
        ca_model["compatibility_antoine"].pop("B")
    elif mutation == "extra_antoine_key":
        ca_model["compatibility_antoine"]["D"] = 0.0
    elif mutation == "nonfinite_antoine":
        ca_model["compatibility_antoine"]["B"] = math.nan
    elif mutation == "boolean_antoine":
        ca_model["compatibility_antoine"]["A"] = True
    elif mutation == "reversed_range":
        ca_model["compatibility_valid_range_K"] = [2000.0, 1000.0]
    elif mutation == "malformed_range_shape":
        ca_model["compatibility_valid_range_K"] = [1000.0]
    elif mutation == "nonfinite_range":
        ca_model["compatibility_valid_range_K"] = [1000.0, math.inf]
    else:
        ca_model["compatibility_valid_range_K"] = [True, 1000.0]

    with pytest.raises(CatalogCompileError, match=match):
        compile_vapour_rail_catalog(payload, emit_u0_request_rules=False)


def test_nonfinite_named_field_yields_field_specific_not_digest_message() -> None:
    """Ordering regression: schema field message must beat digest non-finite guard.

    t-517's compile-input digest refuses NaN/Inf with a generic message. That
    guard must not fire *before* field validation, or operators lose which
    named coefficient/range entry is bad. Both paths stay fail-closed; this
    asserts the diagnostic that wins is the field-specific one.
    """

    payload = _yaml("vapor_pressures.yaml")
    ca_model = payload["families"]["metals_ca_family"]["physical_properties"][
        "species"
    ]["Ca"]["pressure_models"][0]
    ca_model["compatibility_antoine"]["B"] = math.nan

    with pytest.raises(
        CatalogCompileError,
        match=r"compatibility_antoine\.B must be a finite number",
    ) as caught:
        compile_vapour_rail_catalog(payload, emit_u0_request_rules=False)
    assert "compile-input digest requires finite floats" not in str(caught.value)


def test_pure_component_phase_requires_explicit_non_melt_identity() -> None:
    payload = _yaml("vapor_pressures.yaml")
    nacl_model = payload["families"]["foulant_vapor_nacl_family"][
        "physical_properties"
    ]["species"]["NaCl"]["pressure_models"][0]
    nacl_model.pop("activity_semantics")
    with pytest.raises(CatalogCompileError, match="pure-component saturation requires"):
        compile_vapour_rail_catalog(payload, emit_u0_request_rules=False)

    payload = _yaml("vapor_pressures.yaml")
    nacl_family = payload["families"]["foulant_vapor_nacl_family"]
    nacl_family["code_metadata"]["source_account"] = "process.cleaned_melt"
    identity = nacl_family["physical_properties"]["species"]["NaCl"][
        "pressure_models"
    ][0]["pure_condensed_phase_identity"]
    identity["source_account"] = "process.cleaned_melt"
    with pytest.raises(CatalogCompileError, match="dedicated non-melt"):
        compile_vapour_rail_catalog(payload, emit_u0_request_rules=False)


@pytest.mark.parametrize(
    "mutation,match",
    [
        ("missing_identity", "pure_condensed_phase_identity must be a mapping"),
        ("wrong_component", "does not match species formula"),
        ("gas_phase", "phase must be 'condensed_solid' or 'condensed_liquid'"),
        ("wrong_account", "does not match code_metadata"),
    ],
)
def test_pure_component_phase_identity_is_independently_enforced(
    mutation: str,
    match: str,
) -> None:
    payload = _yaml("vapor_pressures.yaml")
    nacl_model = payload["families"]["foulant_vapor_nacl_family"][
        "physical_properties"
    ]["species"]["NaCl"]["pressure_models"][0]
    identity = nacl_model["pure_condensed_phase_identity"]
    if mutation == "missing_identity":
        nacl_model.pop("pure_condensed_phase_identity")
    elif mutation == "wrong_component":
        identity["component_id"] = "KCl"
    elif mutation == "gas_phase":
        identity["phase"] = "gas"
    else:
        identity["source_account"] = "process.other_foulant"

    with pytest.raises(CatalogCompileError, match=match):
        compile_vapour_rail_catalog(payload, emit_u0_request_rules=False)


def test_species_catalog_closes_collision_gases_and_carrier_only_rows() -> None:
    payload = _yaml("species_catalog.yaml")
    validate_species_catalog(payload)
    by_id = {row["id"]: row for row in payload["species"]}

    collision_rows = {
        species_id for species_id, row in by_id.items() if row.get("catalog_role") == "gas"
    }
    assert collision_rows == COLLISION_GASES
    for species_id in COLLISION_GASES:
        row = by_id[species_id]
        expected_formula = (
            "FeO"
            if species_id == "FeO_association_gas"
            else species_id.removesuffix("_gas")
        )
        assert row["formula"] == expected_formula
        assert row["atoms"]
        assert row["phase"] == "gas"
        assert row["validation"]["status"] == "pending_validation"
        assert row["pressure_observable"] == "equilibrium_partial_pressure"
        assert row["valid_domain"]
        assert row["extrapolation_policy"] == "conservative_slope_continuation"
        assert row["out_of_range_status"] == OUT_OF_RANGE_STATUS
        assert row["acquisition_flag"].endswith(species_id)
        assert row["code_metadata"]["formula_id"] == row["formula"]
        assert row["code_metadata"]["collision_only_suffix"] is (
            species_id not in ACTIVE_COLLISION_GASES
        )

    carrier_rows = {
        species_id
        for species_id, row in by_id.items()
        if row.get("catalog_role") == "carrier_only"
    }
    assert carrier_rows == CARRIER_ONLY
    for species_id in CARRIER_ONLY:
        row = by_id[species_id]
        assert row["formula"] is None
        assert row["pressure_models"] == []
        assert row["direct_vapour_flux"] is False
        assert row["requires_balanced_decomposition"] is True


def test_collision_gases_do_not_enter_legacy_volatile_runtime_registry() -> None:
    formulas, aliases, catalog_specs, formula_texts = _volatile_runtime_catalog()

    inactive_collision_gases = COLLISION_GASES - ACTIVE_COLLISION_GASES
    assert inactive_collision_gases.isdisjoint(formulas)
    assert inactive_collision_gases.isdisjoint(aliases)
    assert inactive_collision_gases.isdisjoint(catalog_specs)
    assert inactive_collision_gases.isdisjoint(formula_texts)
    # Once a collision-safe gas row becomes a direct carrier its suffixed id,
    # formula alias, atom spec, and formula text must all enter together; ledger
    # code cannot infer TiO2(g) from condensed TiO2 by string collision.
    for species_id in ACTIVE_COLLISION_GASES:
        assert species_id in formulas
        assert species_id in aliases
        assert species_id in catalog_specs
        assert species_id in formula_texts


def test_b1_standard_reaction_without_antoine_uses_shared_evaluator() -> None:
    payload = _reaction_fixture()
    serialized = yaml.safe_dump(payload)
    assert "antoine" not in serialized

    catalog = compile_vapour_rail_catalog(payload)
    evaporation_evaluator = catalog.evaluator_for_hot_train("K")
    condensation_evaluator = catalog.evaluator_for_hot_train("K")
    assert evaporation_evaluator is condensation_evaluator

    evaporation = evaporation_evaluator.evaluate(
        1100.0,
        source_activity=0.25,
        pO2_bar=1.0e-4,
    )
    condensation = condensation_evaluator.evaluate(
        1100.0,
        source_activity=0.25,
        pO2_bar=1.0e-4,
    )
    assert evaporation.pressure_pa == pytest.approx(condensation.pressure_pa)
    assert evaporation.pressure_pa > 0.0

    # Direct evaluator callers cannot bypass the typed activity/fO2 path by
    # inheriting unity activity or the oxygen reference pressure.
    with pytest.raises(CatalogCompileError, match="explicit source_activity"):
        condensation_evaluator.evaluate(1100.0)
    with pytest.raises(CatalogCompileError, match="explicit source_activity"):
        condensation_evaluator.evaluate(1100.0, pO2_bar=1.0e-4)
    with pytest.raises(CatalogCompileError, match="explicit intrinsic_melt"):
        condensation_evaluator.evaluate(1100.0, source_activity=0.25)


def test_activity_dependent_model_requires_explicit_activity_declaration() -> None:
    payload = _reaction_fixture()
    reaction = payload["families"]["potassium_test_family"][
        "physical_properties"
    ]["species"]["K"]["source_reactions"][0]
    reaction.pop("activity_input")

    with pytest.raises(CatalogCompileError, match="silent a=1 is forbidden"):
        compile_vapour_rail_catalog(payload, emit_u0_request_rules=False)


@pytest.mark.parametrize(
    "mutation,match",
    [
        ("missing_exponent", "implicit a=1 is forbidden"),
        ("missing_semantics", "activity_semantics"),
        ("unknown_model", "unsupported by catalog channel evaluation"),
        ("wrong_component", "is not a selected-reaction reactant"),
    ],
)
def test_activity_contract_identity_mutations_fail_compile(
    mutation: str, match: str
) -> None:
    payload = _reaction_fixture()
    species = payload["families"]["potassium_test_family"][
        "physical_properties"
    ]["species"]["K"]
    model = species["pressure_models"][0]
    activity_input = species["source_reactions"][0]["activity_input"]
    if mutation == "missing_exponent":
        model.pop("activity_exponent")
    elif mutation == "missing_semantics":
        model.pop("activity_semantics")
    elif mutation == "unknown_model":
        activity_input["activity_model"] = "nonsense_unvalidated"
    else:
        activity_input["component_id"] = "DefinitelyNotAReactant"

    with pytest.raises(CatalogCompileError, match=match):
        compile_vapour_rail_catalog(payload, emit_u0_request_rules=False)


def test_b1_oxide_row_requires_activity_for_condensation_without_antoine() -> None:
    payload = _reaction_fixture()
    family = payload["families"]["potassium_test_family"]
    family["code_metadata"]["compatibility_projection"] = "oxide_vapors"
    provider = BuiltinVaporPressureProvider(payload)
    request = IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(
            accounts={"process.cleaned_melt": {"K2O": 1.0}},
            species_formula_registry={},
        ),
        temperature_C=826.85,
        pressure_bar=1.0e-6,
        fO2_log=-4.0,
        control_inputs={"pO2_bar": 1.0e-4, "intrinsic_fO2_log": -4.0},
    )

    result = provider.dispatch(request)
    diagnostic = result.diagnostic or {}
    assert diagnostic["vapor_pressures_Pa"]["K"] > 0.0
    assert diagnostic["vapor_pressure_numerator_provenance"]["K"][
        "P_reference_model_Pa"
    ] > 0.0
    assert _species_has_antoine_data("K", vapor_pressure_data=payload)
    pressure_pa, refused = _try_antoine_psat_pa(
        "K", 1100.0, vapor_pressure_data=payload
    )
    assert refused is True
    assert pressure_pa is None

    model = CondensationModel(
        CondensationTrain.create_default(),
        vapor_pressure_data=payload,
        wall_temperature_C=400.0,
    )
    model.configure_operating_conditions(
        overhead_pressure_mbar=10.0,
        pipe_diameter_m=0.12,
        carrier_gas="O2",
        species_partial_pressures_mbar={"K": 1.0},
        stage_area_m2_by_stage={
            str(stage.stage_number): 1.0 for stage in model.train.stages
        },
    )
    melt = MeltState()
    melt.temperature_C = 826.85
    route = model.route(
        EvaporationFlux(species_kg_hr={"K": 1.0}, total_kg_hr=1.0),
        melt,
    )
    # A source-reaction pressure is not a wall saturation curve. Refuse that
    # capture quantity while preserving the independently evaluated source.
    k_refusal = route.condensation_refusals_by_species.get("K")
    assert k_refusal is not None
    assert k_refusal.get("status") == "refused"
    authority = route.condensation_authority_by_species["K"]
    assert authority["status"] == "missing"
    assert authority["authoritative_for_condensation"] is False
    assert k_refusal["upstream_authority_status"] == "missing"
    assert authority["stage_condensed_mass_kg_hr"] == 0.0
    assert any(item["eta"] is None and item["refusal_type"] == "WallSaturationPressureRefusal"
               for item in k_refusal["stage_outcomes"] if item["status"] == "refused")
    assert "K" not in route.wall_deposit_by_species
    assert route.remaining_by_species["K"] == 1.0
    assert not route.retained_in_source_by_species
    assert route.remaining_by_species["K"] + authority[
        "stage_condensed_mass_kg_hr"
    ] == pytest.approx(1.0)


def test_metals_projection_reference_evaluation_declares_neutral_inputs() -> None:
    payload = _reaction_fixture()
    provider = BuiltinVaporPressureProvider(payload)
    request = IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(
            accounts={"process.cleaned_melt": {"K2O": 1.0}},
            species_formula_registry={},
        ),
        temperature_C=826.85,
        pressure_bar=1.0e-6,
        fO2_log=-4.0,
        control_inputs={"pO2_bar": 1.0e-4, "intrinsic_fO2_log": -4.0},
    )

    result = provider.dispatch(request)
    diagnostic = result.diagnostic or {}
    assert diagnostic["vapor_pressures_Pa"]["K"] > 0.0


def test_config_bundle_retains_compiler_capability_for_b1(
    tmp_path: Path,
) -> None:
    path = tmp_path / "vapor_pressures.yaml"
    path.write_text(yaml.safe_dump(_reaction_fixture()))
    bundle = load_config_bundle(DATA_DIR, vapor_pressures_path=path)

    catalog_payload = bundle.vapor_pressures.catalog_payload
    assert catalog_payload["schema_version"] == 2
    assert _species_has_antoine_data(
        "K", vapor_pressure_data=catalog_payload
    )
    pressure_pa, refused = _try_antoine_psat_pa(
        "K", 1100.0, vapor_pressure_data=catalog_payload
    )
    assert refused is True
    assert pressure_pa is None

    sim = PyrolysisSimulator(
        InternalAnalyticalBackend(),
        bundle.setpoints,
        bundle.feedstocks,
        bundle.vapor_pressures,
        materials=bundle.materials,
    )
    assert sim.vapour_rail_catalog is not None
    assert sim.vapour_rail_catalog.evaluator_for_hot_train(
        "K"
    ) is sim.vapour_rail_catalog.evaluator_for_hot_train("K")


def test_anti_cliff_continuation_is_status_bearing_physical_reciprocal_T() -> None:
    """Out-of-domain continues with a physical 1/T (van 't Hoff) estimate.

    Rationale (t-538 / b-145 class): the prior attenuated linear-T slope
    invented multi-dex low-T pressure. Anti-cliff intent is preserved
    (nonzero, continuous from the domain edge, status-bearing OOR mark);
    the *value* is the family-keyed physical continuation — for this
    tabulated_equilibrium reference, the linear-in-1/T chord through the
    two edge cells.
    """
    evaluator = compile_vapour_rail_catalog(_reaction_fixture()).evaluator_for("K")
    endpoint = evaluator.evaluate(
        1200.0, source_activity=1.0, pO2_bar=1.0
    ).pressure_pa
    beyond = evaluator.evaluate(1300.0, source_activity=1.0, pO2_bar=1.0)
    below = evaluator.evaluate(900.0, source_activity=1.0, pO2_bar=1.0)

    assert beyond.out_of_range is True
    assert beyond.status == OUT_OF_RANGE_STATUS
    assert beyond.acquisition_flag == "acquire:test:K"
    # Anti-cliff: never silent zero; continuous at the edge.
    assert beyond.pressure_pa > 0.0
    assert endpoint > 0.0
    assert below.pressure_pa > 0.0
    assert below.out_of_range is True

    # Premise: tabulated edge cells (1000 K, 1 Pa) and (1200 K, 100 Pa)
    # define a unique linear-in-1/T line. Algebra:
    #   log10 P(T) = log10 P0 + (log10 P1 − log10 P0)
    #                · (1/T − 1/T0) / (1/T1 − 1/T0)
    # Unit Pa. Sanity: at T=T0/T1 the line recovers the cell pressures;
    # at T=1300 K (above) and T=900 K (below) the OOR path matches.
    t0, p0 = 1000.0, 1.0
    t1, p1 = 1200.0, 100.0
    log0, log1 = math.log10(p0), math.log10(p1)
    inv0, inv1 = 1.0 / t0, 1.0 / t1

    def expected_log10(T: float) -> float:
        return log0 + (log1 - log0) * ((1.0 / T) - inv0) / (inv1 - inv0)

    # Outer activity/pO2 factors are unity at a=1, pO2=1 bar with the
    # fixture's exponents, so pressure_pa is the reference continuation.
    assert math.log10(beyond.pressure_pa) == pytest.approx(
        expected_log10(1300.0), rel=0.0, abs=1.0e-12
    )
    assert math.log10(below.pressure_pa) == pytest.approx(
        expected_log10(900.0), rel=0.0, abs=1.0e-12
    )
    # Edge identity: 1/T continuation at T=Tb recovers the boundary value
    # (anti-cliff; no jump). Finite-diff approach the high edge from above.
    just_above = evaluator.evaluate(
        1200.0 + 1.0e-6, source_activity=1.0, pO2_bar=1.0
    )
    assert just_above.out_of_range is True
    assert math.log10(just_above.pressure_pa) == pytest.approx(
        math.log10(endpoint), abs=1.0e-6
    )


def test_sio_typed_evaluator_applies_oxygen_mass_action_once() -> None:
    evaluator = compile_vapour_rail_catalog(
        _yaml("vapor_pressures.yaml")
    ).evaluator_for("SiO")
    at_reference = evaluator.evaluate(
        1800.0, source_activity=1.0, pO2_bar=1.0e-9
    ).pressure_pa
    oxygen_rich = evaluator.evaluate(
        1800.0, source_activity=1.0, pO2_bar=1.0e-6
    ).pressure_pa

    assert evaluator.pO2_exponent == pytest.approx(-0.5)
    assert oxygen_rich / at_reference == pytest.approx(
        math.sqrt(1.0e-9 / 1.0e-6)
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "extra_stratum",
        "unbalanced",
        "no_reference",
        "missing_routing_field",
        "missing_code_field",
    ],
)
def test_compiler_rejects_ambiguous_or_invalid_schema(mutation: str) -> None:
    payload = deepcopy(_reaction_fixture())
    family = payload["families"]["potassium_test_family"]
    row = family["physical_properties"]["species"]["K"]
    model = row["pressure_models"][0]
    if mutation == "extra_stratum":
        family["runtime"] = {}
    elif mutation == "unbalanced":
        row["source_reactions"][0]["products"][1]["stoichiometry"] = 0.5
    elif mutation == "no_reference":
        model.pop("reference_pressure_model")
    elif mutation == "missing_routing_field":
        family["fiat_routing"].pop("process_or_terminal_destination")
    else:
        family["code_metadata"].pop("source_account")

    with pytest.raises(CatalogCompileError):
        compile_vapour_rail_catalog(payload)


def test_catalog_operating_envelope_no_nonphysical_pressure() -> None:
    """b-148 regression: no hot-train in-domain row yields P > 1e9 Pa.

    Sweep every *hot-train* compiled evaluator over a physical process
    envelope (T band × melt pO2 band × activity). Stage-0-only carriers
    (P-ladder, …) are excluded: they are gated off the hot train and
    their large negative pO2 powers at unit activity are a separate
    Stage-0 envelope question. A failure here means a stoichiometry /
    sign / units defect or an unbounded oxygen mass-action path — the
    class that produced AlO2 ~1e63 Pa in CI full-run dumps.
    """
    from simulator.physical_constants import (
        CATALOG_PHYSICAL_PRESSURE_CEILING_PA,
        MELT_DISSOCIATION_PO2_MAX_BAR,
    )

    catalog = compile_vapour_rail_catalog(
        _yaml("vapor_pressures.yaml"), emit_u0_request_rules=False
    )
    temperatures_K = (1400.0, 1600.0, 1800.0, 2000.0, 2200.0)
    # Process-representative melt pO2 band (inside the physical envelope).
    pO2_bars = (1.0e-12, 1.0e-9, 1.0e-6, 1.0e-3, 1.0, 10.0)
    activities = (1.0e-4, 0.01, 0.1, 1.0)
    ceiling = CATALOG_PHYSICAL_PRESSURE_CEILING_PA
    offenders: list[str] = []
    assert MELT_DISSOCIATION_PO2_MAX_BAR >= 10.0

    for species_id, species in catalog.species.items():
        evaluator = species.evaluator
        if evaluator is None:
            continue
        hot = str(
            getattr(species.code_metadata, "hot_train_applicability", "") or ""
        )
        if hot in {"stage0_only", "not_applicable"}:
            continue
        for temperature_K in temperatures_K:
            for pO2_bar in pO2_bars:
                for activity in activities:
                    kwargs: dict[str, float] = {}
                    if evaluator.activity_exponent:
                        kwargs["source_activity"] = activity
                    if evaluator.pO2_exponent:
                        kwargs["pO2_bar"] = pO2_bar
                    try:
                        evaluation = evaluator.evaluate(temperature_K, **kwargs)
                    except CatalogCompileError:
                        continue
                    # In-domain only: OOR continuation may be multi-dex and is
                    # already status-bearing (t-538 / b-145). The b-148 class
                    # is non-physical pressure *claiming* a usable value.
                    if evaluation.out_of_range:
                        continue
                    if evaluation.pressure_pa > ceiling:
                        offenders.append(
                            f"{species_id}: T={temperature_K:g} K "
                            f"pO2={pO2_bar:g} bar a={activity:g} "
                            f"P={evaluation.pressure_pa:.3e} Pa "
                            f"pO2_exp={evaluator.pO2_exponent}"
                        )

    assert not offenders, (
        "catalog operating-envelope physical ceiling exceeded "
        f"(>{ceiling:g} Pa in-domain):\n" + "\n".join(offenders[:40])
    )


def test_alo2_extreme_oxygen_is_predicted_and_flagged() -> None:
    """Finite oxygen remains the input even far outside the evaluator band."""

    catalog = compile_vapour_rail_catalog(
        _yaml("vapor_pressures.yaml"), emit_u0_request_rules=False
    )
    evaluator = catalog.evaluator_for("AlO2")
    assert evaluator.pO2_exponent == pytest.approx(0.25)
    assert evaluator.reference_model.physical_composite_ood is True

    # Unit-reference sanity: physical composite at a=1, pO2_ref=1 bar.
    unit = evaluator.evaluate(2000.0, source_activity=1.0, pO2_bar=1.0)
    assert unit.pressure_pa < 1.0e-3
    assert unit.pressure_pa > 0.0

    # Al2O3(l) + 1/2 O2(g) -> 2 AlO2(g) gives P_AlO2 proportional to
    # pO2**(1/4) at fixed T/activity. From 100 to 1e300 bar, the dimensionless
    # oxygen ratio is 1e298, hence 298/4 = 74.5 pressure decades. The same
    # expression continues through the band edge; the notice limits evidence.
    exploded = evaluator.evaluate(
        1800.0 + 273.15, source_activity=0.1, pO2_bar=1.0e300
    )
    capped = evaluator.evaluate(
        1800.0 + 273.15, source_activity=0.1, pO2_bar=100.0
    )
    assert math.isfinite(exploded.pressure_pa)
    assert math.log10(exploded.pressure_pa / capped.pressure_pa) == pytest.approx(74.5)
    notice = exploded.extrapolation_notice
    assert notice["reason"] == "pO2_outside_evaluator_band"
    assert notice["pO2_bar_used"] == 1e300
    assert notice["certified_band"]["pO2_bar"] == (1e-30, 100.0)
    assert notice["authority_level"] == "extrapolated"
    assert notice["was_clamped"] is False
    assert capped.extrapolation_notice is None


def test_physical_melt_dissociation_pO2_bar_clamps_sentinel() -> None:
    from engines.builtin.vapor_pressure import physical_melt_dissociation_pO2_bar
    from simulator.physical_constants import (
        MELT_DISSOCIATION_PO2_MAX_BAR,
        MELT_DISSOCIATION_PO2_MIN_BAR,
    )

    p, clamped = physical_melt_dissociation_pO2_bar(300.0)
    assert clamped is True
    assert p == MELT_DISSOCIATION_PO2_MAX_BAR

    p, clamped = physical_melt_dissociation_pO2_bar(-9.0)
    assert clamped is False
    assert p == pytest.approx(1.0e-9)

    p, clamped = physical_melt_dissociation_pO2_bar(-400.0)
    assert clamped is True
    assert p == MELT_DISSOCIATION_PO2_MIN_BAR


def test_physical_melt_dissociation_pO2_bar_inf_uses_sign_aware_envelope() -> None:
    """SC-170 C1(a): ±inf are envelope outliers; the log's sign chooses the edge.

    The sign-blind `return MAX_BAR` at the isfinite gate preempted the
    sign-aware form ten lines below, mapping -inf (infinitely reducing)
    onto the most oxidizing edge. The file already wrote the correct
    expression; this test pins that it is reachable.
    """
    from engines.builtin.vapor_pressure import physical_melt_dissociation_pO2_bar
    from simulator.physical_constants import (
        MELT_DISSOCIATION_PO2_MAX_BAR,
        MELT_DISSOCIATION_PO2_MIN_BAR,
    )

    p, clamped = physical_melt_dissociation_pO2_bar(float("-inf"))
    assert clamped is True
    assert p == MELT_DISSOCIATION_PO2_MIN_BAR

    p, clamped = physical_melt_dissociation_pO2_bar(float("inf"))
    assert clamped is True
    assert p == MELT_DISSOCIATION_PO2_MAX_BAR


def test_physical_melt_dissociation_pO2_bar_refuses_nan_before_envelope() -> None:
    """SC-170 C1(b): nan is missing input and must refuse, not pick an edge.

    nan < 0 is False, so the sign-aware inf mapping would still send nan
    to MAX_BAR. That is a doctrine call: missing input is not an envelope
    outlier. Fe-free melts never reach the Kress91 raw-controls guard.
    """
    from engines.builtin.vapor_pressure import (
        VaporPressureComputationError,
        physical_melt_dissociation_pO2_bar,
    )

    with pytest.raises(VaporPressureComputationError, match="finite"):
        physical_melt_dissociation_pO2_bar(float("nan"))

    provider = BuiltinVaporPressureProvider(_yaml("vapor_pressures.yaml"))
    request = IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(
            accounts={
                "process.cleaned_melt": {
                    "SiO2": 1.0,
                    "Na2O": 0.05,
                    "MgO": 0.1,
                }
            },
            species_formula_registry={},
        ),
        temperature_C=1700.0,
        pressure_bar=1e-9,
        control_inputs={
            "pO2_bar": 1e-9,
            "intrinsic_fO2_log": float("nan"),
        },
    )
    with pytest.raises(VaporPressureComputationError, match="finite"):
        provider.dispatch(request)


def test_na_composites_base_matches_lh_monatomic_and_pins() -> None:
    """b-151: Na2/Na2O_gas must track monatomic L&H Pref (not retired pseudo).

    Premise: composite base_reference_pressure_model is the monatomic
    unit-activity Pref; activity is applied once at runtime. The retired
    VapoRock pseudo_psat (A=5.18586 / B=11127.434869 / C=1000) was an
    activity-folded effective pressure — restoring it reopens dual-attribution.
    Algebra: r_v ∝ r_base^ν_Na; unit-activity probes pin the post-fix surface.
    Unit Pa. Sanity: base coeffs byte-match monatomic Na; pin file points exist.
    """
    catalog = compile_vapour_rail_catalog(_yaml("vapor_pressures.yaml"))
    pins = _yaml("vapour_rail_validation_pins.yaml")
    na_ref = catalog.species["Na"].evaluator.reference_model.coefficients
    retired = {"A": 5.18586, "B": 11127.434869, "C": 1000.0}
    lh = {"A": 11.342243, "B": 12140.316409, "C": -163.701}

    assert na_ref["A"] == pytest.approx(lh["A"])
    assert na_ref["B"] == pytest.approx(lh["B"])
    assert na_ref["C"] == pytest.approx(lh["C"])

    for species_id in ("Na2", "Na2O_gas"):
        # Source YAML: composite base_reference must equal monatomic L&H Pref.
        row = None
        for family in _yaml("vapor_pressures.yaml")["families"].values():
            spp = (family.get("physical_properties") or {}).get("species") or {}
            if species_id in spp:
                row = spp[species_id]
                break
        assert row is not None
        model = row["pressure_models"][0]
        base = model["base_reference_pressure_model"]["coefficients"]
        assert base["A"] == pytest.approx(lh["A"])
        assert base["B"] == pytest.approx(lh["B"])
        assert base["C"] == pytest.approx(lh["C"])
        assert base["A"] != pytest.approx(retired["A"])

        probe = pins["species"][species_id]["validations"]["structural"][
            "unit_activity_probe"
        ]
        assert probe["activity"] == pytest.approx(1.0)
        assert probe["pO2_bar"] == pytest.approx(1.0)
        for point in probe["points"]:
            live = catalog.species[species_id].evaluator.evaluate(
                float(point["T_K"]),
                source_activity=1.0,
                pO2_bar=1.0,
            ).pressure_pa
            # Relative tolerance absorbs float/path noise; pins are honest
            # current-state values from the b-151 catalog probe, not tuned.
            assert live == pytest.approx(
                float(point["pinned_pressure_Pa"]), rel=1.0e-6, abs=0.0
            )


def test_nested_scalar_alpha_citation_cannot_bypass_vaporock_denial() -> None:
    payload = _yaml("vapor_pressures.yaml")
    alpha = payload["families"]["metals_na_family"]["vaporisation_coefficients"][
        "evaporation_alpha"
    ]
    alpha["value"] = {
        "form": "scalar",
        "value": 0.5,
        "citation": "VapoRock fitted alpha",
    }

    with pytest.raises(
        CatalogCompileError,
        match=r"evaporation_alpha\.value\.citation:.*VapoRock",
    ):
        compile_vapour_rail_catalog(payload, emit_u0_request_rules=False)


@pytest.mark.parametrize(
    "pO2_bar",
    [math.nan, math.inf, 0.0, -1.0, "bad"],
)
def test_degenerate_pO2_evaluate_raises_catalog_compile_error(pO2_bar) -> None:
    evaluator = compile_vapour_rail_catalog(
        _yaml("vapor_pressures.yaml"), emit_u0_request_rules=False
    ).evaluator_for("AlO2")

    with pytest.raises(CatalogCompileError):
        evaluator.evaluate(1800.0, source_activity=1.0, pO2_bar=pO2_bar)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("activity_exponent", "bad"),
        ("pO2_exponent", []),
        ("reference_pressure_Pa", "bad"),
    ],
)
def test_malformed_catalog_numerics_raise_catalog_compile_error(
    field, value
) -> None:
    payload = _yaml("vapor_pressures.yaml")
    payload["families"]["oxide_vapors_alo2_family"]["physical_properties"][
        "species"
    ]["AlO2"]["pressure_models"][0][field] = value

    with pytest.raises(CatalogCompileError, match=field):
        compile_vapour_rail_catalog(payload, emit_u0_request_rules=False)


@pytest.mark.parametrize(
    "anchor_refs",
    [[None], [""], ["   "], [1], [None, "kems-001"]],
)
def test_validated_row_rejects_junk_anchor_refs(anchor_refs) -> None:
    payload = _yaml("vapor_pressures.yaml")
    payload["families"]["metals_na_family"]["physical_properties"]["species"][
        "Na"
    ]["validation"] = {
        "status": "validated",
        "anchor_refs": list(anchor_refs),
    }

    with pytest.raises(
        CatalogCompileError,
        match=r"validation\.anchor_refs",
    ):
        compile_vapour_rail_catalog(payload, emit_u0_request_rules=False)
