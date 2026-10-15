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


def build_energyplus_model(building, space_ids, output_directory, *, profile=None):
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
            'Suburbs','0.04','0.4','FullExterior','25','6']),
        _idf_object('Site:Location', [_safe(building.name), location.latitude,
            location.longitude, location.timezone, 50]),
        _idf_object('Timestep', [6]),
        _idf_object('RunPeriod', ['Annual','1','1','','12','31','','Tuesday','Yes','Yes','No','Yes','Yes']),
        _idf_object('GlobalGeometryRules', ['UpperLeftCorner','CounterClockWise','World']),
        _idf_object('ScheduleTypeLimits', ['Fraction','0','1','Continuous']),
        _idf_object('ScheduleTypeLimits', ['Temperature','-60','200','Continuous','Temperature']),
        _idf_object('ScheduleTypeLimits', ['AnyNumber']),
        _idf_object('Schedule:Constant', ['AlwaysOn','Fraction','1']),
        _idf_object('Schedule:Constant', ['Activity65','AnyNumber','65']),
        _idf_object('Schedule:Constant', ['HeatingOccupied','Temperature','18']),
        _idf_object('Schedule:Constant', ['CoolingOccupied','Temperature','26']),
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
            resistance = 1.0/max(float(u_value), 0.05)
            blocks.append(_idf_object('Material:NoMass', [mat,'MediumRough',resistance,
                '0.9',absorptance,absorptance]))
            blocks.append(_idf_object('Construction', [con,mat]))
        blocks.append(_idf_object('Zone', [zone,0,0,0,0,1,1,'autocalculate',
            'autocalculate','autocalculate','TARP','DOE-2','Yes']))
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
                c = opening.sill_height_mm/1000
                d = opening.head_height_mm/1000
                offset = opening.plane_offset_mm/1000
                glazing = f'Glazing_{zone_index}_{wall_index}_{opening_index}'
                glazing_con = f'WindowCon_{zone_index}_{wall_index}_{opening_index}'
                blocks.append(_idf_object('WindowMaterial:SimpleGlazingSystem', [glazing,
                    opening.u_value or thermal.U_win,
                    opening.solar_heat_gain_coefficient or thermal.SC_glass,
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
                                  '',0.2,len(verts)]
                        for vertex in verts:
                            fields.extend(vertex)
                        blocks.append(_idf_object('Shading:Zone:Detailed',fields))
                for label, coords, _rho, _spec in device_faces(space,wall,opening):
                    shade_index += 1
                    fields = [f'Shade_{shade_index}', '', 0.2, len(coords)]
                    for u,z,o in coords:
                        fields.extend(point(u,z,o+offset))
                    blocks.append(_idf_object('Shading:Zone:Detailed',fields))
        outer = [(p.x/1000,p.y/1000) for p in space.analysis_outer_points()]
        holes = [[(p.x/1000,p.y/1000) for p in loop] for loop in space.analysis_hole_points()]
        polygon = Polygon(outer, holes)
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
        blocks.extend([
            _idf_object('People',[f'{zone}_People',zone,'TeachingHVAC','People/Area','',
                max(float(thermal.q_people),0)/65.0,'','0.3','','Activity65']),
            _idf_object('Lights',[f'{zone}_Lights',zone,'TeachingLighting','Watts/Area','',
                max(float(use.lighting_power_density_w_m2),0),'','0','0.5','0.2','1','General']),
            _idf_object('ElectricEquipment',[f'{zone}_Equipment',zone,'TeachingHVAC','Watts/Area','',
                max(float(thermal.q_equipment),0),'','0','0','0']),
            _idf_object('ZoneInfiltration:DesignFlowRate',[f'{zone}_Infiltration',zone,'AlwaysOn',
                'AirChanges/Hour','','','',max(float(thermal.n_ach),0)]),
            _idf_object('HVACTemplate:Thermostat',[f'{zone}_Thermostat','HeatingOccupied','',
                'CoolingOccupied','']),
            _idf_object('HVACTemplate:Zone:IdealLoadsAirSystem',[zone,f'{zone}_Thermostat','TeachingHVAC']),
        ])
    blocks.extend([
        _idf_object('Output:Variable',['*','Zone Operative Temperature','Hourly']),
        _idf_object('Output:Variable',['*','Zone Ideal Loads Supply Air Total Heating Energy','Hourly']),
        _idf_object('Output:Variable',['*','Zone Ideal Loads Supply Air Total Cooling Energy','Hourly']),
        _idf_object('Output:Variable',['*','Zone Lights Electricity Energy','Hourly']),
        _idf_object('Output:SQLite',['SimpleAndTabular']),
        _idf_object('OutputControl:Table:Style',['CommaAndHTML','JtoKWH']),
    ])
    model_path = out/'annual_model.idf'
    model_path.write_text('\n'.join(blocks),encoding='utf-8')
    manifest = {
        'software_version':'5.1.0','created_utc':datetime.now(timezone.utc).isoformat(),
        'building_id':building.id,'space_ids':[space.id for space in spaces],
        'teaching_days':teaching_days,'profile':asdict(use),
        'engine':'EnergyPlus 25.2 native IDF',
        'claim_scope':'仅模型文件；必须配合真实EPW并由EnergyPlus成功执行后才能声称年能耗结果。',
    }
    (out/'energyplus_model_manifest.json').write_text(
        json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    return model_path


def find_energyplus(executable=None):
    candidates = []
    if executable:
        candidates.append(Path(executable))
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
    result = subprocess.run([str(engine),'-w',str(weather),'-d',str(output),str(model)],
        cwd=model.parent,capture_output=True,text=True,check=False)
    if result.returncode:
        raise RuntimeError('EnergyPlus计算失败：\n'+result.stderr[-3000:])
    csv_path = output/'eplusout.csv'
    if not csv_path.exists():
        raise RuntimeError('EnergyPlus完成但未生成eplusout.csv')
    frame = pd.read_csv(csv_path)
    def columns(fragment):
        return [name for name in frame.columns if fragment.lower() in name.lower()]
    def energy(fragment):
        names = columns(fragment)
        return float(frame[names].sum(numeric_only=True).sum()/3_600_000) if names else 0.0
    heating = energy('Total Heating Energy')
    cooling = energy('Total Cooling Energy')
    lighting = energy('Lights Electricity Energy')
    temp_cols = columns('Operative Temperature')
    temperatures = frame[temp_cols].mean(axis=1).to_numpy(dtype=float) if temp_cols else np.array([])
    schedule = pd.read_csv(model.parent/'teaching_schedule.csv')['hvac_fraction'].to_numpy(dtype=float)
    hourly = schedule.reshape(-1,6).mean(axis=1)
    count = min(len(temperatures),len(hourly))
    over = float(np.sum(np.maximum(temperatures[:count]-26,0)*hourly[:count]))
    under = float(np.sum(np.maximum(18-temperatures[:count],0)*hourly[:count]))
    result_obj = EnergyPlusResult(heating,cooling,lighting,heating+cooling+lighting,
        over,under,'EnergyPlus 25.2',str(output))
    (output/'年度结果摘要.json').write_text(json.dumps(asdict(result_obj),ensure_ascii=False,indent=2),encoding='utf-8')
    return result_obj
