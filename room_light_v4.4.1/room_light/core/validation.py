"""
core/validation.py — 按坐标取 DF / 生成实测对比测线  v2.6.0（新增）
======================================================================
用于把程序的采光计算结果与真实教室实测数据交叉验证，不改动 Ds/Dext/Dint 核心算法，
只是在已算好的 DaylightResult 网格上做取值/取点的轻量封装。

坐标系（与 core/daylight.py 完全一致）：
  世界坐标，单位米：X = 面宽方向，Y = 进深方向；DaylightResult.grid_x/grid_y 为
  1-D 网格坐标（单位 mm），DF/E_lux/Ds/Dext/Dint 为 (ny, nx) 数组，[iy, ix] 对应
  (grid_x[ix], grid_y[iy])。网格为规则等间距（np.linspace 生成），故可直接做双线性插值。

窗户局部坐标 → 世界坐标：Window.x/y 是"沿墙局部坐标"（mm，从墙左边缘/地面起算），
  不是世界坐标。换算复用 core/daylight._wall_axes(wall) 给出的 (法向, u轴, v轴, 原点)
  变换，与核心引擎保持完全一致的朝向约定，不重新发明。
"""
from __future__ import annotations
from typing import Optional, List, Tuple, Dict, Any

import numpy as np

from core.models import RoomModel, WALL_MAP_R
from core.daylight import DaylightResult, _wall_axes, compute as _compute_daylight


# ── 双线性插值（网格规则，退化为 1×N/N×1 时自动降为线性/常数）──────────────────
def _interp_field(grid_x_mm: np.ndarray, grid_y_mm: np.ndarray,
                  arr: np.ndarray, x_mm: float, y_mm: float) -> Tuple[float, bool]:
    """返回 (插值结果, 是否落在网格范围内)。范围外按边界值双线性外推（clamp 坐标）。"""
    nx, ny = len(grid_x_mm), len(grid_y_mm)
    in_x = grid_x_mm[0] <= x_mm <= grid_x_mm[-1]
    in_y = grid_y_mm[0] <= y_mm <= grid_y_mm[-1]
    xg = min(max(x_mm, grid_x_mm[0]), grid_x_mm[-1])
    yg = min(max(y_mm, grid_y_mm[0]), grid_y_mm[-1])

    if nx == 1:
        ix0 = ix1 = 0; tx = 0.0
    else:
        ix0 = int(np.searchsorted(grid_x_mm, xg, side="right") - 1)
        ix0 = min(max(ix0, 0), nx - 2)
        ix1 = ix0 + 1
        dx = grid_x_mm[ix1] - grid_x_mm[ix0]
        tx = (xg - grid_x_mm[ix0]) / dx if dx > 1e-9 else 0.0

    if ny == 1:
        iy0 = iy1 = 0; ty = 0.0
    else:
        iy0 = int(np.searchsorted(grid_y_mm, yg, side="right") - 1)
        iy0 = min(max(iy0, 0), ny - 2)
        iy1 = iy0 + 1
        dy = grid_y_mm[iy1] - grid_y_mm[iy0]
        ty = (yg - grid_y_mm[iy0]) / dy if dy > 1e-9 else 0.0

    v00, v10 = arr[iy0, ix0], arr[iy0, ix1]
    v01, v11 = arr[iy1, ix0], arr[iy1, ix1]
    v0 = v00 * (1 - tx) + v10 * tx
    v1 = v01 * (1 - tx) + v11 * tx
    return float(v0 * (1 - ty) + v1 * ty), (in_x and in_y)


