import unittest
from pathlib import Path
import tempfile

from bp_editor.model import DraftDocument, Point
from core.legacy_adapter import building_from_room
from core.models import RoomModel
from core.space_geometry import has_geometry_errors, validate_space
from io_utils.bp_bridge import (
    BP_DRAFT_METADATA_KEY,
    building_from_document,
    document_from_building,
)
from io_utils.project_io import load_building_project, save_building_project


class EmbeddedBpIntegrationTests(unittest.TestCase):
    def _document(self) -> DraftDocument:
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
                width_mm=240.0,
                axis="center",
            )
            for index in range(4)
        ]
        document.add_window(
            walls[0].id,
            1000.0,
            width_mm=2400.0,
            sill_height_mm=600.0,
            height_mm=2400.0,
        )
        document.add_railing(
            Point(0.0, -1200.0),
            Point(6000.0, -1200.0),
            height_mm=1100.0,
            material="金属栏杆",
            width_mm=50.0,
        )
        document.add_line(Point(0.0, 5000.0), Point(6000.0, 5000.0))
        document.add_dimension(
            [Point(0.0, 0.0), Point(6000.0, 0.0)],
            offset_mm=800.0,
        )
        return document

    def test_bp_document_converts_to_valid_roomlight_building(self):
        previous = building_from_room(RoomModel())
        previous.location.latitude = 31.25
        previous.get_space("legacy_space").material.rho_wall = 0.63
        building, active_id, summary = building_from_document(
            self._document(),
            previous,
            project_name="内嵌BP测试",
            previous_active_space_id="legacy_space",
        )
        self.assertEqual(summary.spaces, 1)
        self.assertEqual(summary.windows, 1)
        self.assertEqual(summary.railings, 1)
        self.assertIsNotNone(building.get_space(active_id))
        self.assertAlmostEqual(building.location.latitude, 31.25)
        self.assertAlmostEqual(
            building.get_space(active_id).material.rho_wall,
            0.63,
        )
        self.assertFalse(
            has_geometry_errors(validate_space(building.get_space(active_id)))
        )

    def test_exact_bp_draft_is_restored_from_rlproj_metadata(self):
        previous = building_from_room(RoomModel())
        original = self._document()
        building, _active_id, _summary = building_from_document(
            original,
            previous,
            project_name="草图恢复测试",
            previous_active_space_id="legacy_space",
        )
        self.assertIn(BP_DRAFT_METADATA_KEY, building.metadata)
        restored, note = document_from_building(building)
        self.assertEqual(restored.to_dict(), original.to_dict())
        self.assertEqual(len(restored.lines), 1)
        self.assertEqual(len(restored.dimensions), 1)
        self.assertIn("BP原始草图", note)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "embedded_bp.rlproj"
            save_building_project(
                str(path),
                building,
                active_space_id=_active_id,
            )
            loaded, _weather, loaded_active, error = load_building_project(
                str(path)
            )
        self.assertEqual(error, "")
        self.assertEqual(loaded_active, _active_id)
        loaded_document, _loaded_note = document_from_building(loaded)
        self.assertEqual(loaded_document.to_dict(), original.to_dict())

    def test_window_endpoints_resize_width_without_leaving_host_wall(self):
        document = DraftDocument()
        wall = document.add_wall(
            Point(0.0, 0.0),
            Point(6000.0, 0.0),
            height_mm=3600.0,
            width_mm=200.0,
            axis="center",
        )
        window = document.add_window(
            wall.id,
            1000.0,
            width_mm=2000.0,
            sill_height_mm=600.0,
            height_mm=2400.0,
        )

        document.resize_window_endpoint(window.id, "start", 500.0)
        self.assertAlmostEqual(window.offset_mm, 500.0)
        self.assertAlmostEqual(window.width_mm, 2500.0)
        self.assertAlmostEqual(window.end_offset_mm, 3000.0)

        document.resize_window_endpoint(window.id, "end", 4200.0)
        self.assertAlmostEqual(window.offset_mm, 500.0)
        self.assertAlmostEqual(window.width_mm, 3700.0)
        self.assertAlmostEqual(window.end_offset_mm, 4200.0)

        with self.assertRaisesRegex(ValueError, "宿主墙范围"):
            document.resize_window_endpoint(window.id, "end", 6500.0)

    def test_window_endpoint_resize_rejects_overlap(self):
        document = DraftDocument()
        wall = document.add_wall(
            Point(0.0, 0.0),
            Point(6000.0, 0.0),
            height_mm=3600.0,
            width_mm=200.0,
            axis="center",
        )
        first = document.add_window(
            wall.id,
            500.0,
            width_mm=1200.0,
            sill_height_mm=600.0,
            height_mm=1800.0,
        )
        document.add_window(
            wall.id,
            2500.0,
            width_mm=1200.0,
            sill_height_mm=600.0,
            height_mm=1800.0,
        )
        with self.assertRaisesRegex(ValueError, "重叠"):
            document.resize_window_endpoint(first.id, "end", 2600.0)
        self.assertAlmostEqual(first.width_mm, 1200.0)


if __name__ == "__main__":
    unittest.main()
