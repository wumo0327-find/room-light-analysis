"""
io_utils/project_io.py — 项目文件保存与读取  v4.2.2
格式: JSON (.rlproj)  向后兼容所有历史版本

向后兼容策略:
  - 所有字段读取均使用 .get(key, default)
  - 缺失字段自动填入当前版本默认值
  - 旧版本文件（无 thermal/shading 字段）可正常加载
  - file_version 字段用于记录，不影响加载逻辑
"""
from __future__ import annotations
import json
import datetime
import os
from dataclasses import asdict, fields
from typing import Tuple, Optional

from core.models import (
    RoomModel, Window, MaterialParams, ThermalParams,
    ShadingDevice, LocationParams
)
from core.complex_models import (
    BoundaryLoop, BuildingModel, ExteriorBarrier, FloorLoop, Point2D,
    SpaceModel, StoreyModel,
    WallOpening, WallSegment,
)
from core.legacy_adapter import building_from_room
from core.space_geometry import has_geometry_errors, validate_space
from io_utils.weather_data import WeatherDataset, YIYANG_TMY_TEMP

FILE_VERSION = "4.2.2"
FILE_EXT     = ".rlproj"
FILE_FILTER  = "采光分析项目文件 (*.rlproj);;所有文件 (*)"


# ─────────────────────────────────────────────────────────────────────────────
def save_project(path: str, room: RoomModel,
                 weather: Optional[WeatherDataset] = None) -> None:
    """序列化完整项目到 JSON。"""
    t = room.thermal
    s = room.shading
    l = room.location

    data = {
        "file_version": FILE_VERSION,
        "project_kind":  "legacy_room",
        "saved_at":     datetime.datetime.now().isoformat(timespec="seconds"),
        "room": {
            "length": room.length,
            "width":  room.width,
            "height": room.height,
            "orientation_deg": l.orientation_deg,
            "windows": [
                {
                    "id":     w.id,
                    "wall":   w.wall,
                    "x":      w.x,
                    "y":      w.y,
                    "width":  w.width,
                    "height": w.height,
                    "tau":    w.tau,
                }
                for w in room.windows
            ],
            "material": {
                "rho_wall":    room.material.rho_wall,
                "rho_ceiling": room.material.rho_ceiling,
                "rho_floor":   room.material.rho_floor,
                "rho_ground":  room.material.rho_ground,
            },
            "thermal": {
                "U_wall":           t.U_wall,
                "U_roof":           t.U_roof,
                "U_floor":          t.U_floor,
                "U_win":            t.U_win,
                "wall_thickness_mm":t.wall_thickness_mm,
                "wall_density":     t.wall_density,
                "wall_specific_c":  t.wall_specific_c,
                "wall_solar_abs":   t.wall_solar_abs,
                "SC_glass":         t.SC_glass,
                "eta_frame":        t.eta_frame,
                "q_people":         t.q_people,
                "q_equipment":      t.q_equipment,
                "q_lighting":       t.q_lighting,
                "n_ach":            t.n_ach,
                "psi_edge":         t.psi_edge,
            },
            "shading": {
                "type":                  s.type,
                "overhang_depth_mm":     s.overhang_depth_mm,
                "overhang_height_mm":    s.overhang_height_mm,
                "overhang_tilt_deg":     s.overhang_tilt_deg,
                "overhang_overrides":    s.overhang_overrides,
                "diffuse_residual":      s.diffuse_residual,
                "visible_reflectance":   s.visible_reflectance,
                "solar_reflectance":     s.solar_reflectance,
                "thermal_emissivity":    s.thermal_emissivity,
                "specular_fraction":     s.specular_fraction,
                "vertical_fin_enabled":  s.vertical_fin_enabled,
                "vertical_fin_depth_mm": s.vertical_fin_depth_mm,
                "fin_column_width_mm":   s.fin_column_width_mm,
                "fin_overrides":         s.fin_overrides,
                "louver_width_mm":       s.louver_width_mm,
                "louver_angle_deg":      s.louver_angle_deg,
                "louver_spacing_mm":     s.louver_spacing_mm,
                "louver_reflect":        s.louver_reflect,
                "light_shelf_depth_mm":  s.light_shelf_depth_mm,
                "light_shelf_reflect":   s.light_shelf_reflect,
            },
            "location": {
                "latitude":        l.latitude,
                "longitude":       l.longitude,
                "timezone":        l.timezone,
                "orientation_deg": l.orientation_deg,
            },
        },
        "weather": {
            "source":       weather.source,
            "location":     weather.location,
            "monthly_lux":  weather.monthly_lux,
            "monthly_temp": weather.monthly_temp,
        } if weather is not None else None,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _weather_to_dict(weather: Optional[WeatherDataset]):
    if weather is None:
        return None
    return {
        "source": weather.source,
        "location": weather.location,
        "monthly_lux": weather.monthly_lux,
        "monthly_temp": weather.monthly_temp,
    }


def _weather_from_dict(data) -> Optional[WeatherDataset]:
    if not data or not data.get("monthly_lux"):
        return None
    lux = [float(value) for value in data["monthly_lux"]]
    if len(lux) != 12:
        return None
    raw_temp = data.get("monthly_temp", [])
    temperatures = (
        [float(value) for value in raw_temp]
        if len(raw_temp) == 12
        else list(YIYANG_TMY_TEMP)
    )
    return WeatherDataset(
        source=data.get("source", "项目文件"),
        location=data.get("location", ""),
        monthly_lux=lux,
        monthly_temp=temperatures,
    )


def _dataclass_from_dict(cls, data):
    """Construct a dataclass while ignoring fields added by newer files."""
    allowed = {item.name for item in fields(cls) if item.init}
    return cls(**{
        key: value
        for key, value in (data or {}).items()
        if key in allowed
    })


def _point_to_dict(point: Point2D):
    return [float(point.x), float(point.y)]


def _point_from_data(data) -> Point2D:
    if not isinstance(data, (list, tuple)) or len(data) != 2:
        raise ValueError(f"无效二维坐标：{data!r}")
    return Point2D(float(data[0]), float(data[1]))


def _opening_to_dict(opening: WallOpening):
    return {
        "id": opening.id,
        "kind": opening.kind,
        "name": opening.name,
        "offset_mm": opening.offset_mm,
        "width_mm": opening.width_mm,
        "sill_height_mm": opening.sill_height_mm,
        "height_mm": opening.height_mm,
        "visible_transmittance": opening.visible_transmittance,
        "u_value": opening.u_value,
        "solar_heat_gain_coefficient": opening.solar_heat_gain_coefficient,
        "plane_offset_mm": opening.plane_offset_mm,
        "metadata": opening.metadata,
    }


def _opening_from_dict(data) -> WallOpening:
    return WallOpening(
        id=str(data.get("id") or ""),
        kind=str(data.get("kind", "window")),
        name=str(data.get("name", "")),
        offset_mm=float(data.get("offset_mm", 0.0)),
        width_mm=float(data.get("width_mm", 1500.0)),
        sill_height_mm=float(data.get("sill_height_mm", 900.0)),
        height_mm=float(data.get("height_mm", 1500.0)),
        visible_transmittance=float(data.get("visible_transmittance", 0.71)),
        u_value=(
            None if data.get("u_value") is None
            else float(data["u_value"])
        ),
        solar_heat_gain_coefficient=(
            None
            if data.get("solar_heat_gain_coefficient") is None
            else float(data["solar_heat_gain_coefficient"])
        ),
        plane_offset_mm=float(data.get("plane_offset_mm", 0.0)),
        metadata=dict(data.get("metadata", {})),
    )


def _wall_to_dict(wall: WallSegment):
    return {
        "id": wall.id,
        "name": wall.name,
        "start": _point_to_dict(wall.start),
        "end": _point_to_dict(wall.end),
        "boundary_type": wall.boundary_type,
        "thickness_mm": wall.thickness_mm,
        "u_value": wall.u_value,
        "adjacent_space_id": wall.adjacent_space_id,
        "openings": [_opening_to_dict(item) for item in wall.openings],
        "metadata": wall.metadata,
    }


def _wall_from_dict(data) -> WallSegment:
    return WallSegment(
        id=str(data.get("id") or ""),
        name=str(data.get("name", "")),
        start=_point_from_data(data.get("start")),
        end=_point_from_data(data.get("end")),
        boundary_type=str(data.get("boundary_type", "exterior")),
        thickness_mm=float(data.get("thickness_mm", 240.0)),
        u_value=(
            None if data.get("u_value") is None
            else float(data["u_value"])
        ),
        adjacent_space_id=data.get("adjacent_space_id"),
        openings=[
            _opening_from_dict(item)
            for item in data.get("openings", [])
        ],
        metadata=dict(data.get("metadata", {})),
    )


def _barrier_to_dict(barrier: ExteriorBarrier):
    return {
        "id": barrier.id,
        "name": barrier.name,
        "kind": barrier.kind,
        "start": _point_to_dict(barrier.start),
        "end": _point_to_dict(barrier.end),
        "bottom_height_mm": barrier.bottom_height_mm,
        "top_height_mm": barrier.top_height_mm,
        "visible_transmittance": barrier.visible_transmittance,
        "ray_scope": barrier.ray_scope,
        "metadata": barrier.metadata,
    }


def _barrier_from_dict(data) -> ExteriorBarrier:
    return ExteriorBarrier(
        id=str(data.get("id") or ""),
        name=str(data.get("name", "")),
        kind=str(data.get("kind", "wall")),
        start=_point_from_data(data.get("start")),
        end=_point_from_data(data.get("end")),
        bottom_height_mm=float(data.get("bottom_height_mm", 0.0)),
        top_height_mm=float(data.get("top_height_mm", 1100.0)),
        visible_transmittance=float(
            data.get("visible_transmittance", 0.0)
        ),
        ray_scope=str(data.get("ray_scope", "outside_window")),
        metadata=dict(data.get("metadata", {})),
    )


def building_to_dict(building: BuildingModel):
    """Serialize a v4 building without losing source/audit metadata."""
    return {
        "id": building.id,
        "name": building.name,
        "north_angle_deg": building.north_angle_deg,
        "location": asdict(building.location),
        "metadata": building.metadata,
        "storeys": [
            {
                "id": storey.id,
                "name": storey.name,
                "elevation_mm": storey.elevation_mm,
                "default_height_mm": storey.default_height_mm,
                "metadata": storey.metadata,
                "spaces": [
                    {
                        "id": space.id,
                        "name": space.name,
                        "height_mm": space.height_mm,
                        "floor_elevation_mm": space.floor_elevation_mm,
                        "roof_exposed": bool(space.roof_exposed),
                        "floor_exposed": bool(space.floor_exposed),
                        "metadata": space.metadata,
                        "material": asdict(space.material),
                        "thermal": asdict(space.thermal),
                        "shading": asdict(space.shading),
                        "exterior_barriers": [
                            _barrier_to_dict(barrier)
                            for barrier in space.exterior_barriers
                        ],
                        "boundary_loops": [
                            {
                                "id": loop.id,
                                "name": loop.name,
                                "kind": loop.kind,
                                "metadata": loop.metadata,
                                "segments": [
                                    _wall_to_dict(wall)
                                    for wall in loop.segments
                                ],
                            }
                            for loop in space.boundary_loops
                        ],
                        "analysis_floor_loops": [
                            {
                                "id": loop.id,
                                "kind": loop.kind,
                                "metadata": loop.metadata,
                                "points": [
                                    [float(point.x), float(point.y)]
                                    for point in loop.points
                                ],
                            }
                            for loop in space.analysis_floor_loops
                        ],
                    }
                    for space in storey.spaces
                ],
            }
            for storey in building.storeys
        ],
    }


def building_from_dict(data) -> BuildingModel:
    """Parse v4/legacy building data. Geometry is validated by the caller."""
    storeys = []
    for storey_data in data.get("storeys", []):
        spaces = []
        for space_data in storey_data.get("spaces", []):
            loops = [
                BoundaryLoop(
                    id=str(loop_data.get("id") or ""),
                    name=str(loop_data.get("name", "")),
                    kind=str(loop_data.get("kind", "outer")),
                    metadata=dict(loop_data.get("metadata", {})),
                    segments=[
                        _wall_from_dict(item)
                        for item in loop_data.get("segments", [])
                    ],
                )
                for loop_data in space_data.get("boundary_loops", [])
            ]
            spaces.append(SpaceModel(
                id=str(space_data.get("id") or ""),
                name=str(space_data.get("name", "未命名空间")),
                height_mm=float(space_data.get("height_mm", 3000.0)),
                floor_elevation_mm=float(
                    space_data.get("floor_elevation_mm", 0.0)
                ),
                roof_exposed=bool(space_data.get("roof_exposed", True)),
                floor_exposed=bool(space_data.get("floor_exposed", True)),
                boundary_loops=loops,
                analysis_floor_loops=[
                    FloorLoop(
                        id=str(item.get("id") or ""),
                        kind=str(item.get("kind", "outer")),
                        metadata=dict(item.get("metadata", {})),
                        points=[
                            _point_from_data(point)
                            for point in item.get("points", [])
                        ],
                    )
                    for item in space_data.get("analysis_floor_loops", [])
                ],
                material=_dataclass_from_dict(
                    MaterialParams,
                    space_data.get("material"),
                ),
                thermal=_dataclass_from_dict(
                    ThermalParams,
                    space_data.get("thermal"),
                ),
                shading=_dataclass_from_dict(
                    ShadingDevice,
                    space_data.get("shading"),
                ),
                exterior_barriers=[
                    _barrier_from_dict(item)
                    for item in space_data.get("exterior_barriers", [])
                ],
                metadata=dict(space_data.get("metadata", {})),
            ))
        storeys.append(StoreyModel(
            id=str(storey_data.get("id") or ""),
            name=str(storey_data.get("name", "未命名楼层")),
            elevation_mm=float(storey_data.get("elevation_mm", 0.0)),
            default_height_mm=float(
                storey_data.get("default_height_mm", 3000.0)
            ),
            spaces=spaces,
            metadata=dict(storey_data.get("metadata", {})),
        ))
    return BuildingModel(
        id=str(data.get("id") or ""),
        name=str(data.get("name", "未命名建筑")),
        north_angle_deg=float(data.get("north_angle_deg", 0.0)),
        location=_dataclass_from_dict(
            LocationParams,
            data.get("location"),
        ),
        storeys=storeys,
        metadata=dict(data.get("metadata", {})),
    )


def save_building_project(
    path: str,
    building: BuildingModel,
    weather: Optional[WeatherDataset] = None,
    active_space_id: Optional[str] = None,
) -> None:
    """
    Save a validated v4 complex-space project.

    Invalid or unresolved geometry is rejected instead of being silently
    written into a file that the physical engines could later calculate.
    """
    all_issues = [
        issue
        for space in building.spaces()
        for issue in validate_space(space)
    ]
    if has_geometry_errors(all_issues):
        details = "\n".join(
            f"- {issue.message}"
            for issue in all_issues
            if issue.severity == "error"
        )
        raise ValueError(f"复杂空间几何未通过校验，不能保存：\n{details}")
    if active_space_id and building.get_space(active_space_id) is None:
        raise ValueError(f"当前计算空间不存在：{active_space_id}")

    data = {
        "file_version": FILE_VERSION,
        "project_kind": "building",
        "saved_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "active_space_id": active_space_id,
        "building": building_to_dict(building),
        "weather": _weather_to_dict(weather),
    }
    with open(path, "w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)


def load_building_project(path: str):
    """
    Load a v4/legacy building project or an old rectangle project.

    Returns ``(building, weather, active_space_id, error_msg)``.  Old projects
    are deterministically converted into one building/storey/space hierarchy.
    """
    try:
        with open(path, "r", encoding="utf-8") as stream:
            data = json.load(stream)
    except Exception as exc:
        return BuildingModel(), None, None, f"文件读取失败: {exc}"

    if data.get("building") is None:
        room, weather, error = load_project(path)
        if error:
            return BuildingModel(), None, None, error
        building = building_from_room(
            room,
            building_name=os.path.splitext(os.path.basename(path))[0],
        )
        return building, weather, "legacy_space", ""

    try:
        building = building_from_dict(data["building"])
        issues = [
            issue
            for space in building.spaces()
            for issue in validate_space(space)
        ]
        if has_geometry_errors(issues):
            details = "\n".join(
                f"- {issue.message}"
                for issue in issues
                if issue.severity == "error"
            )
            return (
                BuildingModel(),
                None,
                None,
                f"复杂空间几何校验失败：\n{details}",
            )
        active_space_id = data.get("active_space_id")
        if active_space_id and building.get_space(active_space_id) is None:
            return (
                BuildingModel(),
                None,
                None,
                f"当前计算空间不存在：{active_space_id}",
            )
        return (
            building,
            _weather_from_dict(data.get("weather")),
            active_space_id,
            "",
        )
    except Exception as exc:
        import traceback
        return BuildingModel(), None, None, (
            f"复杂空间项目解析失败: {exc}\n{traceback.format_exc()}"
        )


# ─────────────────────────────────────────────────────────────────────────────
def load_project(path: str) -> Tuple[RoomModel, Optional[WeatherDataset], str]:
    """
    从 .rlproj 加载项目（向后兼容所有历史版本）。
    返回 (room, weather, error_msg)；error_msg=="" 表示成功。
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return RoomModel(), None, f"文件读取失败: {e}"

    try:
        r_data = data.get("room", {})
        room   = RoomModel()

        # ── 基本尺寸（所有版本必有）──────────────────────────────────────────
        room.length = float(r_data.get("length", 6000))
        room.width  = float(r_data.get("width",  4000))
        room.height = float(r_data.get("height", 3000))

        # ── 窗户──────────────────────────────────────────────────────────────
        room.windows = []
        max_id = 0
        for wd in r_data.get("windows", []):
            w = Window(
                wall   = wd.get("wall",   "south"),
                x      = float(wd.get("x",      300)),
                y      = float(wd.get("y",      900)),
                width  = float(wd.get("width",  1500)),
                height = float(wd.get("height", 1500)),
                tau    = float(wd.get("tau",    0.71)),
                id     = int(wd.get("id",       0)),
            )
            room.windows.append(w)
            max_id = max(max_id, w.id)
        room._next_win_id = max_id + 1

        # ── 材料参数（v0.1+）────────────────────────────────────────────────
        md = r_data.get("material", {})
        room.material = MaterialParams(
            rho_wall    = float(md.get("rho_wall",    0.50)),
            rho_ceiling = float(md.get("rho_ceiling", 0.70)),
            rho_floor   = float(md.get("rho_floor",   0.20)),
            rho_ground  = float(md.get("rho_ground",  0.20)),
        )

        # ── 热工参数（v2.2+，旧文件全部使用默认值）──────────────────────────
        td = r_data.get("thermal", {})
        room.thermal = ThermalParams(
            U_wall           = float(td.get("U_wall",           1.50)),
            U_roof           = float(td.get("U_roof",           1.00)),
            U_floor          = float(td.get("U_floor",          1.50)),
            U_win            = float(td.get("U_win",            2.70)),
            wall_thickness_mm= float(td.get("wall_thickness_mm",240.0)),
            wall_density     = float(td.get("wall_density",     1800.0)),
            wall_specific_c  = float(td.get("wall_specific_c",  1050.0)),
            wall_solar_abs   = float(td.get("wall_solar_abs",   0.65)),
            SC_glass         = float(td.get("SC_glass",         0.85)),
            eta_frame        = float(td.get("eta_frame",        0.70)),
            q_people         = float(td.get("q_people",         6.0)),
            q_equipment      = float(td.get("q_equipment",      5.0)),
            q_lighting       = float(td.get("q_lighting",       4.0)),
            n_ach            = float(td.get("n_ach",            0.5)),
            psi_edge         = float(td.get("psi_edge",         0.10)),
        )

        # ── 遮阳参数（v2.2+）────────────────────────────────────────────────
        sd = r_data.get("shading", {})
        # v2.10: 逐窗/逐位置覆盖字典，旧文件无此字段则为空字典（等价于全部沿用全局值）
        # v2.12.0 修正：只保留JSON里实际写了的子键（depth_mm/gap_mm/tilt_deg），
        # 不再对缺失子键强填0.0——此前的写法会让"只覆盖gap_mm、没覆盖depth_mm"
        # 的条目被误当成"depth_mm覆盖为0"，导致该窗挑檐深度被错误清零而不是回退
        # 到全局默认值（get_overhang_for的.get(key, 全局默认)要求缺失键真的不存在）。
        raw_over = sd.get("overhang_overrides", {}) or {}
        overhang_overrides = {}
        for k, v in raw_over.items():
            if not isinstance(v, dict):
                continue
            entry = {}
            for key in ("depth_mm", "gap_mm", "tilt_deg"):
                if key in v:
                    entry[key] = float(v[key])
            if entry:
                overhang_overrides[str(k)] = entry
        raw_fin = sd.get("fin_overrides", {}) or {}
        fin_overrides = {str(k): float(v) for k, v in raw_fin.items()}

        room.shading = ShadingDevice(
            type               = sd.get("type",               "none"),
            overhang_depth_mm  = float(sd.get("overhang_depth_mm",  0.0)),
            overhang_height_mm = float(sd.get("overhang_height_mm", 0.0)),
            overhang_tilt_deg  = float(sd.get("overhang_tilt_deg",  90.0)),
            overhang_overrides = overhang_overrides,
            diffuse_residual   = float(sd.get("diffuse_residual",   0.30)),
            visible_reflectance= float(sd.get("visible_reflectance", 0.32)),
            solar_reflectance  = float(sd.get("solar_reflectance", 0.32)),
            thermal_emissivity = float(sd.get("thermal_emissivity", 0.90)),
            specular_fraction  = float(sd.get("specular_fraction", 0.03)),
            vertical_fin_enabled  = bool(sd.get("vertical_fin_enabled",  False)),
            vertical_fin_depth_mm = float(sd.get("vertical_fin_depth_mm", 0.0)),
            fin_column_width_mm  = float(sd.get("fin_column_width_mm",  540.0)),
            fin_overrides      = fin_overrides,
            louver_width_mm    = float(sd.get("louver_width_mm",    100.0)),
            louver_angle_deg   = float(sd.get("louver_angle_deg",   45.0)),
            louver_spacing_mm  = float(sd.get("louver_spacing_mm",  100.0)),
            louver_reflect     = float(sd.get("louver_reflect",     0.60)),
            light_shelf_depth_mm  = float(sd.get("light_shelf_depth_mm",  0.0)),
            light_shelf_reflect   = float(sd.get("light_shelf_reflect",   0.80)),
        )

        # ── 地理位置（v0.2+）────────────────────────────────────────────────
        ld = r_data.get("location", {})
        room.location = LocationParams(
            latitude        = float(ld.get("latitude",        28.59)),
            longitude       = float(ld.get("longitude",       112.33)),
            timezone        = int(ld.get("timezone",           8)),
            orientation_deg = float(ld.get("orientation_deg", 0.0)),
        )

        # ── 气象数据（v0.2+）────────────────────────────────────────────────
        weather = None
        wd_data = data.get("weather")
        if wd_data and wd_data.get("monthly_lux"):
            lux = [float(v) for v in wd_data["monthly_lux"]]
            if len(lux) == 12:
                # monthly_temp: v2.2+ 有此字段，旧版本填益阳默认值
                raw_temp = wd_data.get("monthly_temp", [])
                temp = ([float(v) for v in raw_temp]
                        if len(raw_temp) == 12
                        else list(YIYANG_TMY_TEMP))
                weather = WeatherDataset(
                    source       = wd_data.get("source",   "项目文件"),
                    location     = wd_data.get("location", ""),
                    monthly_lux  = lux,
                    monthly_temp = temp,
                )

        return room, weather, ""

    except Exception as e:
        import traceback
        return RoomModel(), None, (
            f"项目文件解析失败: {e}\n{traceback.format_exc()}")



