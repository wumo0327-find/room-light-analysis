"""Shared experimental inputs, sampling, provenance and stage records (V5.4)."""
from copy import deepcopy
from dataclasses import asdict
from functools import wraps
import hashlib
import inspect
import json
from pathlib import Path

import numpy as np

VERSION = "5.4.1"
EXPERIMENT_GRID_MM = 250.0
METRICS = ("Ra", "Ra2", "daylight_score", "Cd2", "U0", "DF_avg", "E_avg",
           "DF_min", "DF_max", "E_min", "E_max", "sensor_count", "zero_illuminance_count",
           "thermal_discomfort", "annual_lighting_kwh", "annual_cooling_kwh",
           "annual_heating_kwh", "annual_heating_load_kwh", "annual_cooling_load_kwh",
           "annual_operating_kwh", "annual_operating_cost",
           "annual_total_cost", "cost", "room_performance_matrix", "room_results_json")


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def no_device_space(space):
    from core.models import ShadingDevice
    result = deepcopy(space)
    # Intrinsic walls/windows/railings stay; every optional added device resets.
    result.shading = ShadingDevice(type="none", components=[],
        vertical_fin_enabled=False, overhang_overrides={}, fin_overrides={})
    return result


def _physical(value):
    if isinstance(value, dict):
        result = {k: _physical(v) for k, v in value.items()
                  if k not in {"id", "name", "metadata"}}
        physical_metadata = {k: _physical(v) for k,v in value.get('metadata', {}).items()
            if k in {'analysis_inner_start', 'analysis_inner_end', 'analysis_length_mm'}}
        if physical_metadata:
            result['metadata'] = physical_metadata
        return result
    if isinstance(value, list):
        return [_physical(v) for v in value]
    return value


def model_fingerprints(space):
    base = asdict(no_device_space(space))
    base.pop("shading", None)
    model = _physical(base)
    component = asdict(space.shading)
    return {"baseline_model_fingerprint": digest(model),
            "component_config_fingerprint": digest(component),
            "scheme_fingerprint": digest({"base": model, "component": component})}


def fixed_sampling(space, grid_mm=250.0):
    """BBox-anchored exact-pitch cells, clipped to net floor, holes excluded.

    Each intersecting cell has its clipped area as weight. If its centre lies
    outside the polygon, use the clipped piece's representative point. Never
    remove a sensor because its illumination happens to be zero.
    """
    from shapely.geometry import Polygon, Point, box
    if not np.isfinite(grid_mm) or grid_mm <= 0:
        raise ValueError("采光网格必须是有限正数")
    polygon = Polygon([(p.x, p.y) for p in space.analysis_outer_points()],
        [[(p.x, p.y) for p in ring] for ring in space.analysis_hole_points()])
    if not polygon.is_valid or polygon.area <= 0:
        raise ValueError("净空间边界无效，不能布置统一测点")
    x0, y0, x1, y1 = polygon.bounds
    xs = x0 + (np.arange(max(1, int(np.ceil((x1-x0)/grid_mm))))+.5)*grid_mm
    ys = y0 + (np.arange(max(1, int(np.ceil((y1-y0)/grid_mm))))+.5)*grid_mm
    X, Y = np.meshgrid(xs, ys)
    weights = np.zeros(X.shape)
    for iy, y in enumerate(ys):
        for ix, x in enumerate(xs):
            piece = polygon.intersection(box(x-grid_mm/2, y-grid_mm/2,
                                             x+grid_mm/2, y+grid_mm/2))
            if piece.area <= 1e-8:
                continue
            weights[iy, ix] = piece.area / 1e6
            point = Point(x, y)
            if not piece.contains(point):
                point = piece.representative_point()
            X[iy, ix], Y[iy, ix] = point.x, point.y
    mask = weights > 0
    return xs, ys, mask, X, Y, weights


