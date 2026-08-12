from pathlib import Path
import tempfile
import unittest

import numpy as np

from core.complex_models import (
    BoundaryLoop,
    BuildingModel,
    Point2D,
    SpaceModel,
    StoreyModel,
    WallOpening,
    WallSegment,
)
from core.legacy_adapter import building_from_room
from core.complex_daylight import (
    _ds_point as complex_ds_point,
    compute_complex_daylight,
)
from core.daylight import compute as compute_legacy_daylight
from core.complex_thermal import (
    compute_complex_thermal,
    orientation_factors_for_azimuth,
    wall_azimuth_deg,
)
from core.thermal import compute_thermal as compute_legacy_thermal
from core.models import (
    DEFAULT_SELECTED_MATERIALS,
    RoomModel,
    ShadingDevice,
)
from core.complex_experiments import (
    build_solution_space,
    run_all_complex_experiments,
)
from core.space_geometry import (
    has_geometry_errors,
    point_in_space,
    space_floor_area_mm2,
    validate_space,
)
from io_utils.project_io import (
    load_building_project,
    save_building_project,
    save_project,
)


def make_loop(coordinates, kind="outer", boundary_type="exterior"):
    points = [Point2D(float(x), float(y)) for x, y in coordinates]
    segments = [
        WallSegment(
            start=point,
            end=points[(index + 1) % len(points)],
            boundary_type=boundary_type,
            id=f"{kind}_wall_{index}",
        )
        for index, point in enumerate(points)
    ]
    return BoundaryLoop(segments=segments, kind=kind, id=f"{kind}_loop")


class ComplexSpaceGeometryTests(unittest.TestCase):
    def test_legacy_rectangle_conversion_is_valid(self):
        room = RoomModel(length=6000.0, width=4000.0, height=3000.0)
        east_window = room.add_window("east")
        east_window.x = 1000.0
        east_window.width = 1500.0
        east_window.y = 900.0
        east_window.height = 1500.0
        room.shading.overhang_overrides[str(east_window.id)] = {
            "depth_mm": 850.0,
            "gap_mm": 120.0,
            "tilt_deg": 95.0,
        }

        building = building_from_room(room)
        space = building.get_space("legacy_space")
        self.assertIsNotNone(space)
        issues = validate_space(space)
        self.assertFalse(has_geometry_errors(issues), issues)
        self.assertAlmostEqual(space_floor_area_mm2(space), 24_000_000.0)
        self.assertAlmostEqual(space.volume_m3, 72.0)

        east_wall = space.get_wall("legacy_wall_east")
        self.assertIsNotNone(east_wall)
        self.assertEqual(len(east_wall.openings), 1)
        # Old east-wall coordinates run from north to south.  The v3 outer
        # loop runs south to north, so the offset must be reversed.
        self.assertAlmostEqual(east_wall.openings[0].offset_mm, 3500.0)
        shading_key = str(
            east_wall.openings[0].metadata["legacy_window_id"]
        )
        self.assertEqual(
            space.shading.get_overhang_for(shading_key),
            (850.0, 120.0, 95.0),
        )

    def test_l_shaped_space_area_and_point_mask(self):
        loop = make_loop([
            (0, 0),
            (6000, 0),
            (6000, 3000),
            (3000, 3000),
            (3000, 6000),
            (0, 6000),
        ])
        space = SpaceModel(
            name="L形活动室",
            height_mm=3600.0,
            boundary_loops=[loop],
            id="space_l",
        )
        issues = validate_space(space)
        self.assertFalse(has_geometry_errors(issues), issues)
        self.assertAlmostEqual(space_floor_area_mm2(space), 27_000_000.0)
        self.assertTrue(point_in_space(Point2D(1000, 5000), space))
        self.assertTrue(point_in_space(Point2D(5000, 1000), space))
        self.assertFalse(point_in_space(Point2D(5000, 5000), space))

    def test_self_intersection_is_rejected(self):
        space = SpaceModel(
            boundary_loops=[make_loop([
                (0, 0),
                (4000, 4000),
                (0, 4000),
                (4000, 0),
            ])],
            id="space_bow_tie",
        )
        issues = validate_space(space)
        codes = {issue.code for issue in issues}
        self.assertIn("loop_self_intersection", codes)
        self.assertTrue(has_geometry_errors(issues))

    def test_opening_outside_wall_is_rejected(self):
        loop = make_loop([(0, 0), (4000, 0), (4000, 3000), (0, 3000)])
        loop.segments[0].openings.append(WallOpening(
            id="bad_window",
            offset_mm=3500.0,
            width_mm=1000.0,
            sill_height_mm=900.0,
            height_mm=1500.0,
        ))
        space = SpaceModel(boundary_loops=[loop], id="space_bad_window")
        codes = {issue.code for issue in validate_space(space)}
        self.assertIn("opening_after_wall", codes)


