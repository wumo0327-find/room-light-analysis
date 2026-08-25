"""
core/daylight.py — Daylight Factor & Illuminance Engine  v2.10.2
==============================================================
理论依据见 README.md § 计算理论验证
所有内部坐标: mm；物理计算转换为 m

高精度模式（对照实验专用）:
  - WIN_DIV = 40  → 1600个面元/窗，立体角精度提升 4×
  - 网格步长 250mm → 更密集测点捕捉照度梯度
  - 双精度浮点贯穿全部计算
  - 微小参数变化（如窗宽 ±10mm、τ ±0.01）均可产生可辨别的结果差异
"""
from __future__ import annotations
import math
from typing import Optional
import numpy as np
from core.models import RoomModel

# ── 计算参数 ──────────────────────────────────────────────────────────────────
WORK_PLANE_MM  = 750.0    # 工作面高度 GB/T 50033-2013
GRID_MM        = 250.0    # 网格步长（越小越敏感，250mm → 精细梯度）
WIN_DIV        = 40       # 窗口立体角离散数 (40×40=1600面元，高精度)
WALL_MARGIN_MM = 500.0    # 边界剔除 GB/T 50033


def _wall_axes(wall: str):
    """(法向量 inward, u轴, v轴, 原点偏移函数)"""
    if wall == "south":
        return (np.array([0.,1.,0.]), np.array([1.,0.,0.]), np.array([0.,0.,1.]),
                lambda W,L: np.array([0.,0.,0.]))
    if wall == "north":
        return (np.array([0.,-1.,0.]), np.array([-1.,0.,0.]), np.array([0.,0.,1.]),
                lambda W,L: np.array([W,L,0.]))
    if wall == "east":
        return (np.array([-1.,0.,0.]), np.array([0.,-1.,0.]), np.array([0.,0.,1.]),
                lambda W,L: np.array([W,L,0.]))
    if wall == "west":
        return (np.array([1.,0.,0.]), np.array([0.,1.,0.]), np.array([0.,0.,1.]),
                lambda W,L: np.array([0.,0.,0.]))
    raise ValueError(wall)