def sensor_signature(X, Y, weights, mask, height):
    return digest({"coordinates_mm": np.column_stack((X[mask], Y[mask])).tolist(),
                   "area_m2": weights[mask].tolist(), "height_mm": float(height)})


def weighted_daylight_stats(df, illuminance, mask, weights, threshold):
    values = np.asarray(df)[mask]
    light = np.asarray(illuminance)[mask]
    areas = np.asarray(weights)[mask]
    if (not len(values) or not np.isfinite(values).all() or
        not np.isfinite(light).all() or not np.isfinite(areas).all() or
        np.any(values < 0) or np.any(light < 0) or np.any(areas <= 0)):
        raise ValueError("有效采光测点/面积权重含无效数值")
    if not np.isfinite(threshold) or threshold <= 0:
        raise ValueError("DF达标阈值必须大于0")
    avg = float(np.average(light, weights=areas))
    return {"DF_avg": float(np.average(values, weights=areas)),
        "DF_min": float(values.min()), "DF_max": float(values.max()),
        "E_avg": avg, "E_min": float(light.min()), "E_max": float(light.max()),
        "U0": float(light.min()/avg) if avg > 0 else 0.0,
        "Ra": float(np.average(values >= threshold, weights=areas)),
        "daylight_score": float(np.average(np.clip(values/threshold, 0, 1), weights=areas))}


def shared_experiment(function):
    """Bind all public experimental paths to one immutable effective config."""
    signature = inspect.signature(function)
    @wraps(function)
    def wrapped(*args, **kwargs):
        from core.engine_service import settings, configure
        from core.operation import profile_from_mapping
        bound = signature.bind(*args, **kwargs)
        supplied = set(bound.arguments)
        bound.apply_defaults()
        previous = settings()
        effective = deepcopy(previous)
        if bound.arguments.get("operation_profile") is not None:
            effective.profile = profile_from_mapping(bound.arguments["operation_profile"]).to_dict()
        else:
            effective.profile = profile_from_mapping(effective.profile).to_dict()
        if "north_angle_deg" in supplied:
            effective.north_angle_deg = float(bound.arguments["north_angle_deg"])
        elif "north_angle_deg" in bound.arguments:
            bound.arguments['north_angle_deg'] = effective.north_angle_deg
        if "grid_mm" in bound.arguments:
            # All stages use the same sensor coordinates/weights. Only ray
            # sampling and bounce depth change for convergence checking.
            bound.arguments["grid_mm"] = EXPERIMENT_GRID_MM
            effective.grid_mm = EXPERIMENT_GRID_MM
        bound.arguments["operation_profile"] = effective.profile
        configure(effective)
        try:
            return function(*bound.args, **bound.kwargs)
        finally:
            configure(previous)
    return wrapped


def stage_record(current, scan, *, final=False):
    record = dict(current)
    for key in METRICS:
        if key in scan:
            record["scan_"+key] = scan.get("scan_"+key, scan[key])
        if final and key in record:
            record["final_"+key] = record[key]
            if key in record and isinstance(record[key], (int, float, np.number)):
                origin = record.get("scan_"+key)
                if isinstance(origin, (int, float, np.number)):
                    record["refinement_delta_"+key] = float(record[key])-float(origin)
    for key in ("ndiv", "ambient_bounces", "workplane_grid_mm", "sensor_fingerprint"):
        if key in scan:
            record["scan_"+key] = scan.get("scan_"+key, scan[key])
    if "refinement_delta_daylight_score" in record:
        record["refinement_delta_Cd"] = record["refinement_delta_daylight_score"]
    if "refinement_delta_thermal_discomfort" in record:
        record["refinement_delta_thermal"] = record["refinement_delta_thermal_discomfort"]
    return record


