"""
core/decision.py — 成本约束与光热择优判定  v4.4.3

本模块只做可追溯的判定，不负责光热计算。成本阈值由论文第二章/实验侧栏预先
给定，程序不会根据结果自动改变阈值。有限离散搜索没有可行解时，结论统一为
“在本研究参数域内未发现可行解”，并报告主要违反约束及归一化残余差距。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Mapping

import numpy as np
import pandas as pd


INTERVENTION_ORDER = {
    "L0基准": 0,
    "L1单构件": 1,
    "L2组合构件": 2,
    "L3主动补偿": 3,
}


@dataclass(frozen=True)
class ConstraintSet:
    """论文预注册的筛选约束。

    数值为 0 时关闭相应约束。默认值是用于程序试跑的“研究阈值”，不是规范
    自动赋值；正式论文应在实验前依据所采用的指标口径和规范/文献重新确认。
    """

    daylight_score_min: float = 0.80
    ra_min: float = 0.75
    u0_min: float = 0.0
    thermal_discomfort_max: float = 60.0
    annual_total_cost_max: float = 0.0

    @classmethod
    def from_value(cls, value=None) -> "ConstraintSet":
        if isinstance(value, cls):
            return value
        if isinstance(value, Mapping):
            fields = cls.__dataclass_fields__
            return cls(**{
                key: float(raw)
                for key, raw in value.items()
                if key in fields and raw is not None
            })
        return cls()

    def to_dict(self) -> dict:
        return asdict(self)


_CONSTRAINT_META = (
    ("daylight_score", "daylight_score_min", "min", "连续采光达标度Cd"),
    ("Ra", "ra_min", "min", "采光达标面积比Ra"),
    ("U0", "u0_min", "min", "采光均匀度U0"),
    (
        "thermal_discomfort",
        "thermal_discomfort_max",
        "max",
        "热不舒适度",
    ),
    ("annual_total_cost", "annual_total_cost_max", "max", "年综合费用"),
)


def _normalised_violation(value: float, limit: float, direction: str) -> float:
    if not np.isfinite(value):
        return 1.0
    denominator = max(abs(float(limit)), 1e-9)
    if direction == "min":
        return max(0.0, (float(limit) - value) / denominator)
    return max(0.0, (value - float(limit)) / denominator)


def annotate_feasibility(
    dataframe: pd.DataFrame,
    constraints: ConstraintSet | Mapping | None = None,
) -> pd.DataFrame:
    """为每个方案增加逐约束违反度、可行性和主导瓶颈列。"""
    spec = ConstraintSet.from_value(constraints)
    out = dataframe.copy()
    if out.empty:
        for column in (
            "feasible",
            "constraint_max_violation",
            "constraint_total_violation",
            "constraint_violated_count",
            "violated_constraints",
            "dominant_constraint",
            "feasibility_status",
        ):
            out[column] = pd.Series(dtype=object)
        return out

    active = []
    for metric, limit_field, direction, label in _CONSTRAINT_META:
        limit = float(getattr(spec, limit_field))
        # 所有约束均以0表示“关闭”；正式阈值均应为正数。
        if limit <= 0.0:
            continue
        active.append((metric, limit, direction, label))
        worst_metric = {
            "daylight_score": "worst_room_Cd",
            "Ra": "worst_room_Ra",
            "U0": "worst_room_U0",
            "thermal_discomfort": "worst_room_thermal",
        }.get(metric)
        source_metric = (
            worst_metric if worst_metric and worst_metric in out.columns else metric
        )
        if source_metric not in out.columns:
            out[source_metric] = np.nan
        out[f"violation_{metric}"] = [
            _normalised_violation(float(value), limit, direction)
            for value in pd.to_numeric(out[source_metric], errors="coerce")
        ]
        out[f"constraint_source_{metric}"] = source_metric

    if not active:
        out["constraint_max_violation"] = 0.0
        out["constraint_total_violation"] = 0.0
        out["constraint_violated_count"] = 0
        out["violated_constraints"] = ""
        out["dominant_constraint"] = "无（未启用硬约束）"
        out["feasible"] = True
        out["feasibility_status"] = "可行（未启用硬约束）"
    else:
        violation_columns = [f"violation_{metric}" for metric, *_ in active]
        violations = out[violation_columns].astype(float)
        out["constraint_max_violation"] = violations.max(axis=1)
        out["constraint_total_violation"] = violations.sum(axis=1)
        out["constraint_violated_count"] = (violations > 1e-12).sum(axis=1)
        label_by_column = {
            f"violation_{metric}": label
            for metric, _limit, _direction, label in active
        }
        out["violated_constraints"] = [
            "、".join(
                label_by_column[column]
                for column in violation_columns
                if float(row[column]) > 1e-12
            )
            for _index, row in violations.iterrows()
        ]
        out["dominant_constraint"] = [
            (
                "无"
                if float(row.max()) <= 1e-12
                else label_by_column[str(row.idxmax())]
            )
            for _index, row in violations.iterrows()
        ]
        out["feasible"] = out["constraint_max_violation"] <= 1e-12
        out["feasibility_status"] = np.where(
            out["feasible"],
            "满足全部硬约束",
            "未满足：" + out["violated_constraints"].astype(str),
        )

    if "intervention_level" not in out.columns:
        out["intervention_level"] = np.where(
            out.get("group", pd.Series(index=out.index, dtype=str)).astype(str)
            == "原始模型基准",
            "L0基准",
            "L1单构件",
        )
    out["intervention_level_order"] = [
        INTERVENTION_ORDER.get(str(value), 99)
        for value in out["intervention_level"]
    ]
    # L3 compensation demand is reported as a gap, not silently treated as a
    # simulated active-system solution.  The monthly screen cannot size HVAC
    # equipment or prove occupied-hour compliance; those tasks remain in the
    # independent hourly verification stage.
    cd_limit = float(spec.daylight_score_min)
    thermal_limit = float(spec.thermal_discomfort_max)
    cd_values = pd.to_numeric(out.get(
        "worst_room_Cd", out.get("daylight_score", np.nan)
    ), errors="coerce")
    thermal_values = pd.to_numeric(
        out.get("worst_room_thermal", out.get("thermal_discomfort", np.nan)),
        errors="coerce",
    )
    out["active_lighting_gap_Cd"] = (
        np.maximum(cd_limit - cd_values, 0.0) if cd_limit > 0.0 else 0.0
    )
    out["active_hvac_gap_degree_month"] = (
        np.maximum(thermal_values - thermal_limit, 0.0)
        if thermal_limit > 0.0 else 0.0
    )
    out["active_compensation_note"] = [
        (
            "无需主动补偿（被动方案满足当前硬约束）"
            if bool(feasible)
            else "需人工照明和/或空调补偿；本列仅报告残余差距，设备容量与逐时能耗须另行复核"
        )
        for feasible in out["feasible"]
    ]
    return out


def _criterion_column(criterion: str) -> str:
    return {
        "构件数量": "component_count",
        "初投资": "construction_cost",
        "年综合费用": "annual_total_cost",
        "生命周期成本": "lifecycle_total_cost",
        "component_count": "component_count",
        "construction_cost": "construction_cost",
        "annual_total_cost": "annual_total_cost",
        "lifecycle_total_cost": "lifecycle_total_cost",
    }.get(str(criterion), "construction_cost")


def annotate_minimum_intervention(
    dataframe: pd.DataFrame,
    constraints: ConstraintSet | Mapping | None = None,
    criterion: str = "初投资",
) -> pd.DataFrame:
    """先执行成本/硬约束筛选，再按光热表现标出唯一决策推荐。

    有可行解时，成本只作为门槛，不再进入推荐得分；在满足门槛的方案中按
    连续采光达标度更高、热不舒适度更低的等权理想点距离选优。
    如果至少一个真实改造方案的年运行费用低于改造前基准，L0基准只作为参照，
    不再参与最终推荐。程序只在这些节能改造方案中继续执行成本门槛和光热择优。
    这里比较的是 annual_operating_cost，不含改造建设费用。无完全可行解时，
    仍优先在成本门槛内选光热表现最好者，并明确标成参考方案而非达标最优。
    """
    spec = ConstraintSet.from_value(constraints)
    out = annotate_feasibility(dataframe, spec)
    out["decision_recommended"] = False
    out["decision_kind"] = ""
    out["decision_reason"] = ""
    out["minimum_feasible_level"] = ""
    out["required_next_level"] = ""
    out["recommendation_pool"] = "全部方案（含改造前基准）"
    out["cost_requirement_met"] = True
    out["light_thermal_distance"] = np.nan
    out["light_thermal_rank"] = np.nan
    if out.empty:
        return out

    # 把“运行期是否节省”与“改造投资是否划算”拆开记录。前者决定基准是否仍能
    # 进入推荐池；后者继续通过初投资、年综合费用、生命周期成本和回收期展示，
    # 不再让零投资的L0基准天然压过所有真实改造方案。
    out["baseline_annual_operating_cost"] = np.nan
    out["annual_operating_saving"] = np.nan
    out["operating_saving_rate"] = np.nan
    out["simple_payback_years"] = np.nan
    out["operating_cost_lower_than_baseline"] = False
    selection_pool = out.copy()
    baseline_rows = out[
        pd.to_numeric(out["intervention_level_order"], errors="coerce") == 0
    ]
    operating_cost = (
        pd.to_numeric(out["annual_operating_cost"], errors="coerce")
        if "annual_operating_cost" in out.columns
        else pd.Series(np.nan, index=out.index, dtype=float)
    )
    baseline_costs = (
        pd.to_numeric(
            baseline_rows["annual_operating_cost"], errors="coerce"
        ).dropna()
        if "annual_operating_cost" in baseline_rows.columns
        else pd.Series(dtype=float)
    )
    energy_saving_pool_active = False
    baseline_cost = np.nan
    if not baseline_costs.empty:
        baseline_cost = float(baseline_costs.iloc[0])
        saving = baseline_cost - operating_cost
        out["baseline_annual_operating_cost"] = baseline_cost
        out["annual_operating_saving"] = saving
        if baseline_cost > 1e-9:
            out["operating_saving_rate"] = saving / baseline_cost
        construction_cost = (
            pd.to_numeric(out["construction_cost"], errors="coerce")
            if "construction_cost" in out.columns
            else pd.Series(np.nan, index=out.index, dtype=float)
        )
        positive_saving = saving > 1e-9
        out.loc[positive_saving, "simple_payback_years"] = (
            construction_cost[positive_saving] / saving[positive_saving]
        )
        retrofit = (
            pd.to_numeric(out["intervention_level_order"], errors="coerce") > 0
        )
        if "is_candidate" in out.columns:
            raw_candidate = out["is_candidate"]
            if raw_candidate.dtype == bool:
                retrofit &= raw_candidate
            else:
                retrofit &= raw_candidate.astype(str).str.lower().isin(
                    {"true", "1", "yes", "是"}
                )
        saving_retrofit = retrofit & positive_saving
        out["operating_cost_lower_than_baseline"] = saving_retrofit
        if bool(saving_retrofit.any()):
            energy_saving_pool_active = True
            selection_pool = out[saving_retrofit].copy()
            out["recommendation_pool"] = (
                "节能改造方案（年运行费用低于改造前基准；L0仅作参照）"
            )

    # 成本只负责形成可行域。0表示未设置成本上限，此时全部方案通过成本门槛。
    cost_limit = float(spec.annual_total_cost_max)
    annual_cost = pd.to_numeric(
        out.get("annual_total_cost", np.nan), errors="coerce"
    )
    if cost_limit > 0.0:
        out["cost_requirement_met"] = annual_cost <= cost_limit + 1e-9
    else:
        out["cost_requirement_met"] = True
    cost_pool = selection_pool[
        out.loc[selection_pool.index, "cost_requirement_met"].astype(bool)
    ].copy()
    fully_feasible = cost_pool[cost_pool["feasible"].astype(bool)].copy()
    no_cost_eligible = False
    if not fully_feasible.empty:
        pool = fully_feasible
        residual_gap = False
    elif not cost_pool.empty:
        pool = cost_pool
        residual_gap = True
    else:
        # 没有方案满足成本门槛时仍给出最接近成本要求的参考方案，但明确标为未达标。
        no_cost_eligible = True
        pool = selection_pool.copy()
        pool["_cost_overrun"] = np.maximum(
            pd.to_numeric(pool.get("annual_total_cost", np.nan), errors="coerce")
            - cost_limit,
            0.0,
        ).fillna(np.inf)
        minimum_overrun = float(pool["_cost_overrun"].min())
        pool = pool[np.isclose(pool["_cost_overrun"], minimum_overrun)]
        residual_gap = True

    light_col = (
        "worst_room_Cd" if "worst_room_Cd" in pool.columns
        else "daylight_score" if "daylight_score" in pool.columns
        else "Ra"
    )
    thermal_col = (
        "worst_room_thermal" if "worst_room_thermal" in pool.columns
        else "thermal_discomfort"
    )

    def benefit(values: pd.Series, maximize: bool) -> pd.Series:
        numeric = pd.to_numeric(values, errors="coerce")
        finite = numeric[np.isfinite(numeric)]
        if finite.empty:
            return pd.Series(0.0, index=values.index)
        lo, hi = float(finite.min()), float(finite.max())
        if hi - lo <= 1e-12:
            return pd.Series(1.0, index=values.index)
        normal = (numeric - lo) / (hi - lo)
        result = normal if maximize else 1.0 - normal
        return result.fillna(0.0)

    light_benefit = benefit(pool[light_col], True)
    thermal_benefit = benefit(pool[thermal_col], False)
    pool["_light_thermal_distance"] = np.sqrt(
        ((1.0 - light_benefit) ** 2 + (1.0 - thermal_benefit) ** 2) / 2.0
    )
    pool["_annual_cost"] = pd.to_numeric(
        pool.get("annual_total_cost", np.nan), errors="coerce"
    ).fillna(np.inf)
    pool = pool.sort_values(
        ["_light_thermal_distance", light_col, thermal_col, "_annual_cost"],
        ascending=[True, False, True, True],
        kind="mergesort",
    )
    for rank, index in enumerate(pool.index, start=1):
        out.at[index, "light_thermal_distance"] = float(
            pool.at[index, "_light_thermal_distance"]
        )
        out.at[index, "light_thermal_rank"] = rank

    chosen_index = pool.index[0]
    chosen_level = str(out.at[chosen_index, "intervention_level"])
    out.loc[:, "minimum_feasible_level"] = chosen_level
    out.at[chosen_index, "decision_recommended"] = True
    if no_cost_eligible:
        chosen_annual_cost = float(annual_cost.loc[chosen_index])
        cost_text = (
            f"没有方案满足年综合费用≤{cost_limit:.2f}元/年的门槛；"
            f"本参考方案费用为{chosen_annual_cost:.2f}元/年"
        )
    elif cost_limit > 0.0:
        cost_text = f"年综合费用满足≤{cost_limit:.2f}元/年"
    else:
        cost_text = "未设置年综合费用上限，全部候选通过成本门槛"
    if not residual_gap:
        out.at[chosen_index, "decision_kind"] = "成本约束内光热最优方案"
        out.at[chosen_index, "decision_reason"] = (
            f"{cost_text}；在满足全部硬约束的方案中，按采光↑与热不舒适度↓"
            "等权理想点距离选择，不再因造价更低而牺牲光热表现。"
        )
    else:
        out.at[chosen_index, "decision_kind"] = (
            "未满足成本要求的光热参考方案"
            if no_cost_eligible else "成本优先的光热最优参考方案"
        )
        out.at[chosen_index, "decision_reason"] = (
            f"{cost_text}；当前没有同时满足全部硬约束的方案，因此选择当前范围内"
            "的光热等权表现最佳参考项，并保留未满足约束提示。"
        )
        out.loc[:, "required_next_level"] = "需复核未满足约束"

    if energy_saving_pool_active:
        chosen_cost = float(operating_cost.loc[chosen_index])
        saving_value = float(out.at[chosen_index, "annual_operating_saving"])
        payback = float(out.at[chosen_index, "simple_payback_years"])
        payback_text = (
            f"，静态回收期约{payback:.1f}年" if np.isfinite(payback) else ""
        )
        out.at[chosen_index, "decision_reason"] += (
            f" 本方案运行费用{chosen_cost:.2f}元/年，较基准节省"
            f"{saving_value:.2f}元/年{payback_text}。"
        )
    return out


def select_combination_seeds(
    dataframe: pd.DataFrame,
    constraints: ConstraintSet | Mapping | None = None,
    per_device: int = 2,
) -> pd.DataFrame:
    """为 L2 组合选择每类单构件残余差距最小的少量种子。"""
    assessed = annotate_feasibility(dataframe, constraints)
    candidates = assessed[
        (assessed["intervention_level"] == "L1单构件")
        & assessed.get("is_candidate", True).astype(bool)
    ].copy()
    if candidates.empty or "device_type" not in candidates.columns:
        return candidates.iloc[0:0]
    parts = []
    for _device, group in candidates.groupby("device_type", sort=False):
        group = group.sort_values(
            [
                "constraint_max_violation",
                "constraint_total_violation",
                "annual_total_cost",
            ],
            kind="mergesort",
        )
        parts.append(group.head(max(1, int(per_device))))
    return pd.concat(parts, axis=0) if parts else candidates.iloc[0:0]


def feasibility_summary(
    dataframe: pd.DataFrame,
    constraints: ConstraintSet | Mapping | None = None,
    criterion: str = "初投资",
) -> str:
    """生成可直接显示和导出的中文判定摘要。"""
    assessed = annotate_minimum_intervention(dataframe, constraints, criterion)
    if assessed.empty:
        return "没有候选方案，无法判定可行域。"
    spec = ConstraintSet.from_value(constraints)
    active_text = []
    for metric, field, direction, label in _CONSTRAINT_META:
        limit = float(getattr(spec, field))
        if limit <= 0.0:
            continue
        symbol = "≥" if direction == "min" else "≤"
        active_text.append(f"{label}{symbol}{limit:g}")
    recommended = assessed[assessed["decision_recommended"].astype(bool)]
    row = recommended.iloc[0] if not recommended.empty else assessed.iloc[0]
    saving_pool_active = bool(
        assessed["operating_cost_lower_than_baseline"].fillna(False).any()
    )
    eligible = (
        assessed[
            assessed["operating_cost_lower_than_baseline"].fillna(False)
        ]
        if saving_pool_active
        else assessed
    )
    feasible = eligible[eligible["feasible"].astype(bool)]
    if feasible.empty:
        verdict = (
            f"在当前推荐池的{len(eligible)}个方案中未发现同时满足全部硬约束的"
            f"可行解；成本门槛内的光热最优参考方案仍违反“{row.get('violated_constraints', '—')}”，"
            f"主导瓶颈为“{row.get('dominant_constraint', '—')}”，最大归一化违反度="
            f"{float(row.get('constraint_max_violation', 0.0)):.3f}。"
        )
    else:
        verdict = (
            f"发现{len(feasible)}个可行方案；成本作为进入门槛，不参与推荐得分，"
            "在满足要求的方案中按采光更高、热不舒适度更低的等权光热表现选优。"
        )
    return (
        "硬约束（实验前锁定）："
        + ("；".join(active_text) if active_text else "未启用")
        + "。\n"
        + verdict
        + f"\n推荐池：{row.get('recommendation_pool', '全部方案（含改造前基准）')}。"
        + f"\n推荐/参考方案：{row.get('param_label', row.get('solution_id', '—'))}。"
    )
