"""Point-in-time CIE overcast research calculation using official Radiance."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import csv
import json
import math
import os
from pathlib import Path
import subprocess

import numpy as np
from matplotlib.figure import Figure

from core.complex_daylight import build_workplane_grid, WORK_PLANE_MM
from core.research_scene import space_scene


@dataclass
class RadianceResult:
    grid_x: np.ndarray
    grid_y: np.ndarray
    valid_mask: np.ndarray
    illuminance_lux: np.ndarray
    daylight_factor: np.ndarray
    outdoor_horizontal_lux: float
    average_lux: float
    minimum_lux: float
    uniformity_u0: float
    ra_300: float
    engine: str
    method: str
    scene_fingerprint: str
    run_directory: str


def _transmissivity(transmittance):
    t = float(np.clip(transmittance, 1e-6, .999))
    return (math.sqrt(.8402528435 + .0072522239*t*t) - .9166530661) / (.0036261119*t)


def _modifier(surface, index):
    name = f'mat_{index}'
    if surface.transmittance > 0:
        tv = _transmissivity(surface.transmittance)
        return name, f'void glass {name}\n0\n0\n3 {tv:.7g} {tv:.7g} {tv:.7g}\n'
    spec = float(np.clip(surface.specularity, 0, .95))
    rho = float(np.clip(surface.reflectance, 0, .95))
    return name, f'void plastic {name}\n0\n0\n5 {rho:.7g} {rho:.7g} {rho:.7g} {spec:.7g} 0\n'


def _scene_rad(scene):
    blocks = []
    for index, surface in enumerate(scene.surfaces):
        modifier, definition = _modifier(surface, index)
        coords = ' '.join(f'{value:.8g}' for point in surface.vertices for value in point)
        blocks.append(definition + f'{modifier} polygon s_{index}\n0\n0\n{len(surface.vertices)*3} {coords}\n')
    return '\n'.join(blocks)


def _radiance_env(bin_dir):
    env = os.environ.copy()
    lib_dir = Path(bin_dir).parent / 'lib'
    current = env.get('RAYPATH', '')
    env['RAYPATH'] = os.pathsep.join(part for part in ('.', str(lib_dir), current) if part)
    return env


def _run(command, *, cwd, input_text=None, env=None):
    result = subprocess.run(command, cwd=cwd, input=input_text, text=True,
                            capture_output=True, check=False, env=env)
    if result.returncode:
        raise RuntimeError(f"Radiance命令失败 ({Path(command[0]).name}):\n{result.stderr[-2000:]}")
    return result.stdout


def _rgb_lux(text):
    rows = []
    for line in text.splitlines():
        values = line.split()
        if len(values) >= 3:
            r,g,b = map(float, values[:3])
            rows.append(47.4*r + 119.9*g + 11.6*b)
    return np.asarray(rows, dtype=float)


def run_radiance_daylight(space, output_directory, *, grid_mm=150, ambient_bounces=5,
                          ambient_divisions=4096, progress_cb=None, radiance_bin=None):
    """Run an auditable Radiance calculation and export the complete evidence package."""
    out = Path(output_directory).resolve()
    out.mkdir(parents=True, exist_ok=True)
    if radiance_bin is None:
        radiance_bin = Path(__file__).resolve().parents[2] / 'engines' / 'radiance' / 'bin'
    bin_dir = Path(radiance_bin)
    executables = {name: bin_dir / f'{name}.exe' for name in ('gensky','oconv','rtrace')}
    missing = [str(path) for path in executables.values() if not path.exists()]
    if missing:
        raise FileNotFoundError('Radiance未完整安装：' + '、'.join(missing))
    process_env = _radiance_env(bin_dir)
    if progress_cb: progress_cb(1, 5)
    scene = space_scene(space, include_devices=True)
    (out/'模型几何.rad').write_text(_scene_rad(scene), encoding='utf-8')
    sky_body = _run([str(executables['gensky']),'6','21','12','-c'], cwd=out, env=process_env)
    sky = sky_body + '''\nskyfunc glow sky_glow\n0\n0\n4 1 1 1 0\nsky_glow source sky\n0\n0\n4 0 0 1 180\nskyfunc glow ground_glow\n0\n0\n4 1 1 1 0\nground_glow source ground\n0\n0\n4 0 0 -1 180\n'''
    (out/'标准全阴天.rad').write_text(sky, encoding='utf-8')
    if progress_cb: progress_cb(2, 5)
    # oconv writes a binary octree, so write stdout directly to a binary file.
    with (out/'场景.oct').open('wb') as stream:
        result = subprocess.run([str(executables['oconv']),'标准全阴天.rad','模型几何.rad'],
            cwd=out,stdout=stream,stderr=subprocess.PIPE,check=False,env=process_env)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors='replace'))
    with (out/'天空.oct').open('wb') as stream:
        result = subprocess.run([str(executables['oconv']),'标准全阴天.rad'],cwd=out,
            stdout=stream,stderr=subprocess.PIPE,check=False,env=process_env)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors='replace'))
    xs,ys,mask=build_workplane_grid(space,float(grid_mm))
    points=[(x/1000,y/1000,space.floor_elevation_mm/1000+WORK_PLANE_MM/1000)
            for iy,y in enumerate(ys) for x,valid in zip(xs,mask[iy]) if valid]
    sensors=''.join(f'{x:.8g} {y:.8g} {z:.8g} 0 0 1\n' for x,y,z in points)
    (out/'测点.pts').write_text(sensors,encoding='ascii')
    if progress_cb: progress_cb(3, 5)
    args=[str(executables['rtrace']),'-I+','-h','-ab',str(ambient_bounces),'-ad',str(ambient_divisions),
          '-as',str(max(ambient_divisions//2,1)),'-aa','0.08','-lw','1e-6','场景.oct']
    indoor=_rgb_lux(_run(args,cwd=out,input_text=sensors,env=process_env))
    outdoor=_rgb_lux(_run([str(executables['rtrace']),'-I+','-h','-ab','1','-ad','4096',
        '-aa','.02','天空.oct'],cwd=out,input_text='0 0 1 0 0 1\n',env=process_env))
    if indoor.size != len(points) or outdoor.size != 1 or outdoor[0] <= 0:
        raise RuntimeError('Radiance返回的测点数量或室外参考照度异常')
    scale=10000.0/outdoor[0]
    indoor*=scale
    outdoor_lux=10000.0
    df=indoor/outdoor_lux*100
    illuminance=np.full(mask.shape,np.nan); daylight=np.full(mask.shape,np.nan)
    illuminance[mask]=indoor; daylight[mask]=df
    avg=float(np.mean(indoor)); minimum=float(np.min(indoor))
    result_obj=RadianceResult(xs,ys,mask,illuminance,daylight,outdoor_lux,avg,minimum,
        minimum/avg if avg else 0.,float(np.mean(indoor>=300)),
        'LBNL Radiance 6.0.2 official binary',
        f'CIE overcast, rtrace irradiance, ab={ambient_bounces}, ad={ambient_divisions}',
        scene.fingerprint(),str(out))
    if progress_cb: progress_cb(4, 5)
    with (out/'逐点结果.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.writer(stream); writer.writerow(['X_m','Y_m','Z_m','照度_lux','采光系数_DF_percent'])
        for point,lux,value in zip(points,indoor,df): writer.writerow([*point,lux,value])
    manifest={k:v for k,v in asdict(result_obj).items() if not isinstance(v,np.ndarray)}
    manifest.update({'software_version':'5.1.0','timestamp_utc':datetime.now(timezone.utc).isoformat(),
        'grid_mm':grid_mm,'sensor_count':len(points),'warnings':scene.warnings,
        'claim_scope':'Radiance点时标准全阴天结果；不等于全年sDA/UDI/ASE或DGP。'})
    (out/'计算溯源.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    np.savez_compressed(out/'完整网格结果.npz',grid_x=xs,grid_y=ys,valid_mask=mask,
        illuminance_lux=illuminance,daylight_factor=daylight)
    figure = Figure(figsize=(11,5.2),dpi=150,layout='constrained')
    for axis, values, title, label in (
        (figure.add_subplot(1,2,1),illuminance,'Illuminance','lux'),
        (figure.add_subplot(1,2,2),daylight,'Daylight factor','%'),
    ):
        plot = axis.pcolormesh(xs/1000,ys/1000,np.ma.masked_invalid(values),
                              shading='nearest',cmap='viridis')
        outline = space.analysis_outer_points()
        if outline:
            px = [point.x/1000 for point in outline]+[outline[0].x/1000]
            py = [point.y/1000 for point in outline]+[outline[0].y/1000]
            axis.plot(px,py,color='#1f2937',linewidth=1.1)
        axis.set_aspect('equal',adjustable='box'); axis.set_title(title)
        axis.set_xlabel('X / m'); axis.set_ylabel('Y / m')
        figure.colorbar(plot,ax=axis,label=label,shrink=.82)
    figure.suptitle('Radiance CIE overcast | '
                    f'Eavg={avg:.1f} lux, U0={result_obj.uniformity_u0:.3f}, '
                    f'Ra300={result_obj.ra_300:.3f}')
    figure.savefig(out/'Radiance采光精算图.png',bbox_inches='tight')
    if progress_cb: progress_cb(5, 5)
    return result_obj