def _fin_slots_full_m(room: RoomModel, wall: str):
    """
    v2.10.1（垂直翼板遮挡几何重新设计）: 返回该墙全部垂直翼板/装饰柱位置的完整
    立体槽形状（米）[(u0, u1, D_eff, z0, z1), ...]，含两端外墙位置与窗间墙位置。

    与 v2.10.0 的区别（根因见下方"诊断"）：翼板/装饰柱不再被当作贴在"某一扇
    窗户自己边缘"的零宽度虚拟面，而是当作一个真实占据横向 [u0,u1]（窗间墙/端墙
    的真实宽度）、出挑方向 [0,D_eff]、竖向 [z0,z1] 的实体（长方体）。任意窗户的
    天空面元，只要其视线的连续路径穿过这个长方体的体积，就算被遮挡——不再限定
    "只测试自身两侧那两个位置"。

    诊断（v2.10.0 遗留问题的根因，解析证明）：v2.10.0 的判定把翼板当作贴在窗户
    自身边缘的零宽度面，且只用该窗自身的面元去测试该窗自身两侧的面。可以证明：
    若测点 P 位于某扇窗 w 的横向范围之外（例如窗间墙测点），则 P 看向 w 自身
    任一面元、并测试 w 自身"近侧"翼板面时，射线到达该面的参数恒有 t<1（在到达
    窗洞面之前就已经"穿过"该翼板面所在的横向坐标），因此 v2.10.0 的判定条件
    （要求 t≥1，即遮挡必须发生在窗洞面之外的室外空间）恒不成立——即窗间墙测点
    通过"紧邻窗户自身翼板"这条路径，理论上永远不会被判定为遮挡，这正是 v2.9.1~
    v2.10.0 一直记录的"窗间墙测点遮挡幅度被系统性低估"的数学根源，并非概率性的
    "多数情况漏判"。改为完整体积相交判定后，窗间墙测点自身就位于翼板体积的横向
    范围内，天然能触发遮挡；窗户自身中心测点则仍按其视线是否真的穿过该体积精确
    判定，不再有上述结构性缺陷。

    深度换算：出挑深度已按 v2.10.1 的墙厚修正（get_fin_depth_for 取到的是标注
    深度，此处再扣减 room.thermal.wall_thickness_mm 得到有效出挑，见该版本变更
    日志的墙厚偏移诊断），深度≤0 的位置不返回（不生成遮挡）。

    v2.10.2 修正（柱宽，非概率性——按用户提供的现场CAD图纸核实）：两端外墙
    位置的柱子宽度是固定值 room.shading.fin_column_width_mm（现场实测540mm，
    与窗间墙宽度巧合相同但概念不同），柱子贴着窗户边缘、只占这么宽，端墙上
    柱子之外剩余的墙体是普通墙、不参与遮挡。v2.10.0/v2.10.1 曾把端墙柱子
    错误地画成"整段端墙剩余宽度"（如1540mm），比真实柱宽宽了近3倍，是窗1/
    窗5验证误差异常恶化的直接原因。窗间墙位置的柱子仍按真实窗间墙宽度（该
    宽度本身就等于现场柱宽，是柱子恰好填满整个窗间墙——两者数值相同是巧合，
    不是同一个参数）。
    """
    sh = room.shading
    if not getattr(sh, "vertical_fin_enabled", False):
        return []
    wins = sorted(room.windows_on(wall), key=lambda w: w.x)
    if not wins:
        return []
    axis_len_mm = wins[0].wall_length(room)
    wall_thick_mm = room.thermal.wall_thickness_mm
    col_w_mm = max(0.0, sh.fin_column_width_mm)

    def _eff_m(nominal_mm: float) -> float:
        return max(0.0, nominal_mm - wall_thick_mm) / 1e3

    slots = []
    d0 = sh.get_fin_depth_for(wins[0].id, "L")
    D0 = _eff_m(d0)
    if D0 > 0.0 and wins[0].x > 1e-6:
        w0 = min(col_w_mm, wins[0].x)   # 柱宽不超过端墙可用宽度，避免越界
        if w0 > 1e-6:
            slots.append((max(0.0, wins[0].x - w0) / 1e3, wins[0].x / 1e3, D0,
                          wins[0].sill / 1e3, wins[0].head / 1e3))
    for i in range(len(wins) - 1):
        u0_mm, u1_mm = wins[i].right, wins[i + 1].x
        if u1_mm <= u0_mm + 1e-6:
            continue
        d_r = sh.get_fin_depth_for(wins[i].id, "R")
        d_l = sh.get_fin_depth_for(wins[i + 1].id, "L")
        D = _eff_m(max(d_r, d_l))
        if D > 0.0:
            z0 = min(wins[i].sill, wins[i + 1].sill) / 1e3
            z1 = max(wins[i].head, wins[i + 1].head) / 1e3
            slots.append((u0_mm / 1e3, u1_mm / 1e3, D, z0, z1))
    d_last = sh.get_fin_depth_for(wins[-1].id, "R")
    Dlast = _eff_m(d_last)
    if Dlast > 0.0 and wins[-1].right < axis_len_mm - 1e-6:
        w_last = min(col_w_mm, axis_len_mm - wins[-1].right)
        if w_last > 1e-6:
            slots.append((wins[-1].right / 1e3, (wins[-1].right + w_last) / 1e3, Dlast,
                          wins[-1].sill / 1e3, wins[-1].head / 1e3))
    return slots


