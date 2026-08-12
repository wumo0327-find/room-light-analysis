"""
core/daylight.py — Daylight Factor & Illuminance Engine  v2.7.1
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


def _vertical_fin_positions_mm(win, room: RoomModel):
    """
    v2.7: 返回该窗户两侧"窗间墙垂直翼板/装饰柱"近侧面的沿墙局部坐标 u（mm）列表。
    仅当同一墙面上该窗左/右确实存在相邻窗户时才生成对应一侧的翼板（即翼板只
    出现在窗与窗之间的窗间墙位置），两端外墙默认不设（简化假设，无实测确认时
    的默认处理，与水平挑檐的 SC_reduction_factor 一样可通过 vertical_fin_enabled
    整体关闭）。翼板近侧面正好在相邻窗户的边界处，不依赖窗间墙宽度本身。
    """
    sh = room.shading
    if not getattr(sh, "vertical_fin_enabled", False) or sh.vertical_fin_depth_mm <= 0.0:
        return []
    same_wall = sorted(room.windows_on(win.wall), key=lambda w: w.x)
    idx = same_wall.index(win)
    positions = []
    if idx > 0:
        positions.append(win.x)        # 左侧窗间墙翼板近侧面
    if idx < len(same_wall) - 1:
        positions.append(win.right)    # 右侧窗间墙翼板近侧面
    return positions


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

    # ── 水平挑檐遮挡（v2.5 新增，几何可见性判定）────────────────────────────────
    # 物理：测点 P 到天空面元 Q 的视线延伸到室外后，若在挑檐所在水平面(Z=z_over)上的
    #       交点落在挑檐出挑覆盖范围内(外挑 0<s≤D 且横向在窗宽内)，则该天空面元对 P
    #       不可见，从 Ds 积分中剔除（并入 valid 掩码，被遮面元跳过累加）。
    # 复用现有积分框架：只加一个条件筛选，不重写积分逻辑。
    sh = room.shading
    if getattr(sh, "type", "none") == "horizontal_overhang" and sh.overhang_depth_mm > 0.0:
        D_m    = sh.overhang_depth_mm  / 1e3      # 出挑深度 m（水平向外）
        gap_m  = sh.overhang_height_mm / 1e3      # 挑檐下沿高出窗顶的距离 m（贴窗顶=0）
        z_over = win.head / 1e3 + gap_m           # 挑檐下沿世界高度 m（vax=(0,0,1),orig_z=0）
        u0m    = win.x     / 1e3                  # 窗口沿墙(u轴)左边界 m
        wum    = win.width / 1e3                  # 窗宽 m（默认挑檐横向覆盖= 窗宽）
        onx, ony = -norm[0], -norm[1]             # 墙面外法向的水平分量
        with np.errstate(divide="ignore", invalid="ignore"):
            tstar = (z_over - P[2]) / dZ          # 视线到达挑檐高度处的参数 t*
        Wx = P[0] + tstar * dX                    # 交点世界坐标（水平）
        Wy = P[1] + tstar * dY
        s_out = (Wx - orig[0]) * onx + (Wy - orig[1]) * ony   # 交点外挑距离
        lat   = (Wx - orig[0]) * uax[0] + (Wy - orig[1]) * uax[1]  # 沿墙横向坐标
        blocked = ((dZ > 0.0) & np.isfinite(tstar) & (tstar >= 1.0)
                   & (s_out > 0.0) & (s_out <= D_m)
                   & (lat >= u0m - 1e-9) & (lat <= u0m + wum + 1e-9))
        valid = valid & (~blocked)
    # TODO(v2.5 阶段二): Dext 目前仅计地面反射，未含挑檐下表面反射光贡献；
    #   若需精算挑檐自身反射对室内的补光，应在 _dext_point 增加挑檐反射项。

    # ── 垂直遮阳翼板/装饰柱遮挡（v2.7 新增，与水平挑檐镜像对称的可见性判定）───────
    # 物理：与水平挑檐完全对称——挑檐是"固定高度水平面、随外挑距离限宽、随窗宽限侧向
    # 范围"，翼板则是"固定沿墙坐标的竖直面、随外挑距离限宽、随窗高限竖向范围"。
    # 视线延伸到翼板所在竖直面(u=u_fin)的交点，若外挑距离在 [0,D] 内、且交点高度落在
    # 该窗窗台~窗顶范围内，则该天空面元对 P 不可见。两侧翼板各自独立叠加剔除，
    # 与水平挑檐（若同时启用）在同一 valid 掩码上共同生效，互不影响。
    # TODO(v2.9.1 已知局限，真实数据验证发现): 该判定只能有效遮挡"该窗自身的临边
    #   斜视线"（测点在该窗视野内看向翼板一侧边缘的方向）；对"窗间墙测点"看向左右
    #   两扇相邻窗户的斜向穿越路径，射线在到达翼板出挑区(t≥1)之前往往已在窗洞面处
    #   终止（t<1），因此难以触发遮挡。两间真实教室(411/509)实测显示"窗间墙/沿窗"DF
    #   比值稳定在 0.51~0.93（窗间墙测点明显更暗），而本模型算出的比值始终在 0.92~
    #   1.06（两测线几乎同亮）——即窗间墙测点的差异化遮挡被显著低估。如需让窗间墙测线
    #   也能定量对比，需要重新设计该场景的遮挡几何（而非照搬水平挑檐的镜像数学），
    #   本版本未实现，仅作定性趋势验证。
    fin_us_mm = _vertical_fin_positions_mm(win, room)
    if fin_us_mm:
        Dfin_m = sh.vertical_fin_depth_mm / 1e3
        z0, z1 = win.sill / 1e3, win.head / 1e3        # 翼板竖向范围＝该窗窗台~窗顶
        onx, ony = -norm[0], -norm[1]
        lat_P = (P[0] - orig[0]) * uax[0] + (P[1] - orig[1]) * uax[1]
        dLat  = dX * uax[0] + dY * uax[1]
        for u_fin_mm in fin_us_mm:
            u_fin = u_fin_mm / 1e3
            with np.errstate(divide="ignore", invalid="ignore"):
                tfin = (u_fin - lat_P) / dLat          # 视线到达翼板所在竖直面的参数 t
            Wx2 = P[0] + tfin * dX
            Wy2 = P[1] + tfin * dY
            Wz2 = P[2] + tfin * dZ
            s_out2 = (Wx2 - orig[0]) * onx + (Wy2 - orig[1]) * ony
            blocked_fin = ((np.abs(dLat) > 1e-12) & np.isfinite(tfin) & (tfin >= 1.0)
                          & (s_out2 > 0.0) & (s_out2 <= Dfin_m)
                          & (Wz2 >= z0 - 1e-9) & (Wz2 <= z1 + 1e-9))
            valid = valid & (~blocked_fin)

    dOmega = np.where(valid, cos_a * dA / r2, 0.)
    contrib = np.where(valid, L_sky * cos_b * dOmega, 0.)

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
        "Ra","Ra_threshold",          # v2.5: 采光达标面积比 Ra 及其阈值(%)
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
    res.method  = f"CIE 110-1994 + BRS + Littlefair BRE 209  (ndiv={ndiv})"
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
