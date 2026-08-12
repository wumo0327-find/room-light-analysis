"""
core/experiments.py — 参数化实验批量运行 + 帕累托前沿 + 散点气泡图  v2.11.0（v2.5.0新增，v2.11.0替换默认算例房间为夏热冬冷幼儿园活动室尺寸）
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
    """
    标准算例房间：v2.11.0 起改用夏热冬冷地区幼儿园活动室的代表性尺寸
    （此前为通用占位的 6×4×3m 教室，与实际幼儿园房型无关，现替换为有据可查
    的参考尺寸，使参数化实验对应用场景更贴切）。

    尺寸依据：
      - 层高3.6m、窗台高0.6m、窗高2.4m —— 直接引自《托儿所、幼儿园建筑设计
        规范》JGJ39-2016（2019年版）关于活动室采光计算模型的规定。
      - 进深6.6m —— 该规范"单侧采光的活动室进深不宜大于6.60m"的上限值。
      - 开间9.0m、窗宽4.2m —— 规范未给出单一活动室的开间数值（规范给出的
        6.6m×18.9m/6.9m×18.0m参考单元是多房间合并的"生活单元"整体尺寸，
        非单间活动室），按常见班额（约30人、人均2㎡量级）与窗地比不低于
        1/6的採光要求推算得到，为本项目的合理估算，非规范直接给出的数值。
    """
    room = RoomModel(length=6600.0, width=9000.0, height=3600.0)
    win = room.add_window("south")
    win.width, win.height = 4200.0, 2400.0
    win.y = 600.0
    win.x = (room.width - win.width) / 2.0
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
    tilt_degs: Optional[List[float]] = None,
    depth_mms: Optional[List[float]] = None,
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
    v2.12.0 重新设计：不再用抽象特征角β反算深度，直接对"倾斜角θ×板长L"做
    网格扫描（θ、L都是可直接施工的物理量）。
      θ（°）：约定90°=水平，>90°=上扬（板尖高于板根），<90°=下垂（板尖低于
             板根）——方向已隐含在90°的哪一侧，不需要额外正负号。
      L（mm）：挑檐板自身长度（沿板方向，非水平投影深度）。
    默认 θ∈{60,70,80,90,100,110,120}（对应下垂30°~水平~上扬30°，7档），
    L∈{300,600,900,1200,1500}（5档），共35个组合，每个组合跑一次完整
    采光+热环境分析。

    材料(diffuse_residual)与安装间隙(overhang_gap_mm)在本函数里是**固定值**，
    不参与网格扫描——按论文讨论达成的"先筛选几何主效应、材料/间隙留到候选
    方案上做二阶段敏感性复核"的思路，避免 θ×L×材料×间隙 全交叉组合数爆炸。
    如需材料敏感性分析，对筛出的候选 (θ,L) 组合单独调用本函数、只改
    diffuse_residual 即可（可参考 MATERIAL_PRESETS 里的预设反射率估算值）。

    cost_fn(tilt_deg, depth_mm) 可自定义成本占位；默认给一个随 L 线性的占位值，
    不区分倾斜角（如认为倾斜造型加工成本不同，可自定义 cost_fn 体现）。
    """
    if tilt_degs is None:
        tilt_degs = [60.0, 70.0, 80.0, 90.0, 100.0, 110.0, 120.0]
    if depth_mms is None:
        depth_mms = [300.0, 600.0, 900.0, 1200.0, 1500.0]
    weather = weather or default_dataset()
    if cost_fn is None:
        cost_fn = lambda th, d: 100.0 + d / 10.0    # 占位：后续替换真实造价
    combos = [(th, d) for th in tilt_degs for d in depth_mms]
    rows = []
    for i, (tilt, L_mm) in enumerate(combos):
        if progress_cb:
            progress_cb(i, len(combos))
        room = _base_room()
        if glass is not None:
            for w in room.windows:
                w.tau = glass.tvis
            room.thermal.SC_glass = glass.sc
        room.shading = ShadingDevice(
            type="horizontal_overhang",
            overhang_depth_mm=L_mm,
            overhang_height_mm=overhang_gap_mm,
            overhang_tilt_deg=float(tilt),
            diffuse_residual=diffuse_residual,
        )
        rec = _run_case(room, weather, ndiv, ra_threshold)
        if abs(tilt - 90.0) < 1e-6:
            tag = "水平"
        elif tilt > 90.0:
            tag = f"上扬{tilt - 90.0:.0f}°"
        else:
            tag = f"下垂{90.0 - tilt:.0f}°"
        rec.update({"group": "遮阳θ×L扫描",
                    "param_label": f"{tag} L={L_mm:.0f}",
                    "param_value": float(tilt),   # 主参数（供排序/2D散点默认取用）
                    "tilt_deg": float(tilt),
                    "L_mm": float(L_mm),
                    "cost": float(cost_fn(tilt, L_mm))})
        rows.append(rec)
    return pd.DataFrame(rows)