class ComplexProjectIoTests(unittest.TestCase):
    def _building(self):
        outer = make_loop([(0, 0), (5000, 0), (5000, 4000), (0, 4000)])
        outer.segments[0].openings.append(WallOpening(
            id="window_1",
            offset_mm=1000.0,
            width_mm=2000.0,
            sill_height_mm=900.0,
            height_mm=1500.0,
        ))
        space = SpaceModel(
            id="space_1",
            name="活动室",
            height_mm=3600.0,
            boundary_loops=[outer],
        )
        return BuildingModel(
            id="building_1",
            name="测试建筑",
            north_angle_deg=15.0,
            storeys=[StoreyModel(
                id="storey_1",
                name="一层",
                spaces=[space],
            )],
        )

    def test_v3_project_round_trip(self):
        building = self._building()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "complex.rlproj"
            save_building_project(
                str(path),
                building,
                active_space_id="space_1",
            )
            loaded, weather, active_space_id, error = load_building_project(
                str(path)
            )
        self.assertEqual(error, "")
        self.assertIsNone(weather)
        self.assertEqual(active_space_id, "space_1")
        self.assertEqual(loaded.name, "测试建筑")
        loaded_space = loaded.get_space("space_1")
        self.assertIsNotNone(loaded_space)
        self.assertAlmostEqual(
            space_floor_area_mm2(loaded_space),
            20_000_000.0,
        )
        self.assertEqual(
            loaded_space.get_opening("window_1").width_mm,
            2000.0,
        )

    def test_old_project_loads_as_v3_building(self):
        room = RoomModel(length=6000.0, width=4000.0, height=3000.0)
        room.add_window("south")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.rlproj"
            save_project(str(path), room)
            building, weather, active_space_id, error = load_building_project(
                str(path)
            )
        self.assertEqual(error, "")
        self.assertIsNone(weather)
        self.assertEqual(active_space_id, "legacy_space")
        self.assertIsNotNone(building.get_space("legacy_space"))

    def test_invalid_geometry_cannot_be_saved(self):
        building = self._building()
        space = building.get_space("space_1")
        space.boundary_loops[0].segments[-1].end = Point2D(100.0, 100.0)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.rlproj"
            with self.assertRaisesRegex(ValueError, "不能保存"):
                save_building_project(str(path), building)
            self.assertFalse(path.exists())


