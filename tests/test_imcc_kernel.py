"""
Rung-1 gates for the IMCC-SF04 melt-equilibrium kernel.

- Analytic binary systems (closed-form derivation in comments).
- Limiting cases (x -> 0, x -> 1, K -> 0, K -> infinity).
- Atom balance <= 1e-12 relative on every solve.
- Bit-determinism.
- Typed-refusal behaviour per class.
- Log-space stability on a stiff synthetic system.
"""

from __future__ import annotations

import numpy as np
import pytest

from simulator.melt_backend.imcc_sf04 import (
    ImccDatapack,
    ImccComponentOutsideDomainError,
    ImccCompositionIncompleteError,
    ImccFerricInputUnsupportedError,
    ImccNonconvergenceError,
    ImccTOutsideDatapackDomainError,
    solve_imcc_sf04,
)
from simulator.melt_backend.imcc_sf04.kernel import _active_residual, LOG10


# --------------------------------------------------------------------------- #
# Synthetic datapack helpers
# --------------------------------------------------------------------------- #


def make_ab_datapack(
    A: float = 0.0,
    B: float = 0.0,
    T_lo: float = 0.0,
    T_hi: float = 1.0e6,
    version: str = "synthetic-ab",
) -> ImccDatapack:
    """Single AB complex over two parents A and B."""
    return ImccDatapack(
        reactions=("AB",),
        nu=np.array([[1.0], [1.0]]),
        A=np.array([A]),
        B=np.array([B]),
        domains=[(T_lo, T_hi)],
        version=version,
        parent_oxides=("A", "B"),
    )


def make_8parent_datapack(complexes: list[dict]) -> ImccDatapack:
    """Build a synthetic datapack over the 8 placeholder parents."""
    parents = ("Si", "Mg", "Fe", "Ca", "Al", "Ti", "Na", "K")
    n = len(parents)
    reactions = []
    nu_cols = []
    A = []
    B = []
    domains = []
    for c in complexes:
        reactions.append(c["name"])
        col = np.zeros(n)
        for pname, coeff in c["nu"].items():
            col[parents.index(pname)] = coeff
        nu_cols.append(col)
        A.append(c["A"])
        B.append(c["B"])
        domains.append(c["domain"])
    nu = np.column_stack(nu_cols)
    return ImccDatapack(
        reactions=reactions,
        nu=nu,
        A=np.array(A),
        B=np.array(B),
        domains=domains,
        version="synthetic-8",
        parent_oxides=parents,
    )


# --------------------------------------------------------------------------- #
# Analytic binary solution (A + B -> AB)
# --------------------------------------------------------------------------- #
# For a single AB complex with analytical fractions xA and xB (xA + xB = 1),
# let w = x_AB. From the parent balances:
#     xA* = xA - xB * w
#     xB* = xB - xA * w
# and mass action w = K * xA* * xB*. Substituting:
#     w = K (xA - xB*w)(xB - xA*w)
#       = K [ xA*xB - (xA^2 + xB^2) * w + xA*xB * w^2 ]
# Rearranged to standard quadratic a w^2 + b w + c = 0:
#     a = K * xA * xB
#     b = -[ K * (xA^2 + xB^2) + 1 ]
#     c = K * xA * xB = a
# The physical root is the smaller positive root:
#     w = (-b - sqrt(b^2 - 4 a^2)) / (2 a)
# with a -> 0 limit w -> 0.
# Then gamma_i = x_i* / x_i.


def analytic_ab(xA: float, log10_K: float) -> tuple[float, float, float]:
    K = 10.0 ** log10_K
    xB = 1.0 - xA
    a = K * xA * xB
    if a == 0.0:
        return xA, xB, 0.0
    b = -(K * (xA * xA + xB * xB) + 1.0)
    disc = b * b - 4.0 * a * a
    # The physical root is the smaller positive root. The naive quadratic
    # formula cancels for small K; use the stable form w = (2a) / (-b + sqrtD).
    w = (2.0 * a) / (-b + np.sqrt(disc))
    xAs = xA - xB * w
    xBs = xB - xA * w
    return float(xAs), float(xBs), float(w)


