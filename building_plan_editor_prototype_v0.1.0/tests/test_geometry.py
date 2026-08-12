from pathlib import Path
import tempfile
import unittest

from model import DraftDocument, Point


def add_wall(document, start, end):
    return document.add_wall(
        Point(*start),
        Point(*end),
        height_mm=3000.0,
        width_mm=200.0,
        axis="center",
    )


class RoomRecognitionTests(unittest.TestCase):
    def test_open_walls_do_not_form_room(self):
        document = DraftDocument()
        add_wall(document, (0, 0), (6000, 0))
        add_wall(document, (6000, 0), (6000, 4000))
        add_wall(document, (6000, 4000), (0, 4000))
        self.assertEqual(document.recognised_rooms(), [])

    def test_rectangle_forms_one_room(self):
        document = DraftDocument()
        points = ((0, 0), (6000, 0), (6000, 4000), (0, 4000))
        for index, point in enumerate(points):
            add_wall(document, point, points[(index + 1) % 4])
        rooms = document.recognised_rooms()
        self.assertEqual(len(rooms), 1)
        self.assertAlmostEqual(rooms[0].area_mm2, 24_000_000.0)

    def test_offset_axis_solid_corner_closes_room(self):
        """A 100 mm axis gap is closed by two overlapping 200 mm walls."""
        document = DraftDocument()
        for start, end in (
            ((0, 0), (5900, 0)),
            ((6000, 0), (6000, 4000)),
            ((6000, 4000), (0, 4000)),
            ((0, 4000), (0, 0)),
        ):
            document.add_wall(
                Point(*start),
                Point(*end),
                height_mm=3600,
                width_mm=200,
                axis="left",
            )
        rooms = document.recognised_rooms()
        self.assertEqual(len(rooms), 1)
        self.assertAlmostEqual(rooms[0].area_mm2, 24_000_000.0)

    def test_offset_axis_real_physical_gap_stays_open(self):
        """Nearby axes must not be merged when the solid strips do not touch."""
        document = DraftDocument()
        for start, end in (
            ((0, 0), (5700, 0)),
            ((6000, 0), (6000, 4000)),
            ((6000, 4000), (0, 4000)),
            ((0, 4000), (0, 0)),
        ):
            document.add_wall(
                Point(*start),
                Point(*end),
                height_mm=3600,
                width_mm=200,
                axis="left",
            )
        self.assertEqual(document.recognised_rooms(), [])

    def test_t_junction_partition_forms_two_rooms(self):
        document = DraftDocument()
        for start, end in (
            ((0, 0), (6000, 0)),
            ((6000, 0), (6000, 4000)),
            ((6000, 4000), (0, 4000)),
            ((0, 4000), (0, 0)),
            ((3000, 0), (3000, 4000)),
        ):
            add_wall(document, start, end)
        rooms = document.recognised_rooms()
        self.assertEqual(len(rooms), 2)
        self.assertEqual(
            sorted(room.area_mm2 for room in rooms),
            [12_000_000.0, 12_000_000.0],
        )

    def test_crossing_partitions_form_four_rooms(self):
        document = DraftDocument()
        for start, end in (
            ((0, 0), (6000, 0)),
            ((6000, 0), (6000, 4000)),
            ((6000, 4000), (0, 4000)),
            ((0, 4000), (0, 0)),
            ((3000, 0), (3000, 4000)),
            ((0, 2000), (6000, 2000)),
        ):
            add_wall(document, start, end)
        rooms = document.recognised_rooms()
        self.assertEqual(len(rooms), 4)
        self.assertTrue(all(
            abs(room.area_mm2 - 6_000_000.0) < 1e-6
            for room in rooms
        ))