def _rho_bar(room: RoomModel) -> float:
    """
    BRS 加权平均反射率  ρ̄ = Σ(ρi·Ai) / ΣAi
    Hopkinson et al. 1966, p.384
    """
    m = room.material
    L, W, H = room.length/1e3, room.width/1e3, room.height/1e3
    Ac = L * W
    Af = L * W
    Aw_gross = 2*(L+W)*H
    Aw_wins  = sum((w.width/1e3)*(w.height/1e3) for w in room.windows)
    Aw = max(0., Aw_gross - Aw_wins)
    tot = Ac + Af + Aw
    if tot < 1e-12:
        return 0.5
    return (m.rho_ceiling*Ac + m.rho_floor*Af + m.rho_wall*Aw) / tot


def _ds_point(P: np.ndarray, win, room: RoomModel, ndiv: int = WIN_DIV) -> float:
    """
    天空分量 Ds (%) — 向量化立体角数值积分
    CIE 110-1994 §4;  Hopkinson et al. 1966 pp.33-42

    L(θ) = Lz(1 + 2sinθ)/3
    E_out = 7πLz/9  →  归一化 Lz=1 时 E_out = 7π/9
    Ds = τ/(π·E_out) · ΣΣ L(θij)·cosβij·cosαij·dA/rij²
    """
    norm, uax, vax, orig_fn = _wall_axes(win.wall)
    W_m, L_m = room.width/1e3, room.length/1e3
    orig = orig_fn(W_m, L_m)

    wu  = win.width  / 1e3
    wv  = win.height / 1e3
    u0  = win.x      / 1e3
    v0  = win.y      / 1e3
    dA  = (wu / ndiv) * (wv / ndiv)

    # 面元中心坐标（局部）
    us = u0 + (np.arange(ndiv, dtype=np.float64) + 0.5) * (wu / ndiv)
    vs = v0 + (np.arange(ndiv, dtype=np.float64) + 0.5) * (wv / ndiv)
    UU, VV = np.meshgrid(us, vs, indexing='ij')  # (ndiv, ndiv)

    # 面元世界坐标
    Qx = orig[0] + uax[0]*UU + vax[0]*VV
    Qy = orig[1] + uax[1]*UU + vax[1]*VV
    Qz = orig[2] + uax[2]*UU + vax[2]*VV

    # 向量 P→Q
    dX = Qx - P[0]; dY = Qy - P[1]; dZ = Qz - P[2]
    r2 = dX*dX + dY*dY + dZ*dZ
    r  = np.sqrt(np.maximum(r2, 1e-18))

    # 仰角 sinθ = dZ/r
    sin_t = np.clip(dZ / r, -1., 1.)

    # CIE 天空亮度 L(θ)（归一化 Lz=1）
    L_sky = (1.0 + 2.0 * sin_t) / 3.0

    # cos_α: 面元法向与 入射方向(-PQ) 的夹角
    cos_a = norm[0]*(-dX/r) + norm[1]*(-dY/r) + norm[2]*(-dZ/r)

    # cos_β: 工作面法向(0,0,1) 与 PQ 方向的夹角 = dZ/r
    cos_b = dZ / r

    # 有效性掩码
    valid = (r > 1e-9) & (sin_t > 0.) & (cos_a > 0.) & (cos_b > 0.)
    overhang_transmission = np.ones_like(Qx, dtype=np.float64)

    # ── 水平/倾斜挑檐遮挡（v2.5 新增水平判定；v2.12.0 推广为任意倾斜角θ）─────────
    # 物理：挑檐板从窗顶(贴墙处)向外伸出，板长 L，倾斜角 θ（°，与墙面/竖直方向
    #   的夹角），约定 θ=90° 为水平（不区分正负号——θ>90° 板尖高于板根，即"上扬"；
    #   θ<90° 板尖低于板根，即"下垂"，方向已经隐含在 θ 落在 90° 的哪一侧，不需要
    #   额外的符号位）。板根世界高度 z_over = 窗顶+安装间隙；板尖位置（局部出挑
    #   坐标系）：出挑距离 s_tip=L·sinθ，高度 z_tip=z_over−L·cosθ（θ=90°时退化为
    #   s_tip=L, z_tip=z_over，与旧版水平公式完全一致，θ=90°回归测试可验证）。
    #   挑檐板所在的斜面方程：z = z_over − s_out·cotθ；测点 P 到天空面元 Q 的视线
    #   延伸到室外后，若与该斜面的交点落在板的有限长度范围内(0≤s_out≤s_tip)且
    #   横向在窗宽内，则该天空面元对 P 不可见。θ→0°或180°（板贴墙折平，水平出挑
    #   为0）时退化为不产生遮挡。
    sh = room.shading
    over_depth_mm, over_gap_mm, over_tilt_deg = sh.get_overhang_for(win.id)   # v2.10: 逐窗覆盖
    if getattr(sh, "type", "none") == "horizontal_overhang" and over_depth_mm > 0.0:
        theta = math.radians(over_tilt_deg)
        sin_th, cos_th = math.sin(theta), math.cos(theta)
        if abs(sin_th) > 1e-9:      # sinθ≈0：板贴墙折平，水平出挑为0，不产生遮挡
            L_m    = over_depth_mm / 1e3      # 板长 m（沿板自身方向，非水平投影）
            gap_m  = over_gap_mm   / 1e3      # 挑檐板根高出窗顶的距离 m（贴窗顶=0）
            z_over = win.head / 1e3 + gap_m   # 板根世界高度 m
            u0m    = win.x     / 1e3
            wum    = win.width / 1e3
            s_tip  = L_m * sin_th              # 板尖出挑距离（有限长度上限）
            cot_th = cos_th / sin_th
            onx, ony = -norm[0], -norm[1]
            k_s = onx * dX + ony * dY          # d(s_out)/dt；s_out(t=1)=0（Q在窗洞面上）
            with np.errstate(divide="ignore", invalid="ignore"):
                tstar = (z_over - P[2] + k_s * cot_th) / (dZ + k_s * cot_th)
            Wx = P[0] + tstar * dX
            Wy = P[1] + tstar * dY
            s_out = (Wx - orig[0]) * onx + (Wy - orig[1]) * ony
            lat   = (Wx - orig[0]) * uax[0] + (Wy - orig[1]) * uax[1]
            blocked = (np.isfinite(tstar) & (tstar >= 1.0)
                       & (s_out > 0.0) & (s_out <= s_tip)
                       & (lat >= u0m - 1e-9) & (lat <= u0m + wum + 1e-9))
            rho = float(np.clip(getattr(sh, "visible_reflectance", 0.32), 0.0, 1.0))
            specular = float(np.clip(getattr(sh, "specular_fraction", 0.03), 0.0, 1.0))
            reflected_fraction = float(np.clip(
                0.12 * rho * (1.0 - specular) + 0.02 * rho * specular,
                0.0, 0.12,
            ))
            overhang_transmission = np.where(
                blocked, reflected_fraction, overhang_transmission
            )

    # ── 垂直遮阳翼板/装饰柱遮挡（v2.10.1 重新设计：射线-立方体(AABB)相交判定）──────
    # v2.7~v2.10.0 把翼板当作贴在"某扇窗户自己边缘"的零宽度虚拟面，且只用该窗
    # 自身的面元去测试该窗自身两侧的面，可解析证明：只要测点 P 在窗户 w 的横向
    # 范围之外（例如窗间墙测点），P 看向 w 自身面元、测试 w 自身近侧翼板面时，
    # 射线参数恒有 t<1（在到达窗洞面之前就已"穿过"翼板所在的横向坐标），使得
    # "遮挡须发生在窗洞面之外(t≥1)"这一条件恒不成立——窗间墙测点通过"紧邻窗户
    # 自身翼板"这条路径，理论上永远不会被判定为遮挡。这是 v2.9.1~v2.10.0 一直
    # 记录的"窗间墙测点遮挡幅度被系统性低估"的数学根源，而不是概率性漏判。
    # 同时 v2.10.0 验证还发现相反的问题——窗户自身沿窗中线测点被自身两侧翼板
    # 过度压暗，经核实根因是出挑深度数值(如850mm)系从室内参照线（本模型窗洞
    # 所在的抽象零厚度墙面）起算、包含了墙体厚度，真正伸出外墙皮之外、能挡住
    # 天空视线的有效出挑只有(标注深度 − 墙厚)，直接用标注值当有效出挑代入几何
    # 判定会系统性放大遮挡范围。
    # 重新设计：翼板/装饰柱按其真实占据的横向范围[u0,u1]（窗间墙/端墙实际宽度）
    # ×出挑方向[0,D_eff]（已扣墙厚）×竖向[z0,z1]（相邻窗窗台~窗顶包络）建模为
    # 一个实心长方体（AABB），任意窗户的天空面元只要其视线延伸路径（要求发生在
    # 窗洞面之外，t≥1）与该长方体体积有非空交集就算被遮挡——不再限定"只测自身
    # 两侧"，因为现实中一根柱子会挡住从室内任意角度投过来的、恰好穿过它所占
    # 体积的视线。用标准 slab 法做射线-AABB 相交：分别求射线在 u/出挑/竖向三个
    # 轴上处于该长方体范围内的参数区间，三个区间与 [1,+∞) 的交集非空即遮挡。
    fin_slots = _fin_slots_full_m(room, win.wall)
    if fin_slots:
        onx, ony = -norm[0], -norm[1]
        lat_P   = (P[0] - orig[0]) * uax[0] + (P[1] - orig[1]) * uax[1]
        dLat    = dX * uax[0] + dY * uax[1]
        s_slope = onx * dX + ony * dY     # d(s_out)/dt；s_out(t=1)=0（Q在窗洞面上）
        for (u0_m, u1_m, D_m, z0_m, z1_m) in fin_slots:
            with np.errstate(divide="ignore", invalid="ignore"):
                t_u_a = (u0_m - lat_P) / dLat
                t_u_b = (u1_m - lat_P) / dLat
                t_s_b = 1.0 + D_m / s_slope   # t_s_a 恒为 1（s_out(1)=0，出挑起点）
                t_z_a = (z0_m - P[2]) / dZ
                t_z_b = (z1_m - P[2]) / dZ
            t_lo = np.maximum.reduce([
                np.minimum(t_u_a, t_u_b),
                np.minimum(1.0, t_s_b),
                np.minimum(t_z_a, t_z_b),
                np.full_like(t_u_a, 1.0),
            ])
            t_hi = np.minimum.reduce([
                np.maximum(t_u_a, t_u_b),
                np.maximum(1.0, t_s_b),
                np.maximum(t_z_a, t_z_b),
            ])
            blocked_fin = np.isfinite(t_lo) & np.isfinite(t_hi) & (t_lo <= t_hi + 1e-9)
            valid = valid & (~blocked_fin)

    dOmega = np.where(valid, cos_a * dA / r2, 0.)
    contrib = np.where(
        valid, L_sky * cos_b * dOmega * overhang_transmission, 0.
    )

    E_out_norm = 7.0 * math.pi / 9.0   # 归一化 Lz=1
    Ds = contrib.sum() * win.tau / (math.pi * E_out_norm)
    return float(max(0., Ds * 100.))


