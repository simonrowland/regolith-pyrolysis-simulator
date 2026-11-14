"""Full-precision behavior pin for the current oxygen-buffer equations."""

from __future__ import annotations

from importlib import import_module

from benchmarks.buffer_reproduction import PUBLISHED_BUFFERS


TEMPERATURES_K = (1000.0, 1200.0, 1400.0, 1600.0, 1800.0, 2000.0)
PRESSURES_BAR = (1.0e-6, 0.1, 1.0, 10.0)


def _buffer_function(name: str, old_module: str):
    try:
        owner = import_module("simulator.oxygen_buffers")
    except ModuleNotFoundError as exc:
        if exc.name != "simulator.oxygen_buffers":
            raise
    else:
        if hasattr(owner, name):
            return getattr(owner, name)
    return getattr(import_module(old_module), name)


def _snapshot() -> tuple[tuple[object, ...], ...]:
    oneill = _buffer_function(
        "oneill_qfm_log10_fO2_bar", "simulator.chemistry.qfm_oneill"
    )
    legacy_iw = _buffer_function(
        "legacy_iw_log10_fO2_bar", "simulator.fe_redox"
    )
    feo_iw = _buffer_function("feo_iw_log10_fO2_bar", "simulator.fe_redox")
    cco = _buffer_function("cco_log10_fO2_bar", "simulator.chemistry.graphite_c_co")
    cco_pco = _buffer_function(
        "log10_fo2_c_co_bar", "simulator.chemistry.graphite_c_co"
    )
    cco_buffered = _buffer_function(
        "cco_buffered_fO2", "engines.builtin.cco_redox_buffer"
    )
    qfm_reference = _buffer_function(
        "qfm_log10_fO2_bar", "engines.builtin.cco_redox_buffer"
    )
    frost_qfm = _buffer_function(
        "_qfm_logfo2_frost", "simulator.melt_backend.sulfsat"
    )

    rows: list[tuple[object, ...]] = []
    for temperature_K in TEMPERATURES_K:
        rows.extend(
            (
                ("oneill_qfm", temperature_K, repr(oneill(temperature_K))),
                ("legacy_iw", temperature_K, repr(legacy_iw(temperature_K))),
                ("holzheid_iw", temperature_K, repr(feo_iw(temperature_K))),
                (
                    "holzheid_iw_a_feo_0_25",
                    temperature_K,
                    repr(feo_iw(temperature_K, a_feo=0.25)),
                ),
                ("cco_qfm_reference", temperature_K, repr(qfm_reference(temperature_K))),
            )
        )
        for name, buffer in sorted(PUBLISHED_BUFFERS.items()):
            rows.append((f"frost_{name}", temperature_K, repr(buffer.log10_fo2(temperature_K))))
        for pressure_bar in PRESSURES_BAR:
            point = cco_buffered(temperature_K, pressure_bar, reference_buffer="QFM")
            rows.extend(
                (
                    ("jakobsson_cco", temperature_K, pressure_bar, repr(cco(temperature_K, pressure_bar))),
                    ("jakobsson_c_co_pressure", temperature_K, pressure_bar, repr(cco_pco(temperature_K, pressure_bar))),
                    (
                        "cco_buffered_fO2",
                        temperature_K,
                        pressure_bar,
                        repr(point.log10_fO2_bar),
                        repr(point.fO2_bar),
                        repr(point.reference_log10_fO2_bar),
                        repr(point.delta_log10_fO2_from_reference),
                    ),
                    ("frost_piecewise_qfm", temperature_K, pressure_bar, repr(frost_qfm(temperature_K, pressure_bar))),
                )
            )
    return tuple(rows)


