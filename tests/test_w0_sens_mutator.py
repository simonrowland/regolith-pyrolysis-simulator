"""W0-W-MUTATOR-v1 focused tests — synthetic engine trees only.

Every test drives a fabricated engine source tree with invented parameter
names and values, an injected fake builder (no compiler), and an injected
fake worker runner (no subprocess, no thermoengine). No test touches a
live engine, a real parameter name or value, or a held W.
"""

from __future__ import annotations

import ast
import hashlib
import json
import math
from pathlib import Path

import pytest

from benchmarks.w0_sens import w_mutator
from benchmarks.w0_sens import _custodian_worker as worker
from benchmarks.w0_sens.w_mutator import (
    CONTROL_J,
    FROZEN_PERTURBATIONS_J,
    MINUS_J,
    PLUS_J,
    AbortWMutator,
    CustodianBoundaryError,
    MutationBuild,
    NonProductionMutationBuild,
    W0WMutator,
    comment_form,
    patch_initializer,
    synthetic_response_gate,
)

SYNTH_PARAM = "W(SYNTH-A   ,SYNTH-B)"
SYNTH_OTHER = "W(SYNTH-A   ,SYNTH-C)"
SYNTH_THIRD = "W(SYNTH-B   ,SYNTH-C)"

FAKE_SOURCE = (
    "/* synthetic stand-in for src/LiquidMelts.m */\n"
    "static double referenceValuesOfModelParameters[] = {\n"
    "\t 111.0,  //  0 W(SYNTH-A   ,SYNTH-B   )\n"
    "\t-222.5,  //  1 W(SYNTH-A   ,SYNTH-C   )\n"
    "\t 333.25,  //  2 W(SYNTH-B   ,SYNTH-C   )\n"
    "};\n"
)


def _fake_slots(source_text: str) -> list[tuple[int, str, float]]:
    slots = []
    for line in source_text.splitlines():
        match = w_mutator._INITIALIZER_RE.match(line)
        if match is not None:
            slots.append(
                (int(match.group("idx")), match.group("name"), float(match.group("num")))
            )
    return sorted(slots)


def _fake_values_from_lib(prefix_lib: Path) -> list[tuple[str, float]]:
    # The fake builder writes the (patched) source text as the dylib bytes,
    # so the "build" at a prefix is read back by parsing that text. Comment
    # names are mapped to the runtime form (first component padded, second
    # bare) so the fake enforces the same byte-exact name contract the real
    # worker gets from the live engine.
    text = (Path(prefix_lib) / "libphaseobjc.dylib").read_bytes().decode()

    def runtime_form(comment_name: str) -> str:
        match = w_mutator.PARAM_NAME_RE.fullmatch(comment_name)
        assert match is not None
        return f"W({match.group(1):<10},{match.group(2)})"

    return [(runtime_form(name), value) for _, name, value in _fake_slots(text)]


def _fake_worker_runner(spec):
    """Worker-contract fake: verdicts only, exactly like the real worker."""
    command = spec["command"]
    pairs = _fake_values_from_lib(Path(spec["prefix_lib"]))
    names = [name for name, _ in pairs]
    values = [value for _, value in pairs]
    units = ["joules"] * len(names)
    wanted = spec.get("param_name")

    def slot_of() -> int | None:
        matches = [i for i, name in enumerate(names) if name == wanted]
        return matches[0] if len(matches) == 1 else None

    def slot_abort() -> dict:
        return {
            "ok": False,
            "abort": "ABORT-W-MUTATOR",
            "detail": f"exact parameter name {spec['param_name']!r} does not "
            "match exactly one live slot",
        }

    if command == "capture-pristine":
        slot = slot_of()
        if slot is None:
            return slot_abort()
        envelope = {
            "param_name": spec["param_name"],
            "slot_index": slot,
            "names": names,
            "units": units,
            "values": values,
        }
        Path(spec["envelope_path"]).write_text(json.dumps(envelope))
        return {
            "ok": True,
            "n_slots": len(names),
            "slot_index": slot,
            "vector_sha256": worker._vector_sha256(values),
        }
    if command == "readback-build":
        slot = slot_of()
        if slot is None:
            return slot_abort()
        expected = float(spec["expected_value_J"])
        if values[slot] != expected:
            return {
                "ok": False,
                "abort": "ABORT-W-MUTATOR",
                "detail": "substituted value did not read back exactly",
            }
        envelope = json.loads(Path(spec["envelope_path"]).read_text())
        changed = worker.vector_diff_slots(
            envelope["names"], envelope["units"], envelope["values"],
            names, units, values,
        )
        if changed != (slot,):
            return {
                "ok": False,
                "abort": "ABORT-W-MUTATOR",
                "detail": f"foreign slots changed: {changed!r}",
            }
        return {
            "ok": True,
            "readback_J": values[slot],
            "changed_slots": list(changed),
            "n_slots": len(names),
            "slot_index": slot,
        }
    if command == "verify-restoration":
        envelope = json.loads(Path(spec["envelope_path"]).read_text())
        matches = (
            tuple(envelope["names"]) == tuple(names)
            and tuple(float(v) for v in envelope["values"]) == tuple(values)
        )
        return {
            "ok": True,
            "vector_matches_pristine": matches,
            "vector_sha256": worker._vector_sha256(values),
        }
    raise AssertionError(f"unknown worker command {command!r}")


