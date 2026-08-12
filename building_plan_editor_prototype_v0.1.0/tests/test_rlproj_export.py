"""Regression tests for BP -> RoomLight v3.3 project export."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

from model import DraftDocument, Point
from rlproj_export import build_rlproj_data, export_rlproj
from rlproj_import import load_rlproj


ROOMLIGHT_ROOT = (
    Path(__file__).resolve().parents[2]
    / "room_light_v3.3.0"
    / "room_light"
)
if str(ROOMLIGHT_ROOT) not in sys.path:
    sys.path.append(str(ROOMLIGHT_ROOT))

from io_utils.project_io import load_building_project  # noqa: E402


def add_wall(document, start, end):
    return document.add_wall(
        Point(*start),
        Point(*end),
        height_mm=3600.0,
        width_mm=200.0,
        axis="center",
    )


def rectangle_document() -> tuple[DraftDocument, list]:
    document = DraftDocument()
    walls = [
        add_wall(document, (0, 0), (6000, 0)),
        add_wall(document, (6000, 0), (6000, 4000)),
        add_wall(document, (6000, 4000), (0, 4000)),
        add_wall(document, (0, 4000), (0, 0)),
    ]
    return document, walls


class RlprojExportTests(unittest.TestCase):
    def test_closed_room_exports_window_and_railing(self):
        document, walls = rectangle_document()
        document.add_window(
            walls[0].id,
            1200.0,
            width_mm=1800.0,
            sill_height_mm=600.0,
            height_mm=2400.0,
        )
        document.add_railing(
            Point(0, -1200),
            Point(6000, -1200),
            height_mm=1100.0,
            material="玻璃栏杆",
            width_mm=50.0,
        )

        data, summary = build_rlproj_data(
            document,
            project_name="单房间测试",
        )

        self.assertEqual(data["file_version"], "3.3.0")
        self.assertEqual(data["project_kind"], "building")
        self.assertEqual(summary.spaces, 1)
        self.assertEqual(summary.walls, 4)
        self.assertEqual(summary.windows, 1)
        self.assertEqual(summary.railings, 1)
        space = data["building"]["storeys"][0]["spaces"][0]
        segments = space["boundary_loops"][0]["segments"]
        self.assertEqual(sum(len(item["openings"]) for item in segments), 1)
        self.assertEqual(len(space["exterior_barriers"]), 1)
        self.assertAlmostEqual(
            space["exterior_barriers"][0]["visible_transmittance"],
            0.75,
        )

    def test_shared_wall_exports_as_reciprocal_interior_boundary(self):
        document, _walls = rectangle_document()
        add_wall(document, (3000, 0), (3000, 4000))
        original_wall_ids = [wall.id for wall in document.walls]

        data, summary = build_rlproj_data(
            document,
            project_name="双房间测试",
        )

        self.assertEqual(summary.spaces, 2)
        self.assertEqual(
            [wall.id for wall in document.walls],
            original_wall_ids,
        )
        spaces = data["building"]["storeys"][0]["spaces"]
        ids = {space["id"] for space in spaces}
        for space in spaces:
            interior = [
                wall
                for wall in space["boundary_loops"][0]["segments"]
                if wall["boundary_type"] == "interior"
            ]
            self.assertEqual(len(interior), 1)
            self.assertIn(interior[0]["adjacent_space_id"], ids)
            self.assertNotEqual(interior[0]["adjacent_space_id"], space["id"])

    def test_open_plan_is_rejected(self):
        document = DraftDocument()
        add_wall(document, (0, 0), (6000, 0))
        add_wall(document, (6000, 0), (6000, 4000))
        add_wall(document, (6000, 4000), (0, 4000))

        with self.assertRaisesRegex(ValueError, "闭合房间"):
            build_rlproj_data(document, project_name="未闭合")

    def test_export_passes_roomlight_loader_and_bp_round_trip(self):
        document, walls = rectangle_document()
        document.add_window(
            walls[2].id,
            900.0,
            width_mm=1500.0,
            sill_height_mm=600.0,
            height_mm=2400.0,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "roundtrip.rlproj"
            summary = export_rlproj(
                path,
                document,
                project_name="往返测试",
            )
            building, weather, active_space_id, error = (
                load_building_project(str(path))
            )
            imported = load_rlproj(path)
            raw = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(summary.path.suffix, ".rlproj")
        self.assertEqual(error, "")
        self.assertIsNone(weather)
        self.assertEqual(active_space_id, "bp_space_1")
        self.assertEqual(len(building.spaces()), 1)
        self.assertEqual(raw["building"]["name"], "往返测试")
        self.assertEqual(len(imported.document.recognised_rooms()), 1)
        self.assertEqual(len(imported.document.windows), 1)


if __name__ == "__main__":
    unittest.main()
