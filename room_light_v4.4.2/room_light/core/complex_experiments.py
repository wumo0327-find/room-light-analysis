"""
core/complex_experiments.py — Complex-space shading experiments  v4.4.2

The GUI parameter experiment uses this module once a project has entered the
v3 BuildingModel/SpaceModel data flow.  It never converts an arbitrary polygon
back to the legacy rectangular RoomModel.

Current application scope:
  * one or more selected SpaceModel objects;
  * fixed overhang, horizontal louver and inner/outer light-shelf scans;
  * optional two-device combinations when L1 has no feasible solution;
  * imported project state retained as the pre-retrofit baseline;
  * L=0 calculated exactly once;
  * material, support and installation cost based on actual exterior windows.
"""
from __future__ import annotations

from copy import deepcopy
from itertools import combinations, product
from typing import Callable, Collection, List, Optional, Tuple

import numpy as np
import pandas as pd

from core.complex_daylight import (
    compute_complex_daylight, apply_overhang_material,
    GRID_MM as DAYLIGHT_GRID_MM,
)
from core.complex_models import SpaceModel, WallOpening, WallSegment
from core.complex_thermal import compute_complex_thermal
from core.models import (
    ShadingDevice, get_material, canonical_material_name,
    DEFAULT_SUPPORT_COST_PER_M, DEFAULT_INSTALL_COST_PER_WINDOW,
)
from core.space_geometry import space_floor_area_mm2
from core.operation import (
    BuildingUseProfile, calculate_operation_metrics,
)
from core.decision import (
    ConstraintSet, annotate_feasibility, annotate_minimum_intervention,
    select_combination_seeds,
)
from io_utils.weather_data import WeatherDataset, default_dataset

T_HI = 26.0
T_LO = 18.0
DEFAULT_GRID_MM = DAYLIGHT_GRID_MM


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


def window_selection_key(
    space: SpaceModel,
    wall: WallSegment,
    opening: WallOpening,
) -> str:
    """Stable building-level key used by the plan-view window selector."""
    return f"{space.id}|{wall.id}|{opening.id}"


def opening_shading_key(opening: WallOpening) -> str:
    """Key understood by ShadingDevice's existing per-window overrides."""
    return str(opening.metadata.get("legacy_window_id", opening.id))


