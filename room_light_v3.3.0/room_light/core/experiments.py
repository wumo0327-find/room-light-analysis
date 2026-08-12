"""
core/experiments.py — 参数化实验批量运行 + 全局帕累托前沿 + 2D/3D图  v3.3.0
================================================================================
不依赖 GUI 的批量实验入口，支撑论文的两组实验：

  · 对照组：窗玻璃材质（Tvis, SC）扫描
  · 主实验组：遮阳 倾斜角θ × 板长L × 安装间隙h × 材料 四维网格扫描
    （θ=90°水平、>90°上扬、<90°下垂；材料只经 k_diff 影响热环境+经单位造价影响成本）

每个算例跑完整「采光 + 热环境」流程，汇总为一张 pandas 表；再把所有遮阳材料、
倾角、板长和间隙放入同一个候选池，按「Ra最大、热不舒适度最小、成本最小」
计算全局三目标帕累托集合。玻璃组只作绿色参照，不参与遮阳最优方案筛选。

v2.14.0 图示语义：
  · 每张2D图的红圈和虚线表示该图两个指标自己的全局二维帕累托集合/边界。
  · 3D三个投影面分别复用对应2D图的红圈与边界；空中点云只圈三目标全局帕累托。
  · 金色星标表示等权归一化理想点距离最小的「均衡推荐方案」。
  · 3D轴保留Ra/热指标/成本原始值，并与三张2D图共用坐标范围；三个投影面
    在各目标较差边界相交于空间参考点O（O不要求数值为0）；热轴和成本轴
    反向显示，使低不舒适度、低成本与高Ra共同朝向最外侧。

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
from copy import deepcopy
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


def _experiment_room(base_room: Optional[RoomModel] = None) -> RoomModel:
    """
    参数化实验的基础房间。

    GUI传入当前rlproj的RoomModel时完整克隆房间几何、窗位置/尺寸/玻璃、围护结构和
    地理位置，仅清除原有全部遮阳；命令行未传入时保留历史代表房间作为后备。
    """
    room = deepcopy(base_room) if base_room is not None else _base_room()
    room.shading = ShadingDevice(type="none")
    return room


def room_has_any_shading(room: RoomModel) -> bool:
    """判断当前工程是否存在实际生效的水平或垂直遮阳。"""
    shading = room.shading
    horizontal = (
        shading.type == "horizontal_overhang"
        and float(shading.overhang_depth_mm) > 0.0)
    overhang_overrides = (
        shading.type == "horizontal_overhang"
        and any(
            float(values.get("depth_mm", shading.overhang_depth_mm)) > 0.0
            for values in (shading.overhang_overrides or {}).values()))
    vertical = (
        bool(shading.vertical_fin_enabled)
        and (
            float(shading.vertical_fin_depth_mm) > 0.0
            or any(float(v) > 0.0 for v in (shading.fin_overrides or {}).values())
        )
    )
    return horizontal or overhang_overrides or vertical


def build_solution_room(base_room: RoomModel, solution) -> RoomModel:
    """把结果表中的一个水平遮阳方案装到当前工程的无原遮阳克隆上。"""
    from core.models import get_material
    room = _experiment_room(base_room)
    L_mm = float(solution.get("L_mm", 0.0))
    if L_mm <= 0.0:
        return room
    material = str(solution.get("material", ""))
    spec = get_material(material) or {}
    room.shading = ShadingDevice(
        type="horizontal_overhang",
        overhang_depth_mm=L_mm,
        overhang_height_mm=float(solution.get("gap_mm", 0.0)),
        overhang_tilt_deg=float(solution.get("tilt_deg", 90.0)),
        diffuse_residual=float(spec.get("k_diff", 0.30)),
    )
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


def run_project_baseline(
    base_room: RoomModel,
    weather: Optional[WeatherDataset] = None,
    ndiv: int = 20,
    ra_threshold: float = 2.0,
    eligible_as_no_shade: bool = False,
) -> pd.DataFrame:
    """
    当前rlproj的改造前对比点：完整保留房间、窗、玻璃/热工和现有遮阳状态。
    它通常仅作参照；若工程本来就没有遮阳且本轮扫描包含L=0，则同一个点也作为
    唯一的L=0候选参与全局筛选，无需再次计算。
    """
    weather = weather or default_dataset()
    room = deepcopy(base_room)
    is_no_shade = not room_has_any_shading(room)
    rec = _run_case(room, weather, ndiv, ra_threshold)
    rec.update({
        "group": "原始模型基准",
        "param_label": "原始模型（导入工程当前状态）",
        "param_value": 0.0,
        "cost": 0.0,
        "tilt_deg": np.nan,
        "L_mm": 0.0 if is_no_shade else np.nan,
        "gap_mm": np.nan,
        "material": "无" if is_no_shade else "—(改造前基准)",
        "_plot_color": "#16a34a",
        "is_candidate": bool(eligible_as_no_shade and is_no_shade),
        "panel_area_m2": 0.0,
        "material_unit_price": 0.0,
        "material_cost": 0.0,
        "support_length_m": 0.0,
        "support_unit_price": 0.0,
        "support_cost": 0.0,
        "window_count": len(room.windows),
        "install_unit_price": 0.0,
        "installation_cost": 0.0,
        "cost_basis": "改造前现状参照，新增改造造价=0",
    })
    return pd.DataFrame([rec])


def run_no_shading_candidate(
    base_room: RoomModel,
    weather: Optional[WeatherDataset] = None,
    ndiv: int = 20,
    ra_threshold: float = 2.0,
) -> pd.DataFrame:
    """只生成一次L=0无板候选；不绑定θ、h或材料。"""
    weather = weather or default_dataset()
    room = _experiment_room(base_room)
    rec = _run_case(room, weather, ndiv, ra_threshold)
    rec.update({
        "group": "无遮阳候选",
        "param_label": "L=0（无水平/垂直遮阳）",
        "param_value": 0.0,
        "cost": 0.0,
        "tilt_deg": np.nan,
        "L_mm": 0.0,
        "gap_mm": np.nan,
        "material": "无",
        "_plot_color": "#64748b",
        "is_candidate": True,
        "panel_area_m2": 0.0,
        "material_unit_price": 0.0,
        "material_cost": 0.0,
        "support_length_m": 0.0,
        "support_unit_price": 0.0,
        "support_cost": 0.0,
        "window_count": len(room.windows),
        "install_unit_price": 0.0,
        "installation_cost": 0.0,
        "cost_basis": "L=0无板候选，新增工程造价=0",
    })
    return pd.DataFrame([rec])


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
                    "material": "—(玻璃组)", "_plot_color": "#16a34a",
                    "is_candidate": False})
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
    base_room: Optional[RoomModel] = None,
    material_unit_costs: Optional[dict] = None,
    support_cost_per_m: float = 180.0,
    install_cost_per_window: float = 300.0,
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

    工程造价按当前房间实际窗数、窗宽和板长计算：
      板面积 = Σ(每扇窗宽×板长)
      总价 = 板面积×材料综合单价 + 2×Σ窗宽×支撑构件单价 + 窗数×每窗安装费
    材料综合单价、支撑单价和每窗安装费均可由GUI按当地信息价/报价覆盖；结果表同步
    导出面积、单价和分项费用，便于造价复核。cost_fn仍优先，用于命令行自定义。

    L≤0不进入遮阳组合。无水平遮阳状态由run_all_experiments统一生成一次原始模型
    基准，避免不同θ/h把同一个物理状态重复几十次并虚增帕累托点。
    """
    from core.models import get_material
    if tilt_degs is None:
        tilt_degs = [60.0, 70.0, 80.0, 90.0, 100.0, 110.0, 120.0]
    if depth_mms is None:
        depth_mms = [300.0, 600.0, 900.0, 1200.0, 1500.0]
    if gap_mms is None:
        gap_mms = [0.0]
    weather = weather or default_dataset()
    material_unit_costs = material_unit_costs or {}

    # 材料列表 → [(材料名, k_diff, 颜色, 综合单价元/m²)]；None/空 → 单一默认材料
    mat_specs = []
    for m in (materials or []):
        spec = get_material(m)
        if spec is not None:
            default_price = float(spec.get(
                "installed_cost_per_m2", spec.get("cost", 750.0)))
            unit_price = float(material_unit_costs.get(m, default_price))
            mat_specs.append((m, spec["k_diff"], spec["color"], unit_price))
    if not mat_specs:
        mat_specs = [("默认(k_diff=0.30)", 0.30, _C_ACCENT, 750.0)]

    positive_depths = [float(d) for d in depth_mms if float(d) > 0.0]
    geom_combos = [
        (th, d, g)
        for th in tilt_degs for d in positive_depths for g in gap_mms
    ]
    total = len(geom_combos) * len(mat_specs)
    rows = []
    done = 0
    for (tilt, L_mm, gap_mm) in geom_combos:
        # —— 采光只算一次（与材料无关）——
        room = _experiment_room(base_room)
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
            total_window_width_m = sum(w.width for w in room.windows) / 1000.0
            panel_area_m2 = total_window_width_m * (float(L_mm) / 1000.0)
            support_length_m = 2.0 * total_window_width_m
            material_cost = panel_area_m2 * unit_cost
            support_cost = support_length_m * float(support_cost_per_m)
            installation_cost = len(room.windows) * float(install_cost_per_window)
            # 成本：显式cost_fn优先；否则按实际工程量与可编辑综合单价计算。
            if cost_fn is not None:
                cost = float(cost_fn(tilt, L_mm))
                cost_basis = "自定义cost_fn(θ,L)"
            else:
                cost = material_cost + support_cost + installation_cost
                cost_basis = (
                    "Σ窗宽×L×材料综合单价 + 2×Σ窗宽×支撑单价 + 窗数×安装费"
                )
            rec.update({
                "group": "遮阳θ×L扫描",
                "param_label": f"{tag} L={L_mm:.0f} h={gap_mm:.0f} [{mat_name}]",
                "param_value": float(tilt),
                "tilt_deg": float(tilt),
                "L_mm": float(L_mm),
                "gap_mm": float(gap_mm),
                "material": mat_name,
                "_plot_color": color,
                "is_candidate": True,
                "cost": cost,
                "panel_area_m2": panel_area_m2,
                "material_unit_price": unit_cost,
                "material_cost": material_cost,
                "support_length_m": support_length_m,
                "support_unit_price": float(support_cost_per_m),
                "support_cost": support_cost,
                "window_count": len(room.windows),
                "install_unit_price": float(install_cost_per_window),
                "installation_cost": installation_cost,
                "cost_basis": cost_basis,
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
    base_room: Optional[RoomModel] = None,
    include_baseline: bool = True,
    material_unit_costs: Optional[dict] = None,
    support_cost_per_m: float = 180.0,
    install_cost_per_window: float = 300.0,
) -> pd.DataFrame:
    """
    跑基准/遮阳实验并合并为一张表（统一字段，含group列）。
    v2.13.0：遮阳组扩为 θ×L×h×材料 四维网格（材料多选、间隙h可扫描）；
    传入base_room时使用当前rlproj房间/窗/玻璃/热工/位置，并先移除原遮阳；
    include_baseline=True时始终增加一个“当前工程无遮阳”对比点。未传base_room的
    命令行旧用法仍可用include_glass扫描通用玻璃对照组。
    """
    weather = weather or default_dataset()
    parts = []
    include_zero = (
        depth_mms is not None
        and any(float(value) <= 0.0 for value in depth_mms))
    if base_room is not None and include_baseline:
        df_b = run_project_baseline(
            base_room, weather=weather, ndiv=ndiv, ra_threshold=ra_threshold,
            eligible_as_no_shade=include_zero)
        parts.append(df_b)
        if progress_cb:
            progress_cb("原始模型基准", 0, 1)
        if include_zero and room_has_any_shading(base_room):
            parts.append(run_no_shading_candidate(
                base_room, weather=weather, ndiv=ndiv,
                ra_threshold=ra_threshold))
            if progress_cb:
                progress_cb("L=0无遮阳候选", 0, 1)
    elif include_glass:
        df_g = run_glass_experiment(
            specs=glass_specs, weather=weather, ndiv=ndiv, ra_threshold=ra_threshold,
            progress_cb=(lambda i, n: progress_cb("玻璃对照", i, n)) if progress_cb else None)
        parts.append(df_g)
    df_o = run_overhang_experiment(
        tilt_degs=tilt_degs, depth_mms=depth_mms, gap_mms=gap_mms, materials=materials,
        weather=weather, ndiv=ndiv, ra_threshold=ra_threshold,
        progress_cb=(lambda i, n: progress_cb("遮阳θ×L扫描", i, n)) if progress_cb else None,
        base_room=base_room, material_unit_costs=material_unit_costs,
        support_cost_per_m=support_cost_per_m,
        install_cost_per_window=install_cost_per_window)
    if not df_o.empty:
        parts.append(df_o)
    cols = ["group", "param_label", "param_value", "Ra", "U0", "comfort_ratio",
            "comfort_months", "overheat_months", "overheat_degree_months",
            "underheat_degree_months", "thermal_discomfort", "SC_annual",
            "DF_avg", "E_avg", "cost", "tilt_deg", "L_mm", "gap_mm", "material",
            "is_candidate",
            "panel_area_m2", "material_unit_price", "material_cost",
            "support_length_m", "support_unit_price", "support_cost",
            "window_count", "install_unit_price", "installation_cost", "cost_basis"]
    if not parts:
        return pd.DataFrame(columns=cols)
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
_REFERENCE_GROUPS = {"玻璃对照", "原始模型基准"}
_GROUP_MARKER = {
    "玻璃对照": "o", "原始模型基准": "D",
    "无遮阳候选": "^", "遮阳θ×L扫描": "s"}
_GROUP_COLOR  = {
    "玻璃对照": "#16a34a",
    "原始模型基准": "#16a34a",
    "无遮阳候选": "#64748b",
    "遮阳θ×L扫描": _C_ACCENT,
}

# 统一点大小（v2.13.0：取消按成本缩放的气泡，全部点等大，且比原来缩小约一半；
# 取消白色描边，避免点密集时白边糊成一片）。
_MARK_2D = 30.0
_MARK_3D = 22.0
_C_RECOMMEND = "#f59e0b"


def _color_subgroups(df: pd.DataFrame):
    """
    按"组别→材料"拆成用于着色/描点的子组，产出 (group, key, marker, sub_df)。
      · 玻璃/原始模型基准：整组一个子组。
      · 遮阳组：按 material 再分，key=材料名（仅用于稳定着色，不再分别求帕累托）。
    颜色统一取子组内 _plot_color 列首值。
    """
    for grp, gdf in df.groupby("group"):
        mk = _GROUP_MARKER.get(grp, "s")
        if grp == "无遮阳候选":
            yield grp, "L=0 无遮阳", mk, gdf
        elif grp not in _REFERENCE_GROUPS and "material" in gdf.columns:
            for _mat, mm in gdf.groupby("material"):
                yield grp, str(mm["material"].iloc[0]), mk, mm
        else:
            yield grp, grp, mk, gdf


def _shading_candidates(
    df: pd.DataFrame,
    u0_col: str = "U0",
    u0_min: float = 0.0,
) -> pd.DataFrame:
    """取参与最优方案筛选的合规候选；is_candidate是唯一资格标记。"""
    d = df
    if "is_candidate" in d.columns:
        d = d[d["is_candidate"].fillna(False).astype(bool)]
    elif "group" in d.columns:
        d = d[~d["group"].isin(_REFERENCE_GROUPS)]
    if u0_col in d.columns and u0_min is not None:
        d = d[d[u0_col] >= u0_min]
    return d


def global_pareto_front(
    df: pd.DataFrame,
    thermal_col: str = "thermal_discomfort",
    maximize_thermal: bool = False,
    u0_col: str = "U0",
    u0_min: float = 0.0,
) -> pd.DataFrame:
    """
    所有材料×几何共同竞争的三目标帕累托集合。
    目标固定为Ra↑、热轴按maximize_thermal、cost↓；原始/玻璃基准不参与。
    """
    d = _shading_candidates(df, u0_col=u0_col, u0_min=u0_min)
    needed = {"Ra", thermal_col, "cost"}
    if d.empty or not needed.issubset(d.columns):
        return d.iloc[0:0]
    return pareto_front_nd(
        d, ["Ra", thermal_col, "cost"],
        maximize=[True, maximize_thermal, False],
        u0_col=u0_col, u0_min=u0_min,
    )


def _benefit(series: pd.Series, maximize: bool) -> pd.Series:
    """将指标线性归一为0~1优度；1=最好，0=最差。常量列视为全部同优。"""
    values = series.astype(float)
    lo, hi = float(values.min()), float(values.max())
    if hi - lo <= 1e-12:
        return pd.Series(1.0, index=series.index)
    raw = (values - lo) / (hi - lo)
    return raw if maximize else 1.0 - raw


def balanced_compromise(
    df: pd.DataFrame,
    thermal_col: str = "thermal_discomfort",
    maximize_thermal: bool = False,
    u0_col: str = "U0",
    u0_min: float = 0.0,
    weights=(1.0, 1.0, 1.0),
) -> pd.DataFrame:
    """
    从合规遮阳方案中选取距离理想点(1,1,1)最近的等权/加权折中方案。
    距离相同时依次偏好 Ra更高、热轴更优、成本更低，保证结果稳定。
    """
    d = _shading_candidates(df, u0_col=u0_col, u0_min=u0_min).copy()
    needed = {"Ra", thermal_col, "cost"}
    if d.empty or not needed.issubset(d.columns):
        return d.iloc[0:0]
    w = np.asarray(weights, dtype=float)
    if w.shape != (3,) or not np.all(np.isfinite(w)) or float(w.sum()) <= 0:
        w = np.ones(3, dtype=float)
    w = w / w.sum()
    bx = _benefit(d["Ra"], True)
    by = _benefit(d[thermal_col], maximize_thermal)
    bz = _benefit(d["cost"], False)
    d["_ideal_distance"] = np.sqrt(
        w[0] * (1.0 - bx) ** 2
        + w[1] * (1.0 - by) ** 2
        + w[2] * (1.0 - bz) ** 2
    )
    thermal_ascending = not maximize_thermal
    d = d.sort_values(
        ["_ideal_distance", "Ra", thermal_col, "cost"],
        ascending=[True, False, thermal_ascending, True],
        kind="mergesort",
    )
    return d.iloc[:1]


def annotate_global_selection(
    df: pd.DataFrame,
    thermal_col: str = "thermal_discomfort",
    maximize_thermal: bool = False,
    u0_col: str = "U0",
    u0_min: float = 0.0,
) -> pd.DataFrame:
    """为CSV等下游结果增加全局帕累托/均衡推荐布尔标记。"""
    out = df.copy()
    pf = global_pareto_front(
        out, thermal_col=thermal_col, maximize_thermal=maximize_thermal,
        u0_col=u0_col, u0_min=u0_min)
    rec = balanced_compromise(
        out, thermal_col=thermal_col, maximize_thermal=maximize_thermal,
        u0_col=u0_col, u0_min=u0_min)
    out["global_pareto"] = out.index.isin(pf.index)
    out["balanced_recommended"] = out.index.isin(rec.index)
    return out


def global_selection_summary(
    df: pd.DataFrame,
    thermal_col: str = "thermal_discomfort",
    maximize_thermal: bool = False,
    thermal_label: str = "热不舒适度",
    u0_col: str = "U0",
    u0_min: float = 0.0,
) -> str:
    """
    根据本次实际结果生成全局筛选规则、入选材料分布及未入选原因。

    这里不会给每种材料预留名额：全部材料和几何进入同一个候选池。若某种材料在
    相同几何下同时更贵且热指标不占优，它可以被另一种材料整体支配，最终自然不会
    出现在全局帕累托集合中。
    """
    if df is None or df.empty:
        return "运行实验后，将在这里说明全局筛选标准、材料入选分布和推荐方案。"

    all_shading = df
    if "is_candidate" in all_shading.columns:
        all_shading = all_shading[
            all_shading["is_candidate"].fillna(False).astype(bool)]
    elif "group" in all_shading.columns:
        all_shading = all_shading[~all_shading["group"].isin(_REFERENCE_GROUPS)]
    candidates = _shading_candidates(df, u0_col=u0_col, u0_min=u0_min)

    thermal_rule = f"{thermal_label}{'不低' if maximize_thermal else '不高'}"
    u0_rule = ""
    if u0_col in df.columns and u0_min is not None:
        u0_rule = f"先保留 {u0_col}≥{float(u0_min):g} 的方案；"
    rule = (
        "图示说明：每张2D图及3D对应投影面的红圈/虚线，按该面两个指标单独"
        "判断二维帕累托；3D空中点云红圈才表示三个指标共同判断的全局帕累托。"
        "全局筛选标准：全部材料×倾角θ×板长L×间隙h进入同一候选池；"
        f"{u0_rule}若另一方案同时满足 Ra不低、{thermal_rule}、成本不高，"
        "且至少一项严格更好，则本方案被支配并淘汰。原始模型基准仅作改造前参照。"
    )
    if "application_scope" in df.columns:
        scopes = [
            str(value) for value in df["application_scope"].dropna().unique()
            if str(value).strip()
        ]
        if scopes:
            rule += f"本次遮阳应用范围：{'；'.join(scopes)}。"

    needed = {"Ra", thermal_col, "cost"}
    if candidates.empty or not needed.issubset(candidates.columns):
        return (
            f"{rule}\n本次共有{len(all_shading)}个遮阳方案，但没有满足筛选条件的"
            "合规方案，因而没有全局帕累托点。"
        )

    pf = global_pareto_front(
        df, thermal_col=thermal_col, maximize_thermal=maximize_thermal,
        u0_col=u0_col, u0_min=u0_min)
    rec = balanced_compromise(
        df, thermal_col=thermal_col, maximize_thermal=maximize_thermal,
        u0_col=u0_col, u0_min=u0_min)

    if "material" in pf.columns and not pf.empty:
        counts = pf["material"].astype(str).value_counts(sort=False)
        distribution = "、".join(f"{name} {int(count)}个" for name, count in counts.items())
    else:
        distribution = "无"
    result = (
        f"本次结果：{len(all_shading)}个遮阳方案中有{len(candidates)}个合规，"
        f"得到{len(pf)}个全局帕累托方案；入选材料分布：{distribution}。"
    )

    reasons = []
    if "material" in candidates.columns:
        selected_materials = set(pf["material"].astype(str)) if not pf.empty else set()
        geometry_cols = [
            col for col in ("tilt_deg", "L_mm", "gap_mm")
            if col in candidates.columns
        ]
        eps = 1e-10

        def dominates(other: pd.Series, row: pd.Series) -> bool:
            ra_ok = float(other["Ra"]) >= float(row["Ra"]) - eps
            if maximize_thermal:
                thermal_ok = float(other[thermal_col]) >= float(row[thermal_col]) - eps
                thermal_strict = float(other[thermal_col]) > float(row[thermal_col]) + eps
            else:
                thermal_ok = float(other[thermal_col]) <= float(row[thermal_col]) + eps
                thermal_strict = float(other[thermal_col]) < float(row[thermal_col]) - eps
            cost_ok = float(other["cost"]) <= float(row["cost"]) + eps
            strict = (
                float(other["Ra"]) > float(row["Ra"]) + eps
                or thermal_strict
                or float(other["cost"]) < float(row["cost"]) - eps
            )
            return ra_ok and thermal_ok and cost_ok and strict

        material_order = list(dict.fromkeys(candidates["material"].astype(str).tolist()))
        for material in material_order:
            if material in selected_materials:
                continue
            material_rows = candidates[candidates["material"].astype(str) == material]
            dominated_same_geometry = 0
            dominator_materials = []
            for _, row in material_rows.iterrows():
                others = candidates[candidates["material"].astype(str) != material]
                for col in geometry_cols:
                    others = others[
                        np.isclose(others[col].astype(float), float(row[col]),
                                   rtol=0.0, atol=eps)
                    ]
                row_dominators = [
                    other for _, other in others.iterrows()
                    if dominates(other, row)
                ]
                if row_dominators:
                    dominated_same_geometry += 1
                    dominator_materials.extend(
                        str(other["material"]) for other in row_dominators
                    )

            if dominated_same_geometry == len(material_rows) and len(material_rows):
                names = "、".join(dict.fromkeys(dominator_materials))
                reasons.append(
                    f"{material}未入选：其{len(material_rows)}个合规方案在相同几何下"
                    f"均被{names}支配（Ra相同，{thermal_rule}且成本不高，至少一项更优）"
                )
            else:
                reasons.append(
                    f"{material}未入选：其合规方案均被其他材料或几何组合在"
                    "采光、热指标、成本三项目标上支配"
                )

    if reasons:
        material_note = (
            "材料说明：" + "；".join(reasons) + "。多种材料参与计算不等于每种材料"
            "都必须入选；只有全局非支配材料才会出现红圈。"
        )
    else:
        material_note = "材料说明：当前参与材料均有方案进入全局帕累托集合。"

    if not rec.empty:
        row = rec.iloc[0]
        rec_name = str(row.get("param_label", row.get("material", "当前方案")))
        recommendation = (
            f"均衡推荐（金星）：{rec_name}。先把Ra、{thermal_label}和成本分别转为"
            "0~1优度，按1:1:1等权，选择距理想点(1,1,1)最近的方案；"
            "它不是按材料轮流推荐。"
        )
    else:
        recommendation = "均衡推荐：没有可参与计算的合规遮阳方案。"

    return "\n".join((rule, result, material_note, recommendation))


def _axis_limits(series: pd.Series, pad_ratio: float = 0.06) -> tuple:
    """
    2D/3D共用的原始指标坐标范围。相同指标在二维图与三维投影面上必须使用同一范围，
    才能保证点位、边界线与相对疏密在两种视图中一一对应。
    """
    values = np.asarray(series, dtype=float)
    values = values[np.isfinite(values)]
    if values.size == 0:
        return 0.0, 1.0
    lo, hi = float(values.min()), float(values.max())
    span = hi - lo
    if span <= 1e-12:
        pad = max(abs(lo) * pad_ratio, 1.0)
    else:
        pad = span * pad_ratio
    return lo - pad, hi + pad


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
    show_pareto: bool = True,
    global_thermal_col: str = "thermal_discomfort",
    maximize_global_thermal: bool = False,
) -> str:
    """
    二维投影图：材料只控制颜色；红圈与红色虚线均由当前图的两个指标独立判断，
    表示跨全部材料/几何的二维帕累托点和边界；金星仍是三目标等权均衡推荐。
    原始工程/玻璃只作参照。
    """
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.lines import Line2D

    # 图稍微加宽，给移到图外的图例留位置（不再和数据点/标注重叠）
    fig = Figure(figsize=(10.2, 6), facecolor=_C_BG)
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)
    ax.set_facecolor(_C_BG)

    reference = (
        df["group"].isin(_REFERENCE_GROUPS)
        if "group" in df.columns else pd.Series(False, index=df.index))
    compliant = df[u0_col] >= u0_min if (u0_col in df.columns and u0_min is not None) \
        else pd.Series(True, index=df.index)
    # 改造前基准必须始终可见，即使它本身达不到U₀筛选线；它只作参照，
    # 仍不会进入帕累托或均衡推荐候选池。
    compliant = compliant | reference
    has_color = "_plot_color" in df.columns
    legend_seen = {}

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

    shading = _shading_candidates(df, u0_col=u0_col, u0_min=u0_min)
    recommended = balanced_compromise(
        df, thermal_col=global_thermal_col,
        maximize_thermal=maximize_global_thermal,
        u0_col=u0_col, u0_min=u0_min)

    pf2 = shading.iloc[0:0]
    if show_pareto and not shading.empty:
        pf2 = pareto_front(
            shading, x=x, y=y, maximize_x=maximize_x, maximize_y=maximize_y,
            u0_col=u0_col, u0_min=u0_min)
        if not pf2.empty:
            pf2 = pf2.sort_values(x)
            ax.plot(pf2[x], pf2[y], "--", color=_C_PARETO, lw=1.25,
                    alpha=0.72, zorder=4)
            ax.scatter(
                pf2[x], pf2[y], s=_MARK_2D * 2.5,
                marker="o", facecolors="none", edgecolors=_C_PARETO,
                linewidths=1.35, zorder=5)

    if not recommended.empty:
        r = recommended.iloc[0]
        ax.scatter([r[x]], [r[y]], s=_MARK_2D * 4.0, marker="*",
                   c=_C_RECOMMEND, edgecolors="#92400e", linewidths=0.8, zorder=7)
        if "param_label" in recommended.columns:
            ax.annotate(
                f"均衡推荐：{r['param_label']}",
                (r[x], r[y]), xytext=(7, 8), textcoords="offset points",
                fontsize=7.2, color="#92400e", ha="left", zorder=8)

    if reference.any():
        for idx in df.index[reference]:
            ax.annotate(
                "改造前基准", (df.at[idx, x], df.at[idx, y]),
                xytext=(6, -11), textcoords="offset points",
                fontsize=7.0, color="#166534", ha="left", zorder=8)

    ax.set_xlabel(x_label, color=_C_TEXT, fontsize=11)
    ax.set_ylabel(y_label, color=_C_TEXT, fontsize=11)
    # 与3D对应投影面严格共用原始数值范围；“越小越好”的轴反向显示，
    # 使所有图中的更优方向统一朝右/朝上（3D中则朝外）。
    xlo, xhi = _axis_limits(df[x])
    ylo, yhi = _axis_limits(df[y])
    ax.set_xlim((xlo, xhi) if maximize_x else (xhi, xlo))
    ax.set_ylim((ylo, yhi) if maximize_y else (yhi, ylo))
    ax.set_title(title, color=_C_TEXT, fontsize=12, fontweight="bold", pad=10)
    ax.grid(True, color=_C_GRID, lw=0.6)
    for sp in ax.spines.values():
        sp.set_color(_C_BORDER); sp.set_linewidth(0.7)
    ax.tick_params(colors=_C_SEC, labelsize=9)
    # 图例固定放到图外右侧（不用 loc="best" 自动找位置）——数据点+新增的逐点
    # 标注布满整个绘图区后，"best"经常会跟某个点/文字撞在一起，移到图外彻底避免。
    handles, labels = ax.get_legend_handles_labels()
    if show_pareto and not pf2.empty:
        handles.append(Line2D([0], [0], marker="o", linestyle="none", markersize=9,
                              markerfacecolor="none", markeredgecolor=_C_PARETO,
                              markeredgewidth=1.4, label="当前二维帕累托点"))
        handles.append(Line2D([0], [0], linestyle="--", color=_C_PARETO,
                              linewidth=1.2, label="当前二维帕累托边界"))
    if not recommended.empty:
        handles.append(Line2D([0], [0], marker="*", linestyle="none", markersize=10,
                              markerfacecolor=_C_RECOMMEND, markeredgecolor="#92400e",
                              label="均衡推荐方案"))
    ax.legend(handles=handles, fontsize=8.5, loc="upper left", bbox_to_anchor=(1.01, 1.0),
              labelcolor=_C_TEXT, facecolor="#ffffff", edgecolor=_C_BORDER,
              framealpha=0.95, borderaxespad=0.0)
    fig.tight_layout()
    fig.savefig(out_path, dpi=dpi, facecolor=_C_BG, bbox_inches="tight")
    return out_path


