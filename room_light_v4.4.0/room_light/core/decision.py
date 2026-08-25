"""
core/decision.py — 可行域、残余差距与最小干预判定  v4.4.0

本模块只做可追溯的判定，不负责光热计算。阈值由论文第二章/实验侧栏预先
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
    """按 L0→L1→L2→L3 停止规则标出唯一决策推荐。

    有可行解时，只在最早出现可行解的层级内按用户预注册的“最小”口径选取；
    无可行解时，选归一化最大违反度最小的方案，并明确标成残余差距最小，绝不
    将它冒充达标最优。
    """
    out = annotate_feasibility(dataframe, constraints)
    out["decision_recommended"] = False
    out["decision_kind"] = ""
    out["decision_reason"] = ""
    out["minimum_feasible_level"] = ""
    out["required_next_level"] = ""
    if out.empty:
        return out

    feasible = out[out["feasible"].astype(bool)].copy()
    criterion_column = _criterion_column(criterion)
    if criterion_column not in out.columns:
        out[criterion_column] = np.nan

    if not feasible.empty:
        first_order = int(feasible["intervention_level_order"].min())
        pool = feasible[
            feasible["intervention_level_order"] == first_order
        ].copy()
        level = str(pool.iloc[0]["intervention_level"])
        pool["_criterion"] = pd.to_numeric(
            pool[criterion_column], errors="coerce"
        ).fillna(np.inf)
        pool["_annual"] = pd.to_numeric(
            pool.get("annual_total_cost", np.nan), errors="coerce"
        ).fillna(np.inf)
        pool = pool.sort_values(
            ["_criterion", "_annual", "constraint_total_violation"],
            kind="mergesort",
        )
        chosen_index = pool.index[0]
        out.loc[:, "minimum_feasible_level"] = level
        out.at[chosen_index, "decision_recommended"] = True
        out.at[chosen_index, "decision_kind"] = "最低层级可行方案"
        out.at[chosen_index, "decision_reason"] = (
            f"{level}已出现满足全部硬约束的方案；按“{criterion}”口径在该层级内最小。"
        )
    else:
        pool = out.copy()
        pool["_criterion"] = pd.to_numeric(
            pool[criterion_column], errors="coerce"
        ).fillna(np.inf)
        pool = pool.sort_values(
            [
                "constraint_max_violation",
                "constraint_total_violation",
                "intervention_level_order",
                "_criterion",
            ],
            kind="mergesort",
        )
        chosen_index = pool.index[0]
        out.at[chosen_index, "decision_recommended"] = True
        out.at[chosen_index, "decision_kind"] = "最小残余差距"
        out.at[chosen_index, "decision_reason"] = (
            "在本研究参数域内未发现满足全部硬约束的方案；该方案的最大归一化"
            "违反度最小，仍需进入下一干预层级或采用主动补偿。"
        )
        out.loc[:, "required_next_level"] = "L3主动补偿"
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
    feasible = assessed[assessed["feasible"].astype(bool)]
    recommended = assessed[assessed["decision_recommended"].astype(bool)]
    row = recommended.iloc[0] if not recommended.empty else assessed.iloc[0]
    if feasible.empty:
        verdict = (
            f"在当前参数域的{len(assessed)}个方案中未发现同时满足全部硬约束的"
            f"可行解；最小残余差距方案仍违反“{row.get('violated_constraints', '—')}”，"
            f"主导瓶颈为“{row.get('dominant_constraint', '—')}”，最大归一化违反度="
            f"{float(row.get('constraint_max_violation', 0.0)):.3f}。"
        )
    else:
        first_level = str(row.get("minimum_feasible_level", ""))
        verdict = (
            f"发现{len(feasible)}个可行方案；按L0→L1→L2→L3停止规则，最早可行"
            f"层级为{first_level}，并按“{criterion}”选出该层级推荐方案。"
        )
    return (
        "硬约束（实验前锁定）："
        + ("；".join(active_text) if active_text else "未启用")
        + "。\n"
        + verdict
        + f"\n推荐/残余差距方案：{row.get('param_label', row.get('solution_id', '—'))}。"
    )
