"""Automatic multi-fidelity experiment pipeline for V5.3.

All stages use Radiance and EnergyPlus. Only Radiance numerical precision is
changed; annual EnergyPlus results have a category-specific cache and are
therefore calculated once per physical candidate.
"""
from __future__ import annotations

from copy import deepcopy
import math
import numpy as np
import pandas as pd

from core.engine_service import configure
from core.complex_experiments import (
    _candidate_identity, reevaluate_candidate_rows,
    run_multi_space_experiments,
)
from core.experiments import annotate_global_selection


MARKER_COLUMNS = (
    "decision_recommended", "balanced_recommended", "global_pareto",
    "pareto_light_thermal_2d", "pareto_light_cost_2d",
    "pareto_thermal_cost_2d",
)


def _bool_series(frame, column):
    if column not in frame:
        return pd.Series(False, index=frame.index)
    raw = frame[column]
    return raw if raw.dtype == bool else raw.astype(str).str.lower().isin(
        {"true", "1", "yes", "是"}
    )


def _annotate(frame, params):
    return annotate_global_selection(
        frame,
        thermal_col=params.get("y", "thermal_discomfort"),
        maximize_thermal=params.get("maximize_y", False),
        u0_min=params.get("u0_min", 0.0),
    )


def _shortlist(frame, params, *, fraction, minimum, maximum):
    """Retain references, endpoints, Pareto neighbourhood and ranked schemes."""
    data = _annotate(frame, params)
    level = pd.to_numeric(
        data.get("intervention_level_order", 1), errors="coerce"
    ).fillna(1)
    references = data[level <= 0]
    pool = data[level > 0]
    if "is_candidate" in pool:
        pool = pool[_bool_series(pool, "is_candidate")]
    if pool.empty:
        return references.copy()
    target = min(len(pool), maximum, max(minimum, int(math.ceil(len(pool)*fraction))))
    chosen = []

    def take(indices):
        for index in indices:
            if index not in chosen and len(chosen) < target:
                chosen.append(index)

    take(pool.index[_bool_series(pool, "decision_recommended")])
    take(pool.index[_bool_series(pool, "balanced_recommended")])
    light_col = str(data.get("light_objective", pd.Series("daylight_score", index=data.index)).iloc[0])
    thermal_col = params.get("y", "thermal_discomfort")
    for flag in (
        "global_pareto", "pareto_light_thermal_2d",
        "pareto_light_cost_2d", "pareto_thermal_cost_2d",
    ):
        edge = pool[_bool_series(pool, flag)]
        if edge.empty:
            continue
        for column, ascending in ((light_col, False), (thermal_col, True), ("cost", True)):
            if column in edge:
                take(edge.sort_values(column, ascending=ascending, kind="mergesort").index[:2])
    ranked = pool.copy()
    rank = pd.to_numeric(ranked.get("recommendation_rank", np.nan), errors="coerce")
    ranked["_stage_rank"] = rank.fillna(np.inf)
    take(ranked.sort_values("_stage_rank", kind="mergesort").index)
    selected = data.loc[chosen]
    return pd.concat([references, selected], ignore_index=True, sort=False)


def _metric_delta(previous, current):
    def relative(column):
        a = float(previous.get(column, np.nan)); b = float(current.get(column, np.nan))
        if not np.isfinite(a) or not np.isfinite(b):
            return np.inf
        return abs(b-a)/max(abs(a), 1e-9)
    def absolute(column):
        a = float(previous.get(column, np.nan)); b = float(current.get(column, np.nan))
        return abs(b-a) if np.isfinite(a) and np.isfinite(b) else np.inf
    return {
        "convergence_Eavg_relative": relative("E_avg"),
        "convergence_Ra_absolute": absolute("Ra"),
        "convergence_U0_absolute": absolute("U0"),
        "convergence_energy_relative": relative("annual_operating_kwh"),
    }


