"""Independent arithmetic and path/state checks; not physical validation."""
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from core.engine_service import EngineSettings, configure, settings, _key
from core.experiment_contract import (weighted_daylight_stats, fixed_sampling,
    no_device_space, model_fingerprints, stage_record, export_experiment_records,
    fresh_pareto)
from core.operation import calculate_operation_metrics, profile_from_mapping
from core.thermal import ThermalResult
from core.complex_experiments import (run_multi_space_experiments, build_solution_space,
    run_complex_project_baseline, run_complex_no_shading_candidate)
from core.staged_experiments import _mark_convergence
from core.research_scene import space_scene
from core.research_energy import write_teaching_schedule
from tests.test_v510_kindergarten_regression import _building


@pytest.fixture(autouse=True)
def restore_engine():
    old = settings()
    yield
    configure(old)


def test_weighted_metrics_zero_threshold_and_outside_point():
    # Areas 1,2,1; DF 0,2,4 %. The 999 out-of-domain sample is excluded.
    df = np.array([[0., 2., 4., 999.]])
    mask = np.array([[True, True, True, False]])
    a = np.array([[1., 2., 1., 90.]])
    actual = weighted_daylight_stats(df, df*150., mask, a, 2.)
    assert actual['DF_avg'] == (0*1+2*2+4*1)/4
    assert actual['E_avg'] == (0*1+300*2+600*1)/4
    assert actual['Ra'] == (2+1)/4  # equality at exactly 2% counts
    assert actual['daylight_score'] == (0*1+1*2+1*1)/4
    assert actual['U0'] == 0/300
    light = np.array([[1., 2., 3., 999.]])
    assert weighted_daylight_stats(light, light*150, mask, a, 2)['daylight_score'] == (0.5+2+1)/4


def test_cost_independent_hand_calculation():
    t = ThermalResult()
    t.T_in = np.array([28., 16.]+[22.]*10)
    t.H_envelope, t.H_vent_avg = 100., 20.
    profile = dict(teaching_days_per_year=200, lighting_hours_per_day=7.5,
        hvac_hours_per_day=6, lighting_power_density_w_m2=9,
        electricity_tariff_yuan_kwh=.85, cooling_cop=3.2, heating_cop=3.,
        construction_service_life_years=20, monthly_teaching_days=[100,100]+[0]*10)
    a = calculate_operation_metrics(daylight_score=.4, thermal_result=t,
        floor_area_m2=48.502, construction_cost_yuan=1000, profile=profile)
    full = 48.502*9*200*7.5/1000
    cooling = (28-26)*120*100*6/1000/3.2
    heating = (18-16)*120*100*6/1000/3
    assert a['annual_lighting_full_kwh'] == pytest.approx(654.777)
    assert a['annual_lighting_kwh'] == pytest.approx(full*(1-.4))
    assert a['annual_operating_cost'] == pytest.approx((full*.6+cooling+heating)*.85)
    assert a['annual_total_cost'] == pytest.approx(a['annual_operating_cost']+1000/20)


def test_hourly_energy_is_not_replaced_by_cd():
    t = ThermalResult(); t.T_in = np.full(12, 22.)
    t.engine = 'EnergyPlus'
    t.annual_lighting_kwh=12.; t.annual_cooling_kwh=32.; t.annual_heating_kwh=30.
    a = calculate_operation_metrics(daylight_score=0, thermal_result=t, floor_area_m2=48.502)
    b = calculate_operation_metrics(daylight_score=1, thermal_result=t, floor_area_m2=48.502)
    assert a['annual_operating_kwh'] == b['annual_operating_kwh'] == 74
    assert a['annual_operating_cost'] == pytest.approx(74*.85)
    assert a['annualized_construction_cost'] == 0


def test_fixed_grid_clipped_area_domain_and_ids():
    from shapely.geometry import Point, Polygon
    b, _ = _building(); room = b.get_space('bp_space_2')
    xs, ys, mask, X, Y, weights = fixed_sampling(room, 250)
    polygon = Polygon([(p.x,p.y) for p in room.analysis_outer_points()],
        [[(p.x,p.y) for p in r] for r in room.analysis_hole_points()])
    assert np.allclose(np.diff(xs), 250) and np.allclose(np.diff(ys), 250)
    assert all(polygon.contains(Point(x,y)) for x,y in zip(X[mask],Y[mask]))
    assert weights.sum() == pytest.approx(polygon.area/1e6)
    clone = deepcopy(room); clone.id='unrelated'; clone.name='rename'; clone.metadata={}
    assert model_fingerprints(room)['baseline_model_fingerprint'] == model_fingerprints(clone)['baseline_model_fingerprint']