def run_all_experiments(
    weather: Optional[WeatherDataset] = None,
    ndiv: int = 20,
    ra_threshold: float = 2.0,
    tilt_degs: Optional[List[float]] = None,
    depth_mms: Optional[List[float]] = None,
    diffuse_residual: float = 0.30,
    overhang_gap_mm: float = 0.0,
    glass_specs: Optional[List[GlassSpec]] = None,
    progress_cb: Optional[Callable[[str, int, int], None]] = None,
) -> pd.DataFrame:
    """
    跑两组实验并合并为一张表（统一字段，含 group 列）。
    diffuse_residual（材料 k_diff）、overhang_gap_mm（安装间隙h）在θ×L网格里是
    固定值、不参与扫描——见 run_overhang_experiment() 文档字符串的分阶段实验说明。
    """
    weather = weather or default_dataset()
    df_g = run_glass_experiment(
        specs=glass_specs, weather=weather, ndiv=ndiv, ra_threshold=ra_threshold,
        progress_cb=(lambda i, n: progress_cb("玻璃对照", i, n)) if progress_cb else None)
    df_o = run_overhang_experiment(
        tilt_degs=tilt_degs, depth_mms=depth_mms, diffuse_residual=diffuse_residual,
        overhang_gap_mm=overhang_gap_mm, weather=weather, ndiv=ndiv,
        ra_threshold=ra_threshold,
        progress_cb=(lambda i, n: progress_cb("遮阳θ×L扫描", i, n)) if progress_cb else None)
    cols = ["group", "param_label", "param_value", "Ra", "U0", "comfort_ratio",
            "comfort_months", "overheat_months", "overheat_degree_months",
            "underheat_degree_months", "thermal_discomfort", "SC_annual",
            "DF_avg", "E_avg", "cost", "tilt_deg", "L_mm"]
    df = pd.concat([df_g, df_o], ignore_index=True)
    # 统一列顺序（缺失列如 tilt_deg/L_mm 保留在后）
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


# ── 帕累托前沿（N维，v2.12.0 新增，供3D点云用）──────────────────────────────
def pareto_front_nd(
    df: pd.DataFrame,
    cols: List[str],
    maximize: Optional[List[bool]] = None,
    u0_col: str = "U0",
    u0_min: float = 0.70,
) -> pd.DataFrame:
    """
    N维帕累托前沿（非支配解集）：在满足合规筛选(U0≥u0_min)的点中，求关于
    cols这N个指标的非支配解。maximize[i]=False表示该列越小越好（内部取负）。
    3D及以上的前沿是一个"面"而非一条线，这里只返回非支配点集合，不做连线/
    三角网重建——数据点数量有限时强行连成曲面通常比较难看，也不是必需的。
    算法：O(n²)两两支配关系判定（数据量在实验批量的量级下完全够用，不需要
    更复杂的分治算法）。
    """
    d = df.copy()
    if u0_col in d.columns and u0_min is not None:
        d = d[d[u0_col] >= u0_min]
    if d.empty or not cols:
        return d
    if maximize is None:
        maximize = [True] * len(cols)
    vals = d[cols].to_numpy(dtype=float).copy()
    for j, mx in enumerate(maximize):
        if not mx:
            vals[:, j] = -vals[:, j]
    n = len(vals)
    dominated = np.zeros(n, dtype=bool)
    for i in range(n):
        if dominated[i]:
            continue
        for j in range(n):
            if i == j or dominated[j]:
                continue
            if np.all(vals[j] >= vals[i] - 1e-12) and np.any(vals[j] > vals[i] + 1e-12):
                dominated[i] = True
                break
    return d.iloc[~dominated]


