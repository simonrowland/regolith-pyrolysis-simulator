"""Pin the setup feedstock card: catalog wt%/ranges plus solar_wind_volatiles."""

import re
from html.parser import HTMLParser
from unittest.mock import patch

import app as app_module


class _ClassParser(HTMLParser):
    def __init__(self, classname):
        super().__init__()
        self.classname = classname
        self.found = False

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if name == "class" and value and self.classname in value.split():
                self.found = True


def _has_class(html, classname):
    parser = _ClassParser(classname)
    parser.feed(html)
    return parser.found


def _client():
    return app_module.create_app().test_client()


def _render_card(feedstock):
    client = _client()
    with patch("web.routes.get_visible_feedstock", return_value=feedstock):
        card = client.get("/partials/feedstock-card/review-fixture")
    assert card.status_code == 200
    return card.get_data(as_text=True)


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
    assert re.search(r"<td>He4</td>\s*<td>10</td>", html)
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
    assert not _has_class(html, "feedstock-volatiles")


def test_volatiles_partial_object_omits_empty_headings():
    html = _render_card({
        "label": "sparse",
        "solar_wind_volatiles": {
            "species_ppm": {"He4": {"value": 10}},
            "He3_ppb": {"value": 3},
            "release_windows_C": {"source": "test-only source"},
        },
    })
    assert re.search(r"<td>He4</td>\s*<td>10</td>", html)
    assert "<td>3</td>" in html
    assert "test-only source" in html
    assert "<th>interval</th>" not in html
    assert "<th>source</th>" not in html
    assert "<th>confidence</th>" not in html
    assert "<th>basis</th>" not in html
    assert "<th>release_windows_C</th>" not in html

    html = _render_card({
        "label": "species-value",
        "solar_wind_volatiles": {"species_ppm": {"H": {"value": 40}}},
    })
    assert "<td>H</td>" in html
    assert "<td>40</td>" in html
    assert "<th>interval</th>" not in html

    html = _render_card({
        "label": "he3-value",
        "solar_wind_volatiles": {"He3_ppb": {"value": 3}},
    })
    assert "<td>He3_ppb</td>" in html
    assert "<th>interval</th>" not in html

    html = _render_card({
        "label": "isotope-basis",
        "solar_wind_volatiles": {
            "isotope_ratio_He3_He4": {"basis": "fixture basis only"},
        },
    })
    assert "fixture basis only" in html
    assert "isotope_ratio_He3_He4:" not in html

    html = _render_card({
        "label": "windows-source",
        "solar_wind_volatiles": {
            "release_windows_C": {"source": "fixture source only"},
        },
    })
    assert "fixture source only" in html
    assert "<th>release_windows_C</th>" not in html

    html = _render_card({
        "label": "empty-assay",
        "solar_wind_volatiles": {"species_ppm": {"He4": {}}},
    })
    assert "<th>species_ppm</th>" not in html
    assert "<td>He4</td>" not in html
