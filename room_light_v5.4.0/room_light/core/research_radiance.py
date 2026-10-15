"""Point-in-time CIE overcast research calculation using official Radiance."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
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


def _subprocess_options():
    # Prevent console-window flashes on Windows.  More importantly, keep the
    # child process detached from accidental console-close events.
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def _run(command, *, cwd, input_text=None, env=None):
    from core.engine_process import run_process
    return run_process(command, cwd=cwd, input_text=input_text, env=env).decode('utf-8', errors='replace')


def _rgb_lux(text):
    rows = []
    for line in text.splitlines():
        values = line.split()
        if len(values) >= 3:
            r,g,b = map(float, values[:3])
            rows.append(47.4*r + 119.9*g + 11.6*b)
    return np.asarray(rows, dtype=float)


def _parallel_rtrace(command, sensor_lines, *, cwd, env, workers):
    """Run deterministic, ordered rtrace chunks on Windows CPU cores.

    The bundled Windows binary exposes ``-n`` but does not actually fan out
    worker processes.  Independent sensor rays are therefore divided into
    stable contiguous chunks.  Every child still runs the official rtrace
    executable, and results are concatenated in the original sensor order.
    Context copying preserves the GUI pause/cancel checkpoint in worker
    threads.
    """
    count = len(sensor_lines)
    if count == 0:
        return ""
    worker_count = max(1, min(int(workers), count))
    if worker_count == 1:
        return _run(command, cwd=cwd, input_text="".join(sensor_lines), env=env)
    boundaries = np.linspace(0, count, worker_count + 1, dtype=int)
    chunks = ["".join(sensor_lines[boundaries[i]:boundaries[i + 1]])
              for i in range(worker_count)]

    def submit_one(context, text):
        return context.run(_run, command, cwd=cwd, input_text=text, env=env)

    contexts = [copy_context() for _text in chunks]
    with ThreadPoolExecutor(max_workers=worker_count,
                            thread_name_prefix="radiance-rtrace") as pool:
        futures = [
            pool.submit(submit_one, context, text)
            for context, text in zip(contexts, chunks)
        ]
        # Iterating in submission order is deliberate: the output row order
        # remains identical to the workplane point order.
        return "".join(future.result() for future in futures)


def run_radiance_daylight(space, output_directory, *, grid_mm=250, ambient_bounces=5,
                          ambient_divisions=4096, progress_cb=None, radiance_bin=None,
                          workplane_height_mm=750):
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
    from core.engine_process import run_process
    (out/'场景.oct').write_bytes(run_process(
        [str(executables['oconv']),'标准全阴天.rad','模型几何.rad'],cwd=out,env=process_env))
    (out/'天空.oct').write_bytes(run_process(
        [str(executables['oconv']),'标准全阴天.rad'],cwd=out,env=process_env))
    from core.experiment_contract import fixed_sampling, sensor_signature
    xs,ys,mask,X,Y,weights=fixed_sampling(space,float(grid_mm))
    points=[(x/1000,y/1000,space.floor_elevation_mm/1000+workplane_height_mm/1000)
            for x,y in zip(X[mask],Y[mask])]
    sensor_lines=[f'{x:.8g} {y:.8g} {z:.8g} 0 0 1\n' for x,y,z in points]
    sensors=''.join(sensor_lines)
    (out/'测点.pts').write_text(sensors,encoding='ascii')
    if progress_cb: progress_cb(3, 5)
    # Keep one logical core free for Qt/Windows and cap at eight to avoid
    # excessive process overhead on large parameter batches. ``-u-`` makes
    # the sampling stream repeatable, which is essential when successive
    # precision levels are compared for numerical convergence.
    process_count = max(1, min(8, (os.cpu_count() or 2) - 1))
    args=[str(executables['rtrace']),'-u-','-I+','-h',
          '-ab',str(ambient_bounces),'-ad',str(ambient_divisions),
          '-as',str(max(ambient_divisions//2,1)),'-aa','0.08','-lw','1e-6','场景.oct']
    indoor=_rgb_lux(_parallel_rtrace(
        args, sensor_lines, cwd=out, env=process_env, workers=process_count,
    ))
    outdoor=_rgb_lux(_run([str(executables['rtrace']),'-u-','-I+','-h','-ab','1','-ad','4096',
        '-aa','.02','天空.oct'],cwd=out,input_text='0 0 1 0 0 1\n',env=process_env))
    if (indoor.size != len(points) or outdoor.size != 1 or outdoor[0] <= 0
            or not np.isfinite(indoor).all() or not np.isfinite(outdoor).all()
            or np.any(indoor < 0)):
        raise RuntimeError('Radiance返回的测点数量或室外参考照度异常')
    scale=10000.0/outdoor[0]
    indoor*=scale
    outdoor_lux=10000.0
    df=indoor/outdoor_lux*100
    illuminance=np.full(mask.shape,np.nan); daylight=np.full(mask.shape,np.nan)
    illuminance[mask]=indoor; daylight[mask]=df
    avg=float(np.average(indoor,weights=weights[mask])); minimum=float(np.min(indoor))
    result_obj=RadianceResult(xs,ys,mask,illuminance,daylight,outdoor_lux,avg,minimum,
        minimum/avg if avg else 0.,float(np.mean(indoor>=300)),
        'LBNL Radiance 6.0.2 official binary',
        f'CIE overcast, rtrace irradiance, ab={ambient_bounces}, ad={ambient_divisions}, n={process_count}',
        scene.fingerprint(),str(out))
    if progress_cb: progress_cb(4, 5)
    with (out/'逐点结果.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.writer(stream); writer.writerow(['X_m','Y_m','Z_m','照度_lux','采光系数_DF_percent'])
        for point,lux,value in zip(points,indoor,df): writer.writerow([*point,lux,value])
    manifest={k:v for k,v in asdict(result_obj).items() if not isinstance(v,np.ndarray)}
    manifest.update({'software_version':'5.4.0','timestamp_utc':datetime.now(timezone.utc).isoformat(),
        'grid_mm':grid_mm,'sensor_count':len(points),'radiance_processes':process_count,
        'workplane_height_mm':workplane_height_mm,
        'sensor_fingerprint':sensor_signature(X,Y,weights,mask,workplane_height_mm),
        'area_weight_sum_m2':float(weights.sum()),
        'deterministic_sampling':True,'warnings':scene.warnings,
        'claim_scope':'Radiance点时标准全阴天结果；不等于全年sDA/UDI/ASE或DGP。'})
    (out/'计算溯源.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    np.savez_compressed(out/'完整网格结果.npz',grid_x=xs,grid_y=ys,valid_mask=mask,
        illuminance_lux=illuminance,daylight_factor=daylight, area_weights=weights,
        sensor_x_mm=X, sensor_y_mm=Y)
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