# ── 散点气泡图 ────────────────────────────────────────────────────────────────
# 白底学术主题（与热力图/截面图导出一致）
_C_BG = "#ffffff"; _C_TEXT = "#1a1e2e"; _C_SEC = "#5a6175"
_C_ACCENT = "#2563eb"; _C_BORDER = "#d0d5e0"; _C_GRID = "#f0f2f6"
_C_PARETO = "#dc2626"; _C_NONCOMP = "#9aa0b0"

# 组别 → marker 形状
_GROUP_MARKER = {"玻璃对照": "o", "遮阳θ×L扫描": "s"}
_GROUP_COLOR  = {"玻璃对照": "#16a34a", "遮阳θ×L扫描": _C_ACCENT}


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
    from matplotlib import patheffects as pe

    # 图稍微加宽，给移到图外的图例留位置（不再和数据点/标注重叠）
    fig = Figure(figsize=(10.2, 6), facecolor=_C_BG)
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)
    ax.set_facecolor(_C_BG)

    # 气泡大小映射（成本占位；全相等时用常数）——v2.11.0 缩小整体气泡尺寸范围
    # （原60~400面积太大，点与点之间容易挤在一起重叠，现缩到22~130）
    if size_col in df.columns and df[size_col].notna().any() and df[size_col].std() > 1e-9:
        s = df[size_col].astype(float)
        smin, smax = s.min(), s.max()
        sizes = 22 + (s - smin) / (smax - smin + 1e-9) * 108
    else:
        sizes = pd.Series(55.0, index=df.index)

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
        # 高亮圆圈与原始点之间留出空隙（不紧贴点边缘），而不是套在点上
        ax.scatter(pf_sorted[x], pf_sorted[y], s=sizes.loc[pf_sorted.index] * 2.6,
                   marker="o", facecolors="none", edgecolors=_C_PARETO,
                   linewidths=1.4, zorder=5)

    # v2.11.0: 每个点旁边标注它是哪个参数（β=?° 或 玻璃名），并标数值——
    # 静态图无法悬停查看，不直接标注的话不知道每个点代表什么，帕累托前沿上的
    # 点额外加粗/标红，一眼看出"最优选项是谁"。
    if "param_label" in df.columns:
        pf_idx = set(pf.index) if not pf.empty else set()
        # 按 x 排序后交替上/下偏移，减少点密集处（如帕累托拐点附近）的文字重叠
        order = {idx: k for k, idx in enumerate(df.sort_values(x).index)}
        xmin, xmax = df[x].min(), df[x].max()
        xspan = max(xmax - xmin, 1e-9)
        for i, row in df.iterrows():
            on_front = i in pf_idx
            txt = f"{row['param_label']} ({row[x]:.2f}, {row[y]:.1f})"
            up = order.get(i, 0) % 2 == 0
            # 图例已固定在图外右侧，最靠右的点如果文字往右延伸会撞上图例——
            # x 落在最右 15% 范围内时，文字改往左边长，并右对齐
            near_right = (row[x] - xmin) / xspan > 0.85
            ha = "right" if near_right else "left"
            dx = -6 if near_right else 5
            ax.annotate(
                txt, (row[x], row[y]),
                xytext=(dx, 9 if up else -12), textcoords="offset points",
                fontsize=(7.2 if on_front else 6.3),
                color=(_C_PARETO if on_front else _C_SEC),
                fontweight=("bold" if on_front else "normal"),
                alpha=(1.0 if on_front else 0.85),
                ha=ha, zorder=6,
                path_effects=[pe.withStroke(linewidth=2.2, foreground="#ffffff")])

    ax.set_xlabel(x_label, color=_C_TEXT, fontsize=11)
    ax.set_ylabel(y_label, color=_C_TEXT, fontsize=11)
    ax.set_title(title, color=_C_TEXT, fontsize=12, fontweight="bold", pad=10)
    ax.grid(True, color=_C_GRID, lw=0.6)
    for sp in ax.spines.values():
        sp.set_color(_C_BORDER); sp.set_linewidth(0.7)
    ax.tick_params(colors=_C_SEC, labelsize=9)
    # 图例固定放到图外右侧（不用 loc="best" 自动找位置）——数据点+新增的逐点
    # 标注布满整个绘图区后，"best"经常会跟某个点/文字撞在一起，移到图外彻底避免
    ax.legend(fontsize=8.5, loc="upper left", bbox_to_anchor=(1.01, 1.0),
              labelcolor=_C_TEXT, facecolor="#ffffff", edgecolor=_C_BORDER,
              framealpha=0.95, borderaxespad=0.0)
    fig.tight_layout()
    fig.savefig(out_path, dpi=dpi, facecolor=_C_BG, bbox_inches="tight")
    return out_path


