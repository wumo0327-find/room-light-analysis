"""V5.4 production analysis. No fallback to the legacy screening formulae.

Both GUI analysis and every experiment candidate call these adapters. Cache keys
cover geometry, material, weather, occupancy, engine settings and adapter schema.
"""
from dataclasses import asdict, dataclass, field
from pathlib import Path
from copy import deepcopy
import hashlib
import json
import os

import numpy as np

from core.complex_daylight import ComplexDaylightResult, _fill_stats, _space_rho_bar
from core.thermal import ThermalResult
from core.operation import profile_from_mapping
from core.complex_models import BuildingModel, StoreyModel


@dataclass
class EngineSettings:
    epw_path: str = ''
    energyplus_path: str = ''
    ambient_bounces: int = 5
    ambient_divisions: int = 4096
    grid_mm: float = 250.0
    workplane_height_mm: float = 750.0
    north_angle_deg: float = 0.0
    profile: dict = field(default_factory=dict)
    cache_directory: str = str(Path(__file__).resolve().parents[1] / 'engine_results')


_settings = EngineSettings()


def configure(settings):
    global _settings
    _settings = deepcopy(settings)


def settings():
    return deepcopy(_settings)


def validate_settings(value):
    from core.research_energy import find_energyplus
    from core.research_scene import space_scene  # dependency check before work starts
    if find_energyplus(value.energyplus_path or None) is None:
        raise ValueError('请在计算设置中选择 EnergyPlus 25.2 的 energyplus.exe。')
    path = Path(value.epw_path)
    if not path.is_file() or path.suffix.lower() != '.epw':
        raise ValueError('请选择项目所在地的逐时 EPW 气象文件；月均气象不能用于逐时计算。')
    with path.open(encoding='utf-8-sig', errors='replace') as stream:
        if not stream.readline().startswith('LOCATION,'):
            raise ValueError('EPW 文件头无效。')
    if (value.ambient_bounces < 1 or value.ambient_divisions < 64 or value.grid_mm <= 0
            or not np.isfinite(value.workplane_height_mm) or value.workplane_height_mm <= 0):
        raise ValueError('计算精度参数无效。')


def _key(space, category, extra):
    from core.research_energy import find_energyplus
    source_root = Path(__file__).parent
    if category == 'energyplus':
        dependencies = [source_root / name for name in (
            'engine_service.py', 'research_energy.py', 'research_scene.py', 'operation.py',
            'experiment_contract.py', 'space_geometry.py',
        )]
        executable = find_energyplus(_settings.energyplus_path or None)
    else:
        dependencies = [source_root / name for name in (
            'engine_service.py', 'research_radiance.py', 'research_scene.py', 'complex_daylight.py',
            'experiment_contract.py', 'space_geometry.py', 'operation.py',
        )]
        executable = source_root.parents[1]/'engines'/'radiance'/'bin'/'rtrace.exe'
    if executable:
        dependencies.append(executable)
    signatures = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in dependencies}
    if category == 'radiance':
        numerical = {
            'grid_mm': _settings.grid_mm,
            'ambient_bounces': _settings.ambient_bounces,
            'ambient_divisions': _settings.ambient_divisions,
            'workplane_height_mm': _settings.workplane_height_mm,
            'profile': profile_from_mapping(_settings.profile).to_dict(),
            'north_angle_deg': _settings.north_angle_deg,
            'radiance_processes': max(1,min(8,(os.cpu_count() or 2)-1)),
        }
    else:
        # Radiance refinement must never invalidate a completed annual thermal
        # simulation. This is the main speed gain of the staged workflow.
        numerical = {
            'epw_path': str(Path(_settings.epw_path).resolve()),
            'north_angle_deg': _settings.north_angle_deg,
            'profile': _settings.profile,
            'workplane_height_mm': _settings.workplane_height_mm,
        }
    payload = {'schema': '5.4.0-common-baseline-1', 'space': asdict(space),
               'settings': numerical, 'category': category, 'extra': extra,
               'dependencies': signatures}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=True,
                                    allow_nan=False).encode()).hexdigest()