def selected_exterior_windows(
    space: SpaceModel,
    target_window_keys: Optional[Collection[str]] = None,
) -> List[Tuple[WallSegment, WallOpening]]:
    """Return experiment targets; ``None`` preserves the old all-window scope."""
    windows = exterior_windows(space)
    if target_window_keys is None:
        return windows
    selected = {str(value) for value in target_window_keys}
    return [
        (wall, opening)
        for wall, opening in windows
        if window_selection_key(space, wall, opening) in selected
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


def _target_ids(windows: List[Tuple[WallSegment, WallOpening]]) -> List[str]:
    return [opening_shading_key(opening) for _wall, opening in windows]


def _apply_surface(shading: ShadingDevice, spec: dict) -> None:
    shading.diffuse_residual = float(spec.get("k_diff", 0.30))
    shading.visible_reflectance = float(spec.get("visible_reflectance", 0.32))
    shading.solar_reflectance = float(spec.get("solar_reflectance", 0.32))
    shading.thermal_emissivity = float(spec.get("thermal_emissivity", 0.90))
    shading.specular_fraction = float(spec.get("specular_fraction", 0.03))


def _install_selected_louver(
    space: SpaceModel,
    windows: List[Tuple[WallSegment, WallOpening]],
    *, width_mm: float, spacing_mm: float, angle_deg: float,
    position: str, control_mode: str, material_spec: dict,
) -> None:
    space.shading = ShadingDevice(
        type="louver", components=["louver"],
        target_window_ids=_target_ids(windows),
        louver_width_mm=float(width_mm),
        louver_spacing_mm=float(spacing_mm),
        louver_angle_deg=float(angle_deg),
        louver_position=str(position),
        louver_control_mode=str(control_mode),
        louver_reflect=float(material_spec.get("visible_reflectance", 0.60)),
    )
    _apply_surface(space.shading, material_spec)


def _install_selected_light_shelf(
    space: SpaceModel,
    windows: List[Tuple[WallSegment, WallOpening]],
    *, inside_mm: float, outside_mm: float, tilt_deg: float,
    upper_ratio: float, material_spec: dict,
) -> None:
    space.shading = ShadingDevice(
        type="light_shelf", components=["light_shelf"],
        target_window_ids=_target_ids(windows),
        light_shelf_depth_mm=float(outside_mm),
        light_shelf_inside_depth_mm=float(inside_mm),
        light_shelf_outside_depth_mm=float(outside_mm),
        light_shelf_tilt_deg=float(tilt_deg),
        light_shelf_upper_window_ratio=float(upper_ratio),
        light_shelf_reflect=float(material_spec.get("visible_reflectance", 0.80)),
    )
    _apply_surface(space.shading, material_spec)


def _install_selected_overhang(
    space: SpaceModel,
    windows: List[Tuple[WallSegment, WallOpening]],
    *,
    length_mm: float,
    gap_mm: float,
    tilt_deg: float,
    diffuse_residual: float = 0.30,
    visible_reflectance: float = 0.32,
    solar_reflectance: float = 0.32,
    thermal_emissivity: float = 0.90,
    specular_fraction: float = 0.03,
    use_global_default: bool = False,
) -> None:
    """Install overrides on targets while leaving every other window unshaded."""
    space.shading = ShadingDevice(
        type="horizontal_overhang",
        components=["horizontal_overhang"],
        target_window_ids=[] if use_global_default else _target_ids(windows),
        overhang_depth_mm=float(length_mm) if use_global_default else 0.0,
        overhang_height_mm=float(gap_mm) if use_global_default else 0.0,
        overhang_tilt_deg=float(tilt_deg) if use_global_default else 90.0,
        diffuse_residual=float(diffuse_residual),
        visible_reflectance=float(visible_reflectance),
        solar_reflectance=float(solar_reflectance),
        thermal_emissivity=float(thermal_emissivity),
        specular_fraction=float(specular_fraction),
        overhang_overrides={} if use_global_default else {
            opening_shading_key(opening): {
                "depth_mm": float(length_mm),
                "gap_mm": float(gap_mm),
                "tilt_deg": float(tilt_deg),
            }
            for _wall, opening in windows
        },
    )


def build_solution_space(
    base_space: SpaceModel,
    solution,
    target_window_keys: Optional[Collection[str]] = None,
) -> SpaceModel:
    """Install one result-table passive solution on a no-shading clone."""
    space = experiment_space(base_space)
    device_type = str(solution.get("device_type", "horizontal_overhang"))
    if str(solution.get("group", "")) in {"原始模型基准", "无遮阳候选"}:
        return space
    windows = selected_exterior_windows(space, target_window_keys)
    if not windows:
        return space
    material = str(solution.get("material", ""))
    material_spec = get_material(material) or {}
    if device_type == "louver":
        _install_selected_louver(
            space, windows,
            width_mm=float(solution.get("louver_width_mm", 100.0)),
            spacing_mm=float(solution.get("louver_spacing_mm", 100.0)),
            angle_deg=float(solution.get("louver_angle_deg", 45.0)),
            position=str(solution.get("louver_position", "exterior")),
            control_mode=str(solution.get("louver_control_mode", "fixed")),
            material_spec=material_spec,
        )
    elif device_type == "light_shelf":
        _install_selected_light_shelf(
            space, windows,
            inside_mm=float(solution.get("shelf_inside_mm", 0.0)),
            outside_mm=float(solution.get("shelf_outside_mm", 0.0)),
            tilt_deg=float(solution.get("shelf_tilt_deg", 0.0)),
            upper_ratio=float(solution.get("shelf_upper_ratio", 0.35)),
            material_spec=material_spec,
        )
    else:
        length_mm = float(solution.get("L_mm", 0.0))
        if length_mm <= 0.0:
            return space
        _install_selected_overhang(
            space, windows,
            length_mm=length_mm,
            gap_mm=float(solution.get("gap_mm", 0.0)),
            tilt_deg=float(solution.get("tilt_deg", 90.0)),
            diffuse_residual=float(material_spec.get("k_diff", 0.30)),
            visible_reflectance=float(material_spec.get("visible_reflectance", 0.32)),
            solar_reflectance=float(material_spec.get("solar_reflectance", 0.32)),
            thermal_emissivity=float(material_spec.get("thermal_emissivity", 0.90)),
            specular_fraction=float(material_spec.get("specular_fraction", 0.03)),
            use_global_default=target_window_keys is None,
        )
    return space


def build_combination_space(
    base_space: SpaceModel, solution_a, solution_b,
    target_window_keys: Optional[Collection[str]] = None,
) -> SpaceModel:
    """Construct a true two-device model; performance is recalculated, not added."""
    first = build_solution_space(base_space, solution_a, target_window_keys)
    second = build_solution_space(base_space, solution_b, target_window_keys)
    a, b = first.shading, second.shading
    combined = deepcopy(a)
    combined.type = "combined"
    combined.components = list(dict.fromkeys(
        a.enabled_components() + b.enabled_components()
    ))
    combined.target_window_ids = list(dict.fromkeys(
        list(a.target_window_ids) + list(b.target_window_ids)
    ))
    if b.has_component("horizontal_overhang"):
        for field_name in (
            "overhang_depth_mm", "overhang_height_mm", "overhang_tilt_deg",
            "overhang_overrides",
        ):
            setattr(combined, field_name, deepcopy(getattr(b, field_name)))
    if b.has_component("louver"):
        for field_name in (
            "louver_width_mm", "louver_angle_deg", "louver_spacing_mm",
            "louver_reflect", "louver_position", "louver_control_mode",
        ):
            setattr(combined, field_name, deepcopy(getattr(b, field_name)))
    if b.has_component("light_shelf"):
        for field_name in (
            "light_shelf_depth_mm", "light_shelf_reflect",
            "light_shelf_inside_depth_mm", "light_shelf_outside_depth_mm",
            "light_shelf_tilt_deg", "light_shelf_height_mm",
            "light_shelf_upper_window_ratio",
        ):
            setattr(combined, field_name, deepcopy(getattr(b, field_name)))
    # Shared surface terms are a conservative blend; component-specific visible
    # reflectance is retained in louver_reflect/light_shelf_reflect.
    combined.visible_reflectance = max(a.visible_reflectance, b.visible_reflectance)
    combined.solar_reflectance = float(np.mean([
        a.solar_reflectance, b.solar_reflectance,
    ]))
    combined.diffuse_residual = float(np.mean([
        a.diffuse_residual, b.diffuse_residual,
    ]))
    combined.thermal_emissivity = float(np.mean([
        a.thermal_emissivity, b.thermal_emissivity,
    ]))
    combined.specular_fraction = max(a.specular_fraction, b.specular_fraction)
    first.shading = combined
    return first


def _daylight_metrics(result) -> dict:
    return {
        "Ra": result.Ra,
        "daylight_score": result.daylight_score,
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
        "thermal_time_basis": "12个月月均准稳态（非逐时动态模拟）",
    }


def _scope_metrics(
    space: SpaceModel,
    target_window_keys: Optional[Collection[str]] = None,
) -> dict:
    windows = selected_exterior_windows(space, target_window_keys)
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
        "target_window_count": len(windows),
        "total_window_width_m": (
            sum(opening.width_mm for _wall, opening in windows) / 1000.0
        ),
        "application_scope": f"当前空间选中的{len(windows)}扇外窗统一设置水平遮阳",
    }


