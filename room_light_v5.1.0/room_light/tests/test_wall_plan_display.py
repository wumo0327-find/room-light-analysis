"""Regression tests for real wall-width display in the v3 building view."""
from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from matplotlib.colors import to_rgba
from matplotlib.figure import Figure

from core.complex_models import (
    BoundaryLoop,
    Point2D,
    SpaceModel,
    StoreyModel,
    WallOpening,
    WallSegment,
)
from ui.complex_space_editor import (
    _draw_storey_plan,
    _segment_plan_polygon,
    _wall_thickness_summary,
)


def rectangle_space(*, thickness_mm: float = 300.0) -> SpaceModel:
    points = [
        Point2D(0, 0),
        Point2D(6000, 0),
        Point2D(6000, 4000),
        Point2D(0, 4000),
    ]
    walls = [
        WallSegment(
            start=point,
            end=points[(index + 1) % len(points)],
            thickness_mm=thickness_mm,
            id=f"wall_{index}",
        )
        for index, point in enumerate(points)
    ]
    walls[0].openings.append(WallOpening(
        id="window_1",
        offset_mm=1500,
        width_mm=1800,
        sill_height_mm=600,
        height_mm=2400,
    ))
    return SpaceModel(
        id="space_1",
        name="墙宽测试",
        boundary_loops=[BoundaryLoop(
            id="loop_1",
            kind="outer",
            segments=walls,
        )],
    )


class WallPlanDisplayTests(unittest.TestCase):
    def test_segment_polygon_uses_physical_millimetre_width(self):
        points = _segment_plan_polygon(
            Point2D(0, 0),
            Point2D(4000, 0),
            240,
        )

        self.assertEqual(len(points), 4)
        self.assertAlmostEqual(
            max(point.y for point in points)
            - min(point.y for point in points),
            240,
        )
        self.assertAlmostEqual(min(point.x for point in points), -120)
        self.assertAlmostEqual(max(point.x for point in points), 4120)

    def test_summary_reports_single_and_mixed_wall_thickness(self):
        space = rectangle_space(thickness_mm=200)
        self.assertEqual(_wall_thickness_summary(space), "200 mm")

        space.outer_loop().segments[1].thickness_mm = 300
        self.assertEqual(
            _wall_thickness_summary(space),
            "200–300 mm（2种）",
        )

    def test_storey_plan_draws_wall_bands_and_window_opening(self):
        space = rectangle_space(thickness_mm=300)
        storey = StoreyModel(
            id="storey_1",
            name="首层",
            spaces=[space],
        )
        figure = Figure()
        axis = figure.add_subplot(111)

        _draw_storey_plan(
            axis,
            storey,
            selected_space_ids=[space.id],
            active_space_id=space.id,
        )

        facecolors = [patch.get_facecolor() for patch in axis.patches]
        active_wall = to_rgba("#fee2e2")
        glazing = to_rgba("#67e8f9")
        self.assertGreaterEqual(
            sum(
                all(abs(a - b) < 1e-6 for a, b in zip(color, active_wall))
                for color in facecolors
            ),
            4,
        )
        self.assertTrue(any(
            all(abs(a - b) < 1e-6 for a, b in zip(color, glazing))
            for color in facecolors
        ))


if __name__ == "__main__":
    unittest.main()