# ── 3D 全局帕累托点云 ───────────────────────────────────────────────────────
_3D_VIEW_ELEV, _3D_VIEW_AZIM = 22.0, 42.0
_3D_FOCAL_LENGTH = 0.92


def build_pareto3d_figure(
    df: pd.DataFrame,
    u0_col: str = "U0",
    u0_min: float = 0.0,
    thermal_col: str = "thermal_discomfort",
    maximize_thermal: bool = False,
    thermal_label: str = "热舒适优度",
    show_pareto: bool = True,
):
    """
    采用原始指标值构建透视3D图，返回(fig, ax)：
      X=Ra原值、Y=所选热指标原值、Z=成本原值。
      热轴（当越小越好时）与成本轴反向显示，使低热不舒适度、低成本位于外侧；
      三个投影面固定在各目标的较差边界并交于空间参考点O。O只表示三面交点，
      不表示数值必须为(0,0,0)。三面坐标方向与对应的三张2D图完全一致。
      三个投影面的红圈分别是对应两个指标的二维帕累托点；空中真实点云的红圈
      才是跨材料/几何的全局三目标帕累托；金星是均衡推荐。
    """
    from matplotlib.figure import Figure
    from matplotlib.lines import Line2D
    from matplotlib.ticker import MaxNLocator
    from mpl_toolkits.mplot3d import Axes3D  # noqa: F401  注册 3d 投影，勿删

    d = df.copy()
    if thermal_col not in d.columns:
        thermal_col = "thermal_discomfort"
    has_color = "_plot_color" in d.columns

    reference = (
        d["group"].isin(_REFERENCE_GROUPS)
        if "group" in d.columns else pd.Series(False, index=d.index))
    compliant = d[u0_col] >= u0_min if (u0_col in d.columns and u0_min is not None) \
        else pd.Series(True, index=d.index)
    compliant = compliant | reference
    comp_df = d[compliant]

    pf_global = global_pareto_front(
        d, thermal_col=thermal_col, maximize_thermal=maximize_thermal,
        u0_col=u0_col, u0_min=u0_min)
    recommended = balanced_compromise(
        d, thermal_col=thermal_col, maximize_thermal=maximize_thermal,
        u0_col=u0_col, u0_min=u0_min)
    pf_idx = set(pf_global.index)

    fig = Figure(figsize=(7.2, 6.4), facecolor=_C_BG)
    ax = fig.add_subplot(111, projection="3d")
    ax.set_facecolor(_C_BG)

    xmin, xmax = _axis_limits(d["Ra"])
    ymin, ymax = _axis_limits(d[thermal_col])
    zmin, zmax = _axis_limits(d["cost"])
    # O位于三个目标的较差参考角：低Ra、高热不舒适度（若该热轴越小越好）、高成本。
    # 若用户选择“舒适月数占比”等越大越好的热轴，则热投影面改放在其低值边界。
    y_plane = ymin if maximize_thermal else ymax
    z_plane = zmax

    # —— 空中真实点云 ——
    for grp, key, mk, sub in _color_subgroups(comp_df):
        col = sub["_plot_color"].iloc[0] if has_color else _C_ACCENT
        non_pf = sub[~sub.index.isin(pf_idx)]
        pf_pts = sub[sub.index.isin(pf_idx)]
        if len(non_pf):
            ax.scatter(non_pf["Ra"], non_pf[thermal_col], non_pf["cost"],
                       s=_MARK_3D, marker=mk, c=col, alpha=0.72,
                       edgecolors="none", depthshade=False)
        if len(pf_pts):
            ax.scatter(pf_pts["Ra"], pf_pts[thermal_col], pf_pts["cost"],
                       s=_MARK_3D, marker=mk, c=col, alpha=0.82,
                       edgecolors="none", depthshade=False)
    ncomp = d[~compliant]
    if len(ncomp):
        ax.scatter(ncomp["Ra"], ncomp[thermal_col], ncomp["cost"],
                   s=_MARK_3D * 0.7, marker="x", c=_C_NONCOMP, alpha=0.45)

    # —— 三面网格投影：常量轴固定在对应目标的较差边界，交点为O ——
    for grp, key, mk, sub in _color_subgroups(comp_df):
        col = sub["_plot_color"].iloc[0] if has_color else _C_ACCENT
        ax.scatter(sub["Ra"], sub[thermal_col], np.full(len(sub), z_plane),
                   s=8, c=col, marker=mk,
                   alpha=0.18, edgecolors="none", depthshade=False)
        ax.scatter(sub["Ra"], np.full(len(sub), y_plane), sub["cost"],
                   s=8, c=col, marker=mk,
                   alpha=0.18, edgecolors="none", depthshade=False)
        ax.scatter(np.full(len(sub), xmin), sub[thermal_col], sub["cost"],
                   s=8, c=col, marker=mk,
                   alpha=0.18, edgecolors="none", depthshade=False)

    shading = _shading_candidates(d, u0_col=u0_col, u0_min=u0_min)
    has_projection_front = False
    if show_pareto and not shading.empty:
        # 三个二维前沿只贴在对应坐标面上。
        fronts = [
            (pareto_front(shading, "Ra", thermal_col, True, maximize_thermal,
                          u0_col, u0_min), "Ra", thermal_col, "xy"),
            (pareto_front(shading, "Ra", "cost", True, False,
                          u0_col, u0_min), "Ra", "cost", "xz"),
            (pareto_front(shading, thermal_col, "cost", maximize_thermal, False,
                          u0_col, u0_min), thermal_col, "cost", "yz"),
        ]
        for front, ac, bc, plane in fronts:
            if front.empty:
                continue
            has_projection_front = True
            front = d.loc[front.index].sort_values(ac)
            ring2d_kw = dict(
                s=27, marker="o", facecolors="none",
                edgecolors=_C_PARETO, linewidths=0.8,
                alpha=0.72, depthshade=False)
            if plane == "xy":
                ax.plot(front[ac], front[bc], np.full(len(front), z_plane), "--",
                        color=_C_PARETO, lw=1.0, alpha=0.65)
                ax.scatter(
                    front[ac], front[bc], np.full(len(front), z_plane),
                    **ring2d_kw)
            elif plane == "xz":
                ax.plot(front[ac], np.full(len(front), y_plane), front[bc], "--",
                        color=_C_PARETO, lw=1.0, alpha=0.65)
                ax.scatter(
                    front[ac], np.full(len(front), y_plane), front[bc],
                    **ring2d_kw)
            else:
                ax.plot(np.full(len(front), xmin), front[ac], front[bc], "--",
                        color=_C_PARETO, lw=1.0, alpha=0.65)
                ax.scatter(
                    np.full(len(front), xmin), front[ac], front[bc],
                    **ring2d_kw)

        # 三目标全局帕累托只圈空中的真实点，不投影到三个二维面。
        if not pf_global.empty:
            p = d.loc[pf_global.index]
            ring_kw = dict(s=46, marker="o", facecolors="none",
                           edgecolors=_C_PARETO, linewidths=1.25,
                           depthshade=False)
            ax.scatter(p["Ra"], p[thermal_col], p["cost"], **ring_kw)

    # 均衡推荐始终显示，独立于帕累托红圈/虚线开关。
    if not recommended.empty:
        r = d.loc[recommended.index[0]]
        star_kw = dict(s=105, marker="*", c=_C_RECOMMEND,
                       edgecolors="#92400e", linewidths=0.8, depthshade=False)
        ax.scatter([r["Ra"]], [r[thermal_col]], [r["cost"]], **star_kw)
        proj_star = dict(star_kw); proj_star.update(s=58, alpha=0.68)
        ax.scatter([r["Ra"]], [r[thermal_col]], [z_plane], **proj_star)
        ax.scatter([r["Ra"]], [y_plane], [r["cost"]], **proj_star)
        ax.scatter([xmin], [r[thermal_col]], [r["cost"]], **proj_star)

    ax.set_xlim(xmin, xmax)
    ax.set_ylim((ymin, ymax) if maximize_thermal else (ymax, ymin))
    ax.set_zlim(zmax, zmin)
    thermal_arrow = "↑" if maximize_thermal else "↓"
    ax.set_xlabel("采光达标面积比 Ra ↑", color=_C_TEXT, fontsize=9, labelpad=8)
    ax.set_ylabel(f"{thermal_label} {thermal_arrow}", color=_C_TEXT, fontsize=9, labelpad=8)
    ax.set_zlabel("工程造价估算（元） ↓", color=_C_TEXT, fontsize=9, labelpad=8)
    ax.tick_params(colors=_C_SEC, labelsize=7)
    for axis in (ax.xaxis, ax.yaxis, ax.zaxis):
        axis.set_major_locator(MaxNLocator(nbins=5))
    ax.set_title(
        "遮阳方案三目标全局权衡\n"
        "O=较差参考角；低热不舒适度、低成本向外，更优方案靠近最外侧",
        fontsize=9.2, color=_C_TEXT, pad=4)

    # 三个半透明网格面与投影共面；显式透视投影。
    pane_rgba = (0.96, 0.97, 0.99, 0.58)
    ax.xaxis.set_pane_color(pane_rgba)
    ax.yaxis.set_pane_color(pane_rgba)
    ax.zaxis.set_pane_color(pane_rgba)
    ax.grid(True, color=_C_GRID, lw=0.5)
    ax.set_proj_type("persp", focal_length=_3D_FOCAL_LENGTH)

    handles = [
        Line2D([0], [0], marker="s", linestyle="none", markersize=6,
               markerfacecolor=_C_SEC, markeredgecolor="none", label="普通方案"),
    ]
    if show_pareto and not pf_global.empty:
        handles.append(
            Line2D([0], [0], marker="o", linestyle="none", markersize=8,
                   markerfacecolor="none", markeredgecolor=_C_PARETO,
                   markeredgewidth=1.3, label="空中：三目标全局帕累托"))
    if show_pareto and has_projection_front:
        handles.extend([
            Line2D([0], [0], marker="o", linestyle="none", markersize=7,
                   markerfacecolor="none", markeredgecolor=_C_PARETO,
                   markeredgewidth=1.0, label="面上：各二维帕累托点"),
            Line2D([0], [0], linestyle="--", color=_C_PARETO,
                   linewidth=1.0, label="面上：各二维帕累托边界"),
        ])
    if not recommended.empty:
        handles.append(Line2D([0], [0], marker="*", linestyle="none", markersize=10,
                              markerfacecolor=_C_RECOMMEND, markeredgecolor="#92400e",
                              label="均衡推荐方案"))
    if reference.any():
        handles.append(Line2D(
            [0], [0], marker="D", linestyle="none", markersize=6,
            markerfacecolor="#22c55e", markeredgecolor="#166534",
            label="改造前基准"))
    ax.legend(handles=handles, fontsize=7.3, loc="upper left",
              labelcolor=_C_TEXT, facecolor="#ffffff", edgecolor=_C_BORDER,
              framealpha=0.90)
    ax.view_init(elev=_3D_VIEW_ELEV, azim=_3D_VIEW_AZIM)
    fig.tight_layout()
    return fig, ax