def _run_case(
    space: SpaceModel,
    weather: WeatherDataset,
    ndiv: int,
    grid_mm: float,
    ra_threshold: float,
    latitude_deg: float,
    north_angle_deg: float,
    target_window_keys: Optional[Collection[str]] = None,
    operation_profile: BuildingUseProfile | dict | None = None,
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
    record.update(_scope_metrics(space, target_window_keys))
    record.update(calculate_operation_metrics(
        daylight_score=daylight.daylight_score,
        thermal_result=thermal,
        floor_area_m2=space_floor_area_mm2(space) / 1_000_000.0,
        construction_cost_yuan=0.0,
        profile=operation_profile,
    ))
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
    target_window_keys: Optional[Collection[str]] = None,
    operation_profile: BuildingUseProfile | dict | None = None,
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
        target_window_keys,
        operation_profile,
    )
    record.update({
        "group": "原始模型基准",
        "device_type": "baseline",
        "device_name": "改造前基准",
        "intervention_level": "L0基准",
        "component_count": 0,
        "param_label": "原始模型（当前复杂空间状态）",
        "param_value": 0.0,
        "construction_cost": 0.0,
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
        "construction_cost_basis": "改造前现状参照，新增改造造价=0",
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
    target_window_keys: Optional[Collection[str]] = None,
    operation_profile: BuildingUseProfile | dict | None = None,
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
        target_window_keys,
        operation_profile,
    )
    record.update({
        "group": "无遮阳候选",
        "device_type": "none",
        "device_name": "无遮阳",
        "intervention_level": "L0基准",
        "component_count": 0,
        "param_label": "L=0（复杂空间全部外窗均无新增遮阳）",
        "param_value": 0.0,
        "construction_cost": 0.0,
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
        "construction_cost_basis": "L=0无板候选，新增工程造价=0",
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
    support_cost_per_m: float = DEFAULT_SUPPORT_COST_PER_M,
    install_cost_per_window: float = DEFAULT_INSTALL_COST_PER_WINDOW,
    target_window_keys: Optional[Collection[str]] = None,
    operation_profile: BuildingUseProfile | dict | None = None,
) -> pd.DataFrame:
    """
    Scan one common overhang form across every exterior window in the space.

    Daylight and thermal performance are produced for every geometry-material
    pair.  Expensive ray geometry is traced once per geometry, then its direct
    and intercepted-light bases are recombined by material reflectance.  Solar
    reflectance/emissivity separately affect the thermal screen.
    """
    tilt_degs = tilt_degs or [
        60.0, 70.0, 80.0, 90.0, 100.0, 110.0, 120.0,
    ]
    depth_mms = depth_mms or [300.0, 600.0, 900.0, 1200.0, 1500.0]
    gap_mms = gap_mms or [0.0]
    weather = weather or default_dataset()
    material_unit_costs = material_unit_costs or {}

    windows = selected_exterior_windows(base_space, target_window_keys)
    if not windows and target_window_keys is None:
        raise ValueError("当前复杂空间没有选中参数化遮阳窗，无法运行实验。")

    material_specs = []
    for material in materials or []:
        spec = get_material(material)
        if spec is None:
            continue
        canonical_name = canonical_material_name(material)
        default_price = float(spec.get(
            "installed_cost_per_m2",
            spec.get("cost", 230.0),
        ))
        material_specs.append((
            canonical_name,
            dict(spec),
            str(spec["color"]),
            float(material_unit_costs.get(
                material,
                material_unit_costs.get(canonical_name, default_price),
            )),
        ))
    if not material_specs:
        material_specs = [
            ("默认(k_diff=0.30)", {
                "k_diff": 0.30,
                "visible_reflectance": 0.32,
                "solar_reflectance": 0.32,
                "thermal_emissivity": 0.90,
                "specular_fraction": 0.03,
            }, "#2563eb", 230.0),
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
    scope = _scope_metrics(base_space, target_window_keys)
    rows = []
    completed = 0

    for tilt, length_mm, gap_mm in geometry_combinations:
        space = experiment_space(base_space)
        _install_selected_overhang(
            space,
            selected_exterior_windows(space, target_window_keys),
            length_mm=length_mm,
            gap_mm=gap_mm,
            tilt_deg=tilt,
            use_global_default=target_window_keys is None,
        )
        daylight_basis = compute_complex_daylight(
            space,
            E_out=weather.annual_avg,
            grid_mm=grid_mm,
            ndiv=ndiv,
            store_components=True,
            ra_threshold=ra_threshold,
        )
        if abs(tilt - 90.0) < 1e-6:
            tilt_label = "水平"
        elif tilt > 90.0:
            tilt_label = f"上扬{tilt - 90.0:.0f}°"
        else:
            tilt_label = f"下垂{90.0 - tilt:.0f}°"

        for material, material_spec, color, unit_price in material_specs:
            space.shading.diffuse_residual = float(material_spec.get("k_diff", 0.30))
            space.shading.visible_reflectance = float(
                material_spec.get("visible_reflectance", 0.32)
            )
            space.shading.solar_reflectance = float(
                material_spec.get("solar_reflectance", 0.32)
            )
            space.shading.thermal_emissivity = float(
                material_spec.get("thermal_emissivity", 0.90)
            )
            space.shading.specular_fraction = float(
                material_spec.get("specular_fraction", 0.03)
            )
            daylight = apply_overhang_material(
                daylight_basis,
                space.shading.visible_reflectance,
                space.shading.specular_fraction,
            )
            daylight_metrics = _daylight_metrics(daylight)
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
            construction_cost = (
                material_cost + support_cost + installation_cost
            )
            record = dict(daylight_metrics)
            record.update(_thermal_metrics(thermal))
            record.update(scope)
            record.update({
                "group": "遮阳θ×L扫描",
                "device_type": "horizontal_overhang",
                "device_name": "固定挑檐",
                "intervention_level": "L1单构件",
                "component_count": 1,
                "key_parameters": (
                    f"θ={tilt:.0f}°，L={length_mm:.0f}mm，h={gap_mm:.0f}mm"
                ),
                "param_label": (
                    f"{tilt_label} L={length_mm:.0f} h={gap_mm:.0f} "
                    f"[{material}]｜选中{len(windows)}扇外窗"
                ),
                "param_value": tilt,
                "tilt_deg": tilt,
                "L_mm": length_mm,
                "gap_mm": gap_mm,
                "material": material,
                "visible_reflectance": space.shading.visible_reflectance,
                "solar_reflectance": space.shading.solar_reflectance,
                "thermal_emissivity": space.shading.thermal_emissivity,
                "specular_fraction": space.shading.specular_fraction,
                "daylight_material_model": "挑檐下表面保守一次反射近似",
                "_plot_color": color,
                "is_candidate": True,
                "construction_cost": construction_cost,
                "panel_area_m2": panel_area_m2,
                "material_unit_price": unit_price,
                "material_cost": material_cost,
                "support_length_m": support_length_m,
                "support_unit_price": float(support_cost_per_m),
                "support_cost": support_cost,
                "install_unit_price": float(install_cost_per_window),
                "installation_cost": installation_cost,
                "construction_cost_basis": (
                    "复杂空间选中外窗：Σ窗宽×L×材料综合单价 + "
                    "2×Σ窗宽×支撑单价 + 外窗数×安装费"
                ),
            })
            record.update(calculate_operation_metrics(
                daylight_score=daylight.daylight_score,
                thermal_result=thermal,
                floor_area_m2=scope["floor_area_m2"],
                construction_cost_yuan=construction_cost,
                profile=operation_profile,
            ))
            rows.append(record)
            completed += 1
            if progress_cb:
                progress_cb(completed, total)
    return pd.DataFrame(rows)


def _experiment_materials(
    materials: Optional[List[str]], material_unit_costs: Optional[dict],
) -> list:
    costs = material_unit_costs or {}
    values = []
    for name in materials or []:
        spec = get_material(name)
        if not spec:
            continue
        canonical = canonical_material_name(name)
        default_price = float(spec.get("installed_cost_per_m2", 230.0))
        values.append((
            canonical, dict(spec), str(spec.get("color", "#2563eb")),
            float(costs.get(name, costs.get(canonical, default_price))),
        ))
    return values or [("默认材料", {
        "k_diff": 0.30, "visible_reflectance": 0.60,
        "solar_reflectance": 0.55, "thermal_emissivity": 0.90,
        "specular_fraction": 0.05,
    }, "#2563eb", 370.0)]


def _complete_device_record(
    *, space: SpaceModel, windows, daylight, thermal, scope: dict,
    material: str, color: str, unit_price: float, panel_area_m2: float,
    support_length_m: float, support_cost_per_m: float,
    install_cost_per_window: float, operation_profile, values: dict,
) -> dict:
    material_cost = panel_area_m2 * unit_price
    support_cost = support_length_m * float(support_cost_per_m)
    installation_cost = len(windows) * float(install_cost_per_window)
    construction_cost = material_cost + support_cost + installation_cost
    record = _daylight_metrics(daylight)
    record.update(_thermal_metrics(thermal))
    record.update(scope)
    record.update(values)
    record.update({
        "material": material, "_plot_color": color, "is_candidate": True,
        "construction_cost": construction_cost,
        "panel_area_m2": panel_area_m2,
        "material_unit_price": unit_price, "material_cost": material_cost,
        "support_length_m": support_length_m,
        "support_unit_price": float(support_cost_per_m),
        "support_cost": support_cost,
        "install_unit_price": float(install_cost_per_window),
        "installation_cost": installation_cost,
    })
    record.update(calculate_operation_metrics(
        daylight_score=daylight.daylight_score,
        thermal_result=thermal,
        floor_area_m2=scope["floor_area_m2"],
        construction_cost_yuan=construction_cost,
        profile=operation_profile,
    ))
    return record


def run_complex_louver_experiment(
    base_space: SpaceModel, *, angles=None, widths=None, spacings=None,
    positions=None, controls=None, materials=None, weather=None, ndiv=20,
    grid_mm=DEFAULT_GRID_MM, ra_threshold=2.0, latitude_deg=28.59,
    north_angle_deg=0.0, progress_cb=None, material_unit_costs=None,
    support_cost_per_m=DEFAULT_SUPPORT_COST_PER_M,
    install_cost_per_window=DEFAULT_INSTALL_COST_PER_WINDOW,
    target_window_keys=None, operation_profile=None,
) -> pd.DataFrame:
    """Scan horizontal-louver geometry, location and practical control mode."""
    weather = weather or default_dataset()
    windows = selected_exterior_windows(base_space, target_window_keys)
    if not windows:
        return pd.DataFrame()
    specs = _experiment_materials(materials, material_unit_costs)
    geometries = list(product(
        angles or [0.0, 30.0, 45.0, 60.0], widths or [80.0, 120.0],
        spacings or [100.0, 150.0], positions or ["exterior"],
        controls or ["fixed"],
    ))
    total = len(geometries) * len(specs)
    rows, done = [], 0
    scope = _scope_metrics(base_space, target_window_keys)
    total_width = scope["total_window_width_m"]
    total_height = sum(opening.height_mm for _wall, opening in windows) / 1000.0
    for angle, width, spacing, position, control in geometries:
        for material, spec, color, price in specs:
            space = experiment_space(base_space)
            targets = selected_exterior_windows(space, target_window_keys)
            _install_selected_louver(
                space, targets, width_mm=width, spacing_mm=spacing,
                angle_deg=angle, position=position, control_mode=control,
                material_spec=spec,
            )
            daylight = compute_complex_daylight(
                space, E_out=weather.annual_avg, grid_mm=grid_mm, ndiv=ndiv,
                store_components=True, ra_threshold=ra_threshold,
            )
            thermal = compute_complex_thermal(
                space, weather.monthly_ghi, weather.monthly_temp,
                latitude_deg=latitude_deg, north_angle_deg=north_angle_deg,
            )
            slat_area = sum(
                (opening.width_mm / 1000.0) * (width / 1000.0)
                * max(1, int(np.ceil(opening.height_mm / max(spacing, 1e-6))))
                for _wall, opening in windows
            )
            values = {
                "group": "百叶参数扫描", "device_type": "louver",
                "device_name": "水平百叶", "intervention_level": "L1单构件",
                "component_count": 1, "param_value": float(angle),
                "louver_angle_deg": float(angle), "louver_width_mm": float(width),
                "louver_spacing_mm": float(spacing),
                "louver_position": str(position),
                "louver_control_mode": str(control),
                "tilt_deg": np.nan, "L_mm": np.nan, "gap_mm": np.nan,
                "key_parameters": (
                    f"角度={angle:.0f}°，片宽={width:.0f}mm，间距={spacing:.0f}mm，"
                    f"{position}/{control}"
                ),
                "param_label": (
                    f"百叶 {angle:.0f}° W={width:.0f} S={spacing:.0f} "
                    f"{position}/{control} [{material}]"
                ),
                "daylight_material_model": "周期叶片射线开口率+保守一次反射",
                "construction_cost_basis": (
                    "Σ(窗宽×片宽×叶片数)×材料单价 + 2×Σ窗高×支撑单价 + 安装费"
                ),
            }
            rows.append(_complete_device_record(
                space=space, windows=windows, daylight=daylight, thermal=thermal,
                scope=scope, material=material, color=color, unit_price=price,
                panel_area_m2=slat_area, support_length_m=2.0 * total_height,
                support_cost_per_m=support_cost_per_m,
                install_cost_per_window=install_cost_per_window,
                operation_profile=operation_profile, values=values,
            ))
            done += 1
            if progress_cb:
                progress_cb(done, total)
    return pd.DataFrame(rows)


def run_complex_light_shelf_experiment(
    base_space: SpaceModel, *, inside_depths=None, outside_depths=None,
    tilts=None, upper_ratios=None, materials=None, weather=None, ndiv=20,
    grid_mm=DEFAULT_GRID_MM, ra_threshold=2.0, latitude_deg=28.59,
    north_angle_deg=0.0, progress_cb=None, material_unit_costs=None,
    support_cost_per_m=DEFAULT_SUPPORT_COST_PER_M,
    install_cost_per_window=DEFAULT_INSTALL_COST_PER_WINDOW,
    target_window_keys=None, operation_profile=None,
) -> pd.DataFrame:
    """Scan inner/outer light-shelf geometry and upper-window division."""
    weather = weather or default_dataset()
    windows = selected_exterior_windows(base_space, target_window_keys)
    if not windows:
        return pd.DataFrame()
    specs = _experiment_materials(materials, material_unit_costs)
    geometries = [values for values in product(
        inside_depths or [0.0, 300.0], outside_depths or [300.0, 600.0],
        tilts or [0.0], upper_ratios or [0.30, 0.40],
    ) if float(values[0]) > 0.0 or float(values[1]) > 0.0]
    total = len(geometries) * len(specs)
    rows, done = [], 0
    scope = _scope_metrics(base_space, target_window_keys)
    total_width = scope["total_window_width_m"]
    for inside, outside, tilt, ratio in geometries:
        for material, spec, color, price in specs:
            space = experiment_space(base_space)
            targets = selected_exterior_windows(space, target_window_keys)
            _install_selected_light_shelf(
                space, targets, inside_mm=inside, outside_mm=outside,
                tilt_deg=tilt, upper_ratio=ratio, material_spec=spec,
            )
            daylight = compute_complex_daylight(
                space, E_out=weather.annual_avg, grid_mm=grid_mm, ndiv=ndiv,
                store_components=True, ra_threshold=ra_threshold,
            )
            thermal = compute_complex_thermal(
                space, weather.monthly_ghi, weather.monthly_temp,
                latitude_deg=latitude_deg, north_angle_deg=north_angle_deg,
            )
            panel_area = total_width * (float(inside) + float(outside)) / 1000.0
            values = {
                "group": "反光板参数扫描", "device_type": "light_shelf",
                "device_name": "内外反光板", "intervention_level": "L1单构件",
                "component_count": 1, "param_value": float(outside),
                "shelf_inside_mm": float(inside),
                "shelf_outside_mm": float(outside),
                "shelf_tilt_deg": float(tilt), "shelf_upper_ratio": float(ratio),
                "tilt_deg": np.nan, "L_mm": np.nan, "gap_mm": np.nan,
                "key_parameters": (
                    f"内={inside:.0f}mm，外={outside:.0f}mm，倾角={tilt:.0f}°，"
                    f"上窗比={ratio:.2f}"
                ),
                "param_label": (
                    f"反光板 内{inside:.0f}/外{outside:.0f}mm "
                    f"θ={tilt:.0f}° 上窗比{ratio:.2f} [{material}]"
                ),
                "daylight_material_model": "内外板射线相交+反射率一次反射",
                "construction_cost_basis": (
                    "Σ窗宽×(内伸+外挑)×材料单价 + 2×Σ窗宽×支撑单价 + 安装费"
                ),
            }
            rows.append(_complete_device_record(
                space=space, windows=windows, daylight=daylight, thermal=thermal,
                scope=scope, material=material, color=color, unit_price=price,
                panel_area_m2=panel_area, support_length_m=2.0 * total_width,
                support_cost_per_m=support_cost_per_m,
                install_cost_per_window=install_cost_per_window,
                operation_profile=operation_profile, values=values,
            ))
            done += 1
            if progress_cb:
                progress_cb(done, total)
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
    support_cost_per_m: float = DEFAULT_SUPPORT_COST_PER_M,
    install_cost_per_window: float = DEFAULT_INSTALL_COST_PER_WINDOW,
    latitude_deg: float = 28.59,
    north_angle_deg: float = 0.0,
    target_window_keys: Optional[Collection[str]] = None,
    operation_profile: BuildingUseProfile | dict | None = None,
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
            target_window_keys=target_window_keys,
            operation_profile=operation_profile,
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
                target_window_keys=target_window_keys,
                operation_profile=operation_profile,
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
        target_window_keys=target_window_keys,
        operation_profile=operation_profile,
    )
    if not overhangs.empty:
        parts.append(overhangs)

    if not parts:
        return pd.DataFrame()
    return pd.concat(parts, ignore_index=True, sort=False)


def _aggregate_case_rows(
    rows: pd.DataFrame,
    spaces: List[SpaceModel],
) -> dict:
    """Area-weight room KPIs and sum quantities/costs for one common scheme."""
    first = rows.iloc[0].to_dict()
    areas = np.asarray(rows["floor_area_m2"], dtype=float)
    if float(np.sum(areas)) <= 1e-12:
        areas = np.ones(len(rows), dtype=float)
    weighted_columns = (
        "Ra", "daylight_score", "U0", "DF_avg", "E_avg",
        "comfort_months", "comfort_ratio", "overheat_months",
        "overheat_degree_months", "underheat_degree_months",
        "thermal_discomfort", "SC_annual",
    )
    for column in weighted_columns:
        if column in rows.columns:
            values = np.asarray(rows[column], dtype=float)
            first[column] = float(np.average(values, weights=areas))
    sum_columns = (
        "floor_area_m2", "exterior_wall_count", "shaded_wall_count",
        "window_count", "target_window_count", "total_window_width_m", "cost",
        "panel_area_m2", "material_cost", "support_length_m",
        "support_cost", "installation_cost", "construction_cost",
        "annual_lighting_full_kwh", "annual_lighting_kwh",
        "annual_cooling_kwh", "annual_heating_kwh", "annual_hvac_kwh",
        "annual_operating_kwh", "annual_operating_cost",
        "annualized_construction_cost", "annual_total_cost",
        "lifecycle_total_cost",
    )
    for column in sum_columns:
        if column in rows.columns:
            first[column] = float(np.nansum(
                np.asarray(rows[column], dtype=float)
            ))
    names = [space.name for space in spaces]
    source_names = (
        rows["source_space_name"].astype(str).tolist()
        if "source_space_name" in rows.columns else names
    )
    if len(source_names) != len(rows):
        source_names = names[:len(rows)]
    matrix_parts = []
    for position, (_index, item) in enumerate(rows.iterrows()):
        name = source_names[position] if position < len(source_names) else f"房间{position + 1}"
        matrix_parts.append(
            f"{name}:Cd={float(item.get('daylight_score', np.nan)):.4f},"
            f"Ra={float(item.get('Ra', np.nan)):.4f},"
            f"U0={float(item.get('U0', np.nan)):.4f},"
            f"热不舒适={float(item.get('thermal_discomfort', np.nan)):.3f}"
        )
    first["room_performance_matrix"] = "；".join(matrix_parts)
    for column, mode, output in (
        ("daylight_score", "min", "worst_room_Cd"),
        ("Ra", "min", "worst_room_Ra"),
        ("U0", "min", "worst_room_U0"),
        ("thermal_discomfort", "max", "worst_room_thermal"),
    ):
        if column in rows.columns and len(rows):
            values = pd.to_numeric(rows[column], errors="coerce")
            worst_index = values.idxmin() if mode == "min" else values.idxmax()
            worst_position = list(rows.index).index(worst_index)
            first[output] = float(values.loc[worst_index])
            first[f"{output}_space"] = (
                source_names[worst_position]
                if worst_position < len(source_names) else "—"
            )
    first["space_id"] = "batch:" + ",".join(space.id for space in spaces)
    first["space_name"] = f"选中{len(spaces)}个房间"
    first["selected_space_count"] = len(spaces)
    first["selected_space_ids"] = "；".join(space.id for space in spaces)
    first["selected_space_names"] = "；".join(names)
    first["application_scope"] = (
        f"选中{len(spaces)}个房间内指定的外窗统一采用同一遮阳方案"
    )
    first["param_label"] = (
        f"选中{len(spaces)}房间｜{first.get('param_label', '')}"
    )
    first["cost_basis"] = (
        f"选中{len(spaces)}个房间年照明/空调电费及年化造价求和；"
        "采光和热指标按各房间楼面面积加权"
    )
    return first


def run_multi_space_experiments(
    spaces: List[SpaceModel],
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
    support_cost_per_m: float = DEFAULT_SUPPORT_COST_PER_M,
    install_cost_per_window: float = DEFAULT_INSTALL_COST_PER_WINDOW,
    latitude_deg: float = 28.59,
    north_angle_deg: float = 0.0,
    target_window_keys: Optional[Collection[str]] = None,
    operation_profile: BuildingUseProfile | dict | None = None,
    device_types: Optional[List[str]] = None,
    louver_params: Optional[dict] = None,
    light_shelf_params: Optional[dict] = None,
    constraints: ConstraintSet | dict | None = None,
    minimum_criterion: str = "annual_total_cost",
    enable_combinations: bool = True,
    combination_seed_count: int = 2,
) -> pd.DataFrame:
    """
    Apply each common shading scheme to every selected room and aggregate it.

    The result contains one point per building-level scheme, not one point per
    room.  Daylight/thermal KPIs are weighted by room floor area; construction
    quantities and costs are summed.  This makes the existing Pareto plots
    meaningful for a selected group of classrooms.
    """
    spaces = [deepcopy(space) for space in spaces]
    if not spaces:
        raise ValueError("至少需要选择一个房间运行参数化实验。")
    target_count = sum(
        len(selected_exterior_windows(space, target_window_keys))
        for space in spaces
    )
    if target_count <= 0:
        raise ValueError(
            "所选房间中没有被选为参数化遮阳位置的外窗。"
        )
    weather = weather or default_dataset()
    device_types = list(device_types or ["horizontal_overhang"])
    louver_params = dict(louver_params or {})
    light_shelf_params = dict(light_shelf_params or {})
    include_zero = (
        depth_mms is not None
        and any(float(value) <= 0.0 for value in depth_mms)
    )
    positive_depths = [
        float(value)
        for value in (depth_mms or [])
        if float(value) > 0.0
    ]
    room_frames = []
    completed = 0
    overhang_count = (
        len(tilt_degs or []) * len(positive_depths)
        * len(gap_mms or []) * max(1, len(materials or []))
        if "horizontal_overhang" in device_types else 0
    )
    louver_count = (
        len(louver_params.get("angles", [0, 30, 45, 60]))
        * len(louver_params.get("widths", [80, 120]))
        * len(louver_params.get("spacings", [100, 150]))
        * len(louver_params.get("positions", ["exterior"]))
        * len(louver_params.get("controls", ["fixed"]))
        * max(1, len(materials or []))
        if "louver" in device_types else 0
    )
    shelf_count = (
        len(light_shelf_params.get("inside_depths", [0, 300]))
        * len(light_shelf_params.get("outside_depths", [300, 600]))
        * len(light_shelf_params.get("tilts", [0]))
        * len(light_shelf_params.get("upper_ratios", [0.3, 0.4]))
        * max(1, len(materials or []))
        if "light_shelf" in device_types else 0
    )
    per_room_candidates = (
        (1 if include_baseline else 0)
        + (1 if include_zero else 0)
        + overhang_count + louver_count + shelf_count
    )
    total = max(1, per_room_candidates * len(spaces))

    for space in spaces:
        parts = []
        if include_baseline:
            parts.append(run_complex_project_baseline(
                space,
                weather=weather,
                ndiv=ndiv,
                grid_mm=grid_mm,
                ra_threshold=ra_threshold,
                latitude_deg=latitude_deg,
                north_angle_deg=north_angle_deg,
                eligible_as_no_shade=False,
                target_window_keys=target_window_keys,
                operation_profile=operation_profile,
            ))
            completed += 1
            if progress_cb:
                progress_cb(f"原始基准｜{space.name}", completed, total)
        if include_zero:
            parts.append(run_complex_no_shading_candidate(
                space,
                weather=weather,
                ndiv=ndiv,
                grid_mm=grid_mm,
                ra_threshold=ra_threshold,
                latitude_deg=latitude_deg,
                north_angle_deg=north_angle_deg,
                target_window_keys=target_window_keys,
                operation_profile=operation_profile,
            ))
            completed += 1
            if progress_cb:
                progress_cb(f"L=0｜{space.name}", completed, total)
        if positive_depths and "horizontal_overhang" in device_types:
            def _room_progress(_index, _room_total):
                nonlocal completed
                completed += 1
                if progress_cb:
                    progress_cb(
                        f"遮阳扫描｜{space.name}",
                        completed,
                        total,
                    )

            parts.append(run_complex_overhang_experiment(
                space,
                tilt_degs=tilt_degs,
                depth_mms=positive_depths,
                gap_mms=gap_mms,
                materials=materials,
                weather=weather,
                ndiv=ndiv,
                grid_mm=grid_mm,
                ra_threshold=ra_threshold,
                latitude_deg=latitude_deg,
                north_angle_deg=north_angle_deg,
                progress_cb=_room_progress,
                material_unit_costs=material_unit_costs,
                support_cost_per_m=support_cost_per_m,
                install_cost_per_window=install_cost_per_window,
                target_window_keys=target_window_keys,
                operation_profile=operation_profile,
            ))
        if "louver" in device_types:
            def _louver_progress(_index, _room_total):
                nonlocal completed
                completed += 1
                if progress_cb:
                    progress_cb(f"百叶扫描｜{space.name}", completed, total)

            parts.append(run_complex_louver_experiment(
                space, angles=louver_params.get("angles"),
                widths=louver_params.get("widths"),
                spacings=louver_params.get("spacings"),
                positions=louver_params.get("positions"),
                controls=louver_params.get("controls"), materials=materials,
                weather=weather, ndiv=ndiv, grid_mm=grid_mm,
                ra_threshold=ra_threshold, latitude_deg=latitude_deg,
                north_angle_deg=north_angle_deg, progress_cb=_louver_progress,
                material_unit_costs=material_unit_costs,
                support_cost_per_m=support_cost_per_m,
                install_cost_per_window=install_cost_per_window,
                target_window_keys=target_window_keys,
                operation_profile=operation_profile,
            ))
        if "light_shelf" in device_types:
            def _shelf_progress(_index, _room_total):
                nonlocal completed
                completed += 1
                if progress_cb:
                    progress_cb(f"反光板扫描｜{space.name}", completed, total)

            parts.append(run_complex_light_shelf_experiment(
                space,
                inside_depths=light_shelf_params.get("inside_depths"),
                outside_depths=light_shelf_params.get("outside_depths"),
                tilts=light_shelf_params.get("tilts"),
                upper_ratios=light_shelf_params.get("upper_ratios"),
                materials=materials, weather=weather, ndiv=ndiv,
                grid_mm=grid_mm, ra_threshold=ra_threshold,
                latitude_deg=latitude_deg, north_angle_deg=north_angle_deg,
                progress_cb=_shelf_progress,
                material_unit_costs=material_unit_costs,
                support_cost_per_m=support_cost_per_m,
                install_cost_per_window=install_cost_per_window,
                target_window_keys=target_window_keys,
                operation_profile=operation_profile,
            ))
        room_df = pd.concat(parts, ignore_index=True, sort=False)
        room_df["source_space_id"] = space.id
        room_df["source_space_name"] = space.name
        room_frames.append(room_df)

    detailed = pd.concat(room_frames, ignore_index=True, sort=False)

    def _case_key(row):
        group = str(row.get("group", ""))
        if group == "原始模型基准":
            return ("baseline",)
        if group == "无遮阳候选":
            return ("no_shading",)
        return (
            str(row.get("device_type", "horizontal_overhang")),
            str(row.get("param_label", "")),
        )

    detailed["_batch_case_key"] = [
        _case_key(row) for _index, row in detailed.iterrows()
    ]
    records = []
    for _key, rows in detailed.groupby(
        "_batch_case_key",
        sort=False,
        dropna=False,
    ):
        if len(rows) != len(spaces):
            raise ValueError(
                "多房间实验算例没有完整覆盖全部选中房间，"
                "请检查房间外窗和参数组合。"
            )
        records.append(_aggregate_case_rows(rows, spaces))
    result = pd.DataFrame(records)
    result = annotate_feasibility(result, constraints)

    # L2 is only entered if no L0/L1 solution satisfies every preregistered
    # hard constraint.  A small, auditable seed set prevents combinatorial
    # explosion; every combination below is nevertheless recalculated with the
    # actual joint geometry, never inferred by adding two single-device KPIs.
    early_feasible = result[
        result["feasible"].astype(bool)
        & (result["intervention_level_order"] <= 1)
    ]
    if enable_combinations and early_feasible.empty:
        seeds = select_combination_seeds(
            result, constraints, per_device=combination_seed_count,
        )
        pair_rows = [
            (left, right)
            for (_li, left), (_ri, right) in combinations(seeds.iterrows(), 2)
            if str(left.get("device_type", ""))
            != str(right.get("device_type", ""))
        ]
        combined_records = []
        for pair_index, (left, right) in enumerate(pair_rows, start=1):
            room_rows = []
            for space in spaces:
                combined_space = build_combination_space(
                    space, left, right, target_window_keys,
                )
                daylight = compute_complex_daylight(
                    combined_space, E_out=weather.annual_avg,
                    grid_mm=grid_mm, ndiv=ndiv, store_components=True,
                    ra_threshold=ra_threshold,
                )
                thermal = compute_complex_thermal(
                    combined_space, weather.monthly_ghi, weather.monthly_temp,
                    latitude_deg=latitude_deg, north_angle_deg=north_angle_deg,
                )
                scope = _scope_metrics(combined_space, target_window_keys)
                # Retrieve this room's two already-priced L1 records.
                source = detailed[detailed["source_space_id"] == space.id]
                label_a = str(left.get("param_label", ""))
                label_b = str(right.get("param_label", ""))
                prefix = f"选中{len(spaces)}房间｜"
                if label_a.startswith(prefix):
                    label_a = label_a[len(prefix):]
                if label_b.startswith(prefix):
                    label_b = label_b[len(prefix):]
                cost_a = pd.to_numeric(source.loc[
                    source["param_label"] == label_a,
                    "construction_cost",
                ], errors="coerce").sum()
                cost_b = pd.to_numeric(source.loc[
                    source["param_label"] == label_b,
                    "construction_cost",
                ], errors="coerce").sum()
                construction = float(cost_a + cost_b)
                record = _daylight_metrics(daylight)
                record.update(_thermal_metrics(thermal))
                record.update(scope)
                record.update(calculate_operation_metrics(
                    daylight_score=daylight.daylight_score,
                    thermal_result=thermal,
                    floor_area_m2=scope["floor_area_m2"],
                    construction_cost_yuan=construction,
                    profile=operation_profile,
                ))
                record.update({
                    "group": "两构件联合扫描",
                    "device_type": "combination",
                    "device_name": (
                        f"{left.get('device_name', '')}+{right.get('device_name', '')}"
                    ),
                    "intervention_level": "L2组合构件",
                    "component_count": 2,
                    "param_label": (
                        f"L2｜{left.get('param_label', '')} ＋ {right.get('param_label', '')}"
                    ),
                    "key_parameters": (
                        f"{left.get('key_parameters', '')}；{right.get('key_parameters', '')}"
                    ),
                    "material": (
                        f"{left.get('material', '')}+{right.get('material', '')}"
                    ),
                    "_plot_color": "#ec4899", "is_candidate": True,
                    "construction_cost": construction,
                    "panel_area_m2": float(left.get("panel_area_m2", 0.0))
                        + float(right.get("panel_area_m2", 0.0)),
                    "material_cost": float(left.get("material_cost", 0.0))
                        + float(right.get("material_cost", 0.0)),
                    "support_cost": float(left.get("support_cost", 0.0))
                        + float(right.get("support_cost", 0.0)),
                    "installation_cost": float(left.get("installation_cost", 0.0))
                        + float(right.get("installation_cost", 0.0)),
                    "construction_cost_basis": "两个L1构件工程量与费用分别计算后求和",
                    "combination_a": str(left.get("param_label", "")),
                    "combination_b": str(right.get("param_label", "")),
                })
                room_rows.append(record)
            combined_records.append(_aggregate_case_rows(
                pd.DataFrame(room_rows), spaces,
            ))
            if progress_cb:
                progress_cb("L2组合复算", pair_index, max(1, len(pair_rows)))
        if combined_records:
            result = pd.concat([
                result, pd.DataFrame(combined_records),
            ], ignore_index=True, sort=False)
            result = annotate_feasibility(result, constraints)

    result = annotate_minimum_intervention(
        result, constraints, criterion=minimum_criterion,
    )
    return result