def sample_df_at_points(
    result_or_room,
    points_xy: List[Tuple[float, float]],
    E_out: float = 13500.0,
    ndiv: Optional[int] = None,
    grid_mm: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """
    在任意 (x,y) 世界坐标点（米，x=面宽，y=进深）上取 DF 及其分量。

    result_or_room: 传入已算好的 DaylightResult 可复用同一次网格（多条测线共用，
        避免重复计算）；也可直接传 RoomModel，内部按 E_out/ndiv/grid_mm 现算一次。
    取值方式：网格是规则等间距的，双线性插值成本很低，因此始终做双线性插值
        （不退化为最近点）；点落在网格有效范围外时对坐标做边界 clamp 后再插值，
        并在返回的 "method" 字段标注 "bilinear" / "bilinear_clamped"，
        供调用方判断该测点是否落在计算网格覆盖范围内。
    """
    if isinstance(result_or_room, RoomModel):
        kwargs = {"E_out": E_out}
        if ndiv is not None:
            kwargs["ndiv"] = ndiv
        if grid_mm is not None:
            kwargs["grid_mm"] = grid_mm
        result: DaylightResult = _compute_daylight(result_or_room, **kwargs)
    else:
        result = result_or_room

    gx, gy = result.grid_x, result.grid_y
    out = []
    for x_m, y_m in points_xy:
        x_mm, y_mm = x_m * 1e3, y_m * 1e3
        DF, ok = _interp_field(gx, gy, result.DF, x_mm, y_mm)
        rec = {"x": x_m, "y": y_m, "DF": DF,
              "method": "bilinear" if ok else "bilinear_clamped"}
        for name, arr in (("Ds", result.Ds), ("Dext", result.Dext),
                          ("Dint", result.Dint), ("E_lux", result.E_lux)):
            rec[name] = _interp_field(gx, gy, arr, x_mm, y_mm)[0] if arr is not None else None
        out.append(rec)
    return out


# ── 便捷生成两条测线 ──────────────────────────────────────────────────────────
def make_probe_lines(
    room: RoomModel,
    n_points: int = 5,
    first_offset_m: float = 0.5,
    spacing_m: float = 0.5,
    window_id: Optional[int] = None,
    wall: Optional[str] = None,
) -> Dict[str, Any]:
    """
    生成"沿窗中线"与"沿窗间墙中线"两条测线的世界坐标点（米），供实测比对。

    · window_center_line: x = 指定窗户（默认该墙第一扇）的水平中心，
                          y 从窗面沿房间进深方向、按 first_offset_m 起、
                          spacing_m 等间距取 n_points 个点。
    · pier_center_line:   x = 与该窗相邻的窗间墙垛水平中心，y 同上；
                          若该墙只有一扇窗（找不到窗间墙），返回 None 并在
                          "warnings" 中说明，调用方应跳过该测线。

    window_id 指定则据此确定 wall 与目标窗；否则用 wall（默认优先南墙，
    其次任意有窗的墙）上的第一扇窗。
    """
    warnings: List[str] = []

    if window_id is not None:
        win = room.get_window(window_id)
        if win is None:
            raise ValueError(f"未找到窗户 id={window_id}")
        wall = win.wall
    else:
        if wall is None:
            wall = next((w for w in ("south", "north", "east", "west")
                        if room.windows_on(w)), None)
            if wall is None:
                raise ValueError("房间没有任何窗户，无法生成测线")
        wins_on_wall = sorted(room.windows_on(wall), key=lambda w: w.x)
        if not wins_on_wall:
            raise ValueError(f"{WALL_MAP_R.get(wall, wall)}墙没有窗户")
        win = wins_on_wall[0]

    wins_on_wall = sorted(room.windows_on(wall), key=lambda w: w.x)

    norm, uax, vax, orig_fn = _wall_axes(wall)
    W_m, L_m = room.width / 1e3, room.length / 1e3
    orig = orig_fn(W_m, L_m)   # 米

    def _local_to_world(u_m: float) -> Tuple[float, float]:
        p = orig + uax * u_m
        return float(p[0]), float(p[1])

    def _make_line(u_m: float) -> List[Tuple[float, float]]:
        x0, y0 = _local_to_world(u_m)
        pts = []
        for i in range(n_points):
            depth = first_offset_m + i * spacing_m
            pts.append((x0 + float(norm[0]) * depth, y0 + float(norm[1]) * depth))
        return pts

    win_center_u = win.x / 1e3 + win.width / 2e3
    window_center_line = _make_line(win_center_u)

    pier_center_line = None
    if len(wins_on_wall) >= 2:
        idx = wins_on_wall.index(win)
        if idx < len(wins_on_wall) - 1:
            a, b = wins_on_wall[idx], wins_on_wall[idx + 1]
        else:
            a, b = wins_on_wall[idx - 1], wins_on_wall[idx]
        pier_u = (a.right / 1e3 + b.x / 1e3) / 2.0
        pier_center_line = _make_line(pier_u)
    else:
        warnings.append(
            f"{WALL_MAP_R.get(wall, wall)}墙只有 1 扇窗，找不到明确的窗间墙，"
            "已跳过 pier_center_line。")

    return {
        "window_center_line": window_center_line,
        "pier_center_line":   pier_center_line,
        "wall":               wall,
        "window_id":          win.id,
        "warnings":           warnings,
    }