def _fake_builder(build_root: Path) -> None:
    source = build_root / "src" / "LiquidMelts.m"
    (build_root / "src" / "libphaseobjc.dylib").write_bytes(source.read_bytes())


def _engine_root(tmp_path: Path, source_text: str = FAKE_SOURCE) -> Path:
    root = tmp_path / "engine"
    (root / "src").mkdir(parents=True)
    (root / "Makefile").write_text("# synthetic makefile\n")
    (root / "src" / "LiquidMelts.m").write_text(source_text)
    return root


def _pristine_lib(tmp_path: Path, source_text: str = FAKE_SOURCE) -> Path:
    lib = tmp_path / "pristine-lib"
    lib.mkdir()
    (lib / "libphaseobjc.dylib").write_bytes(source_text.encode())
    (lib / "libswimdew.dylib").write_bytes(b"synthetic-swimdew")
    (lib / "libspeciation.dylib").write_bytes(b"synthetic-speciation")
    return lib


def _mutator(tmp_path: Path, **kwargs) -> W0WMutator:
    kwargs.setdefault("param_name", SYNTH_PARAM)
    kwargs.setdefault("quarantine_dir", tmp_path / "wq1-quarantine")
    kwargs.setdefault("engine_root", _engine_root(tmp_path))
    kwargs.setdefault("pristine_dylib_dir", _pristine_lib(tmp_path))
    kwargs.setdefault("builder", _fake_builder)
    kwargs.setdefault("worker_runner", _fake_worker_runner)
    return W0WMutator(**kwargs)


def test_frozen_perturbation_set_is_exactly_control_and_signed_10kJ() -> None:
    assert CONTROL_J == 0.0
    assert PLUS_J == 10_000.0
    assert MINUS_J == -10_000.0
    assert FROZEN_PERTURBATIONS_J == (0.0, 10_000.0, -10_000.0)


def test_comment_form_pads_both_components_to_width_10() -> None:
    assert comment_form(SYNTH_PARAM) == "W(SYNTH-A   ,SYNTH-B   )"
    assert comment_form("W(Na2SiO3   ,SiO2)") == "W(Na2SiO3   ,SiO2      )"


def test_patch_initializer_replaces_only_the_named_line() -> None:
    patched, result = patch_initializer(FAKE_SOURCE, SYNTH_PARAM, PLUS_J)
    assert result.line_number == 3
    assert result.slot_index == 0
    assert result.n_initializers == 3
    lines = patched.splitlines()
    assert lines[2] == "\t 10000.0,  //  0 W(SYNTH-A   ,SYNTH-B   )"
    assert lines[3] == FAKE_SOURCE.splitlines()[3]
    assert result.patched_source_sha256 == hashlib.sha256(patched.encode()).hexdigest()


def test_patch_initializer_handles_parenthesized_components() -> None:
    source = FAKE_SOURCE + "\t 7.5,  //  3 W(Ca2(PO4)2 ,SiO2      )\n"
    patched, result = patch_initializer(source, "W(Ca2(PO4)2 ,SiO2)", MINUS_J)
    assert result.slot_index == 3
    assert result.n_initializers == 4
    assert patched.splitlines()[-1] == "\t -10000.0,  //  3 W(Ca2(PO4)2 ,SiO2      )"


