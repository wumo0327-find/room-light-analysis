from __future__ import annotations

import unittest

from core.complex_daylight import compute_complex_daylight
from core.complex_experiments import run_multi_space_experiments
from core.complex_models import (
    BoundaryLoop, BuildingModel, Point2D, SpaceModel, StoreyModel,
    WallOpening, WallSegment,
)
from core.complex_thermal import compute_complex_thermal
from core.decision import annotate_minimum_intervention
from core.models import ShadingDevice
from io_utils.weather_data import default_dataset
from io_utils.project_io import building_from_dict, building_to_dict


def sample_space() -> SpaceModel:
    points = [
        Point2D(0, 0), Point2D(6000, 0),
        Point2D(6000, 6000), Point2D(0, 6000),
    ]
    walls = [
        WallSegment(points[index], points[(index + 1) % 4])
        for index in range(4)
    ]
    walls[0].openings.append(WallOpening(
        offset_mm=1500, width_mm=3000, sill_height_mm=600,
        height_mm=2400, visible_transmittance=0.71,
    ))
    return SpaceModel(
        name="v4.4测试活动室", height_mm=3600,
        boundary_loops=[BoundaryLoop(segments=walls)],
    )


class DevicePhysicsTests(unittest.TestCase):
    def setUp(self):
        self.weather = default_dataset()

    def _metrics(self, shading: ShadingDevice):
        space = sample_space()
        space.shading = shading
        daylight = compute_complex_daylight(
            space, grid_mm=1000, ndiv=6, store_components=True,
        )
        thermal = compute_complex_thermal(
            space, self.weather.monthly_ghi, self.weather.monthly_temp,
        )
        return daylight, thermal

    def test_louver_parameters_change_light_and_heat(self):
        base_d, base_t = self._metrics(ShadingDevice())
        louver_d, louver_t = self._metrics(ShadingDevice(
            type="louver", components=["louver"],
            louver_width_mm=120, louver_spacing_mm=100,
            louver_angle_deg=45, louver_position="exterior",
        ))
        self.assertNotAlmostEqual(base_d.daylight_score, louver_d.daylight_score)
        self.assertLess(louver_t.SC_effective, base_t.SC_effective)

    def test_light_shelf_changes_spatial_daylight(self):
        base_d, _ = self._metrics(ShadingDevice())
        shelf_d, _ = self._metrics(ShadingDevice(
            type="light_shelf", components=["light_shelf"],
            light_shelf_inside_depth_mm=400,
            light_shelf_outside_depth_mm=600,
            light_shelf_upper_window_ratio=0.35,
            light_shelf_reflect=0.80,
        ))
        self.assertNotAlmostEqual(base_d.DF_avg, shelf_d.DF_avg)
        self.assertNotAlmostEqual(base_d.U0, shelf_d.U0)
        tilted_d, tilted_t = self._metrics(ShadingDevice(
            type="light_shelf", components=["light_shelf"],
            light_shelf_inside_depth_mm=400,
            light_shelf_outside_depth_mm=600,
            light_shelf_tilt_deg=10,
            light_shelf_upper_window_ratio=0.35,
            light_shelf_reflect=0.80,
        ))
        _horizontal_d, horizontal_t = self._metrics(ShadingDevice(
            type="light_shelf", components=["light_shelf"],
            light_shelf_inside_depth_mm=400,
            light_shelf_outside_depth_mm=600,
            light_shelf_tilt_deg=0,
            light_shelf_upper_window_ratio=0.35,
            light_shelf_reflect=0.80,
        ))
        self.assertNotAlmostEqual(shelf_d.DF_avg, tilted_d.DF_avg)
        self.assertNotAlmostEqual(horizontal_t.SC_effective, tilted_t.SC_effective)

    def test_l2_combination_and_l3_residual_are_reported(self):
        result = run_multi_space_experiments(
            [sample_space()], weather=self.weather, ndiv=4, grid_mm=1200,
            depth_mms=[0], tilt_degs=[90], gap_mms=[0],
            materials=["普通/氧化铝板"],
            device_types=["louver", "light_shelf"],
            louver_params={
                "angles": [45], "widths": [100], "spacings": [100],
                "positions": ["exterior"], "controls": ["fixed"],
            },
            light_shelf_params={
                "inside_depths": [300], "outside_depths": [600],
                "tilts": [0], "upper_ratios": [0.35],
            },
            constraints={
                "daylight_score_min": 1.0, "ra_min": 1.0,
                "thermal_discomfort_max": 0.01,
            },
            enable_combinations=True, combination_seed_count=1,
        )
        self.assertIn("L2组合构件", set(result["intervention_level"]))
        assessed = annotate_minimum_intervention(result, {
            "daylight_score_min": 1.0, "ra_min": 1.0,
            "thermal_discomfort_max": 0.01,
        })
        self.assertEqual(
            set(assessed["required_next_level"]), {"L3主动补偿"},
        )
        self.assertTrue(assessed["decision_recommended"].any())

    def test_new_shading_fields_round_trip_in_complex_project(self):
        space = sample_space()
        space.shading = ShadingDevice(
            type="combined", components=["louver", "light_shelf"],
            target_window_ids=[space.wall_segments()[0].openings[0].id],
            louver_position="between", louver_control_mode="seasonal",
            light_shelf_inside_depth_mm=350,
            light_shelf_outside_depth_mm=650,
            light_shelf_tilt_deg=10,
            light_shelf_upper_window_ratio=0.40,
        )
        building = BuildingModel(storeys=[StoreyModel(spaces=[space])])
        restored = building_from_dict(building_to_dict(building))
        shading = restored.spaces()[0].shading
        self.assertEqual(shading.components, ["louver", "light_shelf"])
        self.assertEqual(shading.louver_control_mode, "seasonal")
        self.assertEqual(shading.light_shelf_outside_depth_mm, 650)


if __name__ == "__main__":
    unittest.main()
