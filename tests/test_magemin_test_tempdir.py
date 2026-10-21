from __future__ import annotations

import os
from pathlib import Path


def test_magemin_openmpi_tmpdir_is_inside_pytest_basetemp(
    magemin_openmpi_tmpdir: Path,
    tmp_path_factory,
) -> None:
    basetemp = tmp_path_factory.getbasetemp().resolve()

    assert magemin_openmpi_tmpdir.is_dir()
    assert magemin_openmpi_tmpdir.resolve().parent == basetemp
    assert Path(os.environ["OMPI_MCA_orte_tmpdir_base"]).resolve() == (
        magemin_openmpi_tmpdir.resolve()
    )
