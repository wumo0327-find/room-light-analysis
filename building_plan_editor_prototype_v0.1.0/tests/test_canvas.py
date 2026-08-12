"""Off-screen interaction smoke tests for numeric drafting commands."""
from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication
from PyQt6.QtTest import QTest
from PyQt6.QtCore import Qt

from canvas import DraftCanvas
from model import DraftDocument, Point


APP = QApplication.instance() or QApplication([])


class DraftCanvasCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.canvas = DraftCanvas()
        self.canvas.set_document(DraftDocument())

    def test_wall_numeric_length_and_undo(self) -> None:
        self.canvas.start_wall(
            {"height_mm": 3600, "width_mm": 200, "axis": "center"}
        )
        self.canvas._last_point = Point(0, 0)
        self.canvas._snap_world = Point(1000, 100)
        self.assertTrue(self.canvas.apply_numeric(3000))
        self.assertEqual(len(self.canvas.document.walls), 1)
        self.assertAlmostEqual(self.canvas.document.walls[0].length_mm, 3000)

        self.canvas.undo()
        self.assertEqual(len(self.canvas.document.walls), 0)

    def test_window_numeric_left_distance(self) -> None:
        wall = self.canvas.document.add_wall(
            Point(0, 0),
            Point(6000, 0),
            height_mm=3600,
            width_mm=200,
            axis="center",
        )
        self.canvas.start_window(
            {"sill_height_mm": 600, "height_mm": 2400, "width_mm": 1800}
        )
        self.canvas._window_preview = (wall, 0)
        self.assertTrue(self.canvas.apply_numeric(2070))

        window = self.canvas.document.windows[0]
        self.assertAlmostEqual(window.offset_mm, 2070)
        self.assertAlmostEqual(
            wall.length_mm - window.end_offset_mm,
            2130,
        )

    def test_railing_numeric_length(self) -> None:
        self.canvas.start_railing(
            {"height_mm": 1100, "width_mm": 50, "material": "金属栏杆"}
        )
        self.canvas._last_point = Point(0, 0)
        self.canvas._snap_world = Point(0, -1000)
        self.assertTrue(self.canvas.apply_numeric(1500))
        self.assertEqual(len(self.canvas.document.railings), 1)
        self.assertAlmostEqual(
            self.canvas.document.railings[0].length_mm, 1500
        )

    def test_line_numeric_length(self) -> None:
        self.canvas.start_line()
        self.canvas._last_point = Point(0, 0)
        self.canvas._snap_world = Point(1000, 1000)
        self.assertTrue(self.canvas.apply_numeric(2000))
        self.assertEqual(len(self.canvas.document.lines), 1)
        self.assertAlmostEqual(
            self.canvas.document.lines[0].length_mm,
            2000,
        )

    def test_midpoint_snap_for_wall_window_and_railing_axes(self) -> None:
        self.canvas.scale = 0.1
        wall = self.canvas.document.add_wall(
            Point(0, 0),
            Point(6000, 0),
            height_mm=3000,
            width_mm=200,
            axis="center",
        )
        self.canvas.document.add_window(
            wall.id,
            800,
            width_mm=1200,
            sill_height_mm=600,
            height_mm=1800,
        )
        self.canvas.document.add_railing(
            Point(0, -2000),
            Point(4000, -2000),
            height_mm=1100,
            material="金属栏杆",
            width_mm=50,
        )

        self.assertEqual(
            self.canvas._drawing_snap(Point(3010, 15)),
            Point(3000, 0),
        )
        self.assertEqual(self.canvas._snap_kind, "midpoint")
        self.assertEqual(
            self.canvas._drawing_snap(Point(1410, 20)),
            Point(1400, 0),
        )
        self.assertEqual(self.canvas._snap_kind, "midpoint")
        self.assertEqual(
            self.canvas._drawing_snap(Point(2010, -1980)),
            Point(2000, -2000),
        )
        self.assertEqual(self.canvas._snap_kind, "midpoint")

    def test_intersection_snap_for_crossing_drafting_axes(self) -> None:
        self.canvas.scale = 0.1
        self.canvas.document.add_line(
            Point(0, 0),
            Point(4000, 0),
        )
        self.canvas.document.add_line(
            Point(1000, -1000),
            Point(1000, 3000),
        )

        self.assertEqual(
            self.canvas._drawing_snap(Point(1015, 20)),
            Point(1000, 0),
        )
        self.assertEqual(self.canvas._snap_kind, "intersection")

    def test_perpendicular_snap_uses_current_drawing_start(self) -> None:
        self.canvas.scale = 0.1
        self.canvas.document.add_wall(
            Point(0, 0),
            Point(4000, 0),
            height_mm=3000,
            width_mm=200,
            axis="center",
        )
        self.canvas.start_line()
        self.canvas._last_point = Point(1200, 1600)

        self.assertEqual(
            self.canvas._drawing_snap(Point(1210, 15)),
            Point(1200, 0),
        )
        self.assertEqual(self.canvas._snap_kind, "perpendicular")

    def test_nearest_snap_preserves_arbitrary_axis_position(self) -> None:
        self.canvas.scale = 0.1
        self.canvas.document.add_wall(
            Point(0, 0),
            Point(4000, 0),
            height_mm=3000,
            width_mm=200,
            axis="center",
        )

        self.assertEqual(
            self.canvas._drawing_snap(Point(2730, 30)),
            Point(2730, 0),
        )
        self.assertEqual(self.canvas._snap_kind, "nearest")

    def test_move_copy_and_mirror_commands_use_current_selection(self) -> None:
        line = self.canvas.document.add_line(Point(0, 0), Point(1000, 0))
        self.canvas._selected_ids = {line.id}
        self.canvas.start_move()
        self.assertEqual(self.canvas.mode, "move_base")
        self.canvas._edit_base = Point(0, 0)
        self.assertTrue(
            self.canvas._apply_edit_operation("move", Point(500, 600))
        )
        self.assertEqual(line.start, Point(500, 600))

        self.canvas._selected_ids = {line.id}
        self.canvas.start_copy()
        self.canvas._edit_base = Point(0, 0)
        self.assertTrue(
            self.canvas._apply_edit_operation("copy", Point(2000, 0))
        )
        self.assertEqual(len(self.canvas.document.lines), 2)

        self.canvas._selected_ids = {line.id}
        self.canvas.start_mirror()
        self.canvas._edit_base = Point(0, -1000)
        self.assertTrue(
            self.canvas._apply_edit_operation("mirror", Point(0, 1000))
        )
        self.assertEqual(len(self.canvas.document.lines), 3)

    def test_box_selection_and_view_switch(self) -> None:
        wall = self.canvas.document.add_wall(
            Point(0, 0),
            Point(3000, 0),
            height_mm=3000,
            width_mm=200,
            axis="center",
        )
        self.canvas.scale = 0.1
        self.canvas.origin.setX(100)
        self.canvas.origin.setY(400)
        self.canvas._apply_box_selection(
            self.canvas.world_to_screen(Point(-300, 300)),
            self.canvas.world_to_screen(Point(3300, -300)),
            False,
        )
        self.assertIn(wall.id, self.canvas._selected_ids)

        self.canvas.set_view_mode("north")
        self.assertEqual(self.canvas.view_mode, "north")

    def test_elevation_depth_follows_observer_direction(self) -> None:
        south_wall = self.canvas.document.add_wall(
            Point(0, 0),
            Point(3000, 0),
            height_mm=3000,
            width_mm=200,
            axis="center",
        )
        north_wall = self.canvas.document.add_wall(
            Point(0, 4000),
            Point(3000, 4000),
            height_mm=3000,
            width_mm=200,
            axis="center",
        )
        self.canvas.set_view_mode("north")
        self.assertGreater(
            self.canvas._elevation_nearness(north_wall),
            self.canvas._elevation_nearness(south_wall),
        )
        self.canvas.set_view_mode("south")
        self.assertGreater(
            self.canvas._elevation_nearness(south_wall),
            self.canvas._elevation_nearness(north_wall),
        )

    def test_edge_on_wall_projects_its_real_width(self) -> None:
        wall = self.canvas.document.add_wall(
            Point(0, 0),
            Point(3000, 0),
            height_mm=3000,
            width_mm=240,
            axis="center",
        )
        self.canvas.set_view_mode("east")
        left, right = self.canvas._elevation_wall_horizontal_bounds(wall)
        self.assertAlmostEqual(right - left, 240)
        self.assertEqual(
            self.canvas._elevation_wall_layer_priority(wall),
            0,
        )

        face_wall = self.canvas.document.add_wall(
            Point(3000, -1000),
            Point(3000, 1000),
            height_mm=3000,
            width_mm=240,
            axis="center",
        )
        self.assertAlmostEqual(
            self.canvas._elevation_nearness(wall),
            self.canvas._elevation_nearness(face_wall),
        )
        self.assertEqual(
            self.canvas._elevation_wall_layer_priority(face_wall),
            1,
        )

        self.canvas.set_view_mode("north")
        left, right = self.canvas._elevation_wall_horizontal_bounds(wall)
        self.assertAlmostEqual(
            right - left,
            3000 + face_wall.width_mm / 2.0,
        )

    def test_u_shape_has_expected_visibility_in_all_four_elevations(
        self,
    ) -> None:
        """
        Regression case for a U-shaped plan open to the west:

        north: north wall alone;
        west: north/south wall ends plus the east wall face;
        south: south wall plus the uncovered part of the north wall;
        east: east wall alone.
        """
        north_wall = self.canvas.document.add_wall(
            Point(0, 8000),
            Point(10000, 8000),
            height_mm=3000,
            width_mm=240,
            axis="center",
        )
        east_wall = self.canvas.document.add_wall(
            Point(10000, 8000),
            Point(10000, 0),
            height_mm=3000,
            width_mm=240,
            axis="center",
        )
        south_wall = self.canvas.document.add_wall(
            Point(10000, 0),
            Point(5000, 0),
            height_mm=3000,
            width_mm=240,
            axis="center",
        )

        def layer_key(wall):
            return (
                self.canvas._elevation_nearness(wall),
                self.canvas._elevation_wall_layer_priority(wall),
            )

        self.canvas.set_view_mode("north")
        self.assertGreater(layer_key(north_wall), layer_key(east_wall))
        self.assertGreater(layer_key(north_wall), layer_key(south_wall))
        north_bounds = self.canvas._elevation_wall_horizontal_bounds(
            north_wall
        )
        east_bounds = self.canvas._elevation_wall_horizontal_bounds(
            east_wall
        )
        south_bounds = self.canvas._elevation_wall_horizontal_bounds(
            south_wall
        )
        self.assertLessEqual(north_bounds[0], south_bounds[0])
        self.assertGreaterEqual(north_bounds[1], south_bounds[1])
        self.assertLessEqual(north_bounds[0], east_bounds[0])
        self.assertGreaterEqual(north_bounds[1], east_bounds[1])

        self.canvas.set_view_mode("east")
        self.assertGreater(layer_key(east_wall), layer_key(north_wall))
        self.assertGreater(layer_key(east_wall), layer_key(south_wall))
        east_bounds = self.canvas._elevation_wall_horizontal_bounds(
            east_wall
        )
        north_bounds = self.canvas._elevation_wall_horizontal_bounds(
            north_wall
        )
        south_bounds = self.canvas._elevation_wall_horizontal_bounds(
            south_wall
        )
        self.assertLessEqual(east_bounds[0], north_bounds[0])
        self.assertGreaterEqual(east_bounds[1], north_bounds[1])
        self.assertLessEqual(east_bounds[0], south_bounds[0])
        self.assertGreaterEqual(east_bounds[1], south_bounds[1])

        self.canvas.set_view_mode("south")
        self.assertGreater(layer_key(south_wall), layer_key(east_wall))
        self.assertGreater(layer_key(south_wall), layer_key(north_wall))
        north_bounds = self.canvas._elevation_wall_horizontal_bounds(
            north_wall
        )
        south_bounds = self.canvas._elevation_wall_horizontal_bounds(
            south_wall
        )
        overlap = min(north_bounds[1], south_bounds[1]) - max(
            north_bounds[0], south_bounds[0]
        )
        self.assertGreater(overlap, 0)
        self.assertLess(
            overlap,
            north_bounds[1] - north_bounds[0],
        )

        self.canvas.set_view_mode("west")
        north_bounds = self.canvas._elevation_wall_horizontal_bounds(
            north_wall
        )
        east_bounds = self.canvas._elevation_wall_horizontal_bounds(
            east_wall
        )
        south_bounds = self.canvas._elevation_wall_horizontal_bounds(
            south_wall
        )
        self.assertAlmostEqual(north_bounds[1] - north_bounds[0], 240)
        self.assertAlmostEqual(south_bounds[1] - south_bounds[0], 240)
        self.assertGreater(east_bounds[1] - east_bounds[0], 7900)

    def test_dimension_uses_cad_three_point_then_continue_flow(self) -> None:
        self.canvas.resize(700, 520)
        self.canvas.show()
        self.canvas.scale = 0.1
        self.canvas.origin.setX(100)
        self.canvas.origin.setY(350)
        self.canvas.start_dimension()

        def click(point: Point) -> None:
            screen = self.canvas.world_to_screen(point).toPoint()
            QTest.mouseMove(self.canvas, screen)
            QTest.mouseClick(
                self.canvas,
                Qt.MouseButton.LeftButton,
                pos=screen,
            )

        click(Point(0, 0))
        self.assertEqual(self.canvas.mode, "dimension_second")
        click(Point(3000, 0))
        self.assertEqual(self.canvas.mode, "dimension_place")
        click(Point(0, -600))
        self.assertEqual(self.canvas.mode, "dimension_continue")
        self.assertEqual(len(self.canvas.document.dimensions), 1)
        self.assertAlmostEqual(
            self.canvas.document.dimensions[0].offset_mm,
            -600,
        )
        click(Point(5000, 0))
        self.assertEqual(
            len(self.canvas.document.dimensions[0].points),
            3,
        )
        self.assertTrue(self.canvas.complete_command())
        self.assertEqual(self.canvas.mode, "select")


if __name__ == "__main__":
    unittest.main()