def atom_balance_residual(result, datapack: ImccDatapack) -> float:
    """Maximum relative residual of the parent-oxide mass balances."""
    reconstructed = result.parent_x_star + datapack.nu @ result.complex_x
    expected = result.parent_x * result.D
    return float(np.max(np.abs(reconstructed - expected)) / result.D)


def assert_solve_ok(result, datapack: ImccDatapack) -> float:
    assert np.all(result.parent_x_star >= 0.0)
    assert np.all(result.complex_x >= -1.0e-15)
    assert np.all(np.isfinite(result.species_x))
    # Independent closure checks (not self-referential).
    assert np.isclose(
        np.sum(result.species_x), 1.0, atol=1.0e-12
    ), "species mole fractions do not sum to 1"
    S = datapack.nu.sum(axis=0)
    D_reconstructed = 1.0 + np.sum((S - 1.0) * result.complex_x)
    assert np.isclose(result.D, D_reconstructed, atol=1.0e-12), (
        f"reported D {result.D:.12g} does not match independent "
        f"reconstruction {D_reconstructed:.12g}"
    )
    rel = atom_balance_residual(result, datapack)
    assert rel <= 1.0e-12, f"atom balance relative residual {rel:.3e}"
    return rel


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("xA", [0.0, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0])
@pytest.mark.parametrize("log10_K", [-10.0, -2.0, 0.0, 2.0, 10.0])
def test_analytic_binary_ab(xA: float, log10_K: float) -> None:
    pack = make_ab_datapack(A=log10_K, B=0.0)
    parent = np.array([xA, 1.0 - xA])
    res = solve_imcc_sf04(parent, 1000.0, pack)
    xAs_exp, xBs_exp, w_exp = analytic_ab(xA, log10_K)

    assert np.isclose(res.parent_x_star[0], xAs_exp, atol=1.0e-12)
    assert np.isclose(res.parent_x_star[1], xBs_exp, atol=1.0e-12)
    assert np.isclose(res.complex_x[0], w_exp, atol=1.0e-12)

    assert np.isclose(res.parent_activity[0], xAs_exp, atol=1.0e-12)
    assert np.isclose(res.parent_activity[1], xBs_exp, atol=1.0e-12)

    if xA > 0.0:
        assert np.isclose(res.parent_gamma[0], xAs_exp / xA, atol=1.0e-12)
    if 1.0 - xA > 0.0:
        assert np.isclose(res.parent_gamma[1], xBs_exp / (1.0 - xA), atol=1.0e-12)

    assert_solve_ok(res, pack)


def test_limiting_K_to_zero() -> None:
    # K -> 0: no complexing, x* = x, gamma = 1.
    pack = make_ab_datapack(A=-1000.0, B=0.0)
    parent = np.array([0.3, 0.7])
    res = solve_imcc_sf04(parent, 1000.0, pack)
    assert np.allclose(res.parent_x_star, parent, atol=1.0e-12)
    assert np.allclose(res.parent_gamma, 1.0, atol=1.0e-12)
    assert np.isclose(res.complex_x[0], 0.0, atol=1.0e-12)
    assert_solve_ok(res, pack)


def test_limiting_K_to_infinity() -> None:
    # K -> infinity: the minority parent is essentially fully complexed.
    # A=20 gives K=1e20, enough to push the minority parent to machine-zero
    # while staying inside the solver's physical basin.
    pack = make_ab_datapack(A=20.0, B=0.0)
    xA = 0.3
    parent = np.array([xA, 1.0 - xA])
    res = solve_imcc_sf04(parent, 1000.0, pack)
    # A is the minority component; its unbound fraction should be driven to ~0.
    assert res.parent_x_star[0] <= 1.0e-8
    # B remains as excess. With one AB complex, total species moles = 1 - w,
    # where w -> xA in the limit, so xB* = (xB - xA) / (1 - xA).
    assert np.isclose(res.parent_x_star[1], (1.0 - xA - xA) / (1.0 - xA), atol=1.0e-8)
    assert_solve_ok(res, pack)


