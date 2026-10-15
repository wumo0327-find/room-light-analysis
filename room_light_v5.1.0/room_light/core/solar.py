"""
core/solar.py — 太阳位置与遮阳剖面角  v2.5.0（新增）
======================================================
为遮阳精算（thermal.py 的 SC_effective 逐月计算）提供前置依赖：
  · 太阳赤纬 / 正午太阳高度角、方位角
  · 墙朝向方位角（含建筑偏转 orientation_deg）
  · 遮阳剖面角 profile angle

代表工况假设（逐月计算需选一个"代表时刻"，此假设需在论文中说明）：
  取每月「月中日（约15日）的太阳正午」位置。正午时太阳位于子午线上，方位角=正南
  （北半球、纬度>赤纬时），对南向窗最贴切——水平挑檐在正午遮阳最关键。
  局限：东/西向窗在正午几乎无直射（太阳在正南），本假设下挑檐对其月度遮阳无效，
  需改用日照时段均值方可刻画，留作后续版本。本项目窗朝向以南向为主，故采用正午。

参考:
  [1] Duffie J.A., Beckman W.A. Solar Engineering of Thermal Processes, 4th ed., 2013, §1.6
  [2] JGJ 237-2011 建筑遮阳工程技术规范
  [3] 柳孝图《建筑物理》第三版, 2010, 遮阳计算
"""
from __future__ import annotations
import math
from typing import Optional, List

# 每月「月中日」的年内日序（day-of-year），与 weather_fetcher 一致
MID_MONTH_DOY = [15, 45, 74, 105, 135, 162, 198, 228, 258, 288, 318, 344]

# 墙朝向的基准方位角（建筑未偏转时，°，自正北顺时针）
#   南=180, 北=0, 东=90, 西=270
WALL_AZIMUTH_BASE = {"south": 180.0, "north": 0.0, "east": 90.0, "west": 270.0}


def declination_deg(doy: int) -> float:
    """太阳赤纬 δ (°)  Cooper (1969) 近似式"""
    return 23.45 * math.sin(math.radians(360.0 * (284 + doy) / 365.0))


def solar_noon_altitude_deg(lat_deg: float, decl_deg: float) -> float:
    """太阳正午高度角 α (°)  = 90 − |纬度 − 赤纬|"""
    return 90.0 - abs(lat_deg - decl_deg)


def solar_noon_azimuth_deg(lat_deg: float, decl_deg: float) -> float:
    """
    太阳正午方位角 (°, 自正北顺时针)。
    北半球正午太阳在子午线上：纬度>赤纬 → 正南(180°)，否则正北(0°)。
    """
    return 180.0 if lat_deg >= decl_deg else 0.0


def wall_azimuth_deg(wall: str, orientation_deg: float = 0.0) -> float:
    """
    墙面外法向方位角 (°)。orientation_deg 为建筑相对正南的偏转
    （0=正南，正值向西偏，见 LocationParams）。
    """
    return (WALL_AZIMUTH_BASE.get(wall, 180.0) + orientation_deg) % 360.0


def profile_angle_deg(alt_deg: float, az_deg: float,
                      wall_az_deg: float) -> Optional[float]:
    """
    遮阳剖面角 (profile angle, °, 自水平起算)：
        tan(p) = tan(α) / cos(Δaz),   Δaz = 太阳方位 − 墙面方位
    太阳在墙背面 (cos(Δaz) ≤ 0) 或位于地平线下 (α ≤ 0) 时返回 None
    （该立面此刻无直射，挑檐对直射无遮挡意义）。
    """
    if alt_deg <= 0.0:
        return None
    c = math.cos(math.radians(az_deg - wall_az_deg))
    if c <= 1e-6:
        return None
    p = math.atan2(math.tan(math.radians(alt_deg)), c)
    return math.degrees(p)


def monthly_noon_profile_angles(lat_deg: float, wall: str,
                                orientation_deg: float = 0.0) -> List[Optional[float]]:
    """
    12 个月「月中日正午」对指定墙朝向的太阳剖面角序列 (°)。
    元素为 None 表示该月该立面正午无直射（如东/西向窗）。
    """
    waz = wall_azimuth_deg(wall, orientation_deg)
    out: List[Optional[float]] = []
    for doy in MID_MONTH_DOY:
        decl = declination_deg(doy)
        alt  = solar_noon_altitude_deg(lat_deg, decl)
        az   = solar_noon_azimuth_deg(lat_deg, decl)
        out.append(profile_angle_deg(alt, az, waz))
    return out
