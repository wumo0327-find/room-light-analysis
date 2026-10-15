"""Postprocessing regression: independent energy/degree-hour expectations."""
import json
import numpy as np
import pandas as pd
import pytest

from core.research_energy import run_energyplus


def prepare(tmp_path, monkeypatch):
    model = tmp_path/'model.idf'
    model.write_text('test input', encoding='utf-8')
    epw = tmp_path/'weather.epw'
    epw.write_text('LOCATION,test', encoding='utf-8')
    (tmp_path/'energyplus_model_manifest.json').write_text(json.dumps({'profile':{
        'heating_cop':3, 'cooling_cop':3.2,
        'comfort_low_c':18, 'comfort_high_c':26}}), encoding='utf-8')
    output = tmp_path/'energyplus_output'
    output.mkdir()
    frame = pd.DataFrame({
        'Zone:Total Heating Energy [J](Hourly)':[3.6e6]*3,
        'Zone:Total Cooling Energy [J](Hourly)':[7.68e6]*3,
        'Zone:Lights Electricity Energy [J](Hourly)':[1.2e6]*3,
        'Zone:Zone Operative Temperature [C](Hourly)':[28,16,22],
    })
    frame.to_csv(output/'eplusout.csv', index=False)
    pd.DataFrame({'hvac_fraction':np.repeat([1,.5,0],6)}).to_csv(
        tmp_path/'teaching_schedule.csv', index=False)
    monkeypatch.setattr('core.research_energy.find_energyplus', lambda _:model)
    monkeypatch.setattr('core.engine_process.run_process', lambda *a, **kw:b'')
    return model, epw, frame


def test_profile_loaded_before_processing(tmp_path, monkeypatch):
    model, epw, _ = prepare(tmp_path, monkeypatch)
    result = run_energyplus(model, epw)
    # Heating=3 kWh / 3; cooling=6.4 kWh / 3.2; lights=1 kWh.
    assert result.annual_heating_load_kwh == pytest.approx(3)
    assert result.annual_cooling_load_kwh == pytest.approx(6.4)
    assert result.annual_heating_kwh == pytest.approx(1)
    assert result.annual_cooling_kwh == pytest.approx(2)
    assert result.annual_total_kwh == pytest.approx(4)
    # Occupied overheating=(28-26)*1; underheating=(18-16)*.5.
    assert result.occupied_overheating_degree_hours == pytest.approx(2)
    assert result.occupied_underheating_degree_hours == pytest.approx(1)


@pytest.mark.parametrize('bad', ['missing_temperature','nan_temperature','short_calendar','nan_calendar'])
def test_invalid_output_does_not_silently_pass(tmp_path, monkeypatch, bad):
    model, epw, frame = prepare(tmp_path, monkeypatch)
    temp = frame.columns[-1]
    if bad == 'missing_temperature':
        frame = frame.drop(columns=[temp])
    elif bad == 'nan_temperature':
        frame.loc[0,temp] = float('nan')
    elif bad == 'short_calendar':
        pd.DataFrame({'hvac_fraction':[1]*6}).to_csv(tmp_path/'teaching_schedule.csv', index=False)
    else:
        pd.DataFrame({'hvac_fraction':[float('nan')]*18}).to_csv(tmp_path/'teaching_schedule.csv', index=False)
    frame.to_csv(tmp_path/'energyplus_output'/'eplusout.csv', index=False)
    with pytest.raises(ValueError):
        run_energyplus(model, epw)
    assert not (tmp_path/'energyplus_output'/'年度结果摘要.json').exists()


def test_shadow_overflow_does_not_pass_as_success(tmp_path, monkeypatch):
    model, epw, _ = prepare(tmp_path, monkeypatch)
    (tmp_path/'energyplus_output'/'eplusout.err').write_text(
        'Warning: Too many figures [>100000] detected in an overlap calculation.', encoding='utf-8')
    with pytest.raises(ValueError, match='阴影'):
        run_energyplus(model, epw)
    assert not (tmp_path/'energyplus_output'/'年度结果摘要.json').exists()


def test_idf_declares_shadow_capacity(tmp_path):
    from core.research_energy import build_energyplus_model
    from tests.test_v510_kindergarten_regression import _building
    building, _ = _building()
    model = build_energyplus_model(building,['bp_space_2'],tmp_path)
    text=model.read_text(encoding='utf-8')
    assert 'ShadowCalculation,\n  PixelCounting,\n  Periodic,\n  20,\n  100000,' in text
    assert 'SutherlandHodgman,\n  2048,' in text


def test_gpu_fallback_is_not_silently_accepted(tmp_path, monkeypatch):
    model, epw, _ = prepare(tmp_path, monkeypatch)
    (tmp_path/'energyplus_output'/'eplusout.err').write_text(
        'Warning: PixelCounting is not available. Will revert to PolygonClipping.',encoding='utf-8')
    with pytest.raises(ValueError, match='GPU'):
        run_energyplus(model, epw)