def _dext_point(P: np.ndarray, win, room: RoomModel) -> float:
    """
    室外反射分量 Dext (%) — Littlefair BRE 209 §2.2
    Dext = ρg·τ·(1 - cosβ_sill) / 2
    """
    norm, uax, vax, orig_fn = _wall_axes(win.wall)
    W_m, L_m = room.width/1e3, room.length/1e3
    orig = orig_fn(W_m, L_m)

    # 窗台底边中点
    sill_u = win.x/1e3 + win.width/2e3
    sill_v = win.y/1e3
    Q_sill = orig + uax*sill_u + vax*sill_v

    PQ   = Q_sill - P
    dist = float(np.linalg.norm(PQ))
    if dist < 1e-12:
        return 0.0

    # 仰角 β_sill
    horiz = math.sqrt(PQ[0]**2 + PQ[1]**2)
    beta  = math.atan2(float(PQ[2]), horiz)
    beta  = max(0., beta)

    rho_g = room.material.rho_ground
    Dext  = rho_g * win.tau * (1. - math.cos(beta)) / 2.
    return float(max(0., Dext * 100.))


def _dint(Ds: float, Dext: float, rho: float) -> float:
    """
    室内反射分量 Dint (%) — BRS 互反射简化法
    Dint = ρ̄(Ds+Dext)/(1-ρ̄)  [Hopkinson 1966, p.384]
    ρ̄ < 0.6 时与完整 Radiosity 误差 <5%
    """
    rho = min(rho, 0.995)
    return float(max(0., rho * (Ds + Dext) / (1. - rho)))