EXPECTED = (
    ("oneill_qfm", 1000.0, "-16.47"),
    ("legacy_iw", 1000.0, "-20.645"),
    ("holzheid_iw", 1000.0, "-19.545331557951705"),
    ("holzheid_iw_a_feo_0_25", 1000.0, "-20.74945154060763"),
    ("cco_qfm_reference", 1000.0, "-16.47"),
    ("frost_IW", 1000.0, "-20.787"),
    ("frost_NNO", 1000.0, "-15.57"),
    ("frost_QFM", 1000.0, "-16.3613"),
    ("frost_WM", 1000.0, "-19.795"),
    ("jakobsson_cco", 1000.0, 1e-06, "-17.478170999829"),
    ("jakobsson_c_co_pressure", 1000.0, 1e-06, "-29.489430959077467"),
    ("cco_buffered_fO2", 1000.0, 1e-06, "-17.478170999829", "3.325285971533559e-18", "-16.47", "-1.0081709998290016"),
    ("frost_piecewise_qfm", 1000.0, 1e-06, "-16.36140999989"),
    ("jakobsson_cco", 1000.0, 0.1, "-17.478153900000002"),
    ("jakobsson_c_co_pressure", 1000.0, 0.1, "-19.489430959077463"),
    ("cco_buffered_fO2", 1000.0, 0.1, "-17.478153900000002", "3.3254169032936905e-18", "-16.47", "-1.0081539000000035"),
    ("frost_piecewise_qfm", 1000.0, 0.1, "-16.361399"),
    ("jakobsson_cco", 1000.0, 1.0, "-17.478"),
    ("jakobsson_c_co_pressure", 1000.0, 1.0, "-17.489430959077463"),
    ("cco_buffered_fO2", 1000.0, 1.0, "-17.478", "3.3265955329400334e-18", "-16.47", "-1.0080000000000027"),
    ("frost_piecewise_qfm", 1000.0, 1.0, "-16.3613"),
    ("jakobsson_cco", 1000.0, 10.0, "-17.476461"),
    ("jakobsson_c_co_pressure", 1000.0, 10.0, "-15.489430959077465"),
    ("cco_buffered_fO2", 1000.0, 10.0, "-17.476461", "3.3384048296861456e-18", "-16.47", "-1.0064610000000016"),
    ("frost_piecewise_qfm", 1000.0, 10.0, "-16.36031"),
    ("oneill_qfm", 1200.0, "-12.295"),
    ("legacy_iw", 1200.0, "-16.109166666666667"),
    ("holzheid_iw", 1200.0, "-15.456343740646174"),
    ("holzheid_iw_a_feo_0_25", 1200.0, "-16.6604637233021"),
    ("cco_qfm_reference", 1200.0, "-12.295"),
    ("frost_IW", 1200.0, "-16.2055"),
    ("frost_NNO", 1200.0, "-11.415"),
    ("frost_QFM", 1200.0, "-12.178583333333332"),
    ("frost_WM", 1200.0, "-14.327166666666667"),
    ("jakobsson_cco", 1200.0, 1e-06, "-13.844309166524166"),
    ("jakobsson_c_co_pressure", 1200.0, 1e-06, "-25.855598003369128"),
    ("cco_buffered_fO2", 1200.0, 1e-06, "-13.844309166524166", "1.43116871297167e-14", "-12.295", "-1.549309166524166"),
    ("frost_piecewise_qfm", 1200.0, 1e-06, "-12.178674999908333"),
    ("jakobsson_cco", 1200.0, 0.1, "-13.844294916666666"),
    ("jakobsson_c_co_pressure", 1200.0, 0.1, "-15.855598003369128"),
    ("cco_buffered_fO2", 1200.0, 0.1, "-13.844294916666666", "1.4312156725478337e-14", "-12.295", "-1.5492949166666659"),
    ("frost_piecewise_qfm", 1200.0, 0.1, "-12.178665833333332"),
    ("jakobsson_cco", 1200.0, 1.0, "-13.844166666666666"),
    ("jakobsson_c_co_pressure", 1200.0, 1.0, "-13.855598003369128"),
    ("cco_buffered_fO2", 1200.0, 1.0, "-13.844166666666666", "1.4316383823048815e-14", "-12.295", "-1.5491666666666664"),
    ("frost_piecewise_qfm", 1200.0, 1.0, "-12.178583333333332"),
    ("jakobsson_cco", 1200.0, 10.0, "-13.842884166666666"),
    ("jakobsson_c_co_pressure", 1200.0, 10.0, "-11.855598003369128"),
    ("cco_buffered_fO2", 1200.0, 10.0, "-13.842884166666666", "1.4358723525694863e-14", "-12.295", "-1.547884166666666"),
    ("frost_piecewise_qfm", 1200.0, 10.0, "-12.177758333333331"),
    ("oneill_qfm", 1400.0, "-9.312857142857142"),
    ("legacy_iw", 1400.0, "-12.869285714285713"),
    ("holzheid_iw", 1400.0, "-12.556814347947418"),
    ("holzheid_iw_a_feo_0_25", 1400.0, "-13.760934330603343"),
    ("cco_qfm_reference", 1400.0, "-9.312857142857142"),
    ("frost_IW", 1400.0, "-12.933000000000002"),
    ("frost_NNO", 1400.0, "-8.447142857142858"),
    ("frost_QFM", 1400.0, "-9.190928571428572"),
    ("frost_WM", 1400.0, "-10.42157142857143"),
    ("jakobsson_cco", 1400.0, 1e-06, "-11.248693571306427"),
    ("jakobsson_c_co_pressure", 1400.0, 1e-06, "-23.26000303500603"),
    ("cco_buffered_fO2", 1400.0, 1e-06, "-11.248693571306427", "5.640354865696829e-12", "-9.312857142857142", "-1.935836428449285"),
    ("frost_piecewise_qfm", 1400.0, 1e-06, "-9.191007142778572"),
    ("jakobsson_cco", 1400.0, 0.1, "-11.248681357142855"),
    ("jakobsson_c_co_pressure", 1400.0, 0.1, "-13.260003035006031"),
    ("cco_buffered_fO2", 1400.0, 0.1, "-11.248681357142855", "5.6405134981192564e-12", "-9.312857142857142", "-1.935824214285713"),
    ("frost_piecewise_qfm", 1400.0, 0.1, "-9.190999285714286"),
    ("jakobsson_cco", 1400.0, 1.0, "-11.248571428571427"),
    ("jakobsson_c_co_pressure", 1400.0, 1.0, "-11.260003035006031"),
    ("cco_buffered_fO2", 1400.0, 1.0, "-11.248571428571427", "5.641941404982867e-12", "-9.312857142857142", "-1.935714285714285"),
    ("frost_piecewise_qfm", 1400.0, 1.0, "-9.190928571428572"),
    ("jakobsson_cco", 1400.0, 10.0, "-11.247472142857141"),
    ("jakobsson_c_co_pressure", 1400.0, 10.0, "-9.260003035006031"),
    ("cco_buffered_fO2", 1400.0, 10.0, "-11.247472142857141", "5.656240369981254e-12", "-9.312857142857142", "-1.934614999999999"),
    ("frost_piecewise_qfm", 1400.0, 10.0, "-9.190221428571428"),
    ("oneill_qfm", 1600.0, "-7.07625"),
    ("legacy_iw", 1600.0, "-10.439374999999998"),
    ("holzheid_iw", 1600.0, "-10.398029648404632"),
    ("holzheid_iw_a_feo_0_25", 1600.0, "-11.602149631060557"),
    ("cco_qfm_reference", 1600.0, "-7.07625"),
    ("frost_IW", 1600.0, "-10.478625"),
    ("frost_NNO", 1600.0, "-6.221250000000001"),
    ("frost_QFM", 1600.0, "-6.9501875"),
    ("frost_WM", 1600.0, "-7.492374999999999"),
    ("jakobsson_cco", 1600.0, 1e-06, "-9.301981874893125"),
    ("jakobsson_c_co_pressure", 1600.0, 1e-06, "-21.31330680873371"),
    ("cco_buffered_fO2", 1600.0, 1e-06, "-9.301981874893125", "4.989053086394704e-10", "-7.07625", "-2.2257318748931247"),
    ("frost_piecewise_qfm", 1600.0, 1e-06, "-6.95025624993125"),
    ("jakobsson_cco", 1600.0, 0.1, "-9.3019711875"),
    ("jakobsson_c_co_pressure", 1600.0, 0.1, "-11.31330680873371"),
    ("cco_buffered_fO2", 1600.0, 0.1, "-9.3019711875", "4.989175861677259e-10", "-7.07625", "-2.2257211874999996"),
    ("frost_piecewise_qfm", 1600.0, 0.1, "-6.950249375"),
    ("jakobsson_cco", 1600.0, 1.0, "-9.301874999999999"),
    ("jakobsson_c_co_pressure", 1600.0, 1.0, "-9.31330680873371"),
    ("cco_buffered_fO2", 1600.0, 1.0, "-9.301874999999999", "4.990280986243238e-10", "-7.07625", "-2.225624999999999"),
    ("frost_piecewise_qfm", 1600.0, 1.0, "-6.9501875"),
    ("jakobsson_cco", 1600.0, 10.0, "-9.300913125"),
    ("jakobsson_c_co_pressure", 1600.0, 10.0, "-7.31330680873371"),
    ("cco_buffered_fO2", 1600.0, 10.0, "-9.300913125", "5.001345704302987e-10", "-7.07625", "-2.2246631249999993"),
    ("frost_piecewise_qfm", 1600.0, 10.0, "-6.94956875"),
    ("oneill_qfm", 1800.0, "-5.336666666666666"),
    ("legacy_iw", 1800.0, "-8.549444444444443"),
    ("holzheid_iw", 1800.0, "-8.73130231649131"),
    ("holzheid_iw_a_feo_0_25", 1800.0, "-9.935422299147234"),
    ("cco_qfm_reference", 1800.0, "-5.336666666666666"),
    ("frost_IW", 1800.0, "-8.569666666666667"),
    ("frost_NNO", 1800.0, "-4.49"),
    ("frost_QFM", 1800.0, "-5.2073888888888895"),
    ("frost_WM", 1800.0, "-5.214111111111112"),
    ("jakobsson_cco", 1800.0, 1e-06, "-7.787872777682778"),
    ("jakobsson_c_co_pressure", 1800.0, 1e-06, "-19.79920974385524"),
    ("cco_buffered_fO2", 1800.0, 1e-06, "-7.787872777682778", "1.629773388878525e-08", "-5.336666666666666", "-2.451206111016112"),
    ("frost_piecewise_qfm", 1800.0, 1e-06, "-5.2074499999388895"),
    ("jakobsson_cco", 1800.0, 0.1, "-7.787863277777777"),
    ("jakobsson_c_co_pressure", 1800.0, 0.1, "-9.799209743855238"),
    ("cco_buffered_fO2", 1800.0, 0.1, "-7.787863277777777", "1.629809039485083e-08", "-5.336666666666666", "-2.4511966111111114"),
    ("frost_piecewise_qfm", 1800.0, 0.1, "-5.207443888888889"),
    ("jakobsson_cco", 1800.0, 1.0, "-7.7877777777777775"),
    ("jakobsson_c_co_pressure", 1800.0, 1.0, "-7.799209743855238"),
    ("cco_buffered_fO2", 1800.0, 1.0, "-7.7877777777777775", "1.6301299332482816e-08", "-5.336666666666666", "-2.4511111111111115"),
    ("frost_piecewise_qfm", 1800.0, 1.0, "-5.2073888888888895"),
    ("jakobsson_cco", 1800.0, 10.0, "-7.786922777777778"),
    ("jakobsson_c_co_pressure", 1800.0, 10.0, "-5.799209743855238"),
    ("cco_buffered_fO2", 1800.0, 10.0, "-7.786922777777778", "1.6333423478833046e-08", "-5.336666666666666", "-2.450256111111112"),
    ("frost_piecewise_qfm", 1800.0, 10.0, "-5.20683888888889"),
    ("oneill_qfm", 2000.0, "-3.9450000000000003"),
    ("legacy_iw", 2000.0, "-7.0375"),
    ("holzheid_iw", 2000.0, "-7.407776961649966"),
    ("holzheid_iw_a_feo_0_25", 2000.0, "-8.611896944305892"),
    ("cco_qfm_reference", 2000.0, "-3.9450000000000003"),
    ("frost_IW", 2000.0, "-7.0425"),
    ("frost_NNO", 2000.0, "-3.1050000000000004"),
    ("frost_QFM", 2000.0, "-3.8131500000000003"),
    ("frost_WM", 2000.0, "-3.3915000000000006"),
    ("jakobsson_cco", 2000.0, 1e-06, "-6.576585499914501"),
    ("jakobsson_c_co_pressure", 2000.0, 1e-06, "-18.58793209195246"),
    ("cco_buffered_fO2", 2000.0, 1e-06, "-6.576585499914501", "2.6510291313297035e-07", "-3.9450000000000003", "-2.6315854999145003"),
    ("frost_piecewise_qfm", 2000.0, 1e-06, "-3.8132049999450004"),
    ("jakobsson_cco", 2000.0, 0.1, "-6.576576950000001"),
    ("jakobsson_c_co_pressure", 2000.0, 0.1, "-8.587932091952462"),
    ("cco_buffered_fO2", 2000.0, 0.1, "-6.576576950000001", "2.651081322403889e-07", "-3.9450000000000003", "-2.6315769500000004"),
    ("frost_piecewise_qfm", 2000.0, 0.1, "-3.8131995"),
    ("jakobsson_cco", 2000.0, 1.0, "-6.5765"),
    ("jakobsson_c_co_pressure", 2000.0, 1.0, "-6.587932091952461"),
    ("cco_buffered_fO2", 2000.0, 1.0, "-6.5765", "2.651551093009208e-07", "-3.9450000000000003", "-2.6315"),
    ("frost_piecewise_qfm", 2000.0, 1.0, "-3.8131500000000003"),
    ("jakobsson_cco", 2000.0, 10.0, "-6.575730500000001"),
    ("jakobsson_c_co_pressure", 2000.0, 10.0, "-4.587932091952462"),
    ("cco_buffered_fO2", 2000.0, 10.0, "-6.575730500000001", "2.6562533798716163e-07", "-3.9450000000000003", "-2.6307305000000003"),
    ("frost_piecewise_qfm", 2000.0, 10.0, "-3.8126550000000003"),
)


def test_oxygen_buffer_equations_match_pre_move_values() -> None:
    actual = _snapshot()
    assert actual == EXPECTED
