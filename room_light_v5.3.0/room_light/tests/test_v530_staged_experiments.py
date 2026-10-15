"""V5.3 automatic multi-fidelity screening regression tests."""
from copy import deepcopy
import os
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from core.engine_service import EngineSettings, _key, configure, settings
from core.experiments import _shading_candidates, build_pareto3d_figure, plot_experiments
from core.research_energy import find_energyplus
from core.staged_experiments import StagedExperimentPipeline
from tests.test_v510_kindergarten_regression import _building


def _table(count=30):
    rows = [{
        "group": "原始模型基准", "device_type": "baseline",
        "param_label": "选中1房间｜改造前基准", "intervention_level_order": 0,
        "intervention_level": "L0基准", "is_candidate": False,
        "daylight_score": .45, "light_objective": "daylight_score",
        "E_avg": 400.0, "Ra": .45, "U0": .55,
        "thermal_discomfort": 70.0, "annual_operating_kwh": 1800.0,
        "cost": 500.0, "feasible": True,
    }]
    for index in range(count):
        rows.append({
            "group": "遮阳θ×L扫描", "device_type": "horizontal_overhang",
            "param_label": f"选中1房间｜方案{index:03d}",
            "intervention_level_order": 1, "intervention_level": "L1单构件",
            "is_candidate": True, "daylight_score": .50 + index / 1000,
            "light_objective": "daylight_score", "E_avg": 500.0 + index,
            "Ra": .55 + index / 1000, "U0": .65,
            "thermal_discomfort": 60.0 - index / 10,
            "annual_operating_kwh": 1500.0 - index,
            "cost": 700.0 + index, "feasible": True,
            "material": "密实混凝土", "L_mm": 600.0,
            "tilt_deg": 90.0, "gap_mm": 0.0,
            "material_unit_price": 230.0, "support_unit_price": 65.0,
            "install_unit_price": 300.0,
        })
    return pd.DataFrame(rows)


def _annotated(frame, *_args, **_kwargs):
    out = frame.copy()
    for column in (
        "decision_recommended", "balanced_recommended", "global_pareto",
        "pareto_light_thermal_2d", "pareto_light_cost_2d",
        "pareto_thermal_cost_2d",
    ):
        out[column] = False
    candidates = out.index[out["is_candidate"].astype(bool)]
    if len(candidates):
        best = candidates[-1]
        out.at[best, "decision_recommended"] = True
        out.at[best, "balanced_recommended"] = True
        out.loc[candidates[-4:], "global_pareto"] = True
        out.loc[candidates[-4:], "pareto_light_thermal_2d"] = True
        out.loc[candidates[-4:], "pareto_light_cost_2d"] = True
        out.loc[candidates[-4:], "pareto_thermal_cost_2d"] = True
        out["recommendation_rank"] = np.nan
        for rank, index in enumerate(reversed(candidates), start=1):
            out.at[index, "recommendation_rank"] = rank
    out["solution_id"] = [f"S{i:04d}" for i in range(len(out))]
    out["decision_kind"] = "测试"
    out["decision_reason"] = "测试"
    out["light_thermal_rank"] = out.get("recommendation_rank", np.nan)
    return out


def _recalculated(_spaces, candidates, *, progress_cb=None, **_kwargs):
    out = candidates.copy()
    precision = settings()
    # Small, deterministic numerical changes allow the high stage to pass the
    # declared convergence thresholds against the medium stage.
    adjustment = {300.0: 1.0, 150.0: 1.005, 75.0: 1.006}.get(
        float(precision.grid_mm), 1.0
    )
    out["E_avg"] = pd.to_numeric(out["E_avg"]) * adjustment
    if progress_cb:
        progress_cb(len(out), len(out))
    return out


