"""Print the OpenIMCC zero-offset residual line for the current checkout."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from simulator.battery.enums import Engine
from simulator.battery.score import compile_residual, dumps_residual_line
import simulator.battery.score as score_module
from tests.battery import test_feo_activity_standard as feo_test


def main() -> None:
    root = Path.cwd()
    if not (root / "pyproject.toml").is_file():
        raise RuntimeError(f"run from a repository root: {root}")

    identity_helper = getattr(feo_test, "_melt_pack_identity", None)
    if callable(identity_helper):
        from simulator.battery import feo_standard as feo_standard_module

        identity = identity_helper("published-core")
        sources = feo_test._openimcc_sources(
            pack_version=identity["pack_version"],
            pack_digest=identity["binding_digest"],
            condensate_digest=(
                feo_standard_module._OPENIMCC_DEFAULT_CONDENSATE_TABLE_DIGEST
            ),
        )
        context, _experiment, reference, prediction = feo_test._case(
            Engine.OPENIMCC,
            sources,
            provenance={"melt_pack_identity": identity},
        )
        normalizer = getattr(
            score_module, "_normalize_openimcc_feo_activity_prediction", None
        )
        if callable(normalizer):
            prediction = normalizer(prediction)
    else:
        sources = feo_test._openimcc_sources(
            pack_version=feo_test._OPENIMCC_DEFAULT_VERSION,
            pack_digest=feo_test._OPENIMCC_DEFAULT_PACK_DIGEST,
            condensate_digest=feo_test._JANAF_FE019_CONDENSATE_DIGEST,
        )
        context, _experiment, reference, prediction = feo_test._case(
            Engine.OPENIMCC,
            sources,
            temperature_K=Decimal("1643.15"),
        )

    residual, candidate = compile_residual(
        reference,
        Engine.OPENIMCC,
        context=context,
        prediction=prediction,
    )
    print(dumps_residual_line(residual, candidate))


if __name__ == "__main__":
    main()
