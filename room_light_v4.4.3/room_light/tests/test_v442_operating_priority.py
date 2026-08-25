import unittest

import pandas as pd

from core.decision import annotate_minimum_intervention


class OperatingPriorityDecisionTests(unittest.TestCase):
    def _constraints(self):
        return {
            "daylight_score_min": 0.80,
            "ra_min": 0.75,
            "u0_min": 0.0,
            "thermal_discomfort_max": 60.0,
            "annual_total_cost_max": 0.0,
        }

    def test_energy_saving_retrofit_excludes_baseline_from_recommendation(self):
        frame = pd.DataFrame([
            {
                "solution_id": "B001",
                "group": "原始模型基准",
                "intervention_level": "L0基准",
                "is_candidate": False,
                "daylight_score": 0.70,
                "Ra": 0.70,
                "thermal_discomfort": 55.0,
                "annual_operating_cost": 100.0,
                "construction_cost": 0.0,
                "annual_total_cost": 100.0,
            },
            {
                "solution_id": "S001",
                "group": "遮阳扫描",
                "intervention_level": "L1单构件",
                "is_candidate": True,
                "daylight_score": 0.68,
                "Ra": 0.68,
                "thermal_discomfort": 50.0,
                "annual_operating_cost": 80.0,
                "construction_cost": 200.0,
                "annual_total_cost": 110.0,
            },
        ])
        assessed = annotate_minimum_intervention(
            frame, self._constraints(), criterion="初投资"
        )
        chosen = assessed[assessed["decision_recommended"]].iloc[0]
        self.assertEqual(chosen["solution_id"], "S001")
        self.assertEqual(chosen["decision_kind"], "成本优先的光热最优参考方案")
        self.assertAlmostEqual(chosen["annual_operating_saving"], 20.0)
        self.assertAlmostEqual(chosen["simple_payback_years"], 10.0)
        self.assertFalse(bool(
            assessed.loc[assessed["solution_id"] == "B001",
                         "operating_cost_lower_than_baseline"].iloc[0]
        ))

    def test_better_light_thermal_can_beat_baseline_without_cost_cap(self):
        frame = pd.DataFrame([
            {
                "solution_id": "B001",
                "group": "原始模型基准",
                "intervention_level": "L0基准",
                "is_candidate": False,
                "daylight_score": 0.80,
                "Ra": 0.75,
                "thermal_discomfort": 50.0,
                "annual_operating_cost": 100.0,
                "construction_cost": 0.0,
                "annual_total_cost": 100.0,
            },
            {
                "solution_id": "S001",
                "group": "遮阳扫描",
                "intervention_level": "L1单构件",
                "is_candidate": True,
                "daylight_score": 0.82,
                "Ra": 0.78,
                "thermal_discomfort": 48.0,
                "annual_operating_cost": 105.0,
                "construction_cost": 100.0,
                "annual_total_cost": 110.0,
            },
        ])
        assessed = annotate_minimum_intervention(
            frame, self._constraints(), criterion="初投资"
        )
        chosen = assessed[assessed["decision_recommended"]].iloc[0]
        self.assertEqual(chosen["solution_id"], "S001")
        self.assertEqual(
            chosen["recommendation_pool"], "全部方案（含改造前基准）"
        )


if __name__ == "__main__":
    unittest.main()
