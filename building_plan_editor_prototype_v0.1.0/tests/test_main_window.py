"""CAD-style global command input and floating-palette behaviour."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from main_window import MainWindow
from model import Point


APP = QApplication.instance() or QApplication([])


class MainWindowInteractionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.window = MainWindow()
        self.window.show()
        self.window.canvas.setFocus()
        APP.processEvents()

    def tearDown(self) -> None:
        self.window.close()
        self.window.deleteLater()
        APP.processEvents()

    def test_global_typing_space_execution_and_palette_switch(self) -> None:
        QTest.keyClicks(self.window.canvas, "hzqt")
        QTest.keyClick(self.window.canvas, Qt.Key.Key_Space)
        self.assertEqual(self.window.canvas.mode, "wall")
        self.assertTrue(self.window.wall_palette.isVisible())

        self.window.canvas._last_point = Point(0, 0)
        self.window.canvas._snap_world = Point(1000, 0)
        QTest.keyClicks(self.window.canvas, "3000")
        QTest.keyClick(self.window.canvas, Qt.Key.Key_Space)
        self.assertEqual(len(self.window.canvas.document.walls), 1)
        self.assertAlmostEqual(
            self.window.canvas.document.walls[0].length_mm,
            3000,
        )

        QTest.keyClicks(self.window.canvas, "mc")
        QTest.keyClick(self.window.canvas, Qt.Key.Key_Space)
        self.assertEqual(self.window.canvas.mode, "window")
        self.assertFalse(self.window.wall_palette.isVisible())
        self.assertTrue(self.window.window_palette.isVisible())

        QTest.keyClick(self.window.canvas, Qt.Key.Key_Escape)
        self.assertEqual(self.window.canvas.mode, "select")
        self.assertFalse(self.window.window_palette.isVisible())

        QTest.keyClick(self.window.canvas, Qt.Key.Key_Space)
        self.assertEqual(self.window.canvas.mode, "window")
        self.assertTrue(self.window.window_palette.isVisible())

    def test_escape_clears_current_selection(self) -> None:
        wall = self.window.canvas.document.add_wall(
            Point(0, 0),
            Point(3000, 0),
            height_mm=3000,
            width_mm=200,
            axis="center",
        )
        self.window.canvas._selected_ids = {wall.id}
        self.window.canvas._selected_id = wall.id
        QTest.keyClick(self.window.canvas, Qt.Key.Key_Escape)
        self.assertEqual(self.window.canvas._selected_ids, set())
        self.assertIsNone(self.window.canvas._selected_id)

    def test_parameter_steps_follow_field_moduli(self) -> None:
        self.assertEqual(self.window.wall_palette.height.singleStep(), 100)
        self.assertEqual(self.window.wall_palette.width.singleStep(), 10)
        self.assertEqual(self.window.window_palette.sill.singleStep(), 100)
        self.assertEqual(self.window.window_palette.width.singleStep(), 100)
        self.assertEqual(self.window.railing_palette.height.singleStep(), 100)
        self.assertEqual(self.window.railing_palette.width.singleStep(), 10)

    def test_new_cad_commands_are_available_from_global_typing(self) -> None:
        for command, expected_mode in (
            ("l", "line"),
            ("m", "move_select"),
            ("co", "copy_select"),
            ("mi", "mirror_select"),
        ):
            QTest.keyClick(self.window.canvas, Qt.Key.Key_Escape)
            QTest.keyClicks(self.window.canvas, command)
            QTest.keyClick(self.window.canvas, Qt.Key.Key_Space)
            self.assertEqual(self.window.canvas.mode, expected_mode)

    def test_double_click_wall_opens_existing_parameter_editor(self) -> None:
        wall = self.window.canvas.document.add_wall(
            Point(0, 0),
            Point(4000, 0),
            height_mm=3600,
            width_mm=200,
            axis="left",
        )
        self.window.canvas.scale = 0.1
        self.window.canvas.origin.setX(180)
        self.window.canvas.origin.setY(350)
        midpoint = self.window.canvas.world_to_screen(
            Point(2000, 0)
        ).toPoint()

        QTest.mouseDClick(
            self.window.canvas,
            Qt.MouseButton.LeftButton,
            pos=midpoint,
        )
        APP.processEvents()

        self.assertTrue(self.window.wall_palette.isVisible())
        self.assertEqual(self.window._editing_entity_id, wall.id)
        self.assertEqual(
            self.window.wall_palette.windowTitle(),
            "编辑墙体参数",
        )
        self.assertEqual(self.window.wall_palette.height.value(), 3600)
        self.assertEqual(self.window.wall_palette.width.value(), 200)
        self.assertEqual(
            self.window.wall_palette.axis.currentData(),
            "left",
        )

        self.window.wall_palette.width.setValue(300)
        APP.processEvents()
        edited = self.window.canvas.document.entity_by_id(
            self.window._editing_entity_id
        )
        self.assertAlmostEqual(edited.width_mm, 300)
        self.assertEqual(
            self.window.canvas._selected_ids,
            {edited.id},
        )

        self.window.wall_palette.hide()
        APP.processEvents()
        self.assertIsNone(self.window._editing_entity_id)

    def test_existing_window_and_railing_parameters_can_be_edited(self) -> None:
        wall = self.window.canvas.document.add_wall(
            Point(0, 0),
            Point(6000, 0),
            height_mm=3600,
            width_mm=200,
            axis="center",
        )
        window = self.window.canvas.document.add_window(
            wall.id,
            1000,
            width_mm=1500,
            sill_height_mm=600,
            height_mm=1800,
        )
        railing = self.window.canvas.document.add_railing(
            Point(0, -1000),
            Point(4000, -1000),
            height_mm=1100,
            width_mm=50,
            material="金属栏杆",
        )

        self.window._edit_existing_entity(window.id)
        self.window.window_palette.sill.setValue(900)
        APP.processEvents()
        self.assertAlmostEqual(window.sill_height_mm, 900)

        self.window._edit_existing_entity(railing.id)
        self.window.railing_palette.material.setCurrentText("玻璃栏杆")
        APP.processEvents()
        self.assertEqual(railing.material, "玻璃栏杆")

    def test_ctrl_1_shows_properties_for_multiple_selection(self) -> None:
        wall = self.window.canvas.document.add_wall(
            Point(0, 0),
            Point(6000, 0),
            height_mm=3600,
            width_mm=240,
            axis="center",
        )
        window = self.window.canvas.document.add_window(
            wall.id,
            1200,
            width_mm=1800,
            sill_height_mm=600,
            height_mm=2400,
        )
        line = self.window.canvas.document.add_line(
            Point(0, -1000),
            Point(3200, -1000),
        )
        self.window.canvas._selected_ids = {
            wall.id,
            window.id,
            line.id,
        }
        self.window.canvas._selected_id = wall.id
        self.window.canvas._notify_selection_changed()

        QTest.keyClick(
            self.window.canvas,
            Qt.Key.Key_1,
            Qt.KeyboardModifier.ControlModifier,
        )
        APP.processEvents()

        palette = self.window.selection_properties
        self.assertTrue(palette.isVisible())
        self.assertIn("当前选择 3 个图元", palette.summary.text())
        self.assertEqual(palette.tree.topLevelItemCount(), 3)
        displayed_types = {
            palette.tree.topLevelItem(index).text(0)
            for index in range(palette.tree.topLevelItemCount())
        }
        self.assertEqual(displayed_types, {"墙体", "窗体", "直线"})

        self.window.canvas._selected_ids = {window.id}
        self.window.canvas._selected_id = window.id
        self.window.canvas._notify_selection_changed()
        APP.processEvents()
        self.assertIn("当前选择 1 个图元", palette.summary.text())
        self.assertEqual(palette.tree.topLevelItemCount(), 1)
        self.assertEqual(palette.tree.topLevelItem(0).text(0), "窗体")

    def test_toolbar_rlproj_export_writes_roomlight_project(self) -> None:
        points = (
            Point(0, 0),
            Point(6000, 0),
            Point(6000, 4000),
            Point(0, 4000),
        )
        for index, start in enumerate(points):
            self.window.canvas.document.add_wall(
                start,
                points[(index + 1) % len(points)],
                height_mm=3600,
                width_mm=200,
                axis="center",
            )

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ui_export.rlproj"
            with patch.object(
                self.window,
                "_hide_parameter_palettes",
            ), patch(
                "main_window.QFileDialog.getSaveFileName",
                return_value=(str(path), "RoomLight工程 (*.rlproj)"),
            ):
                self.window.export_rlproj_project()

            self.assertTrue(path.exists())
            self.assertIn("已导出 ui_export.rlproj", self.window.prompt.text())
            self.assertIn('"project_kind": "building"', path.read_text(
                encoding="utf-8",
            ))


if __name__ == "__main__":
    unittest.main()