def attach_baseline(frame):
    """Reference one separate same-stage no-device record, never a stale CSV."""
    import pandas as pd
    out = frame.copy()
    references = out[out["device_type"].eq("baseline")]
    if references.empty:
        return out
    baseline = references.iloc[0].to_dict()
    out["baseline_record_id"] = digest({"scheme": baseline.get("scheme_fingerprint", ""),
        "config": baseline.get("calculation_config_fingerprint", ""), "ndiv": str(baseline.get("ndiv", ""))})
    out["baseline_model_fingerprint"] = baseline.get("baseline_model_fingerprint", "")
    for key in METRICS:
        if key not in out or not isinstance(baseline.get(key), (int, float, np.number)):
            continue
        out["baseline_"+key] = baseline[key]
        out["delta_from_baseline_"+key] = pd.to_numeric(out[key], errors="coerce")-baseline[key]
    if "annual_operating_cost" in out:
        out["annual_operating_saving"] = baseline["annual_operating_cost"]-out["annual_operating_cost"]
    out.attrs["baseline_records"] = [baseline]
    return out


def fresh_pareto(frame, thermal_col="thermal_discomfort", maximize_thermal=False, u0_min=0.0):
    from core.experiments import annotate_global_selection
    out = annotate_global_selection(frame, thermal_col=thermal_col,
        maximize_thermal=maximize_thermal, u0_min=u0_min)
    out["joint_pareto"] = out["global_pareto"]
    out["within_device_pareto"] = False
    for kind in ("horizontal_overhang", "louver", "light_shelf"):
        indices = out.index[out["device_type"].eq(kind)]
        if len(indices):
            sub = annotate_global_selection(out.loc[indices].copy(), thermal_col=thermal_col,
                maximize_thermal=maximize_thermal, u0_min=u0_min)
            out.loc[indices, "within_device_pareto"] = sub["global_pareto"]
    return out


def export_experiment_records(frame, path):
    """Write independent baseline, original scan and final-stage evidence."""
    import pandas as pd
    path = Path(path)
    for metric in ('Ra','daylight_score','U0','DF_avg','E_avg','thermal_discomfort',
        'annual_operating_cost','annual_total_cost'):
        if metric in frame and not np.isfinite(pd.to_numeric(frame[metric], errors='coerce')).all():
            raise ValueError('不能导出无效的实际计算指标：'+metric)
    frame = frame.replace([np.inf, -np.inf], None)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, encoding="utf-8-sig")
    base = frame.attrs.get("baseline_records")
    if base is None and "device_type" in frame:
        base = frame[frame.device_type.eq("baseline")].to_dict("records")
    if base:
        pd.DataFrame(base).to_csv(path.with_name(path.stem+"_独立无构件基准.csv"), index=False, encoding="utf-8-sig")
    for prefix, title in (("scan_", "原始扫描"), ("final_", "最终复核")):
        columns = [c for c in frame if c.startswith(prefix)]
        if columns:
            identifiers = [c for c in ("solution_id", "device_type", "param_label", "baseline_record_id",
                "baseline_model_fingerprint", "scheme_fingerprint", "calculation_stage", "precision_verified") if c in frame]
            frame[identifiers+columns].to_csv(path.with_name(path.stem+"_"+title+".csv"), index=False, encoding="utf-8-sig")
    if "room_results_json" in frame:
        rooms = []
        for _, row in frame.iterrows():
            for room in json.loads(row["room_results_json"]):
                rooms.append({**room, "param_label": row.get("param_label"), "calculation_stage": row.get("calculation_stage"),
                              "baseline_record_id": row.get("baseline_record_id")})
        pd.DataFrame(rooms).to_csv(path.with_name(path.stem+"_同阶段房间明细.csv"), index=False, encoding="utf-8-sig")
    originals = frame.attrs.get('original_space_configs')
    if originals:
        path.with_name(path.stem+'_原始房间配置.json').write_text(
            json.dumps(originals, ensure_ascii=False, indent=2), encoding='utf-8')
    components = [c for c in ('device_type','param_label','component_config_fingerprint','component_config_json') if c in frame]
    if components:
        frame[components].to_csv(path.with_name(path.stem+'_构件配置.csv'), index=False, encoding='utf-8-sig')
    return str(path)
