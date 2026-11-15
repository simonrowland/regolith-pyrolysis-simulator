from pathlib import Path


def test_score_module_stays_within_monolith_ceiling() -> None:
    score_path = Path(__file__).resolve().parents[1] / "simulator/battery/score.py"
    assert len(score_path.read_text(encoding="utf-8").splitlines()) <= 24898