class TestStagedExperiments:
    def setup_method(self):
        self.original = settings()

    def teardown_method(self):
        configure(self.original)

    def test_energyplus_key_ignores_radiance_precision(self):
        building, _weather = _building()
        space = building.get_space("bp_space_2")
        epw = next((Path(__file__).resolve().parents[2] / "weather").rglob("*.epw"))
        base = EngineSettings(epw_path=str(epw), grid_mm=500,
                              ambient_bounces=3, ambient_divisions=512)
        configure(base)
        energy_a = _key(space, "energyplus", {"epw_sha256": "same"})
        light_a = _key(space, "radiance", {})
        refined = deepcopy(base)
        refined.grid_mm = 75
        refined.ambient_bounces = 7
        refined.ambient_divisions = 16384
        configure(refined)
        assert energy_a == _key(space, "energyplus", {"epw_sha256": "same"})
        assert light_a != _key(space, "radiance", {})

    def test_pipeline_reduces_candidates_and_only_recommends_verified(self, tmp_path):
        building, weather = _building()
        space = building.get_space("bp_space_2")
        params = {"y": "thermal_discomfort", "maximize_y": False, "u0_min": 0.0}
        pipeline = StagedExperimentPipeline(
            [space], weather, EngineSettings(grid_mm=150, ambient_bounces=6,
                                             ambient_divisions=8192), params,
            target_window_keys=None, latitude_deg=28.2, north_angle_deg=0.0,
        )
        events = []
        progress = lambda i, n, label="": events.append((i, n, label))
        with patch("core.staged_experiments.run_multi_space_experiments",
                   return_value=_table()), \
             patch("core.staged_experiments.reevaluate_candidate_rows",
                   side_effect=_recalculated), \
             patch("core.staged_experiments._annotate", side_effect=_annotated):
            pipeline.stage1(progress)
            pipeline.stage2(progress)
            pipeline.stage3(progress)
            result = pipeline.stage4(progress)

        assert len(pipeline.coarse) == 31
        assert len(pipeline.medium) < len(pipeline.coarse)
        assert len(pipeline.high) <= 16
        assert len(result) == len(pipeline.coarse)
        recommended = result[result["decision_recommended"].astype(bool)]
        assert len(recommended) == 1
        assert bool(recommended.iloc[0]["precision_verified"])
        assert result["staged_screening"].astype(bool).all()
        final_candidates = _shading_candidates(result, u0_min=0.0)
        assert not final_candidates.empty
        assert final_candidates["precision_verified"].astype(bool).all()
        plot_experiments(
            result, str(tmp_path / "staged.png"), x="daylight_score",
            y="thermal_discomfort", maximize_y=False, u0_min=0.0,
        )
        figure, _axis = build_pareto3d_figure(result, u0_min=0.0)
        figure.savefig(tmp_path / "staged_3d.png")
        assert (tmp_path / "staged.png").is_file()
        assert (tmp_path / "staged_3d.png").is_file()
        assert any("第一阶段" in label for _i, _n, label in events)
        assert any("第二阶段" in label for _i, _n, label in events)
        assert any("第三阶段" in label for _i, _n, label in events)

    @staticmethod
    @pytest.mark.skipif(
        os.getenv("ROOMLIGHT_ENGINE_TESTS") != "1",
        reason="real Radiance/EnergyPlus integration is opt-in",
    )
    def test_minimal_real_staged_pipeline():
        building, weather = _building()
        space = building.get_space("bp_space_2")
        root = Path(__file__).resolve().parents[2]
        epw = next((root / "weather").rglob("*.epw"))
        engine = EngineSettings(
            epw_path=str(epw), energyplus_path=str(find_energyplus() or ""),
            cache_directory=str(root / "engine_results"),
            grid_mm=150, ambient_bounces=6, ambient_divisions=8192,
            profile={"building_type": "kindergarten"},
        )
        params = {
            "tilt_degs": [90.0], "depth_mms": [600.0], "gap_mms": [0.0],
            "materials": ["密实混凝土"], "device_types": ["horizontal_overhang"],
            "louver_params": {}, "light_shelf_params": {},
            "constraints": {}, "minimum_criterion": "light_thermal",
            "enable_combinations": False, "combination_seed_count": 2,
            "operation_profile": {"building_type": "kindergarten"},
            "y": "thermal_discomfort", "maximize_y": False, "u0_min": 0.0,
        }
        pipeline = StagedExperimentPipeline(
            [space], weather, engine, params, target_window_keys=None,
            latitude_deg=28.2, north_angle_deg=0.0,
        )
        progress = lambda *_args: True
        pipeline.stage1(progress)
        pipeline.stage2(progress)
        pipeline.stage3(progress)
        result = pipeline.stage4(progress)
        assert not result.empty
        recommended = result[result["decision_recommended"].astype(bool)]
        assert len(recommended) == 1
        assert bool(recommended.iloc[0]["precision_verified"])