def test_all_devices_close_and_baseline_state_not_polluted():
    b, _ = _building(); room = b.get_space('bp_space_2')
    room.shading.components=['horizontal_overhang','louver','light_shelf']
    room.shading.type='light_shelf'; room.shading.light_shelf_inside_depth_mm=500
    room.shading.overhang_depth_mm=500; room.shading.vertical_fin_enabled=True
    room.shading.vertical_fin_depth_mm=400
    snapshot=asdict(room)
    plain=no_device_space(room)
    assert plain.shading.enabled_components() == []
    assert not plain.shading.vertical_fin_enabled
    assert plain.shading.light_shelf_inside_depth_mm == 0
    assert asdict(room) == snapshot
    assert not any(s.kind=='shade' for s in space_scene(plain).surfaces)


def _fake_light(space, **kwargs):
    from core.complex_daylight import ComplexDaylightResult
    r=ComplexDaylightResult(); r.Ra=.5; r.daylight_score=.6; r.U0=.4
    r.DF_avg=2.; r.E_avg=300.; r.E_out=15000.; r.Ra_threshold=2.
    r.E_min=120.; r.E_max=600.; r.DF_min=.8; r.DF_max=4.
    r.grid_mm=settings().grid_mm; r.ndiv=settings().ambient_divisions
    r.fingerprints=model_fingerprints(space)
    r.sensor_count=10; r.sensor_fingerprint='same-coordinates'
    return r


def _fake_heat(space, *_args, **kwargs):
    r=ThermalResult(); r.engine='EnergyPlus'; r.run_directory='test-double-not-simulation'
    r.epw_sha256='test-double'; r.T_in=np.full(12,22)
    r.overheat_degree_hours=5.; r.underheat_degree_hours=3.
    r.annual_lighting_kwh=100.; r.annual_cooling_kwh=20.; r.annual_heating_kwh=30.
    return r


def test_three_paths_sequence_export_and_same_baseline(tmp_path):
    b,w=_building(); room=b.get_space('bp_space_2'); snapshot=asdict(room)
    configure(EngineSettings(grid_mm=250, ambient_divisions=512))
    tables=[]
    with patch('core.complex_experiments.compute_complex_daylight',_fake_light), patch('core.complex_experiments.compute_complex_thermal',_fake_heat):
        for kind in ['horizontal_overhang','louver','light_shelf','light_shelf','louver','horizontal_overhang']:
            frame=run_multi_space_experiments([room], weather=w, device_types=[kind],
                tilt_degs=[90],depth_mms=[300],gap_mms=[0],materials=['普通/氧化铝板'],
                louver_params={'angles':[0], 'widths':[100], 'spacings':[150]},
                light_shelf_params={'inside_depths':[0], 'outside_depths':[300], 'tilts':[0], 'upper_ratios':[.35]},
                enable_combinations=False)
            assert len(frame)==2
            baseline=frame[frame.device_type.eq('baseline')].iloc[0]
            assert baseline.construction_cost == baseline.annualized_construction_cost == 0
            assert frame.baseline_model_fingerprint.nunique()==1
            assert baseline.Ra2 == baseline.Ra and baseline.Cd2 == baseline.daylight_score
            export_experiment_records(frame,str(tmp_path/(kind+'.csv')))
            assert (tmp_path/(kind+'_独立无构件基准.csv')).exists()
            tables.append(baseline)
        again=run_complex_no_shading_candidate(room,weather=w).iloc[0]
        assert again.Ra == tables[0].Ra and again.annual_operating_cost==tables[0].annual_operating_cost
    assert asdict(room)==snapshot
    for row in tables:
        for k in ('Ra','daylight_score','U0','DF_avg','E_avg','thermal_discomfort','annual_operating_cost','sensor_count'):
            assert row[k] == tables[0][k]


