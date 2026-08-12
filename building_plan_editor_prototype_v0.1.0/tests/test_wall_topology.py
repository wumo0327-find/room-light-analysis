"""Wall topology, endpoint editing and offset-axis corner regression tests."""
from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from model import DraftDocument, Point

try:
    from PyQt6.QtCore import QPointF, Qt
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication
    from canvas import DraftCanvas
except ModuleNotFoundError:
    QPointF = None
    Qt = None
    QTest = None
    QApplication = None
    DraftCanvas = None


APP = (
    QApplication.instance() or QApplication([])
    if QApplication is not None
    else None
)


class WallTopologyTests(unittest.TestCase):
    @staticmethod
    def add_wall(
        document: DraftDocument,
        start: Point,
        end: Point,
        *,
        axis: str = "center",
    ):
        return document.add_wall(
            start,
            end,
            height_mm=3600,
            width_mm=200,
            axis=axis,
        )

    def test_t_junction_physically_splits_long_wall(self) -> None:
        document = DraftDocument()
        long_wall = self.add_wall(
            document,
            Point(0, 0),
            Point(6000, 0),
        )
        branch = self.add_wall(
            document,
            Point(3000, 0),
            Point(3000, 3000),
        )

        mapping = document.split_walls_at_junctions()

        self.assertEqual(len(document.walls), 3)
        self.assertEqual(len(mapping[long_wall.id]), 2)
        self.assertEqual(
            sorted(
                wall.length_mm
                for wall in document.walls
                if wall.id != branch.id
            ),
            [3000, 3000],
        )
        first_side = mapping[long_wall.id][0]
        second_side = mapping[long_wall.id][1]
        self.assertTrue(document.remove_entity(first_side))
        self.assertIsNotNone(document.wall_by_id(second_side))

    def test_straight_segments_without_branch_merge_into_one_wall(self) -> None:
        document = DraftDocument()
        first = self.add_wall(
            document,
            Point(0, 0),
            Point(3000, 0),
            axis="left",
        )
        second = self.add_wall(
            document,
            Point(3000, 0),
            Point(6000, 0),
            axis="left",
        )

        mapping = document.normalise_wall_topology()

        self.assertEqual(len(document.walls), 1)
        self.assertAlmostEqual(document.walls[0].length_mm, 6000)
        self.assertEqual(mapping[first.id], [document.walls[0].id])
        self.assertEqual(mapping[second.id], [document.walls[0].id])

    def test_t_branch_prevents_straight_segments_from_merging(self) -> None:
        document = DraftDocument()
        self.add_wall(document, Point(0, 0), Point(6000, 0))
        self.add_wall(document, Point(3000, 0), Point(3000, 3000))

        document.normalise_wall_topology()

        self.assertEqual(len(document.walls), 3)

    def test_removing_t_branch_allows_main_wall_to_merge_again(self) -> None:
        document = DraftDocument()
        self.add_wall(document, Point(0, 0), Point(6000, 0))
        branch = self.add_wall(
            document,
            Point(3000, 0),
            Point(3000, 3000),
        )
        document.normalise_wall_topology()
        self.assertEqual(len(document.walls), 3)

        document.remove_entity(branch.id)
        document.normalise_wall_topology()

        self.assertEqual(len(document.walls), 1)
        self.assertAlmostEqual(document.walls[0].length_mm, 6000)

    def test_windows_keep_world_position_when_wall_segments_merge(self) -> None:
        document = DraftDocument()
        first = self.add_wall(
            document,
            Point(0, 0),
            Point(3000, 0),
        )
        second = self.add_wall(
            document,
            Point(3000, 0),
            Point(6000, 0),
        )
        first_window = document.add_window(
            first.id,
            500,
            width_mm=1000,
            sill_height_mm=600,
            height_mm=1800,
        )
        second_window = document.add_window(
            second.id,
            800,
            width_mm=1000,
            sill_height_mm=600,
            height_mm=1800,
        )

        document.normalise_wall_topology()

        self.assertEqual(len(document.walls), 1)
        merged = document.walls[0]
        self.assertEqual(first_window.wall_id, merged.id)
        self.assertEqual(second_window.wall_id, merged.id)
        self.assertAlmostEqual(first_window.offset_mm, 500)
        self.assertAlmostEqual(second_window.offset_mm, 3800)

    def test_window_moves_to_correct_split_piece(self) -> None:
        document = DraftDocument()
        long_wall = self.add_wall(
            document,
            Point(0, 0),
            Point(6000, 0),
        )
        window = document.add_window(
            long_wall.id,
            4000,
            width_mm=1200,
            sill_height_mm=600,
            height_mm=1800,
        )
        self.add_wall(
            document,
            Point(3000, 0),
            Point(3000, 3000),
        )

        mapping = document.split_walls_at_junctions()

        self.assertEqual(window.wall_id, mapping[long_wall.id][1])
        self.assertAlmostEqual(window.offset_mm, 1000)

    def test_split_through_window_is_safely_ignored(self) -> None:
        document = DraftDocument()
        long_wall = self.add_wall(
            document,
            Point(0, 0),
            Point(6000, 0),
        )
        document.add_window(
            long_wall.id,
            2400,
            width_mm=1200,
            sill_height_mm=600,
            height_mm=1800,
        )
        self.add_wall(
            document,
            Point(3000, 0),
            Point(3000, 3000),
        )

        mapping = document.split_walls_at_junctions()

        self.assertEqual(mapping[long_wall.id], [long_wall.id])
        self.assertEqual(len(document.walls), 2)

    def test_resize_start_preserves_window_world_position(self) -> None:
        document = DraftDocument()
        wall = self.add_wall(
            document,
            Point(0, 0),
            Point(6000, 0),
        )
        window = document.add_window(
            wall.id,
            2000,
            width_mm=1200,
            sill_height_mm=600,
            height_mm=1800,
        )
        old_window_start = wall.point_at(window.offset_mm)

        document.resize_wall_endpoint(
            wall.id,
            "start",
            Point(-1000, 0),
        )

        self.assertAlmostEqual(wall.length_mm, 7000)
        self.assertAlmostEqual(window.offset_mm, 3000)
        self.assertEqual(wall.point_at(window.offset_mm), old_window_start)

    def test_resize_rejects_wall_shorter_than_window(self) -> None:
        document = DraftDocument()
        wall = self.add_wall(
            document,
            Point(0, 0),
            Point(6000, 0),
        )
        document.add_window(
            wall.id,
            4000,
            width_mm=1200,
            sill_height_mm=600,
            height_mm=1800,
        )
        with self.assertRaises(ValueError):
            document.resize_wall_endpoint(
                wall.id,
                "end",
                Point(4500, 0),
            )

    @unittest.skipIf(DraftCanvas is None, "PyQt6 is not installed")
    def test_left_axis_corner_cap_stays_on_physical_offset_side(self) -> None:
        canvas = DraftCanvas()
        document = DraftDocument()
        self.add_wall(
            document,
            Point(0, 0),
            Point(1000, 0),
            axis="left",
        )
        self.add_wall(
            document,
            Point(1000, 0),
            Point(1000, -1000),
            axis="left",
        )
        canvas.set_document(document, fit=False)
        canvas.scale = 1.0
        canvas.origin = QPointF(0.0, 0.0)

        polygons = canvas._wall_joint_polygons()

        self.assertEqual(len(polygons), 1)
        points = list(polygons[0])
        self.assertAlmostEqual(min(point.x() for point in points), 1000)
        self.assertAlmostEqual(max(point.x() for point in points), 1200)
        self.assertAlmostEqual(min(point.y() for point in points), -200)
        self.assertAlmostEqual(max(point.y() for point in points), 0)

    @unittest.skipIf(DraftCanvas is None, "PyQt6 is not installed")
    def test_selected_wall_endpoint_can_be_dragged(self) -> None:
        canvas = DraftCanvas()
        canvas.resize(900, 600)
        document = DraftDocument(grid_mm=100)
        wall = self.add_wall(
            document,
            Point(0, 0),
            Point(3000, 0),
        )
        canvas.set_document(document, fit=False)
        canvas.scale = 0.1
        canvas.origin = QPointF(100.0, 300.0)
        canvas._selected_ids = {wall.id}
        canvas._selected_id = wall.id
        canvas.show()
        APP.processEvents()

        start = canvas.world_to_screen(Point(3000, 0)).toPoint()
        target = canvas.world_to_screen(Point(4000, 0)).toPoint()
        QTest.mousePress(
            canvas,
            Qt.MouseButton.LeftButton,
            pos=start,
        )
        QTest.mouseMove(canvas, target)
        QTest.mouseRelease(
            canvas,
            Qt.MouseButton.LeftButton,
            pos=target,
        )

        self.assertAlmostEqual(
            canvas.document.wall_by_id(wall.id).length_mm,
            4000,
        )


if __name__ == "__main__":
    unittest.main()
