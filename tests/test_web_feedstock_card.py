"""Pin the setup feedstock card: catalog wt%/ranges plus solar_wind_volatiles."""

import app as app_module


SOLAR_WIND_KEYS = {
    "status",
    "adopted",
    "basis",
    "species_ppm",
    "He3_ppb",
    "isotope_ratio_He3_He4",
    "release_windows_C",
    "capture_note",
}


def _client():
    return app_module.create_app().test_client()


def test_lunar_mare_low_ti_card_renders_solar_wind_volatiles_and_composition():
    client = _client()
    card = client.get("/partials/feedstock-card/lunar_mare_low_ti")
    html = card.get_data(as_text=True)

    assert card.status_code == 200
    assert "44.50" in html
    assert "43–46" in html
    assert "—" in html
    assert "Source:" in html
    assert "Confidence:" in html
    assert "feedstock-volatiles" in html
    assert (
        "inventory-only (FB-33): NOT consumed by Stage-0/C0 yet -- "
        "kernel wiring + rebaseline is the FB-33 implementation step"
    ) in html
    assert "<td>He4</td>" in html
    assert "<td>10</td>" in html
    assert "<td>H</td>" in html
    assert "[15, 100]" in html
    assert "<th>ppm</th>" in html
    assert "<td>He3_ppb</td>" in html
    assert "<td>3</td>" in html
    assert "[1, 8]" in html
    assert "<th>ppb</th>" in html
    assert "capture_note:" in html
    assert "capture needs a cryogenic trap stage (FB-33 equipment add)" in html
    assert "isotope_ratio_He3_He4:" in html
    assert "release_windows_C" in html
    assert "[200, 700]" in html


def test_mars_basalt_card_omits_solar_wind_volatiles_and_keeps_composition():
    client = _client()
    card = client.get("/partials/feedstock-card/mars_basalt")
    html = card.get_data(as_text=True)

    assert card.status_code == 200
    assert "45.50" in html
    assert "43–48" in html
    assert "Source:" in html
    assert "Confidence:" in html
    assert "feedstock-volatiles" not in html
    assert "Solar-wind volatiles" not in html
    assert "solar_wind" not in html
    assert "He3_ppb" not in html
    assert "species_ppm" not in html
    assert "capture_note" not in html
    assert "inventory-only (FB-33)" not in html
    assert "He-3" not in html
    assert "He3" not in html


def test_api_feedstock_lunar_mare_low_ti_still_carries_solar_wind_volatiles():
    client = _client()
    response = client.get("/api/feedstock/lunar_mare_low_ti")
    data = response.get_json()

    assert response.status_code == 200
    sw = data["solar_wind_volatiles"]
    assert set(sw) == SOLAR_WIND_KEYS
    assert sw["species_ppm"]["He4"]["value"] == 10
    assert sw["species_ppm"]["H"]["interval"] == [15, 100]
    assert sw["He3_ppb"]["value"] == 3
    assert "capture_note" in sw