# ─────────────────────────────────────────────────────────────────────────────
class DaylightResult:
    __slots__ = [
        "grid_x","grid_y","DF","E_lux",
        "E_avg","E_min","E_max","U0",
        "DF_avg","DF_min","DF_max",
        "E_out","rho_bar","grid_mm",
        "Ds","Dext","Dint",
        "compliant_300","compliant_u0",
        "method","quick","ndiv",
        "Ra","Ra_threshold","daylight_score",
    ]
    def __init__(self):
        for s in self.__slots__:
            object.__setattr__(self, s, None)


def compute(
    room: RoomModel,
    E_out: float = 13500.0,
    grid_mm: float = GRID_MM,
    ndiv: int = WIN_DIV,
    store_components: bool = True,
    row_cb=None,           # 逐行进度回调 row_cb(iy) 用于进度条
    ra_threshold: float = 2.0,   # v2.5: Ra 达标阈值 DF(%)，默认 GB/T 50033 III类 2.0%
) -> DaylightResult:
    """
    全房间网格计算主函数。
    高精度: ndiv=40 → 1600面元/窗，grid_mm=250 → 密集测点
    row_cb: 每计算完一行 y 调用一次，用于进度条实时更新
    ra_threshold: Ra(采光达标面积比) 的 DF 阈值(%)，可配置（不同评价等级不同）
    水平挑檐遮阳: 若 room.shading.type=="horizontal_overhang"，Ds 积分自动扣除被挑檐
                 遮挡的天空面元（见 _ds_point），进而影响 DF/Ra/U0/E_avg 等全部下游指标。
    垂直翼板遮阳: 若 room.shading.vertical_fin_enabled=True 且 vertical_fin_depth_mm>0，
                 Ds 积分同时扣除被窗间墙翼板/装饰柱遮挡的天空面元（与水平挑檐独立
                 叠加生效，仅在相邻窗之间自动生成，两端外墙默认不设，见 _ds_point）。
    """
    res         = DaylightResult()
    res.E_out   = E_out
    res.grid_mm = grid_mm
    res.ndiv    = ndiv
    res.Ra_threshold = ra_threshold
    res.method  = (
        f"CIE 110-1994 + BRS + Littlefair BRE 209 + "
        f"conservative overhang first-bounce reflection (ndiv={ndiv})"
    )
    res.quick   = _quick(room, E_out)

    W, L = room.width, room.length
    margin = WALL_MARGIN_MM

    if not room.windows:
        xs = np.array([W/2.]); ys = np.array([L/2.])
        res.grid_x = xs; res.grid_y = ys
        res.DF    = np.zeros((1,1), dtype=np.float64)
        res.E_lux = np.zeros((1,1), dtype=np.float64)
        _fill_stats(res); return res

    # 测点网格（边界内缩 margin，步长 grid_mm）
    x0 = margin + grid_mm/2.; x1 = W - margin
    y0 = margin + grid_mm/2.; y1 = L - margin
    if x1 <= x0 or y1 <= y0:
        x0, y0 = grid_mm/2., grid_mm/2.
        x1, y1 = W - grid_mm/2., L - grid_mm/2.

    nx = max(2, round((x1-x0)/grid_mm) + 1)
    ny = max(2, round((y1-y0)/grid_mm) + 1)
    xs = np.linspace(x0, x1, nx)
    ys = np.linspace(y0, y1, ny)
    res.grid_x = xs; res.grid_y = ys

    rho       = _rho_bar(room)
    res.rho_bar = rho
    z_m       = WORK_PLANE_MM / 1e3

    DF_arr   = np.zeros((ny, nx), dtype=np.float64)
    Ds_arr   = np.zeros((ny, nx), dtype=np.float64)
    Dext_arr = np.zeros((ny, nx), dtype=np.float64)
    Dint_arr = np.zeros((ny, nx), dtype=np.float64)

    for iy in range(ny):
        if row_cb is not None:
            row_cb(iy)
        for ix in range(nx):
            P = np.array([xs[ix]/1e3, ys[iy]/1e3, z_m], dtype=np.float64)
            Ds_t = Dext_t = 0.0
            for w in room.windows:
                Ds_t   += _ds_point(P, w, room, ndiv)
                Dext_t += _dext_point(P, w, room)
            Di = _dint(Ds_t, Dext_t, rho)
            DF_arr  [iy,ix] = Ds_t + Dext_t + Di
            Ds_arr  [iy,ix] = Ds_t
            Dext_arr[iy,ix] = Dext_t
            Dint_arr[iy,ix] = Di

    res.DF    = DF_arr
    res.E_lux = DF_arr / 100.0 * E_out
    if store_components:
        res.Ds = Ds_arr; res.Dext = Dext_arr; res.Dint = Dint_arr

    _fill_stats(res)
    return res


