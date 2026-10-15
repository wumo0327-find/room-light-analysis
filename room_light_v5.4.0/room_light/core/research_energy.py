"""Auditable EnergyPlus model/export and optional annual simulation adapter.

The fast monthly model remains useful for screening.  This module is the V5
research path: it writes an hourly teaching calendar and a native EnergyPlus
IDF, and only reports annual results when the official executable completes.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import csv
import json
import math
import os
from pathlib import Path
import re
import subprocess

import numpy as np
import pandas as pd
from shapely.geometry import Polygon

from core.operation import BuildingUseProfile, profile_from_mapping
from core.research_scene import device_faces, pieces


@dataclass
class EnergyPlusResult:
    annual_heating_kwh: float
    annual_cooling_kwh: float
    annual_lighting_kwh: float
    annual_total_kwh: float
    occupied_overheating_degree_hours: float
    occupied_underheating_degree_hours: float
    engine_version: str
    run_directory: str
    annual_heating_load_kwh: float = 0.0
    annual_cooling_load_kwh: float = 0.0


def _safe(value: object) -> str:
    text = re.sub(r'[^A-Za-z0-9_]+', '_', str(value)).strip('_')
    return text or 'item'


def _idf_object(kind: str, fields: list[object]) -> str:
    lines = [f'{kind},']
    for index, field in enumerate(fields):
        suffix = ';' if index == len(fields) - 1 else ','
        lines.append(f'  {field}{suffix}')
    return '\n'.join(lines) + '\n'


def _surface(name, surface_type, construction, zone, boundary, sun, wind, vertices):
    fields = [name, surface_type, construction, zone, '', boundary, '', sun, wind,
              'Autocalculate', len(vertices)]
    for x, y, z in vertices:
        fields.extend([f'{x:.8g}', f'{y:.8g}', f'{z:.8g}'])
    return _idf_object('BuildingSurface:Detailed', fields)


def _window(name, construction, host, vertices):
    fields = [name, 'Window', construction, host, '', 'Autocalculate', '', 1,
              len(vertices)]
    for x, y, z in vertices:
        fields.extend([f'{x:.8g}', f'{y:.8g}', f'{z:.8g}'])
    return _idf_object('FenestrationSurface:Detailed', fields)


def _daily_fraction(target_hours: float) -> np.ndarray:
    """Ten-minute kindergarten profile with fractional bad-weather occupancy."""
    values = np.zeros(144, dtype=float)
    primary = ((7, 0, 9, 10), (10, 10, 12, 10),
               (14, 30, 15, 30), (16, 30, 18, 0))
    optional = ((9, 10, 10, 10), (15, 30, 16, 30))
    for h0, m0, h1, m1 in primary:
        values[(h0 * 60 + m0)//10:(h1 * 60 + m1)//10] = 1.0
    current = values.sum() / 6.0
    target = max(0.0, min(float(target_hours), 11.0))
    if target <= current and current > 0:
        values *= target / current
        return values
    slots = []
    for h0, m0, h1, m1 in optional:
        slots.extend(range((h0*60+m0)//10, (h1*60+m1)//10))
    if slots:
        addition = min(1.0, (target-current)*6.0/len(slots))
        values[slots] = addition
    remaining = target - values.sum()/6.0
    if remaining > 1e-9:
        free = np.where((values == 0) &
                        (np.arange(144) >= 7*6) &
                        (np.arange(144) < 18*6))[0]
        for slot in free[:int(math.ceil(remaining*6))]:
            value = min(1.0, remaining*6)
            values[slot] = value
            remaining -= value/6
            if remaining <= 1e-9:
                break
    return values


def write_teaching_schedule(path: Path, profile: BuildingUseProfile | dict | None):
    use = profile if isinstance(profile, BuildingUseProfile) else profile_from_mapping(profile)
    light_day = _daily_fraction(use.lighting_hours_per_day)
    hvac_day = _daily_fraction(use.hvac_hours_per_day)
    monthly = np.asarray(use.monthly_teaching_days, dtype=float)
    monthly = np.maximum(monthly, 0)
    if monthly.sum() <= 0:
        monthly = np.ones(12)
    monthly *= max(float(use.teaching_days_per_year), 0.0) / monthly.sum()
    counts = np.floor(monthly).astype(int)
    for index in np.argsort(monthly-counts)[::-1][:int(round(monthly.sum()))-counts.sum()]:
        counts[index] += 1
    calendar = [datetime(2025, 1, 1)+timedelta(days=i) for i in range(365)]
    capacities = np.array([sum(d.weekday() < 5 and d.month == m for d in calendar)
                           for m in range(1, 13)])
    desired = int(round(use.teaching_days_per_year))
    eligible = monthly > 0
    if desired > int(capacities[eligible].sum()):
        raise ValueError('教学日超过非假期月份的可用工作日；请调整月教学日分布')
    counts = np.minimum(counts, capacities)
    while counts.sum() < desired:
        available = np.where(eligible & (counts < capacities))[0]
        if not len(available):
            raise ValueError('无法按教学日配置生成作息日历')
        best = available[np.argmax(monthly[available]-counts[available])]
        counts[best] += 1
    teaching_dates = set()
    day = datetime(2025, 1, 1)
    while day.year == 2025:
        month = day.month - 1
        if day.weekday() < 5 and sum(1 for d in teaching_dates if d.month == day.month) < counts[month]:
            teaching_dates.add(day.date())
        day += timedelta(days=1)
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['lighting_fraction', 'hvac_fraction'])
        day = datetime(2025, 1, 1)
        while day.year == 2025:
            active = day.date() in teaching_dates
            for light, hvac in zip(light_day, hvac_day):
                writer.writerow([float(light if active else 0), float(hvac if active else 0)])
            day += timedelta(days=1)
    return use, len(teaching_dates)


def build_energyplus_model(building, space_ids, output_directory, *, profile=None, free_running=False,
                          workplane_height_mm=750):
    """Write an EnergyPlus 25.2 model without fabricating simulation results."""
    out = Path(output_directory).resolve()
    out.mkdir(parents=True, exist_ok=True)
    use, teaching_days = write_teaching_schedule(out/'teaching_schedule.csv', profile)
    chosen = [building.get_space(value) for value in space_ids]
    spaces = [space for space in chosen if space is not None]
    if not spaces:
        raise ValueError('没有可导出的房间')
    location = building.location
    blocks = [
        _idf_object('Version', ['25.2']),
        _idf_object('SimulationControl', ['No','No','No','No','Yes']),
        _idf_object('Building', [_safe(building.name), building.north_angle_deg,
            'Suburbs','0.04','0.4','FullExteriorWithReflections','25','6']),
        _idf_object('Site:Location', [_safe(building.name), location.latitude,
            location.longitude, location.timezone, 50]),
        _idf_object('Timestep', [6]),
        _idf_object('RunPeriod', ['Annual','1','1','','12','31','','Tuesday','Yes','Yes','No','Yes','Yes']),
        _idf_object('GlobalGeometryRules', ['UpperLeftCorner','CounterClockWise','World']),
        _idf_object('ScheduleTypeLimits', ['Fraction','0','1','Continuous']),
        _idf_object('ScheduleTypeLimits', ['Temperature','-60','200','Continuous','Temperature']),
        _idf_object('ScheduleTypeLimits', ['AnyNumber']),
        _idf_object('Schedule:Constant', ['AlwaysOn','Fraction','1']),
        _idf_object('Schedule:Constant', ['AlwaysOff','Fraction','0']),
        _idf_object('Schedule:Constant', ['DualControl','AnyNumber','4']),
        _idf_object('Schedule:Constant', ['Activity65','AnyNumber','65']),
        _idf_object('Schedule:Constant', ['HeatingOccupied','Temperature',use.comfort_low_c]),
        _idf_object('Schedule:Constant', ['CoolingOccupied','Temperature',use.comfort_high_c]),
        _idf_object('Schedule:File', ['TeachingLighting','Fraction','teaching_schedule.csv','1','1','8760','Comma','No','10']),
        _idf_object('Schedule:File', ['TeachingHVAC','Fraction','teaching_schedule.csv','2','1','8760','Comma','No','10']),
        _idf_object('Material:NoMass', ['WallMat','MediumRough', '0.6666667','0.9','0.65','0.65']),
        _idf_object('Material:NoMass', ['RoofMat','MediumRough', '1.0','0.9','0.7','0.7']),
        _idf_object('Material:NoMass', ['FloorMat','MediumRough', '0.6666667','0.9','0.2','0.2']),
        _idf_object('Construction', ['WallConstruction','WallMat']),
        _idf_object('Construction', ['RoofConstruction','RoofMat']),
        _idf_object('Construction', ['FloorConstruction','FloorMat']),
    ]
    shade_index = 0
    for zone_index, space in enumerate(spaces, 1):
        shading = space.shading
        if (shading.has_component('light_shelf') and shading.light_shelf_inside_depth_mm > 0
                or shading.has_component('louver') and shading.louver_position != 'exterior'):
            raise ValueError('当前EnergyPlus适配器尚未支持室内反光板/室内或中置百叶的光热耦合。'
                             '请勿将其按室外遮阳替代；本次计算已停止，未生成近似结果。')
        zone = f'Zone_{zone_index}_{_safe(space.id)}'
        thermal = space.thermal
        # Each space may have its own effective U-values and glazing system.
        for tag, u_value, absorptance in (
            ('Wall', thermal.U_wall, thermal.wall_solar_abs),
            ('Roof', thermal.U_roof, thermal.wall_solar_abs),
            ('Floor', thermal.U_floor, thermal.wall_solar_abs),
        ):
            mat = f'{tag}Mat_{zone_index}'
            con = f'{tag}Con_{zone_index}'
            resistance = max(0.01, 1.0/max(float(u_value), 0.05) - 0.17)
            thickness = max(float(thermal.wall_thickness_mm)/1000, .01)
            # Effective homogeneous assembly: preserve both declared U and heat
            # capacity. It is not a substitute for measured multilayer construction.
            blocks.append(_idf_object('Material', [mat,'MediumRough',thickness,
                thickness/resistance, thermal.wall_density, thermal.wall_specific_c,
                '0.9',absorptance,absorptance]))
            blocks.append(_idf_object('Construction', [con,mat]))
        from core.space_geometry import space_floor_area_mm2
        net_area = space_floor_area_mm2(space)/1e6
        # Keep existing host/reveal modelling, but do not apply W/m² gains and
        # air-changes to gross axis-floor area while billing net usable area.
        blocks.append(_idf_object('Zone', [zone,0,0,0,0,1,1,space.height_mm/1000,
            net_area*space.height_mm/1000,net_area,'TARP','DOE-2','Yes']))
        floor_z = space.floor_elevation_mm/1000
        height = space.height_mm/1000
        for wall_index, wall in enumerate(space.wall_segments(), 1):
            dx, dy = wall.direction
            ox, oy = wall.outward_normal
            start = (wall.start.x/1000, wall.start.y/1000)
            def point(u, z, offset=0):
                return (start[0]+dx*u+ox*offset, start[1]+dy*u+oy*offset, floor_z+z)
            wall_name = f'{zone}_Wall_{wall_index}'
            verts = [point(0,0), point(wall.length_mm/1000,0),
                     point(wall.length_mm/1000,height), point(0,height)]
            exterior = wall.boundary_type == 'exterior'
            blocks.append(_surface(wall_name,'Wall',f'WallCon_{zone_index}',zone,
                'Outdoors' if exterior else 'Adiabatic',
                'SunExposed' if exterior else 'NoSun',
                'WindExposed' if exterior else 'NoWind',verts))
            for opening_index, opening in enumerate(wall.openings, 1):
                if opening.kind != 'window':
                    continue
                a = opening.offset_mm/1000
                b = opening.end_offset_mm/1000
                host_length = wall.length_mm/1000
                if a < -1e-7 or b > host_length+1e-7:
                    raise ValueError(f'窗洞超出EnergyPlus宿主墙：{opening.id}')
                # Native shadow clipping is unstable for coincident vertices.
                # Only flush edges receive a documented 0.01 mm inward tolerance.
                if abs(a) < 1e-7:
                    a = 1e-5
                if abs(b-host_length) < 1e-7:
                    b = host_length-1e-5
                c = opening.sill_height_mm/1000
                d = opening.head_height_mm/1000
                offset = opening.plane_offset_mm/1000
                glazing = f'Glazing_{zone_index}_{wall_index}_{opening_index}'
                glazing_con = f'WindowCon_{zone_index}_{wall_index}_{opening_index}'
                blocks.append(_idf_object('WindowMaterial:SimpleGlazingSystem', [glazing,
                    opening.u_value or thermal.U_win,
                    opening.solar_heat_gain_coefficient if opening.solar_heat_gain_coefficient is not None else 0.87*thermal.SC_glass,
                    opening.visible_transmittance]))
                blocks.append(_idf_object('Construction', [glazing_con,glazing]))
                # EnergyPlus subsurfaces must be coplanar with their host.  The
                # actual recess is therefore represented by four opaque reveal
                # shading surfaces instead of illegally moving the window.
                blocks.append(_window(f'{zone}_Window_{wall_index}_{opening_index}',
                    glazing_con,wall_name,[point(a,c),point(b,c),
                    point(b,d),point(a,d)]))
                if offset > 1e-9:
                    reveal_edges = [((a,c),(b,c)),((b,c),(b,d)),
                                    ((b,d),(a,d)),((a,d),(a,c))]
                    for reveal_index, ((u0,z0),(u1,z1)) in enumerate(reveal_edges,1):
                        shade_index += 1
                        verts = [point(u0,z0),point(u1,z1),
                                 point(u1,z1,offset),point(u0,z0,offset)]
                        fields = [f'Reveal_{zone_index}_{wall_index}_{opening_index}_{reveal_index}',
                                  wall_name,'',len(verts)]
                        for vertex in verts:
                            fields.extend(vertex)
                        blocks.append(_idf_object('Shading:Zone:Detailed',fields))
                for label, coords, _rho, _spec in device_faces(space,wall,opening):
                    shade_index += 1
                    fields = [f'Shade_{shade_index}', wall_name, '', len(coords)]
                    for u,z,o in coords:
                        fields.extend(point(u,z,o+offset))
                    blocks.append(_idf_object('Shading:Zone:Detailed',fields))
                    blocks.append(_idf_object('ShadingProperty:Reflectance',
                        [f'Shade_{shade_index}',space.shading.solar_reflectance,_rho,0]))
        outer = [(p.x/1000,p.y/1000) for p in space.outer_loop().points()]
        holes = [[(p.x/1000,p.y/1000) for p in loop.points()] for loop in space.hole_loops()]
        polygon = Polygon(outer, holes)
        from shapely.geometry.polygon import orient
        polygon = orient(polygon, sign=1.0)
        for part_index, triangle in enumerate(pieces(polygon), 1):
            blocks.append(_surface(f'{zone}_Floor_{part_index}','Floor',f'FloorCon_{zone_index}',zone,
                'Ground' if space.floor_exposed else 'Adiabatic','NoSun','NoWind',
                [(x,y,floor_z) for x,y in reversed(triangle)]))
            blocks.append(_surface(f'{zone}_Roof_{part_index}','Roof',f'RoofCon_{zone_index}',zone,
                'Outdoors' if space.roof_exposed else 'Adiabatic',
                'SunExposed' if space.roof_exposed else 'NoSun',
                'WindExposed' if space.roof_exposed else 'NoWind',
                [(x,y,floor_z+height) for x,y in triangle]))
        area = space.volume_m3/(height if height else 1)
        reference = Polygon([(p.x/1000,p.y/1000) for p in space.analysis_outer_points()],
            [[(p.x/1000,p.y/1000) for p in ring] for ring in space.analysis_hole_points()]).representative_point()
        blocks.append(_idf_object('Daylighting:ReferencePoint',
            [f'{zone}_Ref',zone,reference.x,reference.y,floor_z+workplane_height_mm/1000]))
        blocks.append(_idf_object('Daylighting:Controls',
            [f'{zone}_Daylight',zone,'SplitFlux','TeachingLighting','ContinuousOff',
             0,0,1,1,'',0,22,'',f'{zone}_Ref',1,300]))
        blocks.extend([
            _idf_object('People',[f'{zone}_People',zone,'TeachingHVAC','People/Area','',
                max(float(thermal.q_people),0)/65.0,'','0.3','','Activity65']),
            _idf_object('Lights',[f'{zone}_Lights',zone,'TeachingLighting','Watts/Area','',
                max(float(use.lighting_power_density_w_m2),0),'','0','0.5','0.2','1','General']),
            _idf_object('ElectricEquipment',[f'{zone}_Equipment',zone,'TeachingHVAC','Watts/Area','',
                max(float(thermal.q_equipment),0),'','0','0','0']),
            _idf_object('ZoneInfiltration:DesignFlowRate',[f'{zone}_Infiltration',zone,'AlwaysOn',
                'AirChanges/Hour','','','',max(float(thermal.n_ach),0)]),
            _idf_object('ThermostatSetpoint:DualSetpoint',[f'{zone}_Setpoints',
                'HeatingOccupied','CoolingOccupied']),
            _idf_object('ZoneControl:Thermostat',[f'{zone}_Thermostat',zone,'DualControl',
                'ThermostatSetpoint:DualSetpoint',f'{zone}_Setpoints']),
            _idf_object('ZoneHVAC:EquipmentConnections',[zone,f'{zone}_EquipmentList',
                f'{zone}_Supply','',f'{zone}_Air',f'{zone}_Return']),
            _idf_object('ZoneHVAC:EquipmentList',[f'{zone}_EquipmentList','SequentialLoad',
                'ZoneHVAC:IdealLoadsAirSystem',f'{zone}_Ideal',1,1]),
            _idf_object('ZoneHVAC:IdealLoadsAirSystem',[f'{zone}_Ideal',
                'AlwaysOff' if free_running else 'TeachingHVAC',f'{zone}_Supply','',
                '',50,13,.015,.009,'NoLimit','','','NoLimit','','','','','None','','None']),
        ])
    blocks.extend([
        _idf_object('Output:Variable',['*','Zone Operative Temperature','Hourly']),
        _idf_object('Output:Variable',['*','Site Outdoor Air Drybulb Temperature','Hourly']),
        _idf_object('Output:Variable',['*','Surface Window Transmitted Solar Radiation Rate','Hourly']),
        _idf_object('Output:Variable',['*','Zone Total Internal Total Heating Rate','Hourly']),
        _idf_object('Output:Variable',['*','Zone Infiltration Sensible Heat Loss Energy','Hourly']),
        _idf_object('Output:Variable',['*','Zone Ideal Loads Supply Air Total Heating Energy','Hourly']),
        _idf_object('Output:Variable',['*','Zone Ideal Loads Supply Air Total Cooling Energy','Hourly']),
        _idf_object('Output:Variable',['*','Zone Lights Electricity Energy','Hourly']),
        _idf_object('Output:SQLite',['SimpleAndTabular']),
        _idf_object('OutputControl:Files',['Yes']),
        _idf_object('OutputControl:Table:Style',['CommaAndHTML','JtoKWH']),
    ])
    model_path = out/'annual_model.idf'
    model_path.write_text('\n'.join(blocks),encoding='utf-8')
    manifest = {
        'software_version':'5.4.0','created_utc':datetime.now(timezone.utc).isoformat(),
        'building_id':building.id,'space_ids':[space.id for space in spaces],
        'teaching_days':teaching_days,'profile':asdict(use),
        'workplane_height_mm': workplane_height_mm,
        'zone_floor_area_basis': '显式净楼面面积；照明W/m²、人员和换气体积均使用净空间',
        'engine':'EnergyPlus 25.2 native IDF',
        'window_edge_tolerance_mm': 0.01,
        'limitations': ['围护结构按U值及厚度/密度/比热建立等效均质层，不是实测多层构造',
            '相邻非外墙按绝热边界；地面Ground温度未另设时采用EnergyPlus默认18℃',
            '全年照明使用EnergyPlus SplitFlux单参考点调光，不是全年Radiance',
            '室内反光板与室内百叶尚未建立EnergyPlus原生光学传热耦合对象'],
        'claim_scope':'仅模型文件；必须配合真实EPW并由EnergyPlus成功执行后才能声称年能耗结果。',
    }
    (out/'energyplus_model_manifest.json').write_text(
        json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    return model_path


def find_energyplus(executable=None):
    candidates = []
    if executable:
        candidates.append(Path(executable))
    bundled = Path(__file__).resolve().parents[2] / 'engines' / 'energyplus'
    if bundled.is_dir():
        candidates.extend(bundled.glob('**/energyplus.exe'))
    env_home = os.environ.get('ENERGYPLUS_HOME')
    if env_home:
        candidates.append(Path(env_home)/'energyplus.exe')
    candidates.extend(Path(f'C:/EnergyPlusV25-2-0/{name}') for name in ('energyplus.exe','EnergyPlus.exe'))
    return next((path.resolve() for path in candidates if path.exists()), None)


def run_energyplus(model_path, epw_path, *, executable=None):
    engine = find_energyplus(executable)
    if engine is None:
        raise FileNotFoundError('未找到官方EnergyPlus 25.2执行程序；模型已生成，但不会伪造年能耗结果。')
    model = Path(model_path).resolve()
    weather = Path(epw_path).resolve()
    if not weather.exists():
        raise FileNotFoundError('EPW气象文件不存在：' + str(weather))
    output = model.parent/'energyplus_output'
    output.mkdir(exist_ok=True)
    options = ({"creationflags": subprocess.CREATE_NO_WINDOW}
               if os.name == "nt" else {})
    from core.engine_process import run_process
    try:
        run_process([str(engine),'-w',str(weather),'-d',str(output),str(model)],
                    cwd=model.parent)
    except RuntimeError as exc:
        error_file = output/'eplusout.err'
        details = error_file.read_text(errors='replace')[-6000:] if error_file.exists() else str(exc)
        raise RuntimeError('EnergyPlus计算失败：\n'+details) from exc
    csv_path = output/'eplusout.csv'
    if not csv_path.exists():
        raise RuntimeError('EnergyPlus完成但未生成eplusout.csv')
    frame = pd.read_csv(csv_path)
    def columns(fragment):
        return [name for name in frame.columns if fragment.lower() in name.lower()]
    def energy(fragment):
        names = columns(fragment)
        if not names or not np.isfinite(frame[names].to_numpy(dtype=float)).all():
            raise ValueError('EnergyPlus必要输出缺失或无效：'+fragment)
        return float(frame[names].sum(numeric_only=True).sum()/3_600_000)
    heating = energy('Total Heating Energy')
    cooling = energy('Total Cooling Energy')
    lighting = energy('Lights Electricity Energy')
    temp_cols = columns('Operative Temperature')
    temperatures = frame[temp_cols].mean(axis=1).to_numpy(dtype=float) if temp_cols else np.array([])
    schedule = pd.read_csv(model.parent/'teaching_schedule.csv')['hvac_fraction'].to_numpy(dtype=float)
    hourly = schedule.reshape(-1,6).mean(axis=1)
    count = min(len(temperatures),len(hourly))
    use = profile_from_mapping(manifest['profile'])
    over = float(np.sum(np.maximum(temperatures[:count]-use.comfort_high_c,0)*hourly[:count]))
    under = float(np.sum(np.maximum(use.comfort_low_c-temperatures[:count],0)*hourly[:count]))
    manifest = json.loads((model.parent/'energyplus_model_manifest.json').read_text(encoding='utf-8'))
    profile = profile_from_mapping(manifest['profile'])
    heat_electric = heating / profile.heating_cop
    cool_electric = cooling / profile.cooling_cop
    result_obj = EnergyPlusResult(heat_electric,cool_electric,lighting,
        heat_electric+cool_electric+lighting,
        over,under,'EnergyPlus 25.2',str(output),heating,cooling)
    (output/'年度结果摘要.json').write_text(json.dumps(asdict(result_obj),ensure_ascii=False,indent=2),encoding='utf-8')
    return result_obj
