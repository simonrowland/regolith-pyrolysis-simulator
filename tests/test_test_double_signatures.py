from pathlib import Path

from tools.test_double_signatures import scan_tests


def test_all_patch_doubles_accept_production_signatures() -> None:
    census = scan_tests(Path(__file__).parent)
    assert census.resolved + len(census.skipped) == census.scanned
    assert not census.violations, "\n".join(census.violations)