class ComplexDaylightTests(unittest.TestCase):
    def test_rectangle_matches_legacy_engine_without_shading(self):
        room = RoomModel(length=6000.0, width=4000.0, height=3000.0)
        window = room.add_window("south")
        window.x = 1000.0
        window.width = 2000.0
        window.y = 900.0
        window.height = 1500.0
        window.tau = 0.71
        space = building_from_room(room).get_space("legacy_space")

        legacy = compute_legacy_daylight(
            room,
            E_out=13_500.0,
            grid_mm=1000.0,
            ndiv=8,
        )
        complex_result = compute_complex_daylight(
            space,
            E_out=13_500.0,
            grid_mm=1000.0,
            ndiv=8,
        )
        self.assertTrue(complex_result.valid_mask.all())
        self.assertEqual(legacy.DF.shape, complex_result.DF.shape)
        np.testing.assert_allclose(
            legacy.DF,
            complex_result.DF,
            rtol=1e-12,
            atol=1e-12,
        )
        self.assertAlmostEqual(legacy.Ra, complex_result.Ra, places=12)

    def test_l_shape_masks_cutout_and_keeps_valid_values(self):
        loop = make_loop([
            (0, 0),
            (6000, 0),
            (6000, 3000),
            (3000, 3000),
            (3000, 6000),
            (0, 6000),
        ])
        loop.segments[0].openings.append(WallOpening(
            id="window_l",
            offset_mm=1000.0,
            width_mm=2000.0,
            sill_height_mm=900.0,
            height_mm=1500.0,
        ))
        space = SpaceModel(
            id="space_l_daylight",
            name="L形活动室",
            height_mm=3600.0,
            boundary_loops=[loop],
        )
        result = compute_complex_daylight(
            space,
            E_out=13_500.0,
            grid_mm=1000.0,
            ndiv=6,
        )
        self.assertTrue(np.any(~result.valid_mask))
        self.assertTrue(np.all(np.isnan(result.DF[~result.valid_mask])))
        self.assertTrue(np.all(np.isfinite(result.DF[result.valid_mask])))
        self.assertGreater(result.DF_max, 0.0)

    def test_concave_return_wall_blocks_window_around_corner(self):
        loop = make_loop([
            (0, 0),
            (6000, 0),
            (6000, 3000),
            (3000, 3000),
            (3000, 6000),
            (0, 6000),
        ])
        east_wall = loop.segments[1]
        opening = WallOpening(
            id="east_window",
            offset_mm=500.0,
            width_mm=1500.0,
            sill_height_mm=900.0,
            height_mm=1500.0,
        )
        east_wall.openings.append(opening)
        space = SpaceModel(
            id="space_corner",
            height_mm=3600.0,
            boundary_loops=[loop],
        )
        visible = complex_ds_point(
            np.array([5.0, 1.5, 0.75]),
            east_wall,
            opening,
            space,
            ndiv=8,
        )
        behind_corner = complex_ds_point(
            np.array([1.5, 4.5, 0.75]),
            east_wall,
            opening,
            space,
            ndiv=8,
        )
        self.assertGreater(visible, 0.0)
        self.assertEqual(behind_corner, 0.0)


class ComplexThermalTests(unittest.TestCase):
    def test_rectangle_matches_legacy_when_solar_radiation_is_zero(self):
        room = RoomModel(length=6000.0, width=4000.0, height=3000.0)
        window = room.add_window("south")
        window.x = 1000.0
        window.width = 2000.0
        window.y = 900.0
        window.height = 1500.0
        space = building_from_room(room).get_space("legacy_space")
        ghi = [0.0] * 12
        temperatures = [
            5.0, 7.0, 12.0, 18.0, 23.0, 27.0,
            30.0, 29.0, 25.0, 19.0, 13.0, 7.0,
        ]
        legacy = compute_legacy_thermal(room, ghi, temperatures)
        complex_result = compute_complex_thermal(
            space,
            ghi,
            temperatures,
            latitude_deg=room.location.latitude,
            north_angle_deg=room.location.orientation_deg,
        )
        self.assertAlmostEqual(legacy.UA_wall, complex_result.UA_wall)
        self.assertAlmostEqual(legacy.UA_win, complex_result.UA_win)
        self.assertAlmostEqual(legacy.UA_roof, complex_result.UA_roof)
        self.assertAlmostEqual(legacy.UA_floor, complex_result.UA_floor)
        self.assertAlmostEqual(legacy.H_bridge, complex_result.H_bridge)
        np.testing.assert_allclose(
            legacy.T_in,
            complex_result.T_in,
            rtol=1e-12,
            atol=1e-12,
        )

    def test_cardinal_and_oblique_wall_azimuths(self):
        south_wall = WallSegment(
            start=Point2D(0, 0),
            end=Point2D(4000, 0),
        )
        self.assertAlmostEqual(wall_azimuth_deg(south_wall), 180.0)
        self.assertAlmostEqual(wall_azimuth_deg(south_wall, 30.0), 210.0)
        south_factors = orientation_factors_for_azimuth(180.0)
        southwest_factors = orientation_factors_for_azimuth(225.0)
        west_factors = orientation_factors_for_azimuth(270.0)
        np.testing.assert_allclose(
            southwest_factors,
            (south_factors + west_factors) / 2.0,
        )


