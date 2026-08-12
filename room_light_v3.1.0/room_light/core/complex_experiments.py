"""
core/complex_experiments.py — Complex-space shading experiments  v3.1.0

The GUI parameter experiment uses this module once a project has entered the
v3 BuildingModel/SpaceModel data flow.  It never converts an arbitrary polygon
back to the legacy rectangular RoomModel.

Current application scope:
  * one active SpaceModel;
  * one common horizontal-overhang form applied to every exterior window;
  * theta × board length × head gap × material scan;
  * imported project state retained as the pre-retrofit baseline;
  * L=0 calculated exactly once;
  * material, support and installation cost based on actual exterior windows.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Callable, List, Optional, Tuple

import numpy as np
import pandas as pd

from core.complex_daylight import compute_complex_daylight
from core.complex_models import SpaceModel, WallOpening, WallSegment
from core.complex_thermal import compute_complex_thermal
from core.models import ShadingDevice, get_material
from core.space_geometry import space_floor_area_mm2
from io_utils.weather_data import WeatherDataset, default_dataset

T_HI = 26.0
T_LO = 18.0
DEFAULT_GRID_MM = 500.0


def exterior_windows(
    space: SpaceModel,
) -> List[Tuple[WallSegment, WallOpening]]:
    """Return the same exterior-window scope used by the complex engines."""
    return [
        (wall, opening)
        for wall in space.wall_segments()
        if wall.boundary_type in {"exterior", "ground"}
        for opening in wall.windows()
    ]


def space_has_horizontal_shading(space: SpaceModel) -> bool:
    """Whether the active space has a supported horizontal overhang."""
    shading = space.shading
    if shading.type != "horizontal_overhang":
        return False
    if float(shading.overhang_depth_mm) > 0.0:
        return True
    return any(
        float(values.get("depth_mm", shading.overhang_depth_mm)) > 0.0
        for values in (shading.overhang_overrides or {}).values()
    )


def experiment_space(base_space: SpaceModel) -> SpaceModel:
    """Clone the active space and remove all existing shading."""
    space = deepcopy(base_space)
    space.shading = ShadingDevice(type="none")
    return space


def build_solution_space(base_space: SpaceModel, solution) -> SpaceModel:
    """Install one result-table overhang on a no-existing-shading clone."""
    space = experiment_space(base_space)
    length_mm = float(solution.get("L_mm", 0.0))
    if length_mm <= 0.0:
        return space
    material = str(solution.get("material", ""))
    material_spec = get_material(material) or {}
    space.shading = ShadingDevice(
        type="horizontal_overhang",
        overhang_depth_mm=length_mm,
        overhang_height_mm=float(solution.get("gap_mm", 0.0)),
        overhang_tilt_deg=float(solution.get("tilt_deg", 90.0)),
        diffuse_residual=float(material_spec.get("k_diff", 0.30)),
    )
    return space


def _daylight_metrics(result) -> dict:
    return {
        "Ra": result.Ra,
        "U0": result.U0,
        "DF_avg": result.DF_avg,
        "E_avg": result.E_avg,
    }


def _thermal_metrics(result) -> dict:
    overheat = float(np.sum(np.maximum(result.T_in - T_HI, 0.0)))
    underheat = float(np.sum(np.maximum(T_LO - result.T_in, 0.0)))
    return {
        "comfort_months": result.comfort_months,
        "comfort_ratio": result.comfort_months / 12.0,
        "overheat_months": result.overheat_months,
        "overheat_degree_months": overheat,
        "underheat_degree_months": underheat,
        "thermal_discomfort": overheat + underheat,
        "SC_annual": result.SC_effective,
    }


def _scope_metrics(space: SpaceModel) -> dict:
    windows = exterior_windows(space)
    window_walls = {wall.id for wall, _opening in windows}
    return {
        "space_name": space.name,
        "floor_area_m2": space_floor_area_mm2(space) / 1_000_000.0,
        "exterior_wall_count": len([
            wall for wall in space.wall_segments()
            if wall.boundary_type in {"exterior", "ground"}
        ]),
        "shaded_wall_count": len(window_walls),
        "window_count": len(windows),
        "total_window_width_m": (
            sum(opening.width_mm for _wall, opening in windows) / 1000.0
        ),
        "application_scope": "当前空间全部外窗统一设置水平遮阳",
    }


def _run_case(
    space: SpaceModel,
    weather: WeatherDataset,
    ndiv: int,
    grid_mm: float,
    ra_threshold: float,
    latitude_deg: float,
    north_angle_deg: float,
) -> dict:
    daylight = compute_complex_daylight(
        space,
        E_out=weather.annual_avg,
        grid_mm=grid_mm,
        ndiv=ndiv,
        store_components=False,
        ra_threshold=ra_threshold,
    )
    thermal = compute_complex_thermal(
        space,
        weather.monthly_ghi,
        weather.monthly_temp,
        latitude_deg=latitude_deg,
        north_angle_deg=north_angle_deg,
    )
    record = _daylight_metrics(daylight)
    record.update(_thermal_metrics(thermal))
    record.update(_scope_metrics(space))
    return record


def run_complex_project_baseline(
    base_space: SpaceModel,
    weather: Optional[WeatherDataset] = None,
    ndiv: int = 20,
    grid_mm: float = DEFAULT_GRID_MM,
    ra_threshold: float = 2.0,
    latitude_deg: float = 28.59,
    north_angle_deg: float = 0.0,
    eligible_as_no_shade: bool = False,
) -> pd.DataFrame:
    """Calculate the imported active-space state once as retrofit baseline."""
    weather = weather or default_dataset()
    space = deepcopy(base_space)
    has_shading = space_has_horizontal_shading(space)
    record = _run_case(
        space,
        weather,
        ndiv,
        grid_mm,
        ra_threshold,
        latitude_deg,
        north_angle_deg,
    )
    record.update({
        "group": "原始模型基准",
        "param_label": "原始模型（当前复杂空间状态）",
        "param_value": 0.0,
        "cost": 0.0,
        "tilt_deg": np.nan,
        "L_mm": np.nan if has_shading else 0.0,
        "gap_mm": np.nan,
        "material": "—(改造前基准)" if has_shading else "无",
        "_plot_color": "#16a34a",
        "is_candidate": bool(eligible_as_no_shade and not has_shading),
        "panel_area_m2": 0.0,
        "material_unit_price": 0.0,
        "material_cost": 0.0,
        "support_length_m": 0.0,
        "support_unit_price": 0.0,
        "support_cost": 0.0,
        "install_unit_price": 0.0,
        "installation_cost": 0.0,
        "cost_basis": "改造前现状参照，新增改造造价=0",
    })
    return pd.DataFrame([record])


def run_complex_no_shading_candidate(
    base_space: SpaceModel,
    weather: Optional[WeatherDataset] = None,
    ndiv: int = 20,
    grid_mm: float = DEFAULT_GRID_MM,
    ra_threshold: float = 2.0,
    latitude_deg: float = 28.59,
    north_angle_deg: float = 0.0,
) -> pd.DataFrame:
    """Calculate one and only one L=0 candidate."""
    weather = weather or default_dataset()
    space = experiment_space(base_space)
    record = _run_case(
        space,
        weather,
        ndiv,
        grid_mm,
        ra_threshold,
        latitude_deg,
        north_angle_deg,
    )
    record.update({
        "group": "无遮阳候选",
        "param_label": "L=0（复杂空间全部外窗均无新增遮阳）",
        "param_value": 0.0,
        "cost": 0.0,
        "tilt_deg": np.nan,
        "L_mm": 0.0,
        "gap_mm": np.nan,
        "material": "无",
        "_plot_color": "#64748b",
        "is_candidate": True,
        "panel_area_m2": 0.0,
        "material_unit_price": 0.0,
        "material_cost": 0.0,
        "support_length_m": 0.0,
        "support_unit_price": 0.0,
        "support_cost": 0.0,
        "install_unit_price": 0.0,
        "installation_cost": 0.0,
        "cost_basis": "L=0无板候选，新增工程造价=0",
    })
    return pd.DataFrame([record])


def run_complex_overhang_experiment(
    base_space: SpaceModel,
    tilt_degs: Optional[List[float]] = None,
    depth_mms: Optional[List[float]] = None,
    gap_mms: Optional[List[float]] = None,
    materials: Optional[List[str]] = None,
    weather: Optional[WeatherDataset] = None,
    ndiv: int = 20,
    grid_mm: float = DEFAULT_GRID_MM,
    ra_threshold: float = 2.0,
    latitude_deg: float = 28.59,
    north_angle_deg: float = 0.0,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    material_unit_costs: Optional[dict] = None,
    support_cost_per_m: float = 180.0,
    install_cost_per_window: float = 300.0,
) -> pd.DataFrame:
    """
    Scan one common overhang form across every exterior window in the space.

    Daylight is calculated once per geometry because material only affects the
    residual thermal transmission factor.  Thermal calculation is repeated per
    selected material.
    """
    tilt_degs = tilt_degs or [
        60.0, 70.0, 80.0, 90.0, 100.0, 110.0, 120.0,
    ]
    depth_mms = depth_mms or [300.0, 600.0, 900.0, 1200.0, 1500.0]
    gap_mms = gap_mms or [0.0]
    weather = weather or default_dataset()
    material_unit_costs = material_unit_costs or {}

    windows = exterior_windows(base_space)
    if not windows:
        raise ValueError("当前复杂空间没有外窗，无法运行遮阳参数化实验。")

    material_specs = []
    for material in materials or []:
        spec = get_material(material)
        if spec is None:
            continue
        default_price = float(spec.get(
            "installed_cost_per_m2",
            spec.get("cost", 750.0),
        ))
        material_specs.append((
            material,
            float(spec["k_diff"]),
            str(spec["color"]),
            float(material_unit_costs.get(material, default_price)),
        ))
    if not material_specs:
        material_specs = [
            ("默认(k_diff=0.30)", 0.30, "#2563eb", 750.0),
        ]

    positive_depths = [
        float(value) for value in depth_mms if float(value) > 0.0
    ]
    geometry_combinations = [
        (float(tilt), length, float(gap))
        for tilt in tilt_degs
        for length in positive_depths
        for gap in gap_mms
    ]
    total = len(geometry_combinations) * len(material_specs)
    total_window_width_m = sum(
        opening.width_mm for _wall, opening in windows
    ) / 1000.0
    scope = _scope_metrics(base_space)
    rows = []
    completed = 0

    for tilt, length_mm, gap_mm in geometry_combinations:
        space = experiment_space(base_space)
        space.shading = ShadingDevice(
            type="horizontal_overhang",
            overhang_depth_mm=length_mm,
            overhang_height_mm=gap_mm,
            overhang_tilt_deg=tilt,
        )
        daylight = compute_complex_daylight(
            space,
            E_out=weather.annual_avg,
            grid_mm=grid_mm,
            ndiv=ndiv,
            store_components=False,
            ra_threshold=ra_threshold,
        )
        daylight_metrics = _daylight_metrics(daylight)

        if abs(tilt - 90.0) < 1e-6:
            tilt_label = "水平"
        elif tilt > 90.0:
            tilt_label = f"上扬{tilt - 90.0:.0f}°"
        else:
            tilt_label = f"下垂{90.0 - tilt:.0f}°"

        for material, k_diff, color, unit_price in material_specs:
            space.shading.diffuse_residual = k_diff
            thermal = compute_complex_thermal(
                space,
                weather.monthly_ghi,
                weather.monthly_temp,
                latitude_deg=latitude_deg,
                north_angle_deg=north_angle_deg,
            )
            panel_area_m2 = (
                total_window_width_m * length_mm / 1000.0
            )
            support_length_m = 2.0 * total_window_width_m
            material_cost = panel_area_m2 * unit_price
            support_cost = (
                support_length_m * float(support_cost_per_m)
            )
            installation_cost = (
                len(windows) * float(install_cost_per_window)
            )
            record = dict(daylight_metrics)
            record.update(_thermal_metrics(thermal))
            record.update(scope)
            record.update({
                "group": "遮阳θ×L扫描",
                "param_label": (
                    f"{tilt_label} L={length_mm:.0f} h={gap_mm:.0f} "
                    f"[{material}]｜全部外窗"
                ),
                "param_value": tilt,
                "tilt_deg": tilt,
                "L_mm": length_mm,
                "gap_mm": gap_mm,
                "material": material,
                "_plot_color": color,
                "is_candidate": True,
                "cost": (
                    material_cost + support_cost + installation_cost
                ),
                "panel_area_m2": panel_area_m2,
                "material_unit_price": unit_price,
                "material_cost": material_cost,
                "support_length_m": support_length_m,
                "support_unit_price": float(support_cost_per_m),
                "support_cost": support_cost,
                "install_unit_price": float(install_cost_per_window),
                "installation_cost": installation_cost,
                "cost_basis": (
                    "复杂空间全部外窗：Σ窗宽×L×材料综合单价 + "
                    "2×Σ窗宽×支撑单价 + 外窗数×安装费"
                ),
            })
            rows.append(record)
            completed += 1
            if progress_cb:
                progress_cb(completed, total)
    return pd.DataFrame(rows)


def run_all_complex_experiments(
    base_space: SpaceModel,
    weather: Optional[WeatherDataset] = None,
    ndiv: int = 20,
    grid_mm: float = DEFAULT_GRID_MM,
    ra_threshold: float = 2.0,
    tilt_degs: Optional[List[float]] = None,
    depth_mms: Optional[List[float]] = None,
    gap_mms: Optional[List[float]] = None,
    materials: Optional[List[str]] = None,
    include_baseline: bool = True,
    progress_cb: Optional[Callable[[str, int, int], None]] = None,
    material_unit_costs: Optional[dict] = None,
    support_cost_per_m: float = 180.0,
    install_cost_per_window: float = 300.0,
    latitude_deg: float = 28.59,
    north_angle_deg: float = 0.0,
) -> pd.DataFrame:
    """Run baseline, unique L=0 and all positive-length complex candidates."""
    weather = weather or default_dataset()
    include_zero = (
        depth_mms is not None
        and any(float(value) <= 0.0 for value in depth_mms)
    )
    parts = []
    if include_baseline:
        parts.append(run_complex_project_baseline(
            base_space,
            weather=weather,
            ndiv=ndiv,
            grid_mm=grid_mm,
            ra_threshold=ra_threshold,
            latitude_deg=latitude_deg,
            north_angle_deg=north_angle_deg,
            eligible_as_no_shade=include_zero,
        ))
        if progress_cb:
            progress_cb("原始模型基准", 1, 1)
        if include_zero and space_has_horizontal_shading(base_space):
            parts.append(run_complex_no_shading_candidate(
                base_space,
                weather=weather,
                ndiv=ndiv,
                grid_mm=grid_mm,
                ra_threshold=ra_threshold,
                latitude_deg=latitude_deg,
                north_angle_deg=north_angle_deg,
            ))
            if progress_cb:
                progress_cb("L=0无遮阳候选", 1, 1)

    overhangs = run_complex_overhang_experiment(
        base_space,
        tilt_degs=tilt_degs,
        depth_mms=depth_mms,
        gap_mms=gap_mms,
        materials=materials,
        weather=weather,
        ndiv=ndiv,
        grid_mm=grid_mm,
        ra_threshold=ra_threshold,
        latitude_deg=latitude_deg,
        north_angle_deg=north_angle_deg,
        progress_cb=(
            (lambda index, total: progress_cb(
                "复杂空间遮阳θ×L扫描",
                index,
                total,
            ))
            if progress_cb else None
        ),
        material_unit_costs=material_unit_costs,
        support_cost_per_m=support_cost_per_m,
        install_cost_per_window=install_cost_per_window,
    )
    if not overhangs.empty:
        parts.append(overhangs)

    if not parts:
        return pd.DataFrame()
    return pd.concat(parts, ignore_index=True, sort=False)