def test_patch_initializer_requires_exactly_one_match() -> None:
    with pytest.raises(AbortWMutator):
        patch_initializer(FAKE_SOURCE, "W(SYNTH-A   ,SYNTH-Z)", PLUS_J)
    doubled = FAKE_SOURCE + "\t 0.5,  //  9 W(SYNTH-A   ,SYNTH-B   )\n"
    with pytest.raises(AbortWMutator):
        patch_initializer(doubled, SYNTH_PARAM, PLUS_J)
    with pytest.raises(AbortWMutator):
        patch_initializer("static double x;\n", SYNTH_PARAM, PLUS_J)


def test_make_build_fresh_prefix_per_call_with_exact_readback(tmp_path) -> None:
    mutator = _mutator(tmp_path)
    plus = mutator.make_build(PLUS_J, row_id="row-1")
    minus = mutator.make_build(MINUS_J, row_id="row-1")

    assert plus.readback_J == PLUS_J
    assert minus.readback_J == MINUS_J
    assert plus.row_id == minus.row_id == "row-1"
    assert plus.prefix != minus.prefix  # fresh build prefix per (row, perturbation)
    assert Path(plus.prefix).is_dir() and Path(minus.prefix).is_dir()
    # Structural diff: exactly the named slot moved, nothing else.
    assert plus.changed_slots == (plus.slot_index,)
    assert plus.n_slots == 3
    # One-line source patch, hashed; binaries differ between signed builds.
    assert plus.patch.line_number == 3
    assert plus.binary_sha256 != minus.binary_sha256
    other = (Path(plus.prefix) / "build" / "src" / "LiquidMelts.m").read_text()
    assert "W(SYNTH-A   ,SYNTH-C   )" in other.splitlines()[3]


def test_make_build_requires_a_row_identity(tmp_path) -> None:
    mutator = _mutator(tmp_path)
    with pytest.raises(TypeError):  # row_id is a required keyword argument
        mutator.make_build(PLUS_J)
    with pytest.raises(AbortWMutator):  # and must be non-empty
        mutator.make_build(PLUS_J, row_id="   ")


def test_build_record_binds_row_identity_and_writes_lock(tmp_path) -> None:
    mutator = _mutator(tmp_path)
    build = mutator.make_build(PLUS_J, row_id="row-7")
    assert build.row_id == "row-7"
    # The evaluation-side guard: the build serves only the row it was made for.
    assert build.require_row("row-7") is None
    with pytest.raises(AbortWMutator):
        build.require_row("row-8")
    # Build provenance an auditor can verify: one lock JSON per
    # (row, perturbation), recording the row binding.
    lock = json.loads(Path(build.lock_path).read_text())
    assert lock["row_id"] == "row-7"
    assert lock["perturbation_J"] == PLUS_J
    assert lock["readback_J"] == PLUS_J
    assert lock["prefix"] == build.prefix
    assert lock["param_name"] == SYNTH_PARAM
    assert lock["record_type"] == "NonProductionMutationBuild"


def test_one_fresh_build_per_row_perturbation_pair(tmp_path) -> None:
    mutator = _mutator(tmp_path)
    mutator.make_build(PLUS_J, row_id="row-1")
    with pytest.raises(AbortWMutator):  # re-issue of the same pair refused
        mutator.make_build(PLUS_J, row_id="row-1")
    other = mutator.make_build(PLUS_J, row_id="row-2")  # a new row builds fresh
    assert other.row_id == "row-2"


def test_injected_seams_mint_nonproduction_build_records(tmp_path) -> None:
    """Review finding: injected fakes must not mint production-typed records."""
    build = _mutator(tmp_path).make_build(PLUS_J, row_id="row-1")
    assert isinstance(build, NonProductionMutationBuild)
    assert not isinstance(build, MutationBuild)
    assert isinstance(build, w_mutator.MutationBuildRecord)


def test_default_seams_mint_production_build_records(tmp_path, monkeypatch) -> None:
    """The all-default-seams configuration is the ONLY production-typed path.

    The module-level production seams are monkeypatched to fakes so no real
    engine runs; the constructor receives no overrides, which is what makes
    the record production-typed.
    """
    monkeypatch.setattr(
        w_mutator, "_default_engine_root", lambda: _engine_root(tmp_path)
    )
    monkeypatch.setattr(
        w_mutator, "_default_pristine_dylib_dir", lambda: _pristine_lib(tmp_path)
    )
    monkeypatch.setattr(w_mutator, "_make_libphaseobjc", _fake_builder)
    monkeypatch.setattr(w_mutator, "_spawn_worker", _fake_worker_runner)
    mutator = W0WMutator(
        param_name=SYNTH_PARAM, quarantine_dir=tmp_path / "wq1-quarantine"
    )
    build = mutator.make_build(PLUS_J, row_id="row-1")
    assert type(build) is MutationBuild
    lock = json.loads(Path(build.lock_path).read_text())
    assert lock["record_type"] == "MutationBuild"


