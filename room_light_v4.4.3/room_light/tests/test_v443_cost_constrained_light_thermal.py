import unittest

import pandas as pd

from core.decision import annotate_minimum_intervention


class CostConstrainedLightThermalDecisionTests(unittest.TestCase):
    @staticmethod
    def _frame():
        return pd.DataFrame([
            {
                "solution_id": "S001", "group": "遮阳扫描",
                "intervention_level": "L1单构件", "is_candidate": True,
                "daylight_score": 0.82, "Ra": 0.78, "U0": 0.72,
                "thermal_discomfort": 56.0, "annual_total_cost": 300.0,
                "annual_operating_cost": 240.0, "construction_cost": 1200.0,
            },
            {
                "solution_id": "S002", "group": "遮阳扫描",
                "intervention_level": "L2组合构件", "is_candidate": True,
                "daylight_score": 0.91, "Ra": 0.88, "U0": 0.80,
                "thermal_discomfort": 43.0, "annual_total_cost": 480.0,
                "annual_operating_cost": 220.0, "construction_cost": 2600.0,
            },
            {
                "solution_id": "S003", "group": "遮阳扫描",
                "intervention_level": "L1单构件", "is_candidate": True,
                "daylight_score": 0.97, "Ra": 0.94, "U0": 0.86,
                "thermal_discomfort": 34.0, "annual_total_cost": 650.0,
                "annual_operating_cost": 200.0, "construction_cost": 3200.0,
            },
        ])

    @staticmethod
    def _constraints(cost_limit):
        return {
            "daylight_score_min": 0.80,
            "ra_min": 0.75,
            "u0_min": 0.70,
            "thermal_discomfort_max": 60.0,
            "annual_total_cost_max": cost_limit,
        }

    def test_better_light_thermal_wins_even_if_not_cheapest(self):
        assessed = annotate_minimum_intervention(
            self._frame(), self._constraints(500.0), criterion="construction_cost"
        )
        chosen = assessed[assessed["decision_recommended"]].iloc[0]
        self.assertEqual(chosen["solution_id"], "S002")
        self.assertEqual(chosen["decision_kind"], "成本约束内光热最优方案")
        self.assertEqual(int(chosen["light_thermal_rank"]), 1)

    def test_best_but_over_budget_solution_is_excluded(self):
        assessed = annotate_minimum_intervention(
            self._frame(), self._constraints(500.0)
        )
        over_budget = assessed[assessed["solution_id"] == "S003"].iloc[0]
        self.assertFalse(bool(over_budget["cost_requirement_met"]))
        self.assertFalse(bool(over_budget["decision_recommended"]))

    def test_zero_cost_limit_disables_only_the_cost_gate(self):
        assessed = annotate_minimum_intervention(
            self._frame(), self._constraints(0.0)
        )
        chosen = assessed[assessed["decision_recommended"]].iloc[0]
        self.assertEqual(chosen["solution_id"], "S003")

    def test_no_budget_compliant_solution_is_only_a_reference(self):
        assessed = annotate_minimum_intervention(
            self._frame(), self._constraints(200.0)
        )
        chosen = assessed[assessed["decision_recommended"]].iloc[0]
        self.assertEqual(chosen["solution_id"], "S001")
        self.assertEqual(chosen["decision_kind"], "未满足成本要求的光热参考方案")
        self.assertFalse(bool(chosen["cost_requirement_met"]))


if __name__ == "__main__":
    unittest.main()
