from pathlib import Path
import unittest

import numpy as np

from core.complex_daylight import compute_complex_daylight
from core.pareto_engine import backend_name, non_dominated_indices
from core.research_scene import space_scene
from io_utils.project_io import load_building_project


FIXTURE = Path(__file__).parent / "fixtures" / "长沙幼儿园_回归基准.rlproj"


def _building():
    building, weather, _active_id, error = load_building_project(str(FIXTURE))
    assert not error
    return building, weather


class KindergartenRegressionTests(unittest.TestCase):
    def test_room2_fast_screening_has_physical_diffuse_light(self):
        building, weather = _building()
        room = building.get_space("bp_space_2")
        result = compute_complex_daylight(
            room,
            E_out=weather.annual_avg,
            grid_mm=150,
            ndiv=40,
        )
        values = result.E_lux[result.valid_mask]
        self.assertEqual(values.size, 2370)
        self.assertTrue(np.isfinite(values).all())
        self.assertGreater(result.E_min, 1.0)
        self.assertLess(result.U0, 0.15)
        self.assertGreater(result.U0, 0.08)
        self.assertGreater(result.E_avg, 340.0)
        self.assertLess(result.E_avg, 360.0)

    def test_corner_to_corner_windows_export_to_research_scene(self):
        building, _weather = _building()
        scene = space_scene(building.get_space("bp_space_2"))
        glass = [surface for surface in scene.surfaces if surface.kind == "glass"]
        self.assertEqual(len(glass), 3)
        self.assertTrue(scene.fingerprint())

    def test_pymoo_front_preserves_equivalent_schemes(self):
        values = np.asarray([
            [0.60, 50.0, 500.0],
            [0.60, 50.0, 500.0],
            [0.70, 49.0, 520.0],
            [0.50, 55.0, 550.0],
        ])
        front = non_dominated_indices(
            values,
            maximize=[True, False, False],
            tolerances=[1e-4, 0.01, 0.5],
        )
        self.assertEqual(set(front.tolist()), {0, 1, 2})
        self.assertEqual(backend_name(), "pymoo.NonDominatedSorting")