def test_limiting_x_to_zero() -> None:
    # A -> 0, B -> 1: no AB can form because A is absent.
    pack = make_ab_datapack(A=2.0, B=0.0)
    res = solve_imcc_sf04(np.array([0.0, 1.0]), 1000.0, pack)
    assert np.isclose(res.parent_x_star[0], 0.0, atol=1.0e-12)
    assert np.isclose(res.parent_x_star[1], 1.0, atol=1.0e-12)
    assert np.isclose(res.complex_x[0], 0.0, atol=1.0e-12)
    assert_solve_ok(res, pack)


def test_limiting_x_to_one() -> None:
    # A -> 1, B -> 0: no AB can form because B is absent.
    pack = make_ab_datapack(A=2.0, B=0.0)
    res = solve_imcc_sf04(np.array([1.0, 0.0]), 1000.0, pack)
    assert np.isclose(res.parent_x_star[0], 1.0, atol=1.0e-12)
    assert np.isclose(res.parent_x_star[1], 0.0, atol=1.0e-12)
    assert np.isclose(res.complex_x[0], 0.0, atol=1.0e-12)
    assert_solve_ok(res, pack)


def test_atom_balance_on_all_solves() -> None:
    # Sweep many states and confirm the ledger closes on every solve.
    worst = 0.0
    pack = make_ab_datapack(A=1.0, B=2000.0)
    for xA in np.linspace(0.01, 0.99, 17):
        for T in np.linspace(800.0, 2200.0, 9):
            parent = np.array([xA, 1.0 - xA])
            res = solve_imcc_sf04(parent, T, pack)
            rel = assert_solve_ok(res, pack)
            worst = max(worst, rel)
    assert worst <= 1.0e-12


def test_bit_determinism() -> None:
    pack = make_ab_datapack(A=1.0, B=500.0)
    parent = np.array([0.3, 0.7])
    res1 = solve_imcc_sf04(parent, 1500.0, pack)
    res2 = solve_imcc_sf04(parent, 1500.0, pack)
    assert np.array_equal(res1.parent_x_star, res2.parent_x_star)
    assert np.array_equal(res1.complex_x, res2.complex_x)
    assert np.array_equal(res1.parent_gamma, res2.parent_gamma)
    assert np.array_equal(res1.species_x, res2.species_x)
    assert res1.D == res2.D
    assert res1.convergence.iterations == res2.convergence.iterations


def test_refusal_T_outside_domain() -> None:
    pack = make_ab_datapack(A=0.0, B=0.0, T_lo=1000.0, T_hi=2000.0)
    parent = np.array([0.5, 0.5])
    with pytest.raises(ImccTOutsideDatapackDomainError):
        solve_imcc_sf04(parent, 900.0, pack)
    with pytest.raises(ImccTOutsideDatapackDomainError):
        solve_imcc_sf04(parent, 2100.0, pack)
    res = solve_imcc_sf04(parent, 900.0, pack, allow_extrapolation=True)
    assert res.extrapolated is True
    assert_solve_ok(res, pack)


def test_refusal_composition_incomplete() -> None:
    pack = make_ab_datapack()
    parent = np.array([0.5, 0.5])
    with pytest.raises(ImccCompositionIncompleteError):
        solve_imcc_sf04(np.array([-0.1, 0.6]), 1000.0, pack)
    with pytest.raises(ImccCompositionIncompleteError):
        solve_imcc_sf04(np.array([0.5, 0.6]), 1000.0, pack, basis=1.0)
    with pytest.raises(ImccCompositionIncompleteError):
        solve_imcc_sf04(np.array([0.0, 0.0]), 1000.0, pack)
    with pytest.raises(ImccCompositionIncompleteError):
        solve_imcc_sf04(np.array([np.nan, 0.5]), 1000.0, pack)


