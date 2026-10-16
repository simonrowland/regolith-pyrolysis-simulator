"""Iron seed authority, buffer conventions, and first-liquid atom accounting."""

from simulator.fe_redox import initialize_feedstock_iron


def test_explicit_liquid_wustite_anchor_preserves_b899_numeric_pin():
    # Captured at 8ad0cc9bf before d-160 changes. This explicit legacy buffer
    # must retain its meaning when literature solid-IW anchors are admitted.
    prepared, diagnostic = initialize_feedstock_iron({
        'composition_wt_pct': {'SiO2': 80.0, 'FeO': 20.0},
        'intrinsic_redox': {
            'delta_iw': -1.0, 'buffer': 'pure_liquid_wustite',
            'formation_temperature_K': 1673.15,
        },
    })
    assert {k: v.hex() for k, v in prepared['composition_wt_pct'].items()} == {
        'SiO2': '0x1.4000000000000p+6',
        'FeO': '0x1.3b9e01a2c7592p+4',
        'Fe2O3': '0x1.37bb097d1be40p-2',
    }
    assert diagnostic['fO2_log'].hex() == '-0x1.57b4427aead95p+3'