def test_zero_j_control_build_reads_back_exactly_zero(tmp_path) -> None:
    build = _mutator(tmp_path).make_build(CONTROL_J, row_id="row-1")
    assert build.readback_J == CONTROL_J
    assert math.copysign(1.0, build.readback_J) == 1.0  # exactly +0.0


def test_write_that_does_not_take_effect_raises_typed_abort(tmp_path) -> None:
    def no_op_builder(build_root: Path) -> None:  # never applies the patch
        pristine = tmp_path / "pristine-lib" / "libphaseobjc.dylib"
        (build_root / "src" / "libphaseobjc.dylib").write_bytes(
            pristine.read_bytes()
        )

    mutator = _mutator(tmp_path, builder=no_op_builder)
    with pytest.raises(AbortWMutator) as excinfo:
        mutator.make_build(PLUS_J, row_id="row-1")
    assert excinfo.value.abort_type == "ABORT-W-MUTATOR"


def test_structural_diff_catches_foreign_slot_change(tmp_path) -> None:
    def rogue_builder(build_root: Path) -> None:
        source = build_root / "src" / "LiquidMelts.m"
        text = source.read_text().replace("-222.5", "-999.0")
        (build_root / "src" / "libphaseobjc.dylib").write_bytes(text.encode())

    mutator = _mutator(tmp_path, builder=rogue_builder)
    with pytest.raises(AbortWMutator) as excinfo:
        mutator.make_build(MINUS_J, row_id="row-1")
    assert excinfo.value.abort_type == "ABORT-W-MUTATOR"


def test_parent_rechecks_worker_reported_readback(tmp_path) -> None:
    def lying_runner(spec):
        payload = _fake_worker_runner(spec)
        if spec["command"] == "readback-build":
            payload = dict(payload, readback_J=payload["readback_J"] + 1.0)
        return payload

    mutator = _mutator(tmp_path, worker_runner=lying_runner)
    with pytest.raises(AbortWMutator):
        mutator.make_build(PLUS_J, row_id="row-1")


def test_exact_engine_name_form_required(tmp_path) -> None:
    # Malformed names are refused at construction...
    with pytest.raises(AbortWMutator):
        _mutator(tmp_path, param_name="not-a-W-name")
    # ...and names that cannot match exactly one live engine slot are
    # refused at first engine contact (byte-exact runtime name match).
    with pytest.raises(AbortWMutator):
        _mutator(tmp_path / "a", param_name="W(SYNTH-A,SYNTH-B)").capture_pristine()
    with pytest.raises(AbortWMutator):
        _mutator(tmp_path / "b", param_name="W(SYNTH-A   ,SYNTH-D)").capture_pristine()


def test_non_frozen_perturbation_refused(tmp_path) -> None:
    with pytest.raises(AbortWMutator):
        _mutator(tmp_path).make_build(5_000.0, row_id="row-1")


def test_quarantine_must_live_outside_the_repo() -> None:
    with pytest.raises(CustodianBoundaryError):
        W0WMutator(
            param_name=SYNTH_PARAM,
            quarantine_dir=w_mutator.repo_root() / "instance" / "wq1",
            engine_root="/nonexistent",
            pristine_dylib_dir="/nonexistent",
        )


def test_restoration_readback_proves_pristine_build_intact(tmp_path) -> None:
    mutator = _mutator(tmp_path)
    mutator.make_build(PLUS_J, row_id="row-1")
    record = mutator.verify_restoration()
    assert record.vector_matches_pristine is True
    assert record.vector_sha256 == mutator.capture_pristine().vector_sha256


