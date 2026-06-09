from __future__ import annotations

from flask import Flask
import pytest

from simulator.ceramic_classifier import CeramicClassification
from web import advisory
from web import routes as web_routes


@pytest.fixture
def client(tmp_path):
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["OPTIMIZER_RUNS_DIR"] = str(tmp_path / "runs")
    app.register_blueprint(web_routes.bp)
    return app.test_client()


def test_dashboard_renders_advisory_panels(client) -> None:
    response = client.get("/")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'id="wall-risk-panel"' in html
    assert 'id="ceramic-rump-panel"' in html
    assert "simulator-advisory.js" in html


def test_wall_risk_api_and_panel_render_uncharacterized_without_rating(
    client,
) -> None:
    response = client.get("/api/wall-risk?species=Mg")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "ok"
    cells = [
        species["chemical_attack"]
        for zone in payload["zones"]
        for material in zone["materials"]
        for species in material["species"]
    ]
    assert cells
    assert all(cell["display"] == "uncharacterized" for cell in cells)
    assert all(cell["value"] is None for cell in cells)

    # Stickiness is rendered independently of chemical_attack; prove it also stays
    # fail-closed (a leaked uncharacterized stickiness class would otherwise pass).
    stickiness_cells = [
        species["stickiness"]
        for zone in payload["zones"]
        for material in zone["materials"]
        for species in material["species"]
    ]
    assert stickiness_cells
    assert all(cell["display"] == "uncharacterized" for cell in stickiness_cells)
    assert all(cell["uncharacterized"] is True for cell in stickiness_cells)

    html = client.get("/partials/wall-risk-panel?species=Mg").get_data(
        as_text=True
    )
    assert "data-wall-risk-panel" in html
    assert "Mg" in html
    assert "attack uncharacterized" in html
    assert "attack low" not in html
    assert "attack moderate" not in html
    assert "attack high" not in html
    assert "stick moderate" not in html
    assert "stick strongly-adhering" not in html


def test_ceramic_rump_panel_renders_match_and_service_rating(client) -> None:
    response = client.get(
        "/partials/ceramic-rump-panel?Al2O3=72&SiO2=28"
    )

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Mullite" in html
    assert "Usable service: 1600 C" in html
    assert "Service kind: service" in html


def test_ceramic_rump_panel_renders_no_match(client) -> None:
    response = client.get("/partials/ceramic-rump-panel?SiO2=100")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "no-match" in html
    assert "composition outside source-supported ceramic windows" in html


def test_ceramic_rump_panel_honors_melting_only_not_service(client) -> None:
    response = client.get(
        "/partials/ceramic-rump-panel?MgO=57.3&SiO2=42.7"
    )

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Forsterite" in html
    assert "melting-only: 1890 C; not a usable service rating" in html
    assert "Usable service: 1890 C" not in html


def test_ceramic_rump_panel_renders_ambiguous(client, monkeypatch) -> None:
    def fake_classifier(composition, **kwargs):
        return CeramicClassification(
            match=None,
            tolerance_wt_pct=0.5,
            status="ambiguous",
            reason="ambiguous ceramic classifier matches: alpha, beta",
        )

    monkeypatch.setattr(advisory, "classify_ceramic_rump", fake_classifier)

    response = client.get("/partials/ceramic-rump-panel?SiO2=50")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "ambiguous" in html
    assert "alpha, beta" in html
