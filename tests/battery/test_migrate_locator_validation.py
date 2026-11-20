"""Corpus regression for locator-backed migrated values and typed refusals."""

from pathlib import Path
import tempfile

import pytest

from simulator.battery import migrate as m
from simulator.battery.enums import RefusalReason
from simulator.battery.store_validation import _ALLOWED_HARD_ISSUE_PATH_SUFFIXES
from simulator.battery.validate import validate_corpus

pytestmark = pytest.mark.timeout(300)
_SOURCE_REFUSALS = (
    ("fredrickson-chasanov-1973-na2o-enthalpy-1300k", 0),
    ("gibbons-1963-thesis-cas-slags-cas", 0),
    ("mathieu-2009-sodium-solubility-thesis", 0),
    ("janz-1979-nsrds61-part2-molten-salt-properties", 0),
    ("brillo-2025-al-ti-thermophysical", 0),
    ("oishi-1986-cu2o-cao-activities", 48),
    ("wood-wade-2013-trace-activities", 15),
    ("stolyarova-2021-ti-al-kems-ru", 16),
    ("pickrell-1994-gaseous-alkali-corrosion", 49),
)


def _assert_located_fields(result) -> None:
    assert all(observation.locator is not None for observation in result.observations.values())
    with tempfile.TemporaryDirectory(prefix="b1230-store-") as directory:
        output = Path(directory)
        m.write_outputs(result, output)
        works, experiments, observations = m.load_migrated_store(output)
        report = validate_corpus(
            works, experiments, observations, benches=result.benches,
            context_rows={
                str(row["context_id"]): row
                for rows in result.context_by_work.values() for row in rows
            },
        )
    forbidden = [
        issue for issue in report.hard_issues
        if issue.reason is RefusalReason.CONDITIONAL_FIELD
        and not issue.path.endswith(_ALLOWED_HARD_ISSUE_PATH_SUFFIXES)
    ]
    assert not forbidden, forbidden


@pytest.mark.parametrize("shard", range(16))
def test_regenerated_locator_fields_satisfy_store_contract(shard: int, monkeypatch) -> None:
    paths = m.discover_extracts(m.REPO_ROOT / "data/literature/extracts")[shard::16]
    if any(path.stem == "mccready-1948-sodium-silicate" for path in paths):
        dependency = m.REPO_ROOT / "data/literature/extracts/kroger-fingas-1935-alkali-silicate-equilibria.yaml"
        if dependency not in paths:
            paths.insert(0, dependency)
    monkeypatch.setattr(m, "discover_extracts", lambda *_args: paths)
    migrator = m.Migrator(m.REPO_ROOT)
    migrator.migrate_extracts()
    migrator.finalize()
    _assert_located_fields(migrator.result)


@pytest.mark.parametrize("source_id,refusals", _SOURCE_REFUSALS)
def test_real_source_locator_refusals_preserve_independent_anchors(source_id, refusals) -> None:
    migrator = m.Migrator(m.REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(m.REPO_ROOT / "data/literature/extracts" / f"{source_id}.yaml")
    migrator.finalize()
    issues = [
        issue for issue in migrator.result.registry_issues
        if issue.path.startswith("extract[") and issue.path.endswith(".locator")
    ]
    assert len(issues) == refusals
    assert all(issue.reason is RefusalReason.INVALID_SOURCE for issue in issues)
    assert all("conflicting locator fields:" in issue.detail for issue in issues)
    _assert_located_fields(migrator.result)


@pytest.mark.parametrize("failure,source_id", [
    ("table_key", "janz-1979-nsrds61-part2-molten-salt-properties"),
    ("table_key", "brillo-2025-al-ti-thermophysical"),
    ("subsection", "fredrickson-chasanov-1973-na2o-enthalpy-1300k"),
    ("pages", "gibbons-1963-thesis-cas-slags-cas"),
    ("pages", "mathieu-2009-sodium-solubility-thesis"),
    ("validation", "oishi-1986-cu2o-cao-activities"),
    ("anchors", "wood-wade-2013-trace-activities"),
    ("anchors", "stolyarova-2021-ti-al-kems-ru"),
    ("grid", "pickrell-1994-gaseous-alkali-corrosion"),
])
def test_real_source_regressions_detect_reverted_emission(failure, source_id, monkeypatch) -> None:
    with monkeypatch.context() as mutation:
        if failure == "validation":
            mutation.setattr(m.Migrator, "_validate_extract_locators", lambda *_args, **_kwargs: None)
        elif failure in {"table_key", "subsection"}:
            mutation.setattr(m, "_LOCATOR_EXTRA_KEYS", m._LOCATOR_EXTRA_KEYS - {failure})
            mutation.setitem(m._LOCATOR_FIELD_ALIASES, failure, "table" if failure == "table_key" else "section")
        elif failure == "pages":
            decode = m._decode_locator_from_plain

            def reject_multiple_pages(raw, **kwargs):
                if isinstance(raw, dict) and isinstance(raw.get("pdf_page_index"), list):
                    raise m._InvalidLocator("pdf_page_index must be an integer")
                return decode(raw, **kwargs)

            mutation.setattr(m, "_decode_locator_from_plain", reject_multiple_pages)
        elif failure == "anchors":
            locate = m.locator_from_mapping

            def drop_anchors(raw, fallback=None):
                try:
                    m._locator_from_plain(raw, refuse_invalid=True)
                except ValueError:
                    return None
                return locate(raw, fallback)

            mutation.setattr(m, "locator_from_mapping", drop_anchors)
        else:
            locate = m._locator_from_plain

            def drop_grid_anchors(raw, *, refuse_invalid=False):
                if not refuse_invalid and isinstance(raw, dict) and "table_row_number" in raw:
                    try:
                        locate(raw, refuse_invalid=True)
                    except ValueError:
                        return None
                return locate(raw, refuse_invalid=refuse_invalid)

            mutation.setattr(m, "_locator_from_plain", drop_grid_anchors)
        with pytest.raises(AssertionError):
            test_real_source_locator_refusals_preserve_independent_anchors(
                source_id, dict(_SOURCE_REFUSALS)[source_id]
            )


def test_nested_explicit_extra_unknown_key_fails_persisted_reader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    locator = {"page": 5, "extras": {"unreviewed_locator_axis": "value"}}
    payload = {"observation": {"locator": locator}}
    with pytest.raises(ValueError, match="extras.unreviewed_locator_axis"):
        m._validate_persisted_locator_mappings(payload)

    first_unsupported = m._locator_mapping_first_unsupported_key

    def outer_keys_only(raw: object) -> str | None:
        if isinstance(raw, dict):
            return next(
                (
                    str(key) for key in raw
                    if not m._locator_key_is_supported(str(key))
                ),
                None,
            )
        return first_unsupported(raw)

    with monkeypatch.context() as mutation:
        mutation.setattr(m, "_locator_mapping_first_unsupported_key", outer_keys_only)
        with pytest.raises(pytest.fail.Exception):
            with pytest.raises(ValueError, match="extras.unreviewed_locator_axis"):
                m._validate_persisted_locator_mappings(payload)
