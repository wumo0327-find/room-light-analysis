"""
core/experiments.py — 参数化实验批量运行 + 帕累托前沿 + 散点气泡图  v2.5.0（新增）
================================================================================
不依赖 GUI 的批量实验入口，支撑论文的两组实验：

  · 对照组：窗玻璃材质（Tvis, SC）扫描
  · 主实验组：水平挑檐遮阳特征角 β 扫描（β→D=H·tan(β)）

每个算例跑完整「采光 + 热环境」流程，汇总为一张 pandas 表；再做 2D 帕累托前沿
提取与散点气泡图导出。

热轴指标说明（重要）：
  离散「舒适月数」在炎热气候+南向单挑檐下往往饱和（夏季远高于26℃上限，遮阳降不到
  舒适区），区分度差。故表中同时给出连续量：
    overheat_degree_months  = Σ max(T_in − 26, 0)   （越低越好）
    underheat_degree_months = Σ max(18 − T_in, 0)
    thermal_discomfort      = 上两者之和
  帕累托/绘图的 y 轴可配置，建议主实验用连续量以获得区分度。

合规筛选：U0（采光均匀度）阈值可配置（默认 0.70）。注意单侧窗深房间 U0 本就偏低
  （侧窗采光固有特性），若默认 0.70 会滤掉全部点，可按需调整 u0_min。
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import List, Optional, Callable

import numpy as np
import pandas as pd

from core.models import RoomModel, ShadingDevice
from core.daylight import compute as compute_daylight
from core.thermal import compute_thermal
from io_utils.weather_data import WeatherDataset, default_dataset

T_HI = 26.0   # 舒适上限 ℃
T_LO = 18.0   # 舒适下限 ℃


# ── 玻璃规格（对照组）────────────────────────────────────────────────────────
@dataclass
class GlassSpec:
    label: str
    tvis:  float          # 可见光透射比 → 采光 τ
    sc:    float          # 遮阳系数 SC → 热工 SC_glass（若手头是 SHGC，SC≈SHGC/0.87）
    cost:  float = 0.0    # 成本占位（后续填真实数据）


# 占位示例数据（5–6 组），后续替换为真实产品参数
DEFAULT_GLASS_SPECS: List[GlassSpec] = [
    GlassSpec("单层透明",       tvis=0.90, sc=0.95, cost=100.0),
    GlassSpec("普通中空",       tvis=0.81, sc=0.85, cost=180.0),
    GlassSpec("单银Low-E中空",  tvis=0.72, sc=0.62, cost=260.0),
    GlassSpec("双银Low-E中空",  tvis=0.60, sc=0.44, cost=340.0),
    GlassSpec("三银Low-E中空",  tvis=0.50, sc=0.34, cost=420.0),
    GlassSpec("阳光控制Low-E",  tvis=0.40, sc=0.28, cost=480.0),
]


def _base_room() -> RoomModel:
    """标准算例房间：6×4×3m 教室，单扇南向窗（默认 1.5×1.5m）。"""
    room = RoomModel()
    room.add_window("south")
    return room


def _run_case(room: RoomModel, weather: WeatherDataset,
              ndiv: int, ra_threshold: float) -> dict:
    """跑一个算例的完整采光+热环境流程，返回指标字典。"""
    dl = compute_daylight(room, E_out=weather.annual_avg,
                          ndiv=ndiv, ra_threshold=ra_threshold)
    th = compute_thermal(room, weather.monthly_ghi, weather.monthly_temp)
    over_dm  = float(np.sum(np.maximum(th.T_in - T_HI, 0.0)))
    under_dm = float(np.sum(np.maximum(T_LO - th.T_in, 0.0)))
    return {
        "Ra":                      dl.Ra,
        "U0":                      dl.U0,
        "DF_avg":                  dl.DF_avg,
        "E_avg":                   dl.E_avg,
        "comfort_months":          th.comfort_months,
        "comfort_ratio":           th.comfort_months / 12.0,
        "overheat_months":         th.overheat_months,
        "overheat_degree_months":  over_dm,
        "underheat_degree_months": under_dm,
        "thermal_discomfort":      over_dm + under_dm,
        "SC_annual":               th.SC_effective,
    }


# ── 对照组：玻璃材质实验 ──────────────────────────────────────────────────────
def run_glass_experiment(
    specs: Optional[List[GlassSpec]] = None,
    weather: Optional[WeatherDataset] = None,
    ndiv: int = 20,
    ra_threshold: float = 2.0,
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> pd.DataFrame:
    """
    对每组玻璃参数运行完整分析，收集 (Ra, 舒适/超温指标, U0, 成本, 标签)。
    玻璃 Tvis → 窗 τ（采光）；SC → 热工 SC_glass。无外遮阳。
    """
    specs   = specs   or DEFAULT_GLASS_SPECS
    weather = weather or default_dataset()
    rows = []
    for i, g in enumerate(specs):
        if progress_cb:
            progress_cb(i, len(specs))
        room = _base_room()
        for w in room.windows:
            w.tau = g.tvis
        room.thermal.SC_glass = g.sc
        room.shading = ShadingDevice(type="none")
        rec = _run_case(room, weather, ndiv, ra_threshold)
        rec.update({"group": "玻璃对照", "param_label": g.label,
                    "param_value": g.tvis, "cost": g.cost})
        rows.append(rec)
    return pd.DataFrame(rows)


# ── 主实验组：遮阳特征角 β 扫描 ───────────────────────────────────────────────
def run_overhang_experiment(
    beta_degs: Optional[List[float]] = None,
    H_mm: float = 1500.0,
    glass: Optional[GlassSpec] = None,
    diffuse_residual: float = 0.30,
    overhang_gap_mm: float = 0.0,
    weather: Optional[WeatherDataset] = None,
    ndiv: int = 20,
    ra_threshold: float = 2.0,
    cost_fn: Optional[Callable[[float, float], float]] = None,
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> pd.DataFrame:
    """
    遮阳特征角实验：β∈[0,60] 步长5（可配置），固定 H；对每个 β 反算 D=H·tan(β)，
    代入水平挑檐运行完整分析，收集 (Ra, 舒适/超温指标, U0, 成本, β)。
    cost_fn(beta_deg, D_mm) 可自定义成本占位；默认给一个随 D 线性的占位值。
    """
    if beta_degs is None:
        beta_degs = list(range(0, 61, 5))          # 0..60 步长5（13组）
    weather = weather or default_dataset()
    if cost_fn is None:
        cost_fn = lambda b, d: 100.0 + d / 10.0    # 占位：后续替换真实造价
    rows = []
    for i, beta in enumerate(beta_degs):
        if progress_cb:
            progress_cb(i, len(beta_degs))
        D_mm = H_mm * math.tan(math.radians(beta))
        room = _base_room()
        if glass is not None:
            for w in room.windows:
                w.tau = glass.tvis
            room.thermal.SC_glass = glass.sc
        room.shading = ShadingDevice(
            type="horizontal_overhang",
            overhang_depth_mm=D_mm,
            overhang_height_mm=overhang_gap_mm,
            diffuse_residual=diffuse_residual,
        )
        rec = _run_case(room, weather, ndiv, ra_threshold)
        rec.update({"group": "遮阳β扫描",
                    "param_label": f"β={beta:.0f}°",
                    "param_value": float(beta),
                    "D_mm": D_mm,
                    "cost": float(cost_fn(beta, D_mm))})
        rows.append(rec)
    return pd.DataFrame(rows)


def run_all_experiments(
    weather: Optional[WeatherDataset] = None,
    ndiv: int = 20,
    ra_threshold: float = 2.0,
    beta_degs: Optional[List[float]] = None,
    H_mm: float = 1500.0,
    glass_specs: Optional[List[GlassSpec]] = None,
    progress_cb: Optional[Callable[[str, int, int], None]] = None,
) -> pd.DataFrame:
    """跑两组实验并合并为一张表（统一字段，含 group 列）。"""
    weather = weather or default_dataset()
    df_g = run_glass_experiment(
        specs=glass_specs, weather=weather, ndiv=ndiv, ra_threshold=ra_threshold,
        progress_cb=(lambda i, n: progress_cb("玻璃对照", i, n)) if progress_cb else None)
    df_o = run_overhang_experiment(
        beta_degs=beta_degs, H_mm=H_mm, weather=weather, ndiv=ndiv,
        ra_threshold=ra_threshold,
        progress_cb=(lambda i, n: progress_cb("遮阳β扫描", i, n)) if progress_cb else None)
    cols = ["group", "param_label", "param_value", "Ra", "U0", "comfort_ratio",
            "comfort_months", "overheat_months", "overheat_degree_months",
            "underheat_degree_months", "thermal_discomfort", "SC_annual",
            "DF_avg", "E_avg", "cost"]
    df = pd.concat([df_g, df_o], ignore_index=True)
    # 统一列顺序（缺失列如 D_mm 保留在后）
    ordered = [c for c in cols if c in df.columns] + \
              [c for c in df.columns if c not in cols]
    return df[ordered]


# ── 帕累托前沿（2D，最大化 x 与 y）────────────────────────────────────────────
def pareto_front(
    df: pd.DataFrame,
    x: str = "Ra",
    y: str = "comfort_ratio",
    maximize_y: bool = True,
    u0_col: str = "U0",
    u0_min: float = 0.70,
) -> pd.DataFrame:
    """
    标准 2D 帕累托前沿：在满足合规筛选(U0≥u0_min)的点中，求关于 (x↑, y↑) 的非支配解。
    maximize_y=False 时（如 y=thermal_discomfort 越低越好）内部对 y 取负再求前沿。
    算法：按 x 降序遍历，维护当前最优 y，遇到严格更优的 y 即为非支配解。
    返回按 x 降序的前沿子表。
    """
    d = df.copy()
    if u0_col in d.columns and u0_min is not None:
        d = d[d[u0_col] >= u0_min]
    if d.empty:
        return d
    yv = d[y] if maximize_y else -d[y]
    d = d.assign(_y=yv).sort_values([x, "_y"], ascending=[False, False])
    best = -np.inf
    keep = []
    for idx, row in d.iterrows():
        if row["_y"] > best + 1e-12:
            keep.append(idx)
            best = row["_y"]
    return df.loc[keep]


# ── 散点气泡图 ────────────────────────────────────────────────────────────────
# 白底学术主题（与热力图/截面图导出一致）
_C_BG = "#ffffff"; _C_TEXT = "#1a1e2e"; _C_SEC = "#5a6175"
_C_ACCENT = "#2563eb"; _C_BORDER = "#d0d5e0"; _C_GRID = "#f0f2f6"
_C_PARETO = "#dc2626"; _C_NONCOMP = "#9aa0b0"

# 组别 → marker 形状
_GROUP_MARKER = {"玻璃对照": "o", "遮阳β扫描": "s"}
_GROUP_COLOR  = {"玻璃对照": "#16a34a", "遮阳β扫描": _C_ACCENT}


def plot_experiments(
    df: pd.DataFrame,
    out_path: str,
    x: str = "Ra",
    y: str = "comfort_ratio",
    maximize_y: bool = True,
    size_col: str = "cost",
    u0_col: str = "U0",
    u0_min: float = 0.70,
    x_label: str = "采光达标面积比 Ra",
    y_label: str = "舒适月数占比",
    title: str = "遮阳/玻璃参数 — 采光·热环境权衡与帕累托前沿",
    dpi: int = 200,
) -> str:
    """
    散点气泡图（白底学术主题）：
      · x=Ra, y=舒适指标；气泡大小∝成本占位
      · 玻璃对照(圆) / 遮阳β扫描(方) 形状区分
      · U0<u0_min（不合规）灰色半透明标出，不参与帕累托，但仍显示
      · 帕累托前沿点高亮(红边)并连线
    """
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg

    fig = Figure(figsize=(9, 6), facecolor=_C_BG)
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)
    ax.set_facecolor(_C_BG)

    # 气泡大小映射（成本占位；全相等时用常数）
    if size_col in df.columns and df[size_col].notna().any() and df[size_col].std() > 1e-9:
        s = df[size_col].astype(float)
        smin, smax = s.min(), s.max()
        sizes = 60 + (s - smin) / (smax - smin + 1e-9) * 340
    else:
        sizes = pd.Series(140.0, index=df.index)

    compliant = df[u0_col] >= u0_min if (u0_col in df.columns and u0_min is not None) \
        else pd.Series(True, index=df.index)

    # 分组散点
    for grp, gdf in df.groupby("group"):
        mk = _GROUP_MARKER.get(grp, "D")
        col = _GROUP_COLOR.get(grp, _C_ACCENT)
        comp = gdf.index[compliant.loc[gdf.index]]
        ncomp = gdf.index[~compliant.loc[gdf.index]]
        if len(comp):
            ax.scatter(df.loc[comp, x], df.loc[comp, y], s=sizes.loc[comp],
                       marker=mk, c=col, alpha=0.80, edgecolors="white",
                       linewidths=0.8, label=f"{grp}", zorder=3)
        if len(ncomp):
            ax.scatter(df.loc[ncomp, x], df.loc[ncomp, y], s=sizes.loc[ncomp],
                       marker=mk, c=_C_NONCOMP, alpha=0.45, edgecolors="white",
                       linewidths=0.8, label=f"{grp}(U0<{u0_min:g}不合规)", zorder=2)

    # 帕累托前沿
    pf = pareto_front(df, x=x, y=y, maximize_y=maximize_y, u0_col=u0_col, u0_min=u0_min)
    if not pf.empty:
        pf_sorted = pf.sort_values(x)
        ax.plot(pf_sorted[x], pf_sorted[y], "-", color=_C_PARETO, lw=1.8,
                alpha=0.9, zorder=4, label="帕累托前沿")
        ax.scatter(pf_sorted[x], pf_sorted[y], s=sizes.loc[pf_sorted.index] * 1.15,
                   marker="o", facecolors="none", edgecolors=_C_PARETO,
                   linewidths=2.0, zorder=5)

    ax.set_xlabel(x_label, color=_C_TEXT, fontsize=11)
    ax.set_ylabel(y_label, color=_C_TEXT, fontsize=11)
    ax.set_title(title, color=_C_TEXT, fontsize=12, fontweight="bold", pad=10)
    ax.grid(True, color=_C_GRID, lw=0.6)
    for sp in ax.spines.values():
        sp.set_color(_C_BORDER); sp.set_linewidth(0.7)
    ax.tick_params(colors=_C_SEC, labelsize=9)
    ax.legend(fontsize=8.5, loc="best", labelcolor=_C_TEXT,
              facecolor="#ffffff", edgecolor=_C_BORDER, framealpha=0.95)
    fig.tight_layout()
    fig.savefig(out_path, dpi=dpi, facecolor=_C_BG, bbox_inches="tight")
    return out_path