class WallWindowRailingTests(unittest.TestCase):
    def test_window_is_attached_and_reports_both_distances(self):
        document = DraftDocument()
        wall = add_wall(document, (0, 0), (6000, 0))
        window = document.add_window(
            wall.id,
            2070.0,
            width_mm=1800.0,
            sill_height_mm=600.0,
            height_mm=2400.0,
        )
        self.assertEqual(window.offset_mm, 2070.0)
        self.assertEqual(wall.length_mm - window.end_offset_mm, 2130.0)

    def test_overlapping_window_is_rejected(self):
        document = DraftDocument()
        wall = add_wall(document, (0, 0), (6000, 0))
        document.add_window(
            wall.id, 1000.0, width_mm=1800.0,
            sill_height_mm=600.0, height_mm=1800.0,
        )
        with self.assertRaisesRegex(ValueError, "重叠"):
            document.add_window(
                wall.id, 2000.0, width_mm=1500.0,
                sill_height_mm=600.0, height_mm=1800.0,
            )

    def test_document_round_trip_preserves_entities(self):
        document = DraftDocument()
        wall = add_wall(document, (0, 0), (6000, 0))
        document.add_window(
            wall.id, 1200.0, width_mm=1800.0,
            sill_height_mm=600.0, height_mm=2400.0,
        )
        document.add_railing(
            Point(0, -1500),
            Point(6000, -1500),
            height_mm=1100.0,
            material="玻璃栏杆",
            width_mm=60.0,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.bplan.json"
            document.save(path)
            loaded = DraftDocument.load(path)
        self.assertEqual(len(loaded.walls), 1)
        self.assertEqual(len(loaded.windows), 1)
        self.assertEqual(len(loaded.railings), 1)
        self.assertEqual(loaded.railings[0].material, "玻璃栏杆")

    def test_dimension_chain_round_trip(self):
        document = DraftDocument()
        document.add_dimension(
            [Point(0, 0), Point(900, 0), Point(3200, 0)],
            offset_mm=-600,
        )
        restored = DraftDocument.from_dict(document.to_dict())
        self.assertEqual(len(restored.dimensions), 1)
        self.assertEqual(len(restored.dimensions[0].points), 3)
        self.assertAlmostEqual(restored.dimensions[0].offset_mm, -600)


class LineAndEditTests(unittest.TestCase):
    def test_line_round_trip(self):
        document = DraftDocument()
        document.add_line(Point(0, 0), Point(3200, 1800))
        restored = DraftDocument.from_dict(document.to_dict())
        self.assertEqual(len(restored.lines), 1)
        self.assertAlmostEqual(restored.lines[0].end.x, 3200)

    def test_move_wall_keeps_window_attached(self):
        document = DraftDocument()
        wall = add_wall(document, (0, 0), (6000, 0))
        window = document.add_window(
            wall.id, 1200, width_mm=1800,
            sill_height_mm=600, height_mm=2400,
        )
        moved = document.move_entities({wall.id}, 500, 900)
        self.assertEqual(moved, {wall.id})
        self.assertEqual(wall.start, Point(500, 900))
        self.assertEqual(window.wall_id, wall.id)
        self.assertAlmostEqual(window.offset_mm, 1200)

    def test_copy_wall_also_copies_hosted_window(self):
        document = DraftDocument()
        wall = add_wall(document, (0, 0), (6000, 0))
        document.add_window(
            wall.id, 1200, width_mm=1800,
            sill_height_mm=600, height_mm=2400,
        )
        created = document.copy_entities({wall.id}, 0, 4000)
        self.assertEqual(len(document.walls), 2)
        self.assertEqual(len(document.windows), 2)
        copied_wall = next(
            item for item in document.walls if item.id in created
        )
        copied_window = next(
            item for item in document.windows if item.wall_id == copied_wall.id
        )
        self.assertAlmostEqual(copied_window.offset_mm, 1200)

    def test_mirror_wall_also_mirrors_hosted_window(self):
        document = DraftDocument()
        wall = add_wall(document, (0, 0), (6000, 0))
        document.add_window(
            wall.id, 1200, width_mm=1800,
            sill_height_mm=600, height_mm=2400,
        )
        created = document.mirror_entities(
            {wall.id},
            Point(8000, -1000),
            Point(8000, 1000),
        )
        self.assertEqual(len(document.walls), 2)
        self.assertEqual(len(document.windows), 2)
        copied_wall = next(
            item for item in document.walls if item.id in created
        )
        self.assertEqual(copied_wall.start, Point(16000, 0))
        self.assertEqual(copied_wall.end, Point(10000, 0))


if __name__ == "__main__":
    unittest.main()