def test_refusal_component_outside_domain() -> None:
    pack = make_ab_datapack()
    parent = np.array([0.5, 0.5])
    # Wrong vector length (extra positive component).
    with pytest.raises(ImccComponentOutsideDomainError):
        solve_imcc_sf04(np.array([0.5, 0.5, 0.0]), 1000.0, pack)
    # Unsupported positive extra component.
    with pytest.raises(ImccComponentOutsideDomainError):
        solve_imcc_sf04(parent, 1000.0, pack, extra_mol={"P2O5": 0.1})


def test_refusal_ferric_input() -> None:
    pack = make_ab_datapack()
    parent = np.array([0.5, 0.5])
    with pytest.raises(ImccFerricInputUnsupportedError):
        solve_imcc_sf04(parent, 1000.0, pack, extra_mol={"Fe2O3": 0.1})
    # Zero Fe2O3 is allowed and must not raise.
    res = solve_imcc_sf04(parent, 1000.0, pack, extra_mol={"Fe2O3": 0.0})
    assert_solve_ok(res, pack)


def test_refusal_nonconvergence() -> None:
    pack = make_ab_datapack(A=1.0, B=0.0)
    parent = np.array([0.5, 0.5])
    with pytest.raises(ImccNonconvergenceError) as exc:
        solve_imcc_sf04(parent, 1000.0, pack, max_iter=0)
    assert exc.value.diagnostics["iterations"] == 0


def test_log_space_stiff_synthetic() -> None:
    # Wide K span across ~13 orders of magnitude, chosen to stay inside the
    # finite range of double-precision exponentials during the solve.
    complexes = [
        {
            "name": "Mg2Si",
            "nu": {"Mg": 2.0, "Si": 1.0},
            "A": 5.0,
            "B": 0.0,
            "domain": (500.0, 2500.0),
        },
        {
            "name": "FeAl2",
            "nu": {"Fe": 1.0, "Al": 2.0},
            "A": -5.0,
            "B": 0.0,
            "domain": (500.0, 2500.0),
        },
        {
            "name": "CaTi",
            "nu": {"Ca": 1.0, "Ti": 1.0},
            "A": 3.0,
            "B": 0.0,
            "domain": (500.0, 2500.0),
        },
        {
            "name": "Na2K",
            "nu": {"Na": 2.0, "K": 1.0},
            "A": 8.0,
            "B": 0.0,
            "domain": (500.0, 2500.0),
        },
        {
            "name": "SiAl2",
            "nu": {"Si": 1.0, "Al": 2.0},
            "A": 0.0,
            "B": 0.0,
            "domain": (500.0, 2500.0),
        },
    ]
    pack = make_8parent_datapack(complexes)
    parent = np.array([0.15, 0.20, 0.10, 0.10, 0.15, 0.10, 0.10, 0.10])
    res = solve_imcc_sf04(parent, 1500.0, pack)
    rel = assert_solve_ok(res, pack)
    assert np.all(res.parent_activity >= 0.0)
    assert np.all(np.isfinite(res.parent_gamma))
    assert res.labels.model_id == "IMCC-SF04"
    assert res.labels.evidence_class == "diagnostic-shadow"
    assert rel <= 1.0e-12


def test_result_labels_and_identity() -> None:
    pack = make_ab_datapack(A=0.0, B=0.0, version="r2.1-test")
    parent = np.array([0.4, 0.6])
    res = solve_imcc_sf04(parent, 1200.0, pack)
    assert res.labels.model_id == "IMCC-SF04"
    assert res.labels.datapack_version == "r2.1-test"
    assert res.labels.evidence_class == "diagnostic-shadow"
    for name in res.species_names:
        assert res.labels.coverage[name] == "A-published-imcc"
    assert res.extrapolated is False


# --------------------------------------------------------------------------- #
# Regression tests for review findings (P1-1, P2-1..P2-7)
# --------------------------------------------------------------------------- #


