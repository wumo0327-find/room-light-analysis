"""Generate an irregular wall/window/railing demo and interface screenshot."""
from __future__ import annotations

import os
from pathlib import Path

from model import DraftDocument, Point


HERE = Path(__file__).resolve().parent
DEMO_FILE = HERE / "demo_activity_plan.bplan.json"
SCREENSHOT = HERE / "prototype_interface.png"
WINDOW_SNAP_SCREENSHOT = HERE / "prototype_window_snap.png"
DIMENSION_SCREENSHOT = HERE / "prototype_dimensions.png"
ELEVATION_SCREENSHOT = HERE / "prototype_elevation.png"
WALL_PALETTE_SCREENSHOT = HERE / "prototype_wall_palette.png"
RLPROJ_SCREENSHOT = HERE / "prototype_rlproj_import.png"
JOIN_SCREENSHOT = HERE / "prototype_wall_join.png"
OCCLUSION_SCREENSHOT = HERE / "prototype_elevation_occlusion.png"
U_SHAPE_ELEVATIONS_SCREENSHOT = (
    HERE / "prototype_u_shape_four_elevations.png"
)


def build_demo() -> DraftDocument:
    document = DraftDocument(grid_mm=100.0, snap_mm=20.0)

    activity = [
        Point(0, 0),
        Point(6000, 0),
        Point(6000, 6600),
        Point(4800, 6600),
        Point(4800, 7200),
        Point(3200, 7200),
        Point(3200, 6600),
        Point(0, 6600),
    ]
    wall_by_pair = {}

    def add_wall(first: Point, second: Point, width: float = 200.0):
        key = tuple(sorted(((first.x, first.y), (second.x, second.y))))
        if key in wall_by_pair:
            return wall_by_pair[key]
        wall = document.add_wall(
            first,
            second,
            height_mm=3600.0,
            width_mm=width,
            axis="center",
        )
        wall_by_pair[key] = wall
        return wall

    # Irregular activity room: the north wall steps into the cloakroom zone.
    for index, point in enumerate(activity):
        add_wall(point, activity[(index + 1) % len(activity)])

    # Cloakroom/service zone sharing the complete stepped activity-room wall.
    add_wall(Point(0, 6600), Point(0, 9000))
    add_wall(Point(0, 9000), Point(6000, 9000))
    add_wall(Point(6000, 9000), Point(6000, 6600))

    # Adjacent sleeping room sharing the activity room's full east wall.
    sleep_south = add_wall(Point(6000, 0), Point(12800, 0), 240.0)
    add_wall(Point(12800, 0), Point(12800, 6600), 240.0)
    add_wall(Point(12800, 6600), Point(6000, 6600), 200.0)

    activity_south = wall_by_pair[
        tuple(sorted(((0, 0), (6000, 0))))
    ]
    for offset, width in ((750, 900), (1850, 2300), (4350, 900)):
        document.add_window(
            activity_south.id,
            offset,
            width_mm=width,
            sill_height_mm=600.0,
            height_mm=2400.0,
        )
    document.add_window(
        sleep_south.id,
        650.0,
        width_mm=5500.0,
        sill_height_mm=600.0,
        height_mm=2400.0,
    )

    # Balcony railing around the adjacent sleeping-room façade.
    document.add_railing(
        Point(6000, -1500),
        Point(12800, -1500),
        height_mm=1100.0,
        width_mm=50.0,
        material="金属栏杆",
    )
    document.add_railing(
        Point(6000, 0),
        Point(6000, -1500),
        height_mm=1100.0,
        width_mm=50.0,
        material="金属栏杆",
    )
    document.add_railing(
        Point(12800, -1500),
        Point(12800, 0),
        height_mm=1100.0,
        width_mm=50.0,
        material="金属栏杆",
    )
    return document