def test_scan_final_delta_and_missing_rule_values():
    scan=dict(Ra=.4,daylight_score=.5,U0=.2,E_avg=300.,DF_avg=2.,thermal_discomfort=10.,
        annual_operating_kwh=100.,annual_operating_cost=85.,device_type='louver',param_label='test')
    high={**scan,'Ra':.41,'daylight_score':.505,'E_avg':301.}
    row=stage_record(high,scan,final=True)
    assert row['scan_Ra']==.4 and row['final_Ra']==.41
    assert row['refinement_delta_Ra']==pytest.approx(.01)
    assert row['refinement_delta_Cd']==pytest.approx(.005)
    verified=_mark_convergence(pd.DataFrame([high]),pd.DataFrame([scan])).iloc[0]
    assert verified.precision_verified
    missing=_mark_convergence(pd.DataFrame([high]),pd.DataFrame([dict(device_type='louver',param_label='test')])).iloc[0]
    assert not missing.precision_verified


def test_schedule_exact_hours_days_and_cache_config(tmp_path):
    use,count=write_teaching_schedule(tmp_path/'schedule.csv',{})
    data=pd.read_csv(tmp_path/'schedule.csv')
    assert count==200
    assert data.lighting_fraction.sum()/6==pytest.approx(200*7.5)
    assert data.hvac_fraction.sum()/6==pytest.approx(200*6.)
    b,_=_building(); s=b.get_space('bp_space_2')
    first=_key(s,'radiance',{})
    config=settings(); config.workplane_height_mm=800; configure(config)
    assert _key(s,'radiance',{})!=first
    first=_key(s,'energyplus',{})
    config.profile={'lighting_hours_per_day':8}; configure(config)
    assert _key(s,'energyplus',{})!=first


def test_invalid_profile_and_no_finite_infinity():
    for update in ({'cooling_cop':0},{'heating_cop':float('nan')}, {'comfort_low_c':30,'comfort_high_c':26}):
        with pytest.raises(ValueError): profile_from_mapping(update)


def test_three_normal_scenes_and_energyplus_export(tmp_path):
    from core.research_energy import build_energyplus_model
    from core.complex_models import BuildingModel, StoreyModel
    b,_=_building(); base=b.get_space('bp_space_2')
    cases=[{'device_type':'horizontal_overhang','L_mm':300,'gap_mm':0,'tilt_deg':90},
           {'device_type':'louver','louver_width_mm':100,'louver_spacing_mm':150,'louver_angle_deg':0},
           {'device_type':'light_shelf','shelf_inside_mm':0,'shelf_outside_mm':300,'shelf_tilt_deg':0,'shelf_upper_ratio':.35}]
    for i,row in enumerate(cases):
        row['material']='普通/氧化铝板'
        space=build_solution_space(base,row)
        assert any(s.kind=='shade' for s in space_scene(space).surfaces)
        model=build_energyplus_model(BuildingModel(storeys=[StoreyModel(spaces=[space])]),
            [space.id],tmp_path/str(i),profile={'comfort_low_c':19,'comfort_high_c':27},workplane_height_mm=800)
        text=model.read_text(encoding='utf-8')
        assert 'Shading:Zone:Detailed' in text and 'TeachingHVAC' in text
        manifest=json.loads((model.parent/'energyplus_model_manifest.json').read_text(encoding='utf-8'))
        assert manifest['workplane_height_mm']==800
        assert manifest['profile']['comfort_low_c']==19


def test_zero_and_extreme_geometry(tmp_path):
    b,w=_building(); base=b.get_space('bp_space_2')
    assert build_solution_space(base,dict(device_type='louver',louver_width_mm=0,
        louver_spacing_mm=100,material='普通/氧化铝板')).shading.enabled_components()==[]
    for row in (dict(device_type='louver',louver_spacing_mm=0),
        dict(device_type='horizontal_overhang',L_mm=500,tilt_deg=0),
        dict(device_type='light_shelf',shelf_outside_mm=300,shelf_tilt_deg=90)):
        with pytest.raises(ValueError): build_solution_space(base,row)
    with patch('core.complex_experiments.compute_complex_daylight',_fake_light),patch('core.complex_experiments.compute_complex_thermal',_fake_heat):
        frame=run_multi_space_experiments([base],weather=w,device_types=['louver'],
            louver_params={'angles':[0],'widths':[0],'spacings':[100]},materials=['普通/氧化铝板'],enable_combinations=False)
        assert frame.construction_cost.max()==0