def make_3parent_inactive_datapack() -> ImccDatapack:
    """3-parent complex over 8 placeholder parents; complex is inactive when Fe=0."""
    parents = ("Si", "Mg", "Fe", "Ca", "Al", "Ti", "Na", "K")
    n = len(parents)
    nu = np.zeros((n, 1))
    nu[0, 0] = 1.0  # Si
    nu[1, 0] = 1.0  # Mg
    nu[2, 0] = 1.0  # Fe
    return ImccDatapack(
        reactions=("SiMgFe",),
        nu=nu,
        A=np.array([0.0]),
        B=np.array([0.0]),
        domains=[(1500.0, 2500.0)],
        version="synthetic-3parent-inactive",
        parent_oxides=parents,
    )


def test_p1_1_adv_4c_inactive_complex_domain_refusal() -> None:
    # 3-parent complex with Fe=0 cannot form; its [1500, 2500] K domain must not
    # cause refusal at 1000 K.
    pack = make_3parent_inactive_datapack()
    parent = np.array([0.2, 0.2, 0.0, 0.1, 0.1, 0.1, 0.1, 0.1])
    res = solve_imcc_sf04(parent, 1000.0, pack)
    assert_solve_ok(res, pack)
    assert res.extrapolated is False


def test_p1_1_adv_4d_inactive_complex_extrapolated_false() -> None:
    # Same inactive-complex setup under allow_extrapolation: nothing is consumed
    # outside its domain, so extrapolated must remain False.
    pack = make_3parent_inactive_datapack()
    parent = np.array([0.2, 0.2, 0.0, 0.1, 0.1, 0.1, 0.1, 0.1])
    res = solve_imcc_sf04(parent, 1000.0, pack, allow_extrapolation=True)
    assert_solve_ok(res, pack)
    assert res.extrapolated is False


def test_p2_1_jacobian_matches_finite_differences() -> None:
    # Verify the analytical Jacobian code against central finite differences.
    # The docstring must describe this form: x_i is the analytical target,
    # not x_i*.
    pack = make_ab_datapack(A=1.0, B=0.0)
    parent = np.array([0.3, 0.7])
    T = 1000.0
    log10_K = pack.A + pack.B / T
    lnK = LOG10 * log10_K
    S = pack.nu.sum(axis=0)
    x = parent / np.sum(parent)
    y0 = np.log(np.maximum(x, 1.0e-12))
    f0, _g, _D, J_analytic = _active_residual(y0, x, pack.nu, lnK, S)

    h = 1.0e-7
    J_fd = np.zeros_like(J_analytic)
    for i in range(len(y0)):
        y_plus = y0.copy()
        y_minus = y0.copy()
        y_plus[i] += h
        y_minus[i] -= h
        f_plus, *_ = _active_residual(y_plus, x, pack.nu, lnK, S)
        f_minus, *_ = _active_residual(y_minus, x, pack.nu, lnK, S)
        J_fd[:, i] = (f_plus - f_minus) / (2.0 * h)

    assert np.allclose(J_analytic, J_fd, atol=1.0e-6)


def test_p2_2_independent_atom_balance_closure() -> None:
    # Atom-balance closure must be checkable without relying on kernel-reported D.
    pack = make_ab_datapack(A=1.0, B=2000.0)
    parent = np.array([0.4, 0.6])
    res = solve_imcc_sf04(parent, 1500.0, pack)
    assert_solve_ok(res, pack)
    # Independent checks repeated explicitly (assert_solve_ok already includes them).
    assert np.isclose(np.sum(res.species_x), 1.0, atol=1.0e-12)
    S = pack.nu.sum(axis=0)
    D_reconstructed = 1.0 + np.sum((S - 1.0) * res.complex_x)
    assert np.isclose(res.D, D_reconstructed, atol=1.0e-12)