class ComplexExperimentTests(unittest.TestCase):
    def _space(self):
        outer = make_loop([
            (0, 0),
            (6000, 0),
            (6000, 3000),
            (3000, 3000),
            (3000, 6000),
            (0, 6000),
        ])
        outer.segments[0].openings.append(WallOpening(
            id="south_window",
            offset_mm=1000.0,
            width_mm=2000.0,
            sill_height_mm=900.0,
            height_mm=1500.0,
        ))
        outer.segments[-1].openings.append(WallOpening(
            id="west_window",
            offset_mm=1000.0,
            width_mm=1000.0,
            sill_height_mm=900.0,
            height_mm=1500.0,
        ))
        return SpaceModel(
            id="experiment_space",
            name="L形活动室",
            height_mm=3600.0,
            boundary_loops=[outer],
        )

    def test_complex_scan_uses_actual_windows_and_unique_zero(self):
        space = self._space()
        materials = DEFAULT_SELECTED_MATERIALS[:2]
        frame = run_all_complex_experiments(
            space,
            ndiv=2,
            grid_mm=1000.0,
            tilt_degs=[90.0],
            depth_mms=[0.0, 600.0],
            gap_mms=[0.0],
            materials=materials,
            material_unit_costs={
                materials[0]: 700.0,
                materials[1]: 900.0,
            },
            support_cost_per_m=100.0,
            install_cost_per_window=200.0,
        )
        self.assertEqual(len(frame), 3)
        zero_rows = frame[frame["L_mm"] == 0.0]
        self.assertEqual(len(zero_rows), 1)
        self.assertTrue(bool(zero_rows.iloc[0]["is_candidate"]))

        positive = frame[frame["L_mm"] == 600.0]
        self.assertEqual(len(positive), 2)
        self.assertTrue((positive["window_count"] == 2).all())
        self.assertTrue((positive["shaded_wall_count"] == 2).all())
        self.assertTrue(np.allclose(positive["panel_area_m2"], 1.8))
        self.assertTrue(np.allclose(positive["support_length_m"], 6.0))
        self.assertTrue(np.allclose(
            positive["installation_cost"], 400.0))
        self.assertAlmostEqual(
            float(positive.iloc[0]["Ra"]),
            float(positive.iloc[1]["Ra"]),
        )
        self.assertEqual(positive["application_scope"].nunique(), 1)

    def test_existing_shading_gets_separate_no_shade_candidate(self):
        space = self._space()
        space.shading = ShadingDevice(
            type="horizontal_overhang",
            overhang_depth_mm=800.0,
        )
        frame = run_all_complex_experiments(
            space,
            ndiv=2,
            grid_mm=1000.0,
            tilt_degs=[90.0],
            depth_mms=[0.0, 600.0],
            gap_mms=[0.0],
            materials=DEFAULT_SELECTED_MATERIALS[:1],
        )
        self.assertEqual(len(frame), 3)
        baseline = frame[
            frame["group"] == "原始模型基准"
        ].iloc[0]
        no_shade = frame[
            frame["group"] == "无遮阳候选"
        ].iloc[0]
        self.assertFalse(bool(baseline["is_candidate"]))
        self.assertTrue(bool(no_shade["is_candidate"]))

    def test_solution_is_a_clone_and_preserves_polygon(self):
        space = self._space()
        solution = {
            "L_mm": 900.0,
            "gap_mm": 150.0,
            "tilt_deg": 105.0,
            "material": DEFAULT_SELECTED_MATERIALS[0],
        }
        result = build_solution_space(space, solution)
        self.assertIsNot(result, space)
        self.assertAlmostEqual(
            result.shading.overhang_depth_mm, 900.0)
        self.assertAlmostEqual(
            result.shading.overhang_height_mm, 150.0)
        self.assertAlmostEqual(
            result.shading.overhang_tilt_deg, 105.0)
        self.assertEqual(
            [point.as_tuple() for point in result.outer_loop().points()],
            [point.as_tuple() for point in space.outer_loop().points()],
        )
        self.assertEqual(space.shading.type, "none")


if __name__ == "__main__":
    unittest.main()
