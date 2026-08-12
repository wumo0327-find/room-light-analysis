"""
core/experiments.py — 参数化实验批量运行 + 帕累托前沿 + 散点气泡图  v2.13.0（v2.5.0新增，v2.11.0幼儿园算例，v2.12.0倾斜角θ×L网格+3D点云，v2.13.0θ×L×h×材料四维网格+按材料着色）
================================================================================
不依赖 GUI 的批量实验入口，支撑论文的两组实验：

  · 对照组：窗玻璃材质（Tvis, SC）扫描
  · 主实验组：遮阳 倾斜角θ × 板长L × 安装间隙h × 材料 四维网格扫描
    （θ=90°水平、>90°上扬、<90°下垂；材料只经 k_diff 影响热环境+经单位造价影响成本）

每个算例跑完整「采光 + 热环境」流程，汇总为一张 pandas 表；再做帕累托前沿提取
与散点图/3D点云导出。v2.13.0 起帕累托**按材料分别求**（每种材料一条自己的前沿），
且 x/y 轴的"越大/越小越好"由 maximize_x/maximize_y 显式指定（修复"热轴×成本"这类
x 为"越小越好"指标时前沿算反的老bug）；成本随材料区分（此前只随L变化、材料重叠）。

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


def _daylight_metrics(dl) -> dict:
    """从 DaylightResult 提取采光指标（仅几何决定，与遮阳材料无关）。"""
    return {"Ra": dl.Ra, "U0": dl.U0, "DF_avg": dl.DF_avg, "E_avg": dl.E_avg}


def _thermal_metrics(th) -> dict:
    """从 ThermalResult 提取热环境指标（受遮阳材料 k_diff 影响）。"""
    over_dm  = float(np.sum(np.maximum(th.T_in - T_HI, 0.0)))
    under_dm = float(np.sum(np.maximum(T_LO - th.T_in, 0.0)))
    return {
        "comfort_months":          th.comfort_months,
        "comfort_ratio":           th.comfort_months / 12.0,
        "overheat_months":         th.overheat_months,
        "overheat_degree_months":  over_dm,
        "underheat_degree_months": under_dm,
        "thermal_discomfort":      over_dm + under_dm,
        "SC_annual":               th.SC_effective,
    }


def _run_case(room: RoomModel, weather: WeatherDataset,
              ndiv: int, ra_threshold: float) -> dict:
    """跑一个算例的完整采光+热环境流程，返回指标字典。"""
    dl = compute_daylight(room, E_out=weather.annual_avg,
                          ndiv=ndiv, ra_threshold=ra_threshold)
    th = compute_thermal(room, weather.monthly_ghi, weather.monthly_temp)
    rec = _daylight_metrics(dl); rec.update(_thermal_metrics(th))
    return rec


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
                    "param_value": g.tvis, "cost": g.cost,
                    "material": "—(玻璃组)", "_plot_color": "#16a34a"})
        rows.append(rec)
    return pd.DataFrame(rows)


# ── 主实验组：遮阳 θ×L×h×材料 网格扫描 ───────────────────────────────────────
def run_overhang_experiment(
    tilt_degs: Optional[List[float]] = None,
    depth_mms: Optional[List[float]] = None,
    gap_mms: Optional[List[float]] = None,
    materials: Optional[List[str]] = None,
    glass: Optional[GlassSpec] = None,
    weather: Optional[WeatherDataset] = None,
    ndiv: int = 20,
    ra_threshold: float = 2.0,
    cost_fn: Optional[Callable[[float, float], float]] = None,
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> pd.DataFrame:
    """
    v2.12.0 起用"倾斜角θ×板长L"网格扫描（θ、L都是可直接施工的物理量）；
    v2.13.0 起再叠加"安装间隙h × 材料"两维，即 θ×L×h×材料 四维网格。
      θ（°）：约定90°=水平，>90°=上扬（板尖高于板根），<90°=下垂（板尖低于
             板根）——方向已隐含在90°的哪一侧，不需要额外正负号。
      L（mm）：挑檐板自身长度（沿板方向，非水平投影深度）。
      h（mm）：窗顶到板根底面的安装间隙（overhang_height_mm）。
      材料：core.models.MATERIAL_LIBRARY 里的材料名列表，每个材料对应一个
            k_diff(diffuse_residual)残余透过比 + 绘图配色。

    **计算优化（重要）**：采光(Ra/U0/DF)只由几何(θ,L,h)决定、与遮阳材料无关
    （材料只通过 k_diff 影响热环境SC）。因此对每个 (θ,L,h) 只算一次采光，
    再对每个材料只补算热环境——避免材料维度成倍放大最耗时的采光网格计算。
    这也意味着散点/点云上"同一(θ,L,h)、不同材料"的点会有相同的Ra(横轴)、
    不同的热轴值，天然呈现材料对热环境的敏感性。

    materials=None 时用单一默认 k_diff=0.30（等价旧行为，材料列标"默认")。
    成本占位（v2.13.0 起随材料区分）：cost = 基础安装费 + L(米) × 材料单位造价，
      材料单位造价取自 MATERIAL_LIBRARY 各材料的 cost 字段（混凝土便宜、铝板/胡桃木
      贵、松木/涂料居中，均为占位量级非真实报价）。这样不同材料在成本轴上分开，
      "采光×成本 / 热轴×成本"两张图才对材料有区分度（此前成本只随L变化、材料完全
      重叠只显示一种）。cost_fn 若显式传入则回退旧行为 cost_fn(tilt, L)（不区分材料）。
    """
    from core.models import get_material
    if tilt_degs is None:
        tilt_degs = [60.0, 70.0, 80.0, 90.0, 100.0, 110.0, 120.0]
    if depth_mms is None:
        depth_mms = [300.0, 600.0, 900.0, 1200.0, 1500.0]
    if gap_mms is None:
        gap_mms = [0.0]
    weather = weather or default_dataset()
    _BASE_INSTALL = 60.0   # 基础安装费占位（元），与材料/尺寸无关的固定项

    # 材料列表 → [(材料名, k_diff, 颜色, 单位造价)]；None/空 → 单一默认材料
    mat_specs = []
    for m in (materials or []):
        spec = get_material(m)
        if spec is not None:
            mat_specs.append((m, spec["k_diff"], spec["color"], float(spec.get("cost", 120.0))))
    if not mat_specs:
        mat_specs = [("默认(k_diff=0.30)", 0.30, _C_ACCENT, 120.0)]

    geom_combos = [(th, d, g) for th in tilt_degs for d in depth_mms for g in gap_mms]
    total = len(geom_combos) * len(mat_specs)
    rows = []
    done = 0
    for (tilt, L_mm, gap_mm) in geom_combos:
        # —— 采光只算一次（与材料无关）——
        room = _base_room()
        if glass is not None:
            for w in room.windows:
                w.tau = glass.tvis
            room.thermal.SC_glass = glass.sc
        room.shading = ShadingDevice(
            type="horizontal_overhang",
            overhang_depth_mm=L_mm,
            overhang_height_mm=gap_mm,
            overhang_tilt_deg=float(tilt),
        )
        dl = compute_daylight(room, E_out=weather.annual_avg,
                              ndiv=ndiv, ra_threshold=ra_threshold)
        dl_metrics = _daylight_metrics(dl)

        if abs(tilt - 90.0) < 1e-6:
            tag = "水平"
        elif tilt > 90.0:
            tag = f"上扬{tilt - 90.0:.0f}°"
        else:
            tag = f"下垂{90.0 - tilt:.0f}°"

        # —— 每个材料只补算热环境 ——
        for (mat_name, k_diff, color, unit_cost) in mat_specs:
            if progress_cb:
                progress_cb(done, total)
            done += 1
            room.shading.diffuse_residual = k_diff
            th = compute_thermal(room, weather.monthly_ghi, weather.monthly_temp)
            rec = dict(dl_metrics); rec.update(_thermal_metrics(th))
            # 成本：显式 cost_fn 优先（旧行为，不区分材料）；否则用材料单位造价
            if cost_fn is not None:
                cost = float(cost_fn(tilt, L_mm))
            else:
                cost = _BASE_INSTALL + (L_mm / 1000.0) * unit_cost
            rec.update({
                "group": "遮阳θ×L扫描",
                "param_label": f"{tag} L={L_mm:.0f} h={gap_mm:.0f} [{mat_name}]",
                "param_value": float(tilt),
                "tilt_deg": float(tilt),
                "L_mm": float(L_mm),
                "gap_mm": float(gap_mm),
                "material": mat_name,
                "_plot_color": color,
                "cost": cost,
            })
            rows.append(rec)
    return pd.DataFrame(rows)


def run_all_experiments(
    weather: Optional[WeatherDataset] = None,
    ndiv: int = 20,
    ra_threshold: float = 2.0,
    tilt_degs: Optional[List[float]] = None,
    depth_mms: Optional[List[float]] = None,
    gap_mms: Optional[List[float]] = None,
    materials: Optional[List[str]] = None,
    glass_specs: Optional[List[GlassSpec]] = None,
    include_glass: bool = True,
    progress_cb: Optional[Callable[[str, int, int], None]] = None,
) -> pd.DataFrame:
    """
    跑两组实验并合并为一张表（统一字段，含 group 列）。
    v2.13.0：遮阳组扩为 θ×L×h×材料 四维网格（材料多选、间隙h可扫描）；
    include_glass=False 时可只跑遮阳组（玻璃对照组可选）。
    """
    weather = weather or default_dataset()
    parts = []
    if include_glass:
        df_g = run_glass_experiment(
            specs=glass_specs, weather=weather, ndiv=ndiv, ra_threshold=ra_threshold,
            progress_cb=(lambda i, n: progress_cb("玻璃对照", i, n)) if progress_cb else None)
        parts.append(df_g)
    df_o = run_overhang_experiment(
        tilt_degs=tilt_degs, depth_mms=depth_mms, gap_mms=gap_mms, materials=materials,
        weather=weather, ndiv=ndiv, ra_threshold=ra_threshold,
        progress_cb=(lambda i, n: progress_cb("遮阳θ×L扫描", i, n)) if progress_cb else None)
    parts.append(df_o)
    cols = ["group", "param_label", "param_value", "Ra", "U0", "comfort_ratio",
            "comfort_months", "overheat_months", "overheat_degree_months",
            "underheat_degree_months", "thermal_discomfort", "SC_annual",
            "DF_avg", "E_avg", "cost", "tilt_deg", "L_mm", "gap_mm", "material"]
    df = pd.concat(parts, ignore_index=True)
    # 统一列顺序（缺失列/内部列如 _plot_color 保留在后）
    ordered = [c for c in cols if c in df.columns] + \
              [c for c in df.columns if c not in cols]
    return df[ordered]


# ── 帕累托前沿（2D，最大化 x 与 y）────────────────────────────────────────────
def pareto_front(
    df: pd.DataFrame,
    x: str = "Ra",
    y: str = "comfort_ratio",
    maximize_x: bool = True,
    maximize_y: bool = True,
    u0_col: str = "U0",
    u0_min: float = 0.70,
) -> pd.DataFrame:
    """
    标准 2D 帕累托前沿：在满足合规筛选(U0≥u0_min)的点中，求关于 (x, y) 的非支配解。
    maximize_x/maximize_y=False 时（如 thermal_discomfort/cost 越低越好）内部对该轴
    取负再求前沿——**v2.13.0 修复**：此前 x 恒按"越大越好"处理，导致"热轴×成本"这类
    x 是"越小越好"指标的投影图帕累托前沿算反（圈错点）。
    算法：先把两轴都折算成"越大越好"，按 x' 降序遍历，维护当前最优 y'，遇到严格
    更优的 y' 即为非支配解。返回前沿子表（保持原 df 的行）。
    """
    d = df.copy()
    if u0_col in d.columns and u0_min is not None:
        d = d[d[u0_col] >= u0_min]
    if d.empty:
        return d
    xv = d[x] if maximize_x else -d[x]
    yv = d[y] if maximize_y else -d[y]
    d = d.assign(_px=xv, _py=yv).sort_values(["_px", "_py"], ascending=[False, False])
    best = -np.inf
    keep = []
    for idx, row in d.iterrows():
        if row["_py"] > best + 1e-12:
            keep.append(idx)
            best = row["_py"]
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

# 统一点大小（v2.13.0：取消按成本缩放的气泡，全部点等大，且比原来缩小约一半；
# 取消白色描边，避免点密集时白边糊成一片）。
_MARK_2D = 30.0
_MARK_3D = 22.0


def _color_subgroups(df: pd.DataFrame):
    """
    按"组别→材料"拆成用于着色/描点的子组，产出 (group, key, marker, sub_df)。
      · 玻璃对照：整组一个子组，key="玻璃对照"。
      · 遮阳组：按 material 再分，key=材料名（每种材料一色、一条自己的帕累托前沿）。
    颜色统一取子组内 _plot_color 列首值。
    """
    for grp, gdf in df.groupby("group"):
        mk = _GROUP_MARKER.get(grp, "s")
        if grp != "玻璃对照" and "material" in gdf.columns:
            for _mat, mm in gdf.groupby("material"):
                yield grp, str(mm["material"].iloc[0]), mk, mm
        else:
            yield grp, grp, mk, gdf


def plot_experiments(
    df: pd.DataFrame,
    out_path: str,
    x: str = "Ra",
    y: str = "comfort_ratio",
    maximize_x: bool = True,
    maximize_y: bool = True,
    size_col: str = "cost",   # 兼容旧签名，v2.13.0 起点大小统一、不再按成本缩放
    u0_col: str = "U0",
    u0_min: float = 0.70,
    x_label: str = "采光达标面积比 Ra",
    y_label: str = "舒适月数占比",
    title: str = "遮阳/玻璃参数 — 采光·热环境权衡与帕累托前沿",
    dpi: int = 200,
) -> str:
    """
    散点图（白底学术主题）——v2.13.0 大改：
      · 点大小统一（比原来小一半）、取消白色描边，点密集也不糊成一片。
      · 按"材料"着色：每种材料一色（_plot_color），玻璃组绿色；marker 按组别区分
        （玻璃=圆、遮阳=方）。
      · **每种材料各画自己的帕累托前沿**（此前只算一条全局前沿，k_diff 最低的材料
        在热轴上恒占优，导致只有它被圈、其它材料的好点全被淹没）。前沿点用红圈高亮、
        并按材料色连一条虚线。
      · x/y 轴的"越大/越小越好"由 maximize_x/maximize_y 指定，前沿方向随之正确
        （修复"热轴×成本"图 x 是"越小越好"却按越大算的老bug）。
      · U0<u0_min（不合规）灰色叉号标出、不参与帕累托。
    """
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib import patheffects as pe
    from matplotlib.lines import Line2D

    # 图稍微加宽，给移到图外的图例留位置（不再和数据点/标注重叠）
    fig = Figure(figsize=(10.2, 6), facecolor=_C_BG)
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)
    ax.set_facecolor(_C_BG)

    compliant = df[u0_col] >= u0_min if (u0_col in df.columns and u0_min is not None) \
        else pd.Series(True, index=df.index)
    has_color = "_plot_color" in df.columns
    legend_seen = {}
    pf_all_idx = set()   # 所有材料前沿点的并集（用于标注）

    for grp, key, mk, sub in _color_subgroups(df):
        col = sub["_plot_color"].iloc[0] if has_color else _GROUP_COLOR.get(grp, _C_ACCENT)
        comp = sub.index[compliant.loc[sub.index]]
        ncomp = sub.index[~compliant.loc[sub.index]]
        if len(comp):
            lab = key if key not in legend_seen else None
            legend_seen[key] = True
            ax.scatter(df.loc[comp, x], df.loc[comp, y], s=_MARK_2D,
                       marker=mk, c=col, alpha=0.85, edgecolors="none",
                       label=lab, zorder=3)
        if len(ncomp):
            ax.scatter(df.loc[ncomp, x], df.loc[ncomp, y], s=_MARK_2D * 0.75,
                       marker="x", c=_C_NONCOMP, alpha=0.45, zorder=2)
        # —— 该材料自己的帕累托前沿 ——
        pf = pareto_front(sub, x=x, y=y, maximize_x=maximize_x, maximize_y=maximize_y,
                          u0_col=u0_col, u0_min=u0_min)
        if not pf.empty:
            pf_sorted = pf.sort_values(x)
            ax.plot(pf_sorted[x], pf_sorted[y], "--", color=col, lw=1.2,
                    alpha=0.75, zorder=4)
            ax.scatter(pf_sorted[x], pf_sorted[y], s=_MARK_2D * 2.4,
                       marker="o", facecolors="none", edgecolors=_C_PARETO,
                       linewidths=1.2, zorder=5)
            pf_all_idx.update(pf.index)

    # 逐点标注：各材料前沿点常有十几个、挤在一处会糊，故文字只标"全局前沿"
    # （所有点合起来的非支配解，通常几个最优方案），各材料自己的前沿仍有红圈可视，
    # 只是不逐个写字。全局前沿点数超过16也不标（留给CSV）。
    pf_global = pareto_front(df, x=x, y=y, maximize_x=maximize_x, maximize_y=maximize_y,
                             u0_col=u0_col, u0_min=u0_min)
    label_idx = list(pf_global.index)
    if "param_label" in df.columns and label_idx and len(label_idx) <= 16:
        order = {idx: k for k, idx in enumerate(df.sort_values(x).index)}
        xmin, xmax = df[x].min(), df[x].max()
        xspan = max(xmax - xmin, 1e-9)
        for i in label_idx:
            row = df.loc[i]
            txt = f"{row['param_label']} ({row[x]:.2f}, {row[y]:.1f})"
            up = order.get(i, 0) % 2 == 0
            near_right = (row[x] - xmin) / xspan > 0.82
            ha = "right" if near_right else "left"
            dx = -6 if near_right else 5
            ax.annotate(
                txt, (row[x], row[y]),
                xytext=(dx, 9 if up else -12), textcoords="offset points",
                fontsize=6.8, color=_C_PARETO, fontweight="bold",
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
    # 标注布满整个绘图区后，"best"经常会跟某个点/文字撞在一起，移到图外彻底避免。
    # 追加一个"帕累托前沿点(红圈)"图例项（各材料共用同一红圈样式）。
    handles, labels = ax.get_legend_handles_labels()
    if pf_all_idx:
        handles.append(Line2D([0], [0], marker="o", linestyle="none", markersize=9,
                              markerfacecolor="none", markeredgecolor=_C_PARETO,
                              markeredgewidth=1.4, label="帕累托前沿点(各材料)"))
    ax.legend(handles=handles, fontsize=8.5, loc="upper left", bbox_to_anchor=(1.01, 1.0),
              labelcolor=_C_TEXT, facecolor="#ffffff", edgecolor=_C_BORDER,
              framealpha=0.95, borderaxespad=0.0)
    fig.tight_layout()
    fig.savefig(out_path, dpi=dpi, facecolor=_C_BG, bbox_inches="tight")
    return out_path


# ── 3D 帕累托点云（v2.12.0 新增，v2.13.0 改轴+按材料着色+去掉点击交互）─────────
# 三轴：x=Ra(采光达标面积比，越大越好) / y=热不舒适度Σ(超温+欠温，越小越好，
#      v2.13.0 由"舒适月数占比"改回连续量热不舒适度、区分度更好) / z=成本节省
#      (=cost上限−该方案cost，越大越好)。
# 为保留"最优方案聚在一个角、一眼能看出"的直觉，y轴(热不舒适度)做反向显示
# （小值画在外侧），这样三轴都是"越往外角=越好"，最优簇落在右-上-外角。
# 所有点大小一致；点的颜色=遮阳材料（玻璃组单独绿色）。
_3D_VIEW_ELEV, _3D_VIEW_AZIM = 22.0, -58.0   # 默认视角（供"恢复默认视图"复位）


def build_pareto3d_figure(
    df: pd.DataFrame,
    u0_col: str = "U0",
    u0_min: float = 0.0,
):
    """
    构建3D帕累托点云图，返回 (fig, ax)。由 UI 层用 FigureCanvasQTAgg 嵌入，
    支持鼠标拖动旋转；不再绑定点击弹二维图（三张二维投影图改由2D模式下的三个
    按钮直接查看，见 UI 层）。

    颜色=遮阳材料（取自 _plot_color 列，玻璃组绿色）。v2.13.0 大改：
      · 空中的真实数据点：普通点=方块(■)、帕累托前沿点=圆点(●红边)，形状即区分
        （不再靠大小/仅红边）；点大小统一、比原来小一半、无白边。
      · 三面墙投影：按材料着色（不再是浅灰），且每面画出**该投影面自己的2D帕累托
        前沿**（红圈）——于是三面墙分别对应三张2D图(采光×热轴 / 采光×成本 / 热轴×
        成本)，空中只留三面汇集的真实点云，墙上只留贴墙的投影。
      · 帕累托按材料分别求（与2D图一致），三面墙的前沿红圈合起来近似构成3D前沿"面"。
      · 网格更密（每轴约8格）。
    """
    from matplotlib.figure import Figure
    from matplotlib.lines import Line2D
    from matplotlib.ticker import MaxNLocator
    from mpl_toolkits.mplot3d import Axes3D  # noqa: F401  注册 3d 投影，勿删

    d = df.copy()
    cost_max = d["cost"].max() if "cost" in d.columns else 0.0
    d["_cost_inv"] = cost_max - d.get("cost", 0.0)
    has_color = "_plot_color" in d.columns
    YCOL = "thermal_discomfort"

    compliant = d[u0_col] >= u0_min if (u0_col in d.columns and u0_min is not None) \
        else pd.Series(True, index=d.index)
    comp_df = d[compliant]

    fig = Figure(figsize=(7.2, 6.4), facecolor=_C_BG)
    ax = fig.add_subplot(111, projection="3d")
    ax.set_facecolor(_C_BG)

    xmin, xmax = float(d["Ra"].min()), float(d["Ra"].max())
    ymin, ymax = float(d[YCOL].min()), float(d[YCOL].max())
    zmin, zmax = float(d["_cost_inv"].min()), float(d["_cost_inv"].max())
    xpad = (xmax-xmin)*0.08 or 0.02
    ypad = (ymax-ymin)*0.08 or 0.02
    zpad = (zmax-zmin)*0.08 or 0.02
    xmin, xmax = xmin - xpad, xmax + xpad
    ymin, ymax = ymin - ypad, ymax + ypad
    zmin, zmax = zmin - zpad, zmax + zpad

    # —— 空中真实点云：按材料分色，每材料自己的3D帕累托点=圆●、其余=方■ ——
    for grp, key, mk, sub in _color_subgroups(comp_df):
        col = sub["_plot_color"].iloc[0] if has_color else _C_ACCENT
        pf = pareto_front_nd(sub, ["Ra", YCOL, "_cost_inv"],
                             maximize=[True, False, True], u0_col=u0_col, u0_min=u0_min)
        pf_idx = set(pf.index)
        non_pf = sub[~sub.index.isin(pf_idx)]
        pf_pts = sub[sub.index.isin(pf_idx)]
        if len(non_pf):
            ax.scatter(non_pf["Ra"], non_pf[YCOL], non_pf["_cost_inv"],
                       s=_MARK_3D, marker="s", c=col, alpha=0.9,
                       edgecolors="none", depthshade=True)
        if len(pf_pts):
            ax.scatter(pf_pts["Ra"], pf_pts[YCOL], pf_pts["_cost_inv"],
                       s=_MARK_3D * 1.5, marker="o", c=col, alpha=1.0,
                       edgecolors=_C_PARETO, linewidths=1.5, depthshade=False)
    ncomp = d[~compliant]
    if len(ncomp):
        ax.scatter(ncomp["Ra"], ncomp[YCOL], ncomp["_cost_inv"],
                   s=_MARK_3D * 0.7, marker="x", c=_C_NONCOMP, alpha=0.45)

    # —— 三面墙投影：按材料着色 + 每面自己的2D帕累托红圈（对应三张2D图）——
    def _draw_wall(a_col, a_max, b_col, b_max, zs, zdir):
        for grp, key, mk, sub in _color_subgroups(comp_df):
            col = sub["_plot_color"].iloc[0] if has_color else _C_ACCENT
            ax.scatter(sub[a_col], sub[b_col], zs=zs, zdir=zdir, s=9, c=col,
                       marker=mk, alpha=0.22, edgecolors="none", depthshade=False)
            pf2 = pareto_front(sub, x=a_col, y=b_col, maximize_x=a_max, maximize_y=b_max,
                               u0_col=u0_col, u0_min=u0_min)
            if not pf2.empty:
                ax.scatter(pf2[a_col], pf2[b_col], zs=zs, zdir=zdir, s=26,
                           facecolors="none", edgecolors=_C_PARETO, linewidths=0.9,
                           marker="o", depthshade=False)

    # 底墙(z=zmin)：采光Ra↑ × 热不舒适度↓ → 对应"采光×热轴"
    _draw_wall("Ra", True, YCOL, False, zmin, "z")
    # 后墙(y=ymax)：采光Ra↑ × 成本节省↑(=成本↓) → 对应"采光×成本"
    _draw_wall("Ra", True, "_cost_inv", True, ymax, "y")
    # 侧墙(x=xmin)：热不舒适度↓ × 成本节省↑ → 对应"热轴×成本"
    _draw_wall(YCOL, False, "_cost_inv", True, xmin, "x")

    ax.set_xlim(xmin, xmax)
    ax.set_ylim(ymax, ymin)   # y轴反向：热不舒适度小值(更好)画在外侧
    ax.set_zlim(zmin, zmax)
    ax.set_xlabel("采光达标面积比 Ra ↑", color=_C_TEXT, fontsize=9, labelpad=8)
    ax.set_ylabel("热不舒适度 ↓(外侧更优)", color=_C_TEXT, fontsize=9, labelpad=8)
    ax.set_zlabel("成本节省 ↑", color=_C_TEXT, fontsize=9, labelpad=8)
    ax.tick_params(colors=_C_SEC, labelsize=7)
    # 网格更密：每轴约8格（点云的三面墙格子更均匀细密）
    ax.xaxis.set_major_locator(MaxNLocator(nbins=8))
    ax.yaxis.set_major_locator(MaxNLocator(nbins=8))
    ax.zaxis.set_major_locator(MaxNLocator(nbins=8))
    ax.set_title("遮阳/玻璃参数 — 三维帕累托点云\n"
                "(x=采光达标面积比↑ y=热不舒适度↓ z=成本节省↑；三轴均\"越往外角=越好\"。"
                "圆●=帕累托前沿点/方■=普通点，点色=材料；三面墙投影=对应的三张2D图)",
                fontsize=9.0, color=_C_TEXT, pad=4)
    ax.xaxis.pane.set_facecolor("#f8f9fb"); ax.xaxis.pane.set_alpha(0.6)
    ax.yaxis.pane.set_facecolor("#f8f9fb"); ax.yaxis.pane.set_alpha(0.6)
    ax.zaxis.pane.set_facecolor("#f8f9fb"); ax.zaxis.pane.set_alpha(0.6)
    ax.grid(True, color=_C_GRID, lw=0.5)
    # 图例：形状(圆/方)与红圈含义
    ax.legend(handles=[
        Line2D([0], [0], marker="s", linestyle="none", markersize=7,
               markerfacecolor=_C_SEC, markeredgecolor="none", label="普通点(方)"),
        Line2D([0], [0], marker="o", linestyle="none", markersize=8,
               markerfacecolor=_C_SEC, markeredgecolor=_C_PARETO,
               markeredgewidth=1.4, label="帕累托前沿点(圆)"),
    ], fontsize=7.5, loc="upper left", labelcolor=_C_TEXT,
        facecolor="#ffffff", edgecolor=_C_BORDER, framealpha=0.9)
    ax.view_init(elev=_3D_VIEW_ELEV, azim=_3D_VIEW_AZIM)
    fig.tight_layout()
    return fig, ax