def test_p2_3_gamma_greater_than_one() -> None:
    # Inert A + B-dimer (B2). B strongly dimerizes, shrinking the total-species
    # denominator; A is weakly complexed and must show gamma_A > 1.
    pack = ImccDatapack(
        reactions=("B2",),
        nu=np.array([[0.0], [2.0]]),
        A=np.array([np.log10(10.0)]),
        B=np.array([0.0]),
        domains=[(0.0, 1.0e6)],
        version="synthetic-b2",
        parent_oxides=("A", "B"),
    )
    parent = np.array([0.4, 0.6])
    res = solve_imcc_sf04(parent, 1000.0, pack)
    assert_solve_ok(res, pack)
    assert res.parent_gamma[0] > 1.0, (
        f"gamma_A = {res.parent_gamma[0]:.6f} should exceed 1 for an inert "
        "parent in a strongly dimerized melt"
    )


def test_p2_4_negative_extra_mol_ferric() -> None:
    pack = make_ab_datapack()
    parent = np.array([0.5, 0.5])
    with pytest.raises(ImccCompositionIncompleteError):
        solve_imcc_sf04(parent, 1000.0, pack, extra_mol={"Fe2O3": -0.1})


def test_p2_4_negative_extra_mol_component() -> None:
    pack = make_ab_datapack()
    parent = np.array([0.5, 0.5])
    with pytest.raises(ImccCompositionIncompleteError):
        solve_imcc_sf04(parent, 1000.0, pack, extra_mol={"P2O5": -0.5})


def test_p2_5_scalar_input() -> None:
    pack = make_ab_datapack()
    with pytest.raises(ImccCompositionIncompleteError):
        solve_imcc_sf04(0.5, 1000.0, pack)


def test_p2_6_nonfinite_residual() -> None:
    # K=1e300 overflows the residual; must raise ImccNonconvergenceError, not a
    # raw scipy ValueError.
    pack = make_ab_datapack(A=1.0e300, B=0.0)
    parent = np.array([0.5, 0.5])
    with pytest.raises(ImccNonconvergenceError) as exc:
        solve_imcc_sf04(parent, 1000.0, pack)
    assert "non-finite" in str(exc.value).lower()


def test_p2_7_total_displacement() -> None:
    pack = make_ab_datapack(A=1.0, B=0.0)
    parent = np.array([0.5, 0.5])
    res = solve_imcc_sf04(parent, 1000.0, pack)
    assert res.convergence.total_displacement > 0.0

    # Force a nonconvergence where the solver reached a y before failing; the
    # diagnostics must report the actual displacement, not a hardcoded 0.0.
    # The 8-parent stiff problem needs more than max_iter=1 evaluations, so
    # least_squares terminates before the residual is below tol.
    stiff_complexes = [
        {
            "name": "Mg2Si",
            "nu": {"Mg": 2.0, "Si": 1.0},
            "A": 5.0,
            "B": 0.0,
            "domain": (500.0, 2500.0),
        },
        {
            "name": "FeAl2",
            "nu": {"Fe": 1.0, "Al": 2.0},
            "A": -5.0,
            "B": 0.0,
            "domain": (500.0, 2500.0),
        },
        {
            "name": "CaTi",
            "nu": {"Ca": 1.0, "Ti": 1.0},
            "A": 3.0,
            "B": 0.0,
            "domain": (500.0, 2500.0),
        },
        {
            "name": "Na2K",
            "nu": {"Na": 2.0, "K": 1.0},
            "A": 8.0,
            "B": 0.0,
            "domain": (500.0, 2500.0),
        },
        {
            "name": "SiAl2",
            "nu": {"Si": 1.0, "Al": 2.0},
            "A": 0.0,
            "B": 0.0,
            "domain": (500.0, 2500.0),
        },
    ]
    stiff_pack = make_8parent_datapack(stiff_complexes)
    stiff_parent = np.array([0.15, 0.20, 0.10, 0.10, 0.15, 0.10, 0.10, 0.10])
    with pytest.raises(ImccNonconvergenceError) as exc:
        solve_imcc_sf04(stiff_parent, 1500.0, stiff_pack, tol=1.0e-12, max_iter=1)
    assert exc.value.diagnostics["total_displacement"] > 0.0
