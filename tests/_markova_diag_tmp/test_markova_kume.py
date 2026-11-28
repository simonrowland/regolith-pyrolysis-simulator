from __future__ import annotations

import markova_diag_support as diag


def test_markova_vi_kume_cms():
    diag.run_cold_markova_vi_internal_analytical("Kume2000-CMS")
