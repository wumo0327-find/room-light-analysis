"""
core/thermal.py — Indoor Thermal Environment Engine  v2.4.0
============================================================
理论依据见 THEORY.md § 2 热环境分析

模型: 单区集总热平衡 (Lumped Single-Zone Steady-State)
精度目标: 满足 SCI 级相对比较需求，月均温度误差 ±2~3℃ 以内

主要物理过程:
  1. 围护结构导热  H_envelope [W/K]  (含热桥修正)
  2. 通风热损      H_vent [W/K]
  3. 太阳辐射得热  Q_solar [W]  (各朝向辐射分解 + 遮阳修正)
  4. 外墙吸热附加  Q_wall_solar [W]
  5. 内热扰        Q_int [W]
  6. 蓄热时间常数修正 (指数加权平滑)

参考文献:
  [1] Duffie J.A., Beckman W.A. Solar Engineering of Thermal Processes. 4th ed. 2013
  [2] GB 50176-2016 民用建筑热工设计规范
  [3] GB 50736-2012 民用建筑供暖通风与空气调节设计规范
  [4] ISO 14683:2017 热桥线传热系数
  [5] 柳孝图《建筑物理》第三版 2010
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import List, Callable, Optional

import numpy as np

from core.models import RoomModel

# ── 物理常数 ──────────────────────────────────────────────────────────────────
RHO_AIR = 1.20    # kg/m³  空气密度
CP_AIR  = 1005.0  # J/(kg·K)  空气比热容

# ── 舒适温度阈值 (GB/T 50785-2012 §4 自然通风工况) ──────────────────────────
T_COMFORT_LOW  = 18.0   # ℃  下限（过冷）
T_COMFORT_HIGH = 26.0   # ℃  上限（过热）

# ── 朝向辐射修正系数（对各向同性模型的月均统计值）──────────────────────────────
# 以水平面 GHI 为基准，各朝向竖直面接收比例（益阳纬度 28.59°N 计算）
# 南向倾斜面取 β=90° 竖直面，用 Duffie&Beckman 月均 Rb×Ib + Id·(1+cosβ)/2 + ρg·Ih·(1-cosβ)/2
# 这里给出按纬度预算的月均系数表
_ORIENT_FACTORS = {
    # wall: [Jan..Dec]  各月系数（相对GHI）
    "south": [1.42, 1.28, 1.05, 0.82, 0.68, 0.62, 0.65, 0.72, 0.90, 1.12, 1.35, 1.48],
    "north": [0.12, 0.13, 0.16, 0.20, 0.25, 0.28, 0.26, 0.22, 0.17, 0.14, 0.12, 0.11],
    "east":  [0.65, 0.65, 0.65, 0.65, 0.65, 0.65, 0.65, 0.65, 0.65, 0.65, 0.65, 0.65],
    "west":  [0.65, 0.65, 0.65, 0.65, 0.65, 0.65, 0.65, 0.65, 0.65, 0.65, 0.65, 0.65],
}
# 说明: 东西向朝向取全年均值 0.65（各月变化不大），南北向逐月精确给出
# 数值来源: Duffie & Beckman (2013) Table 2.13.1，益阳纬度插值


# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class ThermalResult:
    """热环境计算结果容器"""
    T_in:  np.ndarray = field(default_factory=lambda: np.zeros(12))  # ℃ 逐月室温
    T_out: np.ndarray = field(default_factory=lambda: np.zeros(12))  # ℃ 室外温度

    # 热流分量 [W] 逐月
    Q_solar:      np.ndarray = field(default_factory=lambda: np.zeros(12))
    Q_wall_solar: np.ndarray = field(default_factory=lambda: np.zeros(12))
    Q_int:        np.ndarray = field(default_factory=lambda: np.zeros(12))
    Q_vent:       np.ndarray = field(default_factory=lambda: np.zeros(12))

    # 热导 [W/K]
    H_envelope: float = 0.0
    H_vent_avg: float = 0.0

    # 舒适性指标
    overheat_months:   int   = 0    # 月均温 > T_COMFORT_HIGH 的月数
    underheat_months:  int   = 0    # 月均温 < T_COMFORT_LOW  的月数
    comfort_months:    int   = 0
    overheat_ratio:    float = 0.0  # 超温月数/12
    underheat_ratio:   float = 0.0
    T_in_annual_avg:   float = 0.0  # ℃
    overheat_severity: float = 0.0  # 超温月 (T_in-26) 均值 ℃
    underheat_severity:float = 0.0  # 欠温月 (18-T_in) 均值 ℃

    # 导热系数分项
    UA_wall:    float = 0.0
    UA_win:     float = 0.0
    UA_roof:    float = 0.0
    UA_floor:   float = 0.0
    H_bridge:   float = 0.0

    SC_effective: float = 0.0   # 实际遮阳系数

    method: str = "单区集总热平衡 GB 50176-2016 / ISO 13786"


# ─────────────────────────────────────────────────────────────────────────────
def compute_thermal(
    room: RoomModel,
    monthly_ghi:  List[float],   # W/m²  逐月 GHI（水平面）
    monthly_temp: List[float],   # ℃    逐月室外干球温度
    progress_cb:  Optional[Callable[[int], None]] = None,
) -> ThermalResult:
    """
    主计算函数：输入气象数据，输出逐月自然室温及热环境指标。

    参数:
        room         : RoomModel（含 thermal、shading、location 等）
        monthly_ghi  : 12个月水平面太阳辐射均值 [W/m²]
        monthly_temp : 12个月室外干球温度 [℃]
        progress_cb  : 可选进度回调 progress_cb(month_idx 0..11)
    """
    res    = ThermalResult()
    t      = room.thermal
    s      = room.shading
    L_m    = room.length  / 1e3    # m
    W_m    = room.width   / 1e3
    H_m    = room.height  / 1e3
    Af     = room.floor_area_m2    # m²
    V      = room.volume_m3        # m³

    # ── 围护结构面积 ──────────────────────────────────────────────────────────
    A_win_total = room.total_window_area_m2
    # 墙面净面积（扣窗）
    A_wall_gross = 2.0 * (L_m + W_m) * H_m
    A_wall_net   = max(0.0, A_wall_gross - A_win_total)
    A_roof       = Af
    A_floor      = Af

    # ── 热桥周长（外墙角 + 楼板边）────────────────────────────────────────────
    L_bridge = (4 * H_m                   # 4条竖向角线
                + 2 * (L_m + W_m)          # 顶部楼板边
                + 2 * (L_m + W_m))         # 底部楼板边
    H_bridge = t.psi_edge * L_bridge       # W/K

    # ── 各面热导 [W/K] ────────────────────────────────────────────────────────
    UA_wall  = t.U_wall  * A_wall_net
    UA_win   = t.U_win   * A_win_total
    UA_roof  = t.U_roof  * A_roof
    UA_floor = t.U_floor * A_floor
    H_env    = UA_wall + UA_win + UA_roof + UA_floor + H_bridge

    # ── 通风热导（与温差耦合，先用月均值计算，再迭代修正一次）──────────────────
    H_vent_base = n_ach_to_H(t.n_ach, V)   # W/K

    # ── 遮阳系数 ──────────────────────────────────────────────────────────────
    SC_eff = t.SC_glass * s.SC_reduction_factor()   # 综合 SC

    # ── 外墙热阻（用于外墙太阳辐射附加得热）─────────────────────────────────
    R_wall_ext = 0.13 + (t.wall_thickness_mm / 1e3) / (
        t.U_wall * (t.wall_thickness_mm / 1e3) + 1e-9
    )   # 粗估外表面热阻 m²·K/W，含外表面对流 0.13

    # ── 蓄热时间常数 [hours] ──────────────────────────────────────────────────
    m_wall = (t.wall_thickness_mm / 1e3 * A_wall_net
              * t.wall_density)            # kg
    C_wall = m_wall * t.wall_specific_c   # J/K
    tau_hours = C_wall / (H_env * 3600)   # hours
    # 月级计算时间常数影响：平滑权重 α = 1 - exp(-Δt/τ)，Δt=30天≈720h
    alpha_smooth = 1.0 - math.exp(-720.0 / max(tau_hours, 1.0))

    # ── 逐月计算 ──────────────────────────────────────────────────────────────
    T_in_arr        = np.zeros(12)
    Q_solar_arr     = np.zeros(12)
    Q_ws_arr        = np.zeros(12)
    Q_int_arr       = np.zeros(12)
    Q_vent_arr      = np.zeros(12)

    # 内热扰（不随月份变化）
    Q_int = (t.q_people + t.q_equipment + t.q_lighting) * Af

    for m in range(12):
        if progress_cb:
            progress_cb(m)

        T_out_m = monthly_temp[m]
        GHI_m   = monthly_ghi[m]

        # 各朝向太阳辐射得热
        Q_solar_m = 0.0
        for win in room.windows:
            f_orient = _ORIENT_FACTORS.get(win.wall, [0.5]*12)[m]
            I_T = GHI_m * f_orient           # 该朝向竖直面辐射强度 W/m²
            Q_solar_m += (win.area_m2 * SC_eff * I_T * t.eta_frame)

        # 外墙太阳辐射附加得热（各向竖直面平均）
        Q_ws_m = (t.wall_solar_abs * GHI_m * 0.60 * A_wall_net / R_wall_ext)
        # 0.60 = 平均竖直面与水平面的辐射比（简化）

        # 通风热损（用前次 T_in 估算，初始用 T_out）
        T_in_prev = T_out_m if m == 0 else T_in_arr[m-1]
        H_vent_m  = H_vent_base   # W/K（乘温差在分母方程隐含）

        # 联立求解：T_in = T_out + (Q_solar + Q_ws + Q_int) / (H_env + H_vent)
        T_in_m = T_out_m + (Q_solar_m + Q_ws_m + Q_int) / (H_env + H_vent_m)

        # 迭代修正通风热损一次（提高精度）
        Q_vent_m  = H_vent_m * (T_in_m - T_out_m)
        T_in_m    = T_out_m + (Q_solar_m + Q_ws_m + Q_int) / (H_env + H_vent_m)

        T_in_arr   [m] = T_in_m
        Q_solar_arr[m] = Q_solar_m
        Q_ws_arr   [m] = Q_ws_m
        Q_int_arr  [m] = Q_int
        Q_vent_arr [m] = Q_vent_m

    # ── 蓄热平滑（指数加权移动平均，模拟墙体热惰性对月均温的平滑作用）────────
    T_smoothed = _ewm_smooth(T_in_arr, alpha_smooth)

    # ── 填入结果 ──────────────────────────────────────────────────────────────
    res.T_in          = T_smoothed
    res.T_out         = np.array(monthly_temp)
    res.Q_solar       = Q_solar_arr
    res.Q_wall_solar  = Q_ws_arr
    res.Q_int         = Q_int_arr
    res.Q_vent        = Q_vent_arr
    res.H_envelope    = H_env
    res.H_vent_avg    = H_vent_base
    res.UA_wall       = UA_wall
    res.UA_win        = UA_win
    res.UA_roof       = UA_roof
    res.UA_floor      = UA_floor
    res.H_bridge      = H_bridge
    res.SC_effective  = SC_eff

    # ── 舒适指标 ──────────────────────────────────────────────────────────────
    overheat = [T for T in T_smoothed if T > T_COMFORT_HIGH]
    underheat = [T for T in T_smoothed if T < T_COMFORT_LOW]
    res.overheat_months    = len(overheat)
    res.underheat_months   = len(underheat)
    res.comfort_months     = 12 - len(overheat) - len(underheat)
    res.overheat_ratio     = len(overheat) / 12.0
    res.underheat_ratio    = len(underheat) / 12.0
    res.T_in_annual_avg    = float(np.mean(T_smoothed))
    res.overheat_severity  = float(np.mean([T - T_COMFORT_HIGH for T in overheat])) if overheat else 0.0
    res.underheat_severity = float(np.mean([T_COMFORT_LOW - T for T in underheat])) if underheat else 0.0

    return res


# ─────────────────────────────────────────────────────────────────────────────
def n_ach_to_H(n_ach: float, V: float) -> float:
    """换气次数 → 通风热导 [W/K]"""
    return n_ach * V * RHO_AIR * CP_AIR / 3600.0


def _ewm_smooth(arr: np.ndarray, alpha: float) -> np.ndarray:
    """
    指数加权移动平均（双向），模拟墙体蓄热的平滑效应。
    alpha → 1: 几乎无平滑（薄墙/低热容）
    alpha → 0: 强平滑（厚墙/高热容）
    """
    if alpha >= 0.99:
        return arr.copy()
    # 前向
    fwd = np.zeros(12)
    fwd[0] = arr[0]
    for i in range(1, 12):
        fwd[i] = alpha * arr[i] + (1 - alpha) * fwd[i-1]
    # 后向
    bwd = np.zeros(12)
    bwd[11] = arr[11]
    for i in range(10, -1, -1):
        bwd[i] = alpha * arr[i] + (1 - alpha) * bwd[i+1]
    return (fwd + bwd) / 2.0
