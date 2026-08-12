"""
io_utils/project_io.py — 项目文件保存与读取  v2.12.0
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
from typing import Tuple, Optional

from core.models import (
    RoomModel, Window, MaterialParams, ThermalParams,
    ShadingDevice, LocationParams
)
from io_utils.weather_data import WeatherDataset, YIYANG_TMY_TEMP

FILE_VERSION = "2.2.0"
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