def main() -> None:
    document = build_demo()
    document.save(DEMO_FILE)
    rooms = document.recognised_rooms()
    if len(rooms) != 3:
        raise RuntimeError(f"演示图应识别3个房间，实际为{len(rooms)}。")

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    from font_utils import install_chinese_font
    from main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    install_chinese_font(app)
    window = MainWindow()
    window.resize(1500, 900)
    window.canvas.set_document(document)
    window.current_file = DEMO_FILE
    window._update_title()
    window.show()
    app.processEvents()
    window.canvas.fit_document()
    app.processEvents()
    window.grab().save(str(SCREENSHOT), "PNG")

    # Capture the MC command's real-time wall snapping and end-distance display.
    activity_south = document.walls[0]
    window.canvas.start_window(
        {
            "sill_height_mm": 600.0,
            "height_mm": 2400.0,
            "width_mm": 1800.0,
        }
    )
    offset = 2070.0
    window.canvas._window_preview = (activity_south, offset)
    window.canvas._snap_world = activity_south.point_at(
        offset + window.canvas.window_params["width_mm"] / 2.0
    )
    window.canvas.update()
    app.processEvents()
    window.grab().save(str(WINDOW_SNAP_SCREENSHOT), "PNG")

    window.canvas.finish_command(quiet=True)
    document.add_dimension(
        [
            Point(0, 0),
            Point(750, 0),
            Point(1650, 0),
            Point(1850, 0),
            Point(4150, 0),
            Point(4350, 0),
            Point(5250, 0),
            Point(6000, 0),
        ],
        offset_mm=-850.0,
    )
    window.canvas.set_document(document)
    window.canvas.fit_document()
    app.processEvents()
    window.grab().save(str(DIMENSION_SCREENSHOT), "PNG")

    north_index = window.view_combo.findData("north")
    window.view_combo.setCurrentIndex(north_index)
    app.processEvents()
    window.grab().save(str(ELEVATION_SCREENSHOT), "PNG")

    window.wall_command()
    app.processEvents()
    window.wall_palette.grab().save(str(WALL_PALETTE_SCREENSHOT), "PNG")
    window.wall_palette.hide()

    rlproj_path = (
        HERE.parent
        / "room_light_v3.3.0"
        / "room_light"
        / "examples"
        / "长沙幼儿园二层精细模型"
        / "长沙幼儿园_二层活动室精细模型.rlproj"
    )
    if rlproj_path.exists():
        from rlproj_import import load_rlproj

        imported = load_rlproj(rlproj_path)
        window.canvas.set_document(imported.document)
        window.view_combo.setCurrentIndex(
            window.view_combo.findData("plan")
        )
        window.canvas.fit_document()
        app.processEvents()
        window.grab().save(str(RLPROJ_SCREENSHOT), "PNG")

    join_document = DraftDocument(grid_mm=100.0, snap_mm=20.0)
    for start, end in (
        (Point(0, 0), Point(3000, 0)),
        (Point(3000, 0), Point(6000, 0)),
        (Point(6000, 0), Point(6000, 3200)),
        (Point(3000, 0), Point(3000, -2500)),
        (Point(6000, 1800), Point(8200, 1800)),
    ):
        join_document.add_wall(
            start,
            end,
            height_mm=3000,
            width_mm=240,
            axis="center",
        )
    for start, end in (
        (Point(0, -3800), Point(3000, -3800)),
        (Point(3000, -3800), Point(6000, -3800)),
        (Point(6000, -3800), Point(6000, -5500)),
    ):
        join_document.add_railing(
            start,
            end,
            height_mm=1100,
            width_mm=80,
            material="金属栏杆",
        )
    window.canvas.set_document(join_document)
    window.view_combo.setCurrentIndex(window.view_combo.findData("plan"))
    window.canvas.fit_document()
    app.processEvents()
    window.grab().save(str(JOIN_SCREENSHOT), "PNG")

    occlusion_document = DraftDocument(grid_mm=100.0, snap_mm=20.0)
    far_wall = occlusion_document.add_wall(
        Point(0, 0),
        Point(8000, 0),
        height_mm=3600,
        width_mm=240,
        axis="center",
    )
    for offset, width in ((500, 1200), (3250, 1500), (6500, 1000)):
        occlusion_document.add_window(
            far_wall.id,
            offset,
            width_mm=width,
            sill_height_mm=600,
            height_mm=2400,
        )
    occlusion_document.add_wall(
        Point(2000, 3000),
        Point(6000, 3000),
        height_mm=3600,
        width_mm=240,
        axis="center",
    )
    window.canvas.set_document(occlusion_document)
    window.view_combo.setCurrentIndex(window.view_combo.findData("north"))
    window.canvas.fit_document()
    app.processEvents()
    window.grab().save(str(OCCLUSION_SCREENSHOT), "PNG")

    # Exact regression example: a U-shaped three-wall plan open to the west.
    # The four elevations verify both depth occlusion and exposed wall ends.
    u_shape_document = DraftDocument(grid_mm=100.0, snap_mm=20.0)
    for start, end in (
        (Point(0, 8000), Point(10000, 8000)),
        (Point(10000, 8000), Point(10000, 0)),
        (Point(10000, 0), Point(5000, 0)),
    ):
        u_shape_document.add_wall(
            start,
            end,
            height_mm=3000,
            width_mm=240,
            axis="center",
        )
    window.canvas.set_document(u_shape_document)

    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QColor, QFont, QPainter, QPixmap

    elevation_pixmaps = []
    for view_name, label in (
        ("north", "北立面"),
        ("west", "西立面"),
        ("south", "南立面"),
        ("east", "东立面"),
    ):
        window.view_combo.setCurrentIndex(
            window.view_combo.findData(view_name)
        )
        window.canvas.fit_document()
        app.processEvents()
        captured = window.canvas.grab().scaled(
            700,
            340,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        elevation_pixmaps.append((label, captured))

    sheet = QPixmap(1440, 760)
    sheet.fill(QColor("#ffffff"))
    sheet_painter = QPainter(sheet)
    sheet_painter.setPen(QColor("#1e293b"))
    sheet_painter.setFont(QFont("Microsoft YaHei", 15))
    for index, (label, captured) in enumerate(elevation_pixmaps):
        column = index % 2
        row = index // 2
        x = 20 + column * 710
        y = 15 + row * 375
        sheet_painter.drawText(x, y + 24, label)
        sheet_painter.drawPixmap(x, y + 34, captured)
    sheet_painter.end()
    sheet.save(str(U_SHAPE_ELEVATIONS_SCREENSHOT), "PNG")

    window.close()
    app.quit()
    print(f"demo={DEMO_FILE}")
    print(f"screenshot={SCREENSHOT}")
    print(f"window_snap_screenshot={WINDOW_SNAP_SCREENSHOT}")
    print(f"dimension_screenshot={DIMENSION_SCREENSHOT}")
    print(f"elevation_screenshot={ELEVATION_SCREENSHOT}")
    print(f"wall_palette_screenshot={WALL_PALETTE_SCREENSHOT}")
    if rlproj_path.exists():
        print(f"rlproj_screenshot={RLPROJ_SCREENSHOT}")
    print(f"join_screenshot={JOIN_SCREENSHOT}")
    print(f"occlusion_screenshot={OCCLUSION_SCREENSHOT}")
    print(
        "u_shape_elevations_screenshot="
        f"{U_SHAPE_ELEVATIONS_SCREENSHOT}"
    )
    print(
        f"walls={len(document.walls)}, windows={len(document.windows)}, "
        f"railings={len(document.railings)}, rooms={len(rooms)}"
    )


if __name__ == "__main__":
    main()
