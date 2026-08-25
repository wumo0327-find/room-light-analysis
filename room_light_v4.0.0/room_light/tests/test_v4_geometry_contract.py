import tempfile
import unittest
from pathlib import Path

from bp_editor.model import DraftDocument, Point
from core.complex_models import Point2D, WallSegment
from core.legacy_adapter import building_from_room
from core.models import RoomModel
from core.space_geometry import space_floor_area_mm2, validate_space
from io_utils.bp_bridge import building_from_document
from io_utils.model_audit import audit_building_geometry
from io_utils.project_io import load_building_project, save_building_project


class V4GeometryContractTests(unittest.TestCase):
    def _rectangle(self, axis="center", corner_window=True, window_at_end=False):
        document = DraftDocument()
        points = [
            Point(0.0, 0.0),
            Point(6000.0, 0.0),
            Point(6000.0, 4000.0),
            Point(0.0, 4000.0),
        ]
        walls = [
            document.add_wall(
                points[index],
                points[(index + 1) % 4],
                height_mm=3600.0,
                width_mm=200.0,
                axis=axis,
            )
            for index in range(4)
        ]
        if corner_window:
            document.add_window(
                walls[0].id,
                walls[0].length_mm - 1500.0 if window_at_end else 0.0,
                width_mm=1500.0,
                sill_height_mm=600.0,
                height_mm=2400.0,
            )
        return document

    def _convert(self, document):
        building, active_id, _summary = building_from_document(
            document,
            building_from_room(RoomModel()),
            project_name="v4 geometry contract",
            previous_active_space_id="legacy_space",
        )
        return building, building.get_space(active_id)

    def test_wall_axes_and_physical_inner_floor_are_both_preserved(self):
        building, space = self._convert(self._rectangle())
        self.assertIsNotNone(space)
        self.assertEqual(len(space.analysis_floor_loops), 1)
        self.assertAlmostEqual(space_floor_area_mm2(space), 5800.0 * 3800.0)
        # Host axes remain at the authored 6000 x 4000 geometry.  They are not
        # moved inward merely to obtain a net floor polygon.
        self.assertAlmostEqual(space.outer_loop().segments[0].length_mm, 6000.0)
        self.assertAlmostEqual(space.outer_loop().segments[1].length_mm, 4000.0)
        self.assertFalse([issue for issue in validate_space(space) if issue.severity == "error"])
        self.assertTrue(audit_building_geometry(building).ok)

    def test_corner_window_keeps_zero_offset_and_physical_glazing_plane(self):
        _building, space = self._convert(self._rectangle())
        windows = [
            opening
            for wall in space.wall_segments()
            for opening in wall.windows()
        ]
        self.assertEqual(len(windows), 1)
        self.assertAlmostEqual(windows[0].offset_mm, 0.0)
        self.assertAlmostEqual(windows[0].width_mm, 1500.0)
        self.assertAlmostEqual(windows[0].plane_offset_mm, 100.0)

    def test_left_and_right_axis_walls_keep_authored_window_and_net_faces(self):
        expectations = {
            "left": (5600.0 * 3600.0, 0.0),
            "right": (6000.0 * 4000.0, 200.0),
        }
        for axis, (expected_area, expected_plane_offset) in expectations.items():
            with self.subTest(axis=axis):
                _building, space = self._convert(
                    self._rectangle(axis=axis, window_at_end=True)
                )
                opening = next(
                    opening
                    for wall in space.wall_segments()
                    for opening in wall.windows()
                )
                self.assertAlmostEqual(
                    space_floor_area_mm2(space), expected_area
                )
                self.assertAlmostEqual(opening.offset_mm, 4500.0)
                self.assertAlmostEqual(opening.end_offset_mm, 6000.0)
                self.assertAlmostEqual(
                    opening.plane_offset_mm, expected_plane_offset
                )

    def test_net_floor_and_model_fingerprint_survive_rlproj_roundtrip(self):
        building, space = self._convert(self._rectangle())
        space.roof_exposed = False
        space.floor_exposed = False
        before = audit_building_geometry(building)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "v4_contract.rlproj"
            save_building_project(str(target), building, active_space_id=space.id)
            loaded, _weather, active_id, error = load_building_project(str(target))
        self.assertEqual(error, "")
        self.assertEqual(active_id, space.id)
        loaded_space = loaded.get_space(active_id)
        self.assertAlmostEqual(
            space_floor_area_mm2(loaded_space),
            5800.0 * 3800.0,
        )
        self.assertFalse(loaded_space.roof_exposed)
        self.assertFalse(loaded_space.floor_exposed)
        after = audit_building_geometry(loaded)
        self.assertTrue(after.ok, after.issues)
        self.assertEqual(after.fingerprint, before.fingerprint)

    def test_inner_face_cleanup_removes_wall_width_sized_zero_return(self):
        document = DraftDocument()
        # The 200 mm top-right return collapses when a 200 mm wall is offset
        # to its physical inner face.  The net polygon must remain valid and
        # must not contain a duplicate zero-length segment.
        points = [
            Point(0.0, 0.0),
            Point(6000.0, 0.0),
            Point(6000.0, 4000.0),
            Point(5800.0, 4000.0),
            Point(5800.0, 5000.0),
            Point(0.0, 5000.0),
        ]
        for index in range(len(points)):
            document.add_wall(
                points[index],
                points[(index + 1) % len(points)],
                height_mm=3600.0,
                width_mm=200.0,
                axis="center",
            )
        building, space = self._convert(document)
        net = space.analysis_outer_points()
        self.assertGreaterEqual(len(net), 4)
        self.assertTrue(all(
            net[index].distance_to(net[(index + 1) % len(net)]) > 1e-6
            for index in range(len(net))
        ))
        report = audit_building_geometry(building)
        self.assertTrue(report.ok, report.issues)

    def test_collapsed_inner_wall_length_remains_zero_for_thermal_area(self):
        wall = WallSegment(
            start=Point2D(0.0, 0.0),
            end=Point2D(200.0, 0.0),
            metadata={"analysis_length_mm": 0.0},
        )
        self.assertEqual(wall.analysis_length_mm, 0.0)


if __name__ == "__main__":
    unittest.main()