def compute_complex_daylight(space, E_out=15000., grid_mm=None, ndiv=40,
                             store_components=True, row_cb=None, ra_threshold=2.):
    from core.research_radiance import run_radiance_daylight
    grid = float(_settings.grid_mm if grid_mm is None else grid_mm)
    if not np.isfinite(E_out) or E_out <= 0:
        raise ValueError('室外参考照度必须是有限正数')
    if not 0 < _settings.workplane_height_mm < space.height_mm:
        raise ValueError('工作面高度必须位于房间内部')
    epw = Path(_settings.epw_path)
    weather_signature = hashlib.sha256(epw.read_bytes()).hexdigest() if epw.is_file() else ''
    key = _key(space, 'radiance', {'grid': grid, 'E_out': float(E_out),
        'DF_threshold': float(ra_threshold), 'epw_sha256': weather_signature})
    folder = Path(_settings.cache_directory) / key / 'radiance'
    archive = folder / '完整网格结果.npz'
    manifest = folder / '计算溯源.json'
    if not (archive.is_file() and manifest.is_file()):
        run_radiance_daylight(space, folder, grid_mm=grid,
                             ambient_bounces=_settings.ambient_bounces,
                             ambient_divisions=_settings.ambient_divisions,
                             workplane_height_mm=_settings.workplane_height_mm)
    with np.load(archive, allow_pickle=False) as data:
        result = ComplexDaylightResult()
        result.grid_x = data['grid_x']; result.grid_y = data['grid_y']
        result.valid_mask = data['valid_mask']; result.DF = data['daylight_factor']
        result.area_weights = data['area_weights']
        result.sensor_x_mm = data['sensor_x_mm']; result.sensor_y_mm = data['sensor_y_mm']
    result.E_out = float(E_out)
    result.E_lux = result.DF * result.E_out / 100.
    if not np.isfinite(result.E_lux[result.valid_mask]).all():
        raise ValueError('Radiance 有效测点包含非有限数值，停止统计。')
    result.grid_mm = grid; result.ndiv = _settings.ambient_divisions
    result.rho_bar = _space_rho_bar(space); result.Ra_threshold = ra_threshold
    result.method = f'Radiance rtrace｜ab={_settings.ambient_bounces} ad={_settings.ambient_divisions}｜CIE 全阴天'
    result.quick = {}; result.engine = 'Radiance'; result.run_directory = str(folder)
    from core.space_geometry import space_floor_area_mm2
    result.net_floor_area_m2 = space_floor_area_mm2(space) / 1e6
    result.geometry_basis = '物理墙内边界'
    result.window_count = sum(len(w.windows()) for w in space.wall_segments())
    from core.experiment_contract import weighted_daylight_stats, sensor_signature, model_fingerprints, digest
    result.__dict__.update(weighted_daylight_stats(result.DF, result.E_lux,
        result.valid_mask, result.area_weights, ra_threshold))
    result.compliant_300 = result.E_avg >= 300.0
    result.compliant_u0 = result.U0 >= .70
    result.sensor_count = int(result.valid_mask.sum())
    result.zero_illuminance_count = int(np.sum(result.E_lux[result.valid_mask] == 0))
    result.sensor_fingerprint = sensor_signature(result.sensor_x_mm, result.sensor_y_mm,
        result.area_weights, result.valid_mask, _settings.workplane_height_mm)
    result.workplane_height_mm = _settings.workplane_height_mm
    result.ambient_bounces = _settings.ambient_bounces
    result.fingerprints = model_fingerprints(space)
    result.component_config_json = json.dumps(asdict(space.shading), ensure_ascii=False, sort_keys=True, allow_nan=False)
    inputs = asdict(_settings)
    inputs.pop('cache_directory', None)
    configuration={'numerical': inputs, 'E_out': float(E_out),
        'DF_threshold': float(ra_threshold), 'epw_sha256': weather_signature}
    result.calculation_config_fingerprint = digest(configuration)
    result.calculation_config_json = json.dumps(configuration,ensure_ascii=False,sort_keys=True,allow_nan=False)
    if row_cb:
        row_cb(len(result.grid_y)-1)
    return result