def test_restoration_detects_post_build_disturbance(tmp_path) -> None:
    """Disturbed-restore case: a quiet change to the pristine build MUST fail."""
    mutator = _mutator(tmp_path)
    mutator.make_build(PLUS_J, row_id="row-1")
    # Disturb the pristine build AFTER the capture/build: rewrite a foreign
    # synthetic initializer inside the pristine library image.
    pristine = tmp_path / "pristine-lib" / "libphaseobjc.dylib"
    disturbed = pristine.read_text().replace("333.25", "31415.0")
    assert disturbed != pristine.read_text()
    pristine.write_text(disturbed)
    with pytest.raises(AbortWMutator) as excinfo:
        mutator.verify_restoration()
    assert excinfo.value.abort_type == "ABORT-W-MUTATOR"


def test_synthetic_response_gate_uses_fresh_prefix_per_sign(tmp_path) -> None:
    mutator = _mutator(tmp_path)
    scale = 1.0e-5
    seen: list[str] = []

    def evaluate(build) -> float:
        seen.append(build.prefix)
        return math.exp(build.readback_J * scale)

    def analytic(value_J: float) -> float:
        return math.exp(value_J * scale)

    outcomes = synthetic_response_gate(mutator, evaluate, analytic)
    assert [value for value, _, _ in outcomes] == [PLUS_J, MINUS_J]
    assert len(seen) == 2 and seen[0] != seen[1]  # one fresh prefix per sign
    for value, measured, expected in outcomes:
        assert math.isclose(measured, expected, rel_tol=1e-8, abs_tol=1e-12)


def test_synthetic_response_gate_failure_is_typed_abort(tmp_path) -> None:
    mutator = _mutator(tmp_path)

    def evaluate(build) -> float:
        return math.exp(build.readback_J * 1.0e-5)

    def wrong_analytic(value_J: float) -> float:
        return 2.0 * math.exp(value_J * 1.0e-5)

    with pytest.raises(AbortWMutator) as excinfo:
        synthetic_response_gate(mutator, evaluate, wrong_analytic)
    assert excinfo.value.abort_type == "ABORT-W-MUTATOR"


def test_worker_vector_diff_slots_pure_helper() -> None:
    names = ["a", "b", "c"]
    units = ["joules"] * 3
    assert worker.vector_diff_slots(names, units, [1.0, 2.0, 3.0], names, units, [1.0, 2.5, 3.0]) == (1,)
    assert worker.vector_diff_slots(names, units, [1.0, 2.0, 3.0], names, units, [1.0, 2.0, 3.0]) == ()
    with pytest.raises(worker._WorkerAbort):
        worker.vector_diff_slots(names, units, [1.0], ["x", "b", "c"], units, [1.0])


def test_worker_env_scopes_dyld_to_exactly_one_prefix() -> None:
    env = w_mutator._worker_env(Path("/tmp/some-prefix/lib"))
    assert env["DYLD_FALLBACK_LIBRARY_PATH"] == "/tmp/some-prefix/lib"
    assert not any(
        key.startswith("DYLD_") and key != "DYLD_FALLBACK_LIBRARY_PATH"
        for key in env
    )


def test_instrument_never_calls_a_runtime_setter() -> None:
    """Frozen mechanism guard: no set_param_values anywhere in the package."""
    package = Path(__file__).parent.parent / "benchmarks" / "w0_sens"
    offenders = []
    for path in package.glob("*.py"):
        tree = ast.parse(path.read_text(), filename=path.name)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "set_param_values":
                offenders.append(f"{path.name}:{node.lineno}")
            if isinstance(node, ast.Name) and node.id == "set_param_values":
                offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, "runtime setters are forbidden: " + ", ".join(offenders)
    assert not hasattr(w_mutator, "CUSTODIAN_ACK")


def test_no_non_custodian_module_imports_the_instrument() -> None:
    """Custodian boundary: only this package and its tests may import it."""
    repo = Path(__file__).parent.parent
    allowed_prefixes = (
        "benchmarks/w0_sens/",
        "tests/test_w0_sens_",
    )
    violations = []
    for root_name in ("simulator", "engines", "web", "scripts", "benchmarks", "tests"):
        root = repo / root_name
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            relative = path.relative_to(repo).as_posix()
            if relative.startswith(allowed_prefixes):
                continue
            tree = ast.parse(path.read_text(), filename=relative)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                for name in names:
                    if name == "benchmarks.w0_sens" or name.startswith(
                        "benchmarks.w0_sens."
                    ):
                        violations.append(f"{relative}:{node.lineno}: {name}")
    assert not violations, "\n".join(violations)