def _mark_convergence(current, previous):
    older = {_candidate_identity(row): row for _i, row in previous.iterrows()}
    rows = []
    for _i, row in current.iterrows():
        record = row.to_dict()
        before = older.get(_candidate_identity(row))
        values = _metric_delta(before, row) if before is not None else {
            "convergence_Eavg_relative": np.inf,
            "convergence_Ra_absolute": np.inf,
            "convergence_U0_absolute": np.inf,
            "convergence_energy_relative": np.inf,
        }
        record.update(values)
        record["precision_verified"] = bool(
            values["convergence_Eavg_relative"] <= .02
            and values["convergence_Ra_absolute"] <= .02
            and values["convergence_U0_absolute"] <= .01
            and values["convergence_energy_relative"] <= .03
        )
        rows.append(record)
    return pd.DataFrame(rows)


class StagedExperimentPipeline:
    STAGE_COUNT = 4

    def __init__(self, spaces, weather, engine_settings, params, *,
                 target_window_keys, latitude_deg, north_angle_deg):
        self.spaces = deepcopy(spaces)
        self.weather = deepcopy(weather)
        self.base = deepcopy(engine_settings)
        self.params = dict(params)
        self.targets = list(target_window_keys) if target_window_keys is not None else None
        self.latitude = latitude_deg
        self.north = north_angle_deg
        self.coarse = self.medium = self.high = self.final = None
        self.display = None

    def _settings(self, grid, bounces, samples):
        value = deepcopy(self.base)
        value.grid_mm = float(grid)
        value.ambient_bounces = int(bounces)
        value.ambient_divisions = int(samples)
        return value

    def _common_kwargs(self):
        keys = (
            "tilt_degs", "depth_mms", "gap_mms", "materials",
            "material_unit_costs", "support_cost_per_m",
            "install_cost_per_window", "operation_profile", "device_types",
            "louver_params", "light_shelf_params", "constraints",
            "minimum_criterion", "enable_combinations", "combination_seed_count",
        )
        return {key: self.params[key] for key in keys if key in self.params}

    def stage1(self, progress):
        setting = self._settings(500, 3, 512)
        configure(setting)
        progress(0, 1, "第一阶段：正在建立全量粗筛方案")
        def callback(tag, current, total):
            progress(current, total, f"第一阶段：全量粗筛｜{tag}")
        self.coarse = run_multi_space_experiments(
            self.spaces, weather=self.weather, grid_mm=setting.grid_mm,
            target_window_keys=self.targets, latitude_deg=self.latitude,
            north_angle_deg=self.north, progress_cb=callback,
            **self._common_kwargs(),
        )
        self.coarse["calculation_stage"] = "第一阶段｜500mm / ab3 / ad512"
        self.coarse["precision_verified"] = False

    def stage2(self, progress):
        candidates = _shortlist(
            self.coarse, self.params, fraction=.20, minimum=12, maximum=80,
        )
        setting = self._settings(300, 4, 2048)
        configure(setting)
        progress(0, max(1, len(candidates)), "第二阶段：正在建立帕累托附近复筛队列")
        self.medium = reevaluate_candidate_rows(
            self.spaces, candidates, weather=self.weather,
            grid_mm=setting.grid_mm, latitude_deg=self.latitude,
            north_angle_deg=self.north, target_window_keys=self.targets,
            operation_profile=self.params.get("operation_profile"),
            constraints=self.params.get("constraints"),
            minimum_criterion=self.params.get("minimum_criterion", "light_thermal"),
            source_table=self.coarse,
            progress_cb=lambda i, n: progress(i, n, "第二阶段：帕累托附近复筛"),
        )
        self.medium["calculation_stage"] = "第二阶段｜300mm / ab4 / ad2048"
        self.medium["precision_verified"] = False

    def stage3(self, progress):
        candidates = _shortlist(
            self.medium, self.params, fraction=.25, minimum=5, maximum=15,
        )
        setting = self._settings(
            min(float(self.base.grid_mm), 150.0),
            max(int(self.base.ambient_bounces), 6),
            max(int(self.base.ambient_divisions), 8192),
        )
        configure(setting)
        progress(0, max(1, len(candidates)), "第三阶段：正在建立关键方案复算队列")
        calculated = reevaluate_candidate_rows(
            self.spaces, candidates, weather=self.weather,
            grid_mm=setting.grid_mm, latitude_deg=self.latitude,
            north_angle_deg=self.north, target_window_keys=self.targets,
            operation_profile=self.params.get("operation_profile"),
            constraints=self.params.get("constraints"),
            minimum_criterion=self.params.get("minimum_criterion", "light_thermal"),
            source_table=self.coarse,
            progress_cb=lambda i, n: progress(i, n, "第三阶段：关键方案高精度复算"),
        )
        self.high = _mark_convergence(calculated, self.medium)
        self.high["calculation_stage"] = (
            f"第三阶段｜{setting.grid_mm:g}mm / ab{setting.ambient_bounces} / "
            f"ad{setting.ambient_divisions}"
        )

    def stage4(self, progress):
        failures = self.high[~_bool_series(self.high, "precision_verified")]
        final = self.high.copy()
        if not failures.empty:
            setting = self._settings(75, 7, 16384)
            configure(setting)
            progress(0, max(1, len(failures)), "第四阶段：正在建立未收敛方案加密队列")
            ultra = reevaluate_candidate_rows(
                self.spaces, failures, weather=self.weather,
                grid_mm=setting.grid_mm, latitude_deg=self.latitude,
                north_angle_deg=self.north, target_window_keys=self.targets,
                operation_profile=self.params.get("operation_profile"),
                constraints=self.params.get("constraints"),
                minimum_criterion=self.params.get("minimum_criterion", "light_thermal"),
                source_table=self.coarse,
                progress_cb=lambda i, n: progress(i, n, "第四阶段：未收敛方案加密复核"),
            )
            ultra = _mark_convergence(ultra, self.high)
            ultra["calculation_stage"] = "自适应加密｜75mm / ab7 / ad16384"
            replacements = {_candidate_identity(row): row for _i, row in ultra.iterrows()}
            final = pd.DataFrame([
                replacements.get(_candidate_identity(row), row).to_dict()
                for _i, row in final.iterrows()
            ])
        else:
            progress(1, 1, "第四阶段：收敛检查已通过，无需加密")

        verified = final[_bool_series(final, "precision_verified")]
        if verified.empty:
            raise ValueError(
                "关键方案在75mm加密后仍未通过收敛阈值，不能生成高精度推荐。"
                "请缩小参数域或检查几何/材料输入。"
            )
        decided = _annotate(verified, self.params)
        decision_map = {_candidate_identity(row): row for _i, row in decided.iterrows()}
        final_rows = []
        for _i, row in final.iterrows():
            record = row.to_dict()
            for column in MARKER_COLUMNS:
                record[column] = False
            chosen = decision_map.get(_candidate_identity(row))
            if chosen is not None:
                for column in MARKER_COLUMNS + (
                    "recommendation_rank", "solution_id", "decision_kind",
                    "decision_reason", "light_thermal_rank",
                ):
                    if column in chosen:
                        record[column] = chosen[column]
            final_rows.append(record)
        self.final = pd.DataFrame(final_rows)

        ordered = []
        best = {}
        for table in (self.coarse, self.medium, self.final):
            for _index, row in table.iterrows():
                identity = _candidate_identity(row)
                if identity not in best:
                    ordered.append(identity)
                best[identity] = row.to_dict()
        display = pd.DataFrame([best[key] for key in ordered])
        for column in MARKER_COLUMNS:
            display[column] = False
        final_map = {_candidate_identity(row): row for _i, row in self.final.iterrows()}
        for index, row in display.iterrows():
            source = final_map.get(_candidate_identity(row))
            if source is None:
                continue
            for column in MARKER_COLUMNS + (
                "recommendation_rank", "solution_id", "decision_kind",
                "decision_reason", "light_thermal_rank",
            ):
                if column in source:
                    display.at[index, column] = source[column]
        display["software_version"] = "5.3.0"
        display["numerical_stage"] = display["calculation_stage"]
        display["engine"] = "Radiance + EnergyPlus"
        display["staged_screening"] = True
        self.display = display
        configure(self.base)
        return display

    def settings_for_final_row(self, row):
        stage = str(row.get("calculation_stage", ""))
        if stage.startswith("自适应加密"):
            return self._settings(75, 7, 16384)
        return self._settings(
            min(float(self.base.grid_mm), 150.0),
            max(int(self.base.ambient_bounces), 6),
            max(int(self.base.ambient_divisions), 8192),
        )
