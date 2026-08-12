"""
io_utils/project_io.py — 项目文件保存与读取
格式: JSON (.rlproj)
包含: 房间几何、窗户列表、材料参数、地理位置、气象数据
"""
from __future__ import annotations
import json
import datetime
from typing import Tuple

from core.models import RoomModel, Window, MaterialParams, LocationParams
from io_utils.weather_data import WeatherDataset

FILE_VERSION = "2.1.4"
FILE_EXT     = ".rlproj"
FILE_FILTER  = "采光分析项目文件 (*.rlproj);;所有文件 (*)"


# ── 序列化 ────────────────────────────────────────────────────────────────────

def save_project(path: str, room: RoomModel,
                 weather: WeatherDataset | None = None) -> None:
    """
    将 RoomModel + WeatherDataset 序列化为 JSON 并写入 path。
    path 建议以 .rlproj 为后缀。
    """
    data = {
        "file_version": FILE_VERSION,
        "saved_at":     datetime.datetime.now().isoformat(timespec="seconds"),
        "room": {
            "length": room.length,
            "width":  room.width,
            "height": room.height,
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
            "location": {
                "latitude":  room.location.latitude,
                "longitude": room.location.longitude,
                "timezone":  room.location.timezone,
            },
        },
        "weather": {
            "source":      weather.source      if weather else "",
            "location":    weather.location    if weather else "",
            "monthly_lux": weather.monthly_lux if weather else [],
        } if weather is not None else None,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ── 反序列化 ──────────────────────────────────────────────────────────────────

def load_project(path: str) -> Tuple[RoomModel, WeatherDataset | None, str]:
    """
    从 .rlproj 文件读取 RoomModel 和 WeatherDataset。
    返回 (room, weather, error_msg)。
    error_msg == "" 表示成功；否则包含错误描述，room/weather 为默认值。
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return RoomModel(), None, f"文件读取失败: {e}"

    try:
        r_data = data["room"]

        # 房间基本尺寸
        room = RoomModel()
        room.length = float(r_data["length"])
        room.width  = float(r_data["width"])
        room.height = float(r_data["height"])

        # 窗户列表
        room.windows = []
        max_id = 0
        for wd in r_data.get("windows", []):
            w = Window(
                wall   = wd["wall"],
                x      = float(wd["x"]),
                y      = float(wd["y"]),
                width  = float(wd["width"]),
                height = float(wd["height"]),
                tau    = float(wd.get("tau", 0.71)),
                id     = int(wd.get("id", 0)),
            )
            room.windows.append(w)
            max_id = max(max_id, w.id)
        # 恢复 id 计数器，避免新增窗户 id 冲突
        room._next_win_id = max_id + 1

        # 材料参数
        if "material" in r_data:
            md = r_data["material"]
            room.material = MaterialParams(
                rho_wall    = float(md.get("rho_wall",    0.50)),
                rho_ceiling = float(md.get("rho_ceiling", 0.70)),
                rho_floor   = float(md.get("rho_floor",   0.20)),
                rho_ground  = float(md.get("rho_ground",  0.20)),
            )

        # 地理位置
        if "location" in r_data:
            ld = r_data["location"]
            room.location = LocationParams(
                latitude  = float(ld.get("latitude",  28.59)),
                longitude = float(ld.get("longitude", 112.33)),
                timezone  = int(ld.get("timezone", 8)),
            )

        # 气象数据
        weather = None
        w_data  = data.get("weather")
        if w_data and w_data.get("monthly_lux"):
            lux = [float(v) for v in w_data["monthly_lux"]]
            if len(lux) == 12:
                weather = WeatherDataset(
                    source      = w_data.get("source",   "项目文件"),
                    location    = w_data.get("location", ""),
                    monthly_lux = lux,
                )

        return room, weather, ""

    except Exception as e:
        import traceback
        return RoomModel(), None, f"项目文件解析失败: {e}\n{traceback.format_exc()}"
