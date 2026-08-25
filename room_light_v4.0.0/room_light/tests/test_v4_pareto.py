import unittest

import pandas as pd

from core.experiments import (
    annotate_global_selection,
    global_pareto_front,
    pareto_front,
)


def _frame(rows):
    return pd.DataFrame([
        {
            "group": "遮阳候选",
            "is_candidate": True,
            "U0": 0.80,
            "tilt_deg": 90.0,
            "L_mm": 600.0,
            "gap_mm": 0.0,
            **row,
        }
        for row in rows
    ])


class V4ParetoTests(unittest.TestCase):
    def test_equivalent_materials_are_not_silently_dropped_in_2d(self):
        data = _frame([
            {"material": "材料A", "Ra": 0.32, "thermal_discomfort": 50.0, "cost": 100.0},
            {"material": "材料B", "Ra": 0.32, "thermal_discomfort": 50.0, "cost": 100.0},
        ])
        front = pareto_front(
            data,
            "Ra",
            "thermal_discomfort",
            maximize_x=True,
            maximize_y=False,
            u0_min=0.0,
        )
        self.assertEqual(set(front["material"]), {"材料A", "材料B"})

    def test_each_2d_front_uses_only_the_two_visible_metrics(self):
        data = _frame([
            {"material": "便宜材料", "Ra": 0.32, "thermal_discomfort": 50.0, "cost": 100.0},
            {"material": "昂贵材料", "Ra": 0.32, "thermal_discomfort": 50.0, "cost": 500.0},
        ])
        light_thermal = pareto_front(
            data, "Ra", "thermal_discomfort", True, False, u0_min=0.0
        )
        global_front = global_pareto_front(data, u0_min=0.0)
        # Cost is deliberately ignored on the light-thermal projection, but
        # it correctly breaks the tie in the global three-objective result.
        self.assertEqual(len(light_thermal), 2)
        self.assertEqual(global_front["material"].tolist(), ["便宜材料"])

    def test_a_point_better_on_both_visible_axes_dominates(self):
        data = _frame([
            {"material": "A", "Ra": 0.30, "thermal_discomfort": 50.0, "cost": 100.0},
            {"material": "B", "Ra": 0.35, "thermal_discomfort": 49.0, "cost": 200.0},
        ])
        front = pareto_front(
            data, "Ra", "thermal_discomfort", True, False, u0_min=0.0
        )
        self.assertEqual(front["material"].tolist(), ["B"])

    def test_annotation_is_traceable_and_keeps_separate_front_flags(self):
        data = _frame([
            {"material": "A", "Ra": 0.30, "thermal_discomfort": 49.0, "cost": 300.0},
            {"material": "B", "Ra": 0.35, "thermal_discomfort": 51.0, "cost": 100.0},
            {"material": "C", "Ra": 0.34, "thermal_discomfort": 49.5, "cost": 180.0},
        ])
        result = annotate_global_selection(data, u0_min=0.0)
        self.assertEqual(result["solution_id"].tolist(), ["S0001", "S0002", "S0003"])
        self.assertIn("pareto_light_thermal_2d", result.columns)
        self.assertIn("pareto_light_cost_2d", result.columns)
        self.assertIn("pareto_thermal_cost_2d", result.columns)
        self.assertIn("global_pareto", result.columns)
        self.assertEqual(int(result["balanced_recommended"].sum()), 1)


if __name__ == "__main__":
    unittest.main()