# ── 3D 帕累托点云（v2.12.0 新增）─────────────────────────────────────────────
# 三轴：Ra(采光达标面积比)↑ / comfort_ratio(舒适月数占比)↑ /
#      cost_inv=cost.max()-cost(成本越低此值越大)↑ —— 三轴都"数值越大越好"，
# 原点(0,0,0)代表三项都最差，点离原点越远综合表现越好（用户明确要求的坐标
# 方向约定）。所有点大小一致（成本已经是一根轴，不再用气泡大小编码）。
_3D_DIMS = {
    "Ra":            "采光达标面积比 Ra",
    "comfort_ratio": "舒适月数占比",
    "_cost_inv":     "成本节省(cost上限-该方案cost)",
}


def build_pareto3d_figure(
    df: pd.DataFrame,
    u0_col: str = "U0",
    u0_min: float = 0.0,
):
    """
    构建3D帕累托点云图（返回 matplotlib Figure，不直接存盘——由 UI 层用
    FigureCanvasQTAgg 嵌入并绑定鼠标事件，实现"拖动旋转+点击某面墙弹出对应
    二维图"的交互）。

    三面墙背景画同一批点在两两指标上的二维投影（Ra×舒适度 / Ra×成本 /
    舒适度×成本），对应参考图里那种"3D点云+3张墙面投影"的常见画法。

    返回 (fig, wall_pick_map)：wall_pick_map 是 {PathCollection: (x_col, y_col,
    x_label, y_label, maximize_y)}，UI 层可以把它跟 matplotlib 的 pick_event
    配合，判断点击的是哪面墙、进而弹出对应的二维散点图（复用 plot_experiments）。

    3D空间里的帕累托前沿是一个"面"，不是一条线——这里只把非支配点加粗高亮，
    不强行连成三角网曲面（数据点数量有限时连面通常比较难看，也不是必需的）。
    """
    from matplotlib.figure import Figure
    from mpl_toolkits.mplot3d import Axes3D  # noqa: F401  注册 3d 投影，勿删

    d = df.copy()
    cost_max = d["cost"].max() if "cost" in d.columns else 0.0
    d["_cost_inv"] = cost_max - d.get("cost", 0.0)

    compliant = d[u0_col] >= u0_min if (u0_col in d.columns and u0_min is not None) \
        else pd.Series(True, index=d.index)

    pf = pareto_front_nd(d, ["Ra", "comfort_ratio", "_cost_inv"],
                         maximize=[True, True, True], u0_col=u0_col, u0_min=u0_min)
    pf_idx = set(pf.index)

    fig = Figure(figsize=(7.2, 6.4), facecolor=_C_BG)
    ax = fig.add_subplot(111, projection="3d")
    ax.set_facecolor(_C_BG)

    xmin, xmax = float(d["Ra"].min()), float(d["Ra"].max())
    ymin, ymax = float(d["comfort_ratio"].min()), float(d["comfort_ratio"].max())
    zmin, zmax = float(d["_cost_inv"].min()), float(d["_cost_inv"].max())
    # 留一点边距，避免点贴在墙上不好看
    xpad, ypad, zpad = (xmax-xmin)*0.08 or 0.02, (ymax-ymin)*0.08 or 0.02, (zmax-zmin)*0.08 or 0.02
    xmin, xmax = xmin - xpad, xmax + xpad
    ymin, ymax = ymin - ypad, ymax + ypad
    zmin, zmax = zmin - zpad, zmax + zpad

    wall_pick_map = {}

    def _scatter_group(gdf, mask, size, alpha, edgecolor_pareto=False):
        for grp, gg in gdf.groupby("group"):
            sub = gg[mask.loc[gg.index]] if mask is not None else gg
            if sub.empty:
                continue
            mk = _GROUP_MARKER.get(grp, "D")
            col = _GROUP_COLOR.get(grp, _C_ACCENT)
            is_pf = sub.index.isin(pf_idx)
            # 非帕累托点
            non_pf = sub[~is_pf]
            if len(non_pf):
                ax.scatter(non_pf["Ra"], non_pf["comfort_ratio"], non_pf["_cost_inv"],
                          s=size, marker=mk, c=col, alpha=alpha,
                          edgecolors="white", linewidths=0.6, depthshade=True,
                          label=grp)
            pf_pts = sub[is_pf]
            if len(pf_pts):
                ax.scatter(pf_pts["Ra"], pf_pts["comfort_ratio"], pf_pts["_cost_inv"],
                          s=size * 1.5, marker=mk, c=col, alpha=1.0,
                          edgecolors=_C_PARETO, linewidths=1.8, depthshade=True)

    _scatter_group(d, compliant, size=42, alpha=0.85)
    ncomp = d[~compliant]
    if len(ncomp):
        ax.scatter(ncomp["Ra"], ncomp["comfort_ratio"], ncomp["_cost_inv"],
                  s=30, marker="x", c=_C_NONCOMP, alpha=0.5,
                  label=f"U0<{u0_min:g}不合规")

    # 三面墙背景投影（画在坐标范围的最小值那一侧，模拟参考图的"箱体墙面"效果）
    wall_alpha, wall_size = 0.35, 18
    pc_xy = ax.scatter(d["Ra"], d["comfort_ratio"], zs=zmin, zdir="z",
                       s=wall_size, c=_C_SEC, alpha=wall_alpha, picker=True, pickradius=6)
    wall_pick_map[pc_xy] = ("Ra", "comfort_ratio", "采光达标面积比 Ra", "舒适月数占比", True)

    pc_xz = ax.scatter(d["Ra"], d["_cost_inv"], zs=ymax, zdir="y",
                       s=wall_size, c=_C_SEC, alpha=wall_alpha, picker=True, pickradius=6)
    wall_pick_map[pc_xz] = ("Ra", "cost", "采光达标面积比 Ra", "成本", False)

    pc_yz = ax.scatter(d["comfort_ratio"], d["_cost_inv"], zs=xmin, zdir="x",
                       s=wall_size, c=_C_SEC, alpha=wall_alpha, picker=True, pickradius=6)
    wall_pick_map[pc_yz] = ("comfort_ratio", "cost", "舒适月数占比", "成本", False)

    ax.set_xlim(xmin, xmax); ax.set_ylim(ymin, ymax); ax.set_zlim(zmin, zmax)
    ax.set_xlabel(_3D_DIMS["Ra"], color=_C_TEXT, fontsize=9, labelpad=8)
    ax.set_ylabel(_3D_DIMS["comfort_ratio"], color=_C_TEXT, fontsize=9, labelpad=8)
    ax.set_zlabel(_3D_DIMS["_cost_inv"], color=_C_TEXT, fontsize=9, labelpad=8)
    ax.tick_params(colors=_C_SEC, labelsize=7)
    ax.set_title("遮阳/玻璃参数 — 三维帕累托点云\n"
                "(x=采光达标面积比↑ y=舒适月数占比↑ z=成本节省↑，离原点越远越好；"
                "灰点=三面墙背景投影，可点击弹出对应二维图)",
                fontsize=9.5, color=_C_TEXT, pad=4)
    ax.xaxis.pane.set_facecolor("#f8f9fb"); ax.xaxis.pane.set_alpha(0.6)
    ax.yaxis.pane.set_facecolor("#f8f9fb"); ax.yaxis.pane.set_alpha(0.6)
    ax.zaxis.pane.set_facecolor("#f8f9fb"); ax.zaxis.pane.set_alpha(0.6)
    ax.grid(True, color=_C_GRID, lw=0.5)
    fig.tight_layout()
    return fig, wall_pick_map