def _fill_stats(res: DaylightResult):
    if res.DF is None or res.DF.size == 0:
        return
    res.DF_avg = float(np.mean(res.DF))
    res.DF_min = float(np.min(res.DF))
    res.DF_max = float(np.max(res.DF))
    E = res.E_lux if res.E_lux is not None else res.DF/100.*(res.E_out or 13500.)
    res.E_avg  = float(np.mean(E))
    res.E_min  = float(np.min(E))
    res.E_max  = float(np.max(E))
    res.U0     = res.E_min / res.E_avg if res.E_avg > 1e-12 else 0.
    res.compliant_300 = res.E_avg >= 300.
    res.compliant_u0  = res.U0    >= 0.70
    # Ra：采光达标面积比 = DF≥阈值的测点数 / 总测点数（v2.5）
    thr = res.Ra_threshold if res.Ra_threshold is not None else 2.0
    res.Ra = float(np.mean(res.DF >= thr))
    res.daylight_score = float(np.mean(np.clip(res.DF / max(thr, 1e-12), 0.0, 1.0)))


def _quick(room: RoomModel, E_out: float) -> dict:
    """
    Lynes Flux Method 解析估算（即时反馈）
    Eavg = E_out·τ_eff·Aw / (Af·(1-ρ̄))
    Lynes (1968) Principles of Natural Lighting, p.95
    """
    L, W = room.length/1e3, room.width/1e3
    Af   = L * W
    wins = room.windows
    if not wins or Af < 1e-12:
        return {"E_avg": 0., "DF_avg": 0., "WFR": 0.}
    A_win   = sum((w.width/1e3)*(w.height/1e3) for w in wins)
    tau_eff = sum((w.width/1e3)*(w.height/1e3)*w.tau for w in wins) / A_win
    rho     = _rho_bar(room)
    denom   = max(1. - rho, 0.01)
    E_avg   = E_out * tau_eff * A_win / (Af * denom)
    return {
        "E_avg":   E_avg,
        "DF_avg":  E_avg / E_out * 100.,
        "WFR":     A_win / Af,
        "tau_eff": tau_eff,
        "rho_bar": rho,
        "A_win":   A_win,
        "Af":      Af,
    }