def compute_complex_thermal(space, monthly_ghi=None, monthly_temp=None, *,
                            latitude_deg=28.2, north_angle_deg=0., progress_cb=None):
    from core.research_energy import build_energyplus_model, run_energyplus
    validate_settings(_settings)
    epw = Path(_settings.epw_path)
    weather_hash = hashlib.sha256(epw.read_bytes()).hexdigest()
    key = _key(space, 'energyplus', {'epw_sha256': weather_hash})
    folder = Path(_settings.cache_directory) / key / 'energyplus'
    building = BuildingModel(storeys=[StoreyModel(spaces=[deepcopy(space)])],
                             north_angle_deg=_settings.north_angle_deg)
    outputs = {}
    # Separate free-running comfort from the HVAC-controlled annual loads.
    for mode in ('free', 'conditioned'):
        out = folder / mode
        output = out / 'energyplus_output' / 'eplusout.csv'
        success = out / 'energyplus_output' / '年度结果摘要.json'
        if not (output.is_file() and success.is_file()):
            model = build_energyplus_model(building, [space.id], out,
                profile=_settings.profile, free_running=mode == 'free',
                workplane_height_mm=_settings.workplane_height_mm)
            run_energyplus(model, epw, executable=_settings.energyplus_path or None)
        import pandas as pd
        outputs[mode] = pd.read_csv(output)
    frame = outputs['free']
    def column(table, fragment):
        cols = [c for c in table.columns if fragment.lower() in c.lower()]
        if not cols:
            raise ValueError('EnergyPlus 缺少必要输出：' + fragment)
        values = table[cols].to_numpy(dtype=float)
        if not np.isfinite(values).all():
            raise ValueError('EnergyPlus 输出包含无效数据：' + fragment)
        return values.sum(axis=1)
    temp = column(frame, 'Zone Operative Temperature')
    if len(temp) != 8760:
        raise ValueError(f'EnergyPlus 年结果应为8760小时，实际{len(temp)}行。')
    import pandas as pd
    schedule = pd.read_csv(folder/'free'/'teaching_schedule.csv')
    occupancy = schedule.hvac_fraction.to_numpy().reshape(-1, 6).mean(axis=1)
    months = pd.date_range('2025-01-01', periods=8760, freq='h').month.to_numpy()
    def monthly(values):
        return np.array([np.mean(values[months == month]) for month in range(1, 13)])
    result = ThermalResult()
    result.T_in = monthly(temp)
    result.T_out = monthly(column(frame, 'Site Outdoor Air Drybulb Temperature'))
    result.T_in_annual_avg = float(np.mean(temp))
    result.hourly_temperature = temp
    profile = profile_from_mapping(_settings.profile)
    result.overheat_degree_hours = float(np.sum(np.maximum(temp-profile.comfort_high_c, 0)*occupancy))
    result.underheat_degree_hours = float(np.sum(np.maximum(profile.comfort_low_c-temp, 0)*occupancy))
    result.thermal_discomfort = result.overheat_degree_hours + result.underheat_degree_hours
    # Keep legacy fields only for old exporters; all V5.4 UI units are degree-hours.
    result.overheat_degree_months = result.overheat_degree_hours
    result.underheat_degree_months = result.underheat_degree_hours
    result.comfort_months = int(np.sum((result.T_in >= profile.comfort_low_c)&(result.T_in <= profile.comfort_high_c)))
    result.overheat_months = int(np.sum(result.T_in > profile.comfort_high_c))
    result.underheat_months = int(np.sum(result.T_in < profile.comfort_low_c))
    result.Q_solar = monthly(column(frame, 'Surface Window Transmitted Solar Radiation Rate'))
    result.Q_int = monthly(column(frame, 'Zone Total Internal Total Heating Rate'))
    result.Q_vent = monthly(column(frame, 'Zone Infiltration Sensible Heat Loss Energy')) / 3600.
    result.Q_wall_solar = np.ma.masked_all(12)
    result.unavailable_flux_components = ['Q_wall_solar: EnergyPlus未输出独立外墙太阳得热分项']
    conditioned = outputs['conditioned']
    profile = profile_from_mapping(_settings.profile)
    result.annual_heating_load_kwh = float(column(conditioned, 'Total Heating Energy').sum()/3.6e6)
    result.annual_cooling_load_kwh = float(column(conditioned, 'Total Cooling Energy').sum()/3.6e6)
    result.annual_heating_kwh = result.annual_heating_load_kwh/profile.heating_cop
    result.annual_cooling_kwh = result.annual_cooling_load_kwh/profile.cooling_cop
    result.annual_lighting_kwh = float(column(conditioned, 'Lights Electricity Energy').sum()/3.6e6)
    result.engine = 'EnergyPlus'; result.run_directory = str(folder)
    result.method = 'EnergyPlus 25.2｜8760h｜自由运行舒适度+空调负荷/COP电耗｜EPW'
    result.epw_sha256 = weather_hash
    if progress_cb:
        progress_cb(11)
    return result
