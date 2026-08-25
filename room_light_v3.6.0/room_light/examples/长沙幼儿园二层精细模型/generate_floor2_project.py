"""Generate the audited Changsha-kindergarten second-floor v3.3 project.

The source is the user-annotated vector PDF in this directory.  Yellow linework
defines walls, muted green linework defines windows and magenta linework
defines railings.  Horizontal dimensions are transcribed from the explicit
dimension chains instead of inferred from raster pixels.

Plan geometry is authoritative.  Vertical window/railing properties are kept
as explicit assumptions because this floor-plan sheet contains no window
schedule or railing elevation.
"""
from __future__ import annotations

from pathlib import Path
import sys
from typing import Iterable, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.complex_models import (  # noqa: E402
    BoundaryLoop,
    BuildingModel,
    ExteriorBarrier,
    Point2D,
    SpaceModel,
    StoreyModel,
    WallOpening,
    WallSegment,
)
from core.models import LocationParams, MaterialParams, ThermalParams  # noqa: E402
from core.space_geometry import space_floor_area_mm2, validate_space  # noqa: E402
from io_utils.project_io import (  # noqa: E402
    load_building_project,
    save_building_project,
)
from io_utils.weather_data import default_dataset  # noqa: E402


HERE = Path(__file__).resolve().parent
OUTPUT_PROJECT = HERE / "长沙幼儿园_二层活动室精细模型.rlproj"
OUTPUT_PREVIEW = HERE / "长沙幼儿园_二层活动室精细模型预览.png"
OUTPUT_COMPARISON = HERE / "长沙幼儿园_二层CAD与计算模型对照.png"

SIDE_INDEX = {"south": 0, "east": 1, "north": 2, "west": 3}
PLAN_SOURCE = "长沙幼儿园二层标注原图.pdf"
CAD_SOURCE = (
    HERE.parent
    / "长沙幼儿园CAD转换"
    / "长沙幼儿园原始图纸.dxf"
)
CAD_X_ORIGIN = -990_086.231
CAD_Y_ORIGIN = 742_500.135
CAD_FLOOR2_BOUNDS = (
    -990_000.0,
    -943_500.0,
    741_500.0,
    772_500.0,
)
PDF_X_ORIGIN_PT = 212.24
PDF_Y_ORIGIN_BOTTOM_PT = 226.8
PDF_SCALE_X_MM_PER_PT = 45_200.0 / 742.8
PDF_SCALE_Y_MM_PER_PT = 28_200.0 / (690.24 - 226.8)
PLAN_SCALE_NOTE = (
    "PDF尺寸链与DXF二层WALL/WINDOW矢量交叉核对；"
    "总宽45200 mm；活动室折线外墙和退进窗按CAD原坐标"
)
VERTICAL_ASSUMPTION = (
    "窗台600 mm、窗高2400 mm、栏杆高1100 mm及栏杆有效开敞率0.65"
    "为待门窗表/立面复核参数；不属于本平面图已确认尺寸"
)


def _read_dxf_pairs(path: Path):
    lines = path.read_text(encoding="gbk", errors="replace").splitlines()
    return [
        (lines[index].strip(), lines[index + 1].strip())
        for index in range(0, len(lines) - 1, 2)
    ]


def _dxf_entities(pairs):
    in_entities = False
    section_pending = False
    current = None
    for code, value in pairs:
        if code == "0" and value == "SECTION":
            section_pending = True
            continue
        if section_pending and code == "2":
            in_entities = value == "ENTITIES"
            section_pending = False
            continue
        if code == "0" and value == "ENDSEC":
            if current is not None:
                yield current
                current = None
            in_entities = False
            continue
        if not in_entities:
            continue
        if code == "0":
            if current is not None:
                yield current
            current = {"type": value, "pairs": []}
        elif current is not None:
            current["pairs"].append((code, value))
    if current is not None:
        yield current


def _dxf_first(pairs, code, default=""):
    return next((value for item_code, value in pairs if item_code == code), default)


def _dxf_path(entity):
    pairs = entity["pairs"]
    if entity["type"] == "LINE":
        values = {code: value for code, value in pairs}
        try:
            return [
                (float(values["10"]), float(values["20"])),
                (float(values["11"]), float(values["21"])),
            ]
        except (KeyError, ValueError):
            return []
    if entity["type"] != "LWPOLYLINE":
        return []
    vertices = []
    pending_x = None
    for code, value in pairs:
        if code == "10":
            pending_x = float(value)
        elif code == "20" and pending_x is not None:
            vertices.append((pending_x, float(value)))
            pending_x = None
    closed = int(float(_dxf_first(pairs, "70", "0"))) & 1
    if closed and vertices:
        vertices.append(vertices[0])
    return vertices


def _cad_point(x: float, y: float) -> Point2D:
    """Convert the audited second-floor CAD coordinates to local millimetres."""
    return Point2D(
        round(x - CAD_X_ORIGIN, 3),
        round(y - CAD_Y_ORIGIN, 3),
    )


def read_cad_reference() -> dict:
    """Preserve the exact floor-2 WALL/WINDOW/COLUMN vector paths in rlproj."""
    x_min, x_max, y_min, y_max = CAD_FLOOR2_BOUNDS
    paths = {"WALL": [], "WINDOW": [], "COLUMN": []}
    for entity in _dxf_entities(_read_dxf_pairs(CAD_SOURCE)):
        layer = _dxf_first(entity["pairs"], "8", "")
        canonical = (
            "WINDOW"
            if layer in {"WINDOW", "窗-外框", "窗-内框分割"}
            else layer
        )
        if canonical not in paths:
            continue
        path = _dxf_path(entity)
        if len(path) < 2 or not any(
            x_min <= x <= x_max and y_min <= y <= y_max
            for x, y in path
        ):
            continue
        local = [
            [round(x - CAD_X_ORIGIN, 3), round(y - CAD_Y_ORIGIN, 3)]
            for x, y in path
            if (
                x_min - 500 <= x <= x_max + 500
                and y_min - 500 <= y <= y_max + 500
            )
        ]
        if len(local) >= 2:
            paths[canonical].append(local)
    return {
        "source": str(CAD_SOURCE.name),
        "coordinate_unit": "mm",
        "coordinate_transform": {
            "local_x": f"cad_x - ({CAD_X_ORIGIN})",
            "local_y": f"cad_y - ({CAD_Y_ORIGIN})",
        },
        "floor_bounds_cad": list(CAD_FLOOR2_BOUNDS),
        "paths": paths,
        "path_counts": {
            layer: len(items) for layer, items in paths.items()
        },
    }


def read_pdf_reference() -> dict:
    """
    Preserve the annotated PDF as an independent recovery layer.

    The DWG/DXF is useful but not treated as the only authority: yellow walls,
    muted-green windows, magenta railings and cyan glazed canopies are retained
    from the user-annotated PDF in local millimetres.
    """
    import pdfplumber

    source = HERE / "长沙幼儿园二层标注原图.pdf"
    colour_layers = {
        (1.0, 1.0, 0.0): "WALL",
        (0.45098, 0.72157, 0.36078): "WINDOW",
        (1.0, 0.0, 1.0): "RAILING_OR_CANOPY",
        (0.49804, 1.0, 1.0): "GLAZED_CANOPY",
    }
    paths = {layer: [] for layer in colour_layers.values()}

    def local_point(x: float, y_bottom: float) -> list[float]:
        return [
            round(
                (float(x) - PDF_X_ORIGIN_PT) * PDF_SCALE_X_MM_PER_PT,
                3,
            ),
            round(
                (float(y_bottom) - PDF_Y_ORIGIN_BOTTOM_PT)
                * PDF_SCALE_Y_MM_PER_PT,
                3,
            ),
        ]

    with pdfplumber.open(source) as document:
        page = document.pages[0]
        for line in page.lines:
            layer = colour_layers.get(line.get("stroking_color"))
            if layer is None:
                continue
            paths[layer].append([
                local_point(line["x0"], line["y0"]),
                local_point(line["x1"], line["y1"]),
            ])
        for curve in page.curves:
            layer = colour_layers.get(curve.get("stroking_color"))
            if layer is None:
                continue
            points = [
                local_point(x, float(page.height) - top)
                for x, top in curve.get("pts", [])
            ]
            if len(points) >= 2:
                paths[layer].append(points)

    return {
        "source": source.name,
        "coordinate_unit": "mm",
        "role": (
            "CAD损坏/图层缺失时的独立纠错与补全层；"
            "黄色=墙，灰绿色=窗，粉色=栏杆或雨棚边界，青色=玻璃雨棚"
        ),
        "coordinate_transform": {
            "local_x": (
                f"(pdf_x - {PDF_X_ORIGIN_PT}) * "
                f"{PDF_SCALE_X_MM_PER_PT}"
            ),
            "local_y": (
                f"(pdf_y_bottom - {PDF_Y_ORIGIN_BOTTOM_PT}) * "
                f"{PDF_SCALE_Y_MM_PER_PT}"
            ),
        },
        "paths": paths,
        "path_counts": {
            layer: len(items) for layer, items in paths.items()
        },
    }


def audit_source_pdf() -> dict:
    """Reject a replaced/misread PDF before regenerating the project."""
    import pdfplumber

    source = HERE / "长沙幼儿园二层标注原图.pdf"
    with pdfplumber.open(source) as document:
        if len(document.pages) != 1:
            raise ValueError("二层标注PDF必须只有一页。")
        page = document.pages[0]
        colour_counts = {
            colour: sum(
                line.get("stroking_color") == colour
                for line in page.lines
            )
            for colour in (
                (1.0, 1.0, 0.0),
                (0.0, 1.0, 0.0),
                (1.0, 0.0, 1.0),
                (0.45098, 0.72157, 0.36078),
            )
        }
        expected_counts = (373, 230, 78, 204)
        actual_counts = tuple(colour_counts.values())
        if actual_counts != expected_counts:
            raise ValueError(
                "PDF矢量颜色/线段数量与已审核原图不一致："
                f"{actual_counts} != {expected_counts}"
            )
        bright_green_horizontal = [
            abs(float(line["x1"]) - float(line["x0"]))
            for line in page.lines
            if line.get("stroking_color") == (0.0, 1.0, 0.0)
            and abs(float(line["y1"]) - float(line["y0"])) < 1e-8
        ]
        longest_dimension_pt = max(bright_green_horizontal)
        if abs(longest_dimension_pt - 742.8) > 0.02:
            raise ValueError("未找到45200 mm总尺寸线。")

        magenta_horizontal = [
            abs(float(line["x1"]) - float(line["x0"]))
            for line in page.lines
            if line.get("stroking_color") == (1.0, 0.0, 1.0)
            and abs(float(line["y1"]) - float(line["y0"])) < 1e-8
        ]
        railing_lines = [
            length for length in magenta_horizontal
            if 45.9 <= length <= 46.1
        ]
        if len(railing_lines) < 4:
            raise ValueError("未稳定识别两处2800 mm双线栏杆。")

        window_horizontal = [
            abs(float(line["x1"]) - float(line["x0"]))
            for line in page.lines
            if line.get("stroking_color")
            == (0.45098, 0.72157, 0.36078)
            and abs(float(line["y1"]) - float(line["y0"])) < 1e-8
            and 205.0 <= float(line["y0"]) <= 230.0
        ]
        narrow = sum(14.70 <= length <= 14.95 for length in window_horizontal)
        wide = sum(37.70 <= length <= 37.90 for length in window_horizontal)
        if narrow < 24 or wide < 12:
            raise ValueError("900/2300 mm活动室窗组线未完整识别。")
    return {
        "page_count": 1,
        "yellow_wall_lines": actual_counts[0],
        "green_dimension_lines": actual_counts[1],
        "magenta_railing_lines": actual_counts[2],
        "muted_green_window_lines": actual_counts[3],
        "mm_per_pdf_point": 45200.0 / longest_dimension_pt,
        "activity_window_narrow_traces": narrow,
        "activity_window_wide_traces": wide,
    }


def _space(
    room_id: str,
    name: str,
    bounds: tuple[float, float, float, float],
    exterior_sides: Iterable[str] = (),
    *,
    height_mm: float = 3600.0,
) -> SpaceModel:
    x0, y0, x1, y1 = bounds
    points = [
        Point2D(x0, y0),
        Point2D(x1, y0),
        Point2D(x1, y1),
        Point2D(x0, y1),
    ]
    exterior = set(exterior_sides)
    sides = ("south", "east", "north", "west")
    walls = [
        WallSegment(
            id=f"{room_id}_wall_{side}",
            name=f"{name}{side}墙",
            start=points[index],
            end=points[(index + 1) % 4],
            boundary_type="exterior" if side in exterior else "interior",
            thickness_mm=240.0 if side in exterior else 200.0,
            u_value=0.80 if side in exterior else 1.50,
            metadata={
                "source": PLAN_SOURCE,
                "source_colour": "yellow",
                "dimension_basis": PLAN_SCALE_NOTE,
            },
        )
        for index, side in enumerate(sides)
    ]
    return SpaceModel(
        id=room_id,
        name=name,
        height_mm=height_mm,
        boundary_loops=[BoundaryLoop(
            id=f"{room_id}_outer",
            name=f"{name}墙体围合边界",
            kind="outer",
            segments=walls,
            metadata={
                "recognised_as_room": True,
                "source_colour": "yellow",
            },
        )],
        material=MaterialParams(
            rho_wall=0.55,
            rho_ceiling=0.75,
            rho_floor=0.25,
            rho_ground=0.20,
        ),
        thermal=ThermalParams(
            U_wall=0.80,
            U_roof=0.55,
            U_floor=0.75,
            U_win=2.40,
            wall_thickness_mm=240.0,
            SC_glass=0.55,
            eta_frame=0.72,
            q_people=7.0,
            q_equipment=4.0,
            q_lighting=4.0,
            n_ach=0.7,
        ),
        metadata={
            "source_drawing": PLAN_SOURCE,
            "plan_geometry_status": "confirmed_from_annotated_vector_pdf",
            "vertical_geometry_status": "requires_elevation_or_window_schedule",
            "vertical_assumption": VERTICAL_ASSUMPTION,
        },
    )


def _polygon_space(
    room_id: str,
    name: str,
    cad_points: Sequence[tuple[float, float]],
    exterior_segment_indexes: Iterable[int],
    *,
    height_mm: float = 3600.0,
) -> SpaceModel:
    """Build one non-rectangular room directly from audited CAD coordinates."""
    points = [_cad_point(x, y) for x, y in cad_points]
    exterior = set(exterior_segment_indexes)
    walls = []
    for index, start in enumerate(points):
        end = points[(index + 1) % len(points)]
        boundary_type = "exterior" if index in exterior else "interior"
        walls.append(WallSegment(
            id=f"{room_id}_wall_{index + 1}",
            name=f"{name}折线墙{index + 1}",
            start=start,
            end=end,
            boundary_type=boundary_type,
            thickness_mm=240.0 if boundary_type == "exterior" else 200.0,
            u_value=0.80 if boundary_type == "exterior" else 1.50,
            metadata={
                "source": "二层DXF WALL图层",
                "source_cad_start": list(cad_points[index]),
                "source_cad_end": list(
                    cad_points[(index + 1) % len(points)]
                ),
                "geometry_status": "exact_from_cad_vector",
            },
        ))
    return SpaceModel(
        id=room_id,
        name=name,
        height_mm=height_mm,
        boundary_loops=[BoundaryLoop(
            id=f"{room_id}_outer",
            name=f"{name}CAD折线围合边界",
            kind="outer",
            segments=walls,
            metadata={
                "recognised_as_room": True,
                "source": "二层DXF WALL图层",
                "geometry_status": "exact_from_cad_vector",
            },
        )],
        material=MaterialParams(
            rho_wall=0.55,
            rho_ceiling=0.75,
            rho_floor=0.25,
            rho_ground=0.20,
        ),
        thermal=ThermalParams(
            U_wall=0.80,
            U_roof=0.55,
            U_floor=0.75,
            U_win=2.40,
            wall_thickness_mm=240.0,
            SC_glass=0.55,
            eta_frame=0.72,
            q_people=7.0,
            q_equipment=4.0,
            q_lighting=4.0,
            n_ach=0.7,
        ),
        metadata={
            "source_drawing": PLAN_SOURCE,
            "source_cad": CAD_SOURCE.name,
            "plan_geometry_status": "exact_from_cad_vector",
            "vertical_geometry_status": "requires_elevation_or_window_schedule",
            "vertical_assumption": VERTICAL_ASSUMPTION,
        },
    )


def _add_segment_opening(
    space: SpaceModel,
    segment_index: int,
    *,
    opening_id: str,
    name: str,
    kind: str,
    offset_mm: float,
    width_mm: float,
    sill_mm: float = 600.0,
    height_mm: float = 2400.0,
) -> None:
    wall = space.outer_loop().segments[segment_index]
    wall.openings.append(WallOpening(
        id=opening_id,
        name=name,
        kind=kind,
        offset_mm=offset_mm,
        width_mm=width_mm,
        sill_height_mm=0.0 if kind == "door" else sill_mm,
        height_mm=min(space.height_mm, height_mm),
        visible_transmittance=0.0 if kind == "door" else 0.70,
        u_value=None if kind == "door" else 2.40,
        solar_heat_gain_coefficient=None if kind == "door" else 0.55,
        metadata={
            "source": (
                "二层DXF WALL断口"
                if kind == "door"
                else "二层DXF WINDOW图层"
            ),
            "plan_geometry_status": "exact_from_cad_vector",
            "vertical_dimension_status": "assumed_pending_schedule",
            "vertical_assumption": VERTICAL_ASSUMPTION,
        },
    ))


def _exact_activity_spaces() -> list[SpaceModel]:
    """Three activity rooms with their real stepped south façades."""
    activity_1 = _polygon_space(
        "activity_1",
        "活动室1",
        (
            (-983086.231, 742700.135),
            (-982286.231, 742700.135),
            (-978686.231, 742700.135),
            (-978686.231, 743300.135),
            (-977586.231, 743300.135),
            (-977586.231, 742700.135),
            (-976986.231, 742700.135),
            (-976986.231, 744000.135),
            (-977026.231, 744240.135),
            (-977026.231, 750600.135),
            (-983024.555, 750600.135),
            (-983024.555, 744000.135),
            (-983086.231, 744000.135),
        ),
        range(0, 9),
    )
    _add_segment_opening(
        activity_1, 1,
        opening_id="activity_1_window_left",
        name="活动室1南窗左900",
        kind="window",
        offset_mm=0.0,
        width_mm=900.0,
    )
    _add_segment_opening(
        activity_1, 1,
        opening_id="activity_1_window_centre",
        name="活动室1南窗中2300",
        kind="window",
        offset_mm=1100.0,
        width_mm=2300.0,
    )
    _add_segment_opening(
        activity_1, 3,
        opening_id="activity_1_window_right",
        name="活动室1南窗右900",
        kind="window",
        offset_mm=0.0,
        width_mm=900.0,
    )

    activity_2 = _polygon_space(
        "activity_2",
        "活动室2",
        (
            (-967486.231, 743170.135),
            (-967186.231, 743170.135),
            (-967186.231, 743300.135),
            (-965886.231, 743300.135),
            (-965886.231, 742700.135),
            (-963386.231, 742700.135),
            (-963386.231, 743300.135),
            (-962286.231, 743300.135),
            (-962286.231, 742700.135),
            (-961686.231, 742700.135),
            (-961686.231, 744000.135),
            (-961726.231, 744000.135),
            (-961726.231, 750600.135),
            (-967486.231, 750600.135),
        ),
        range(0, 12),
    )
    _add_segment_opening(
        activity_2, 2,
        opening_id="activity_2_window_left",
        name="活动室2南窗左900",
        kind="window",
        offset_mm=200.0,
        width_mm=900.0,
    )
    _add_segment_opening(
        activity_2, 4,
        opening_id="activity_2_window_centre",
        name="活动室2南窗中2300",
        kind="window",
        offset_mm=0.0,
        width_mm=2300.0,
    )
    _add_segment_opening(
        activity_2, 6,
        opening_id="activity_2_window_right",
        name="活动室2南窗右900",
        kind="window",
        offset_mm=0.0,
        width_mm=900.0,
    )

    activity_3 = _polygon_space(
        "activity_3",
        "活动室3",
        (
            (-958486.231, 742700.135),
            (-957886.231, 742700.135),
            (-957886.231, 743300.135),
            (-956586.231, 743300.135),
            (-956586.231, 742700.135),
            (-954086.231, 742700.135),
            (-954086.231, 743300.135),
            (-952986.231, 743300.135),
            (-952986.231, 742700.135),
            (-952386.231, 742700.135),
            (-952386.231, 744000.135),
            (-952446.231, 744000.135),
            (-952446.231, 750600.135),
            (-958446.231, 750600.135),
            (-958446.231, 744240.135),
            (-958486.231, 744000.135),
        ),
        range(0, 12),
    )
    _add_segment_opening(
        activity_3, 2,
        opening_id="activity_3_window_left",
        name="活动室3南窗左900",
        kind="window",
        offset_mm=200.0,
        width_mm=900.0,
    )
    _add_segment_opening(
        activity_3, 4,
        opening_id="activity_3_window_centre",
        name="活动室3南窗中2300",
        kind="window",
        offset_mm=0.0,
        width_mm=2300.0,
    )
    _add_segment_opening(
        activity_3, 6,
        opening_id="activity_3_window_right",
        name="活动室3南窗右900",
        kind="window",
        offset_mm=0.0,
        width_mm=900.0,
    )
    return [activity_1, activity_2, activity_3]


def _add_windows(
    space: SpaceModel,
    side: str,
    windows: Sequence[tuple[float, float]],
    *,
    plane_offset_mm: float = 0.0,
    sill_mm: float = 600.0,
    height_mm: float = 2400.0,
    tau: float = 0.70,
) -> None:
    wall = space.outer_loop().segments[SIDE_INDEX[side]]
    for index, (offset_mm, width_mm) in enumerate(windows, start=1):
        wall.openings.append(WallOpening(
            id=f"{space.id}_{side}_window_{index}",
            name=f"{space.name}{side}窗{index}",
            kind="window",
            offset_mm=offset_mm,
            width_mm=width_mm,
            sill_height_mm=sill_mm,
            height_mm=height_mm,
            visible_transmittance=tau,
            u_value=2.40,
            solar_heat_gain_coefficient=0.55,
            plane_offset_mm=plane_offset_mm,
            metadata={
                "source": PLAN_SOURCE,
                "source_colour": "green",
                "plan_width_status": "confirmed",
                "plane_offset_status": (
                    "confirmed_1500mm_dimension"
                    if plane_offset_mm else "on_host_wall"
                ),
                "vertical_dimension_status": "assumed_pending_schedule",
                "vertical_assumption": VERTICAL_ASSUMPTION,
                "glass": "普通中空玻璃占位，待门窗表复核",
            },
        ))


def _barrier(
    barrier_id: str,
    name: str,
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    kind: str,
    transmittance: float,
    ray_scope: str,
    source_class: str = "pdf_manual_reconstruction",
) -> ExteriorBarrier:
    return ExteriorBarrier(
        id=barrier_id,
        name=name,
        kind=kind,
        start=Point2D(*start),
        end=Point2D(*end),
        bottom_height_mm=0.0,
        top_height_mm=1100.0 if kind == "railing" else 3600.0,
        visible_transmittance=transmittance,
        ray_scope=ray_scope,
        metadata={
            "source": PLAN_SOURCE,
            "source_colour": "magenta" if kind == "railing" else "yellow",
            "plan_geometry_status": "confirmed",
            "source_class": source_class,
            "analysis_roles": [
                "daylight_ray_obstruction",
                "thermal_solar_obstruction_reference",
            ],
            "thermal_solver_status": (
                "几何已识别并保存；当前月度热模型尚未对任意栏杆逐时投影，"
                "不得把本字段误解为已经完成栏杆太阳遮挡精算"
            ),
            "vertical_dimension_status": "assumed_pending_elevation",
            "vertical_assumption": VERTICAL_ASSUMPTION,
        },
    )


def _polyline_barriers(
    target: list[ExteriorBarrier],
    prefix: str,
    name: str,
    points: Sequence[tuple[float, float]],
    *,
    transmittance: float = 0.65,
) -> None:
    for index, (start, end) in enumerate(
        zip(points, points[1:]),
        start=1,
    ):
        target.append(_barrier(
            f"{prefix}_{index}",
            f"{name}{index}",
            start,
            end,
            kind="railing",
            transmittance=transmittance,
            ray_scope="all",
        ))


def _pdf_supplemental_barriers() -> list[ExteriorBarrier]:
    """
    De-duplicated computational centrelines recovered from the annotated PDF.

    The raw double/triple linework remains in ``pdf_reference``.  Only one
    effective line per physical railing is emitted here, preventing one railing
    from multiplying its optical attenuation two or three times.
    """
    barriers: list[ExteriorBarrier] = []
    for barrier_id, name, start, end in (
        (
            "south_railing_gap_1",
            "一二单元间南侧栏杆",
            (13048.9, 671.8),
            (15852.9, 671.8),
        ),
        (
            "south_railing_gap_2",
            "二三单元间南侧栏杆",
            (28354.1, 671.8),
            (31150.8, 671.8),
        ),
    ):
        barriers.append(_barrier(
            barrier_id,
            name,
            start,
            end,
            kind="railing",
            transmittance=0.65,
            ray_scope="all",
        ))

    # South-west and south-east balconies: three sides of each U-shaped guard.
    _polyline_barriers(
        barriers,
        "west_balcony_railing",
        "西侧寝室阳台栏杆",
        ((6550.0, -803.2), (-803.2, -803.2),
         (-803.2, 7499.1), (547.7, 7499.1)),
    )
    _polyline_barriers(
        barriers,
        "east_balcony_railing",
        "东侧寝室阳台栏杆",
        ((37649.6, -803.2), (45002.8, -803.2),
         (45002.8, 7499.1), (43652.0, 7499.1)),
    )

    # The courtyard railing was drawn with several parallel outlines.  Collapse
    # the outlines to one rectangle and retain the two diagonal guard runs.
    _polyline_barriers(
        barriers,
        "courtyard_railing",
        "内院四周栏杆",
        ((21453.6, 11945.9), (35050.1, 11945.9),
         (35050.1, 19247.9), (21453.6, 19247.9),
         (21453.6, 11945.9)),
    )
    _polyline_barriers(
        barriers,
        "courtyard_diagonal_railing",
        "内院斜向栏杆",
        ((21453.6, 11945.9), (22088.9, 18181.8),
         (35050.1, 19247.9)),
    )

    # Long narrow guarded opening/planter beside the audio-activity room.
    _polyline_barriers(
        barriers,
        "audio_side_railing",
        "音体活动室旁围护栏杆",
        ((8762.5, 11745.0), (17437.4, 11745.0),
         (17437.4, 13545.1), (8762.5, 13545.1),
         (8762.5, 11745.0)),
    )
    _polyline_barriers(
        barriers,
        "audio_side_diagonal_railing",
        "音体活动室旁斜向栏杆",
        ((9003.5, 11945.9), (9244.5, 13289.5),
         (17203.7, 13545.1)),
    )

    # Stair handrails are represented by their effective centre paths rather
    # than every tread/parallel outline visible in the PDF.
    _polyline_barriers(
        barriers,
        "west_stair_handrail",
        "西楼梯扶手",
        ((5020.0, 11705.0), (5020.0, 15115.0)),
    )
    _polyline_barriers(
        barriers,
        "west_stair_diagonal_handrail",
        "西楼梯斜扶手",
        ((6550.0, 13720.3), (5885.5, 13318.7),
         (5776.0, 13391.7), (5900.1, 13187.3),
         (5783.3, 13253.0), (5118.8, 12858.7)),
    )
    _polyline_barriers(
        barriers,
        "east_stair_handrail",
        "东楼梯扶手",
        ((42301.1, 11690.4), (42301.1, 15005.4)),
    )
    _polyline_barriers(
        barriers,
        "east_stair_diagonal_handrail",
        "东楼梯斜扶手",
        ((40913.7, 13296.8), (41424.8, 12544.7),
         (41359.1, 12427.9), (41556.3, 12566.6),
         (41497.8, 12449.8), (42009.0, 11697.7)),
    )

    # Guard/edge around the entrance void and its sloping run.
    _polyline_barriers(
        barriers,
        "entrance_void_railing",
        "门厅上空及楼梯栏杆",
        ((37299.1, 21343.5), (37299.1, 15246.4),
         (38284.9, 21153.7), (43155.4, 21599.1)),
    )
    return barriers


def build_floor2() -> tuple[BuildingModel, list[str]]:
    """Build floor-2 rooms and preserve the full audited CAD vector reference."""
    specs = [
        # South living rooms.  The three activity rooms are appended below as
        # exact CAD polygons because their façades are not rectangles.
        ("sleep_1", "寝室1", (800, 1500, 6800, 8100), {"south", "west"}),
        ("sleep_2", "寝室2", (16100, 1500, 22100, 8100), {"south"}),
        ("sleep_3", "寝室3", (37640, 1500, 44200, 8100), {"south", "east"}),
        # 2.4 m sanitary/cloakroom band immediately north of the living rooms.
        ("shower_1", "淋浴间1", (800, 8100, 2600, 10500), {"west"}),
        ("toilet_1", "卫生间1", (2600, 8100, 5000, 10500), set()),
        ("wash_1", "盥洗间1", (5000, 8100, 6800, 10500), set()),
        ("cloak_1", "衣帽间1", (6800, 8100, 12800, 10500), set()),
        ("shower_2", "淋浴间2", (16100, 8100, 17900, 10500), set()),
        ("toilet_2", "卫生间2", (17900, 8100, 20300, 10500), set()),
        ("wash_2", "盥洗间2", (20300, 8100, 22100, 10500), set()),
        ("cloak_2", "衣帽间2", (22100, 8100, 28100, 10500), set()),
        ("cloak_3", "衣帽间3", (31400, 8100, 37400, 10500), set()),
        ("shower_3", "淋浴间3", (37400, 8100, 39200, 10500), set()),
        ("toilet_3", "卫生间3", (39200, 8100, 41600, 10500), set()),
        ("wash_3", "盥洗间3", (41600, 8100, 44200, 10500), {"east"}),
        # Continuous 1.9 m public corridor north of the service-room band.
        ("south_corridor", "二层班级公共走廊", (800, 10500, 44200, 12400),
         {"west", "east"}),
        # North and central rooms retained in the same rlproj.  Their full
        # double-line CAD geometry is preserved separately in cad_reference.
        ("audio_activity", "音体活动室", (6800, 14500, 19660, 24900),
         {"west", "north"}),
        ("equipment", "设备间", (19660, 24900, 22960, 27700), {"north"}),
        ("male_toilet", "男卫生间", (22960, 24900, 26260, 29400),
         {"north"}),
        ("female_toilet", "女卫生间", (26260, 24900, 29460, 29400),
         {"north"}),
        ("office_1", "办公室1", (29460, 24900, 35460, 29400), {"north"}),
        ("office_2", "办公室2", (35460, 24900, 39360, 29400), {"north"}),
        ("office_3", "办公室3", (39360, 24900, 44760, 29400),
         {"north", "east"}),
    ]
    spaces = [
        _space(room_id, name, bounds, exterior)
        for room_id, name, bounds, exterior in specs
    ]
    spaces.extend(_exact_activity_spaces())
    by_id = {space.id: space for space in spaces}

    # Adjacent sleeping rooms are retained so the entire south façade is
    # visible and their return walls can be audited beside the activity rooms.
    _add_windows(
        by_id["sleep_1"],
        "south",
        ((400.0, 1500.0), (2250.0, 1500.0), (4100.0, 1500.0)),
        plane_offset_mm=1500.0,
    )
    _add_windows(
        by_id["sleep_2"],
        "south",
        ((750.0, 900.0), (1850.0, 2300.0), (4350.0, 900.0)),
        plane_offset_mm=1500.0,
    )
    _add_windows(
        by_id["sleep_3"],
        "south",
        ((650.0, 1700.0), (2550.0, 1700.0), (4450.0, 1700.0)),
        plane_offset_mm=1500.0,
    )
    _add_windows(
        by_id["sleep_1"], "west", ((900.0, 1800.0), (3600.0, 1800.0)),
        sill_mm=600.0, height_mm=2400.0,
    )
    _add_windows(
        by_id["sleep_3"], "east", ((900.0, 1800.0), (3600.0, 1800.0)),
        sill_mm=600.0, height_mm=2400.0,
    )

    barriers = _pdf_supplemental_barriers()
    # Every selectable room receives the same world-coordinate obstruction
    # layer.  The ray solver only applies a segment when a ray actually
    # intersects it, so this preserves whole-building context without
    # hard-coding a different incomplete list for each room.
    for space in spaces:
        space.exterior_barriers = list(barriers)

    selected = ["activity_1", "activity_2", "activity_3"]
    cad_reference = read_cad_reference()
    pdf_reference = read_pdf_reference()
    building = BuildingModel(
        id="changsha_kindergarten_floor2_audited",
        name="长沙幼儿园｜二层活动室精细模型",
        north_angle_deg=0.0,
        location=LocationParams(
            latitude=28.2282,
            longitude=112.9388,
            timezone=8,
            orientation_deg=0.0,
        ),
        storeys=[StoreyModel(
            id="kindergarten_floor_2",
            name="二层",
            elevation_mm=3600.0,
            default_height_mm=3600.0,
            spaces=spaces,
            metadata={
                "source": PLAN_SOURCE,
                "storey_plan_total_width_mm": 45200.0,
                "storey_plan_total_depth_mm": 28200.0,
                "cad_reference": cad_reference,
                "pdf_reference": pdf_reference,
                "computational_obstruction_count": len(barriers),
            },
        )],
        metadata={
            "source": PLAN_SOURCE,
            "source_cad": CAD_SOURCE.name,
            "selected_space_ids": selected,
            "conversion_method": (
                "从DXF二层WALL/WINDOW矢量读取精确坐标；"
                "三个活动室按墙体内边线建立非矩形闭合多边形；"
                "PDF黄/绿/粉/青矢量作为独立纠错层补全CAD缺失；"
                "门洞全部封闭为连续墙体；"
                "PDF粉线去重后建立会参与采光射线遮挡的栏杆；"
                "完整CAD与PDF路径分别保存在storey.metadata"
            ),
            "confirmed_plan_dimensions": {
                "overall_width_mm": 45200.0,
                "overall_depth_mm": 28200.0,
                "activity_room_width_mm": 6000.0,
                "activity_room_depth_mm": 6600.0,
                "activity_window_widths_mm": [900.0, 2300.0, 900.0],
                "window_mullion_width_mm": 200.0,
                "inter_unit_railing_length_mm": 2800.0,
                "service_band_depth_mm": 2400.0,
                "corridor_depth_mm": 1900.0,
                "activity_1_facade_pattern": (
                    "左900和中2300深退1500；右900浅退约900"
                ),
                "activity_2_facade_pattern": (
                    "左/右900浅退约900；中2300深退1500"
                ),
                "activity_3_facade_pattern": (
                    "左/右900浅退约900；中2300深退1500"
                ),
                "former_activity_3_cloakroom_opening_mm": 3680.0,
                "door_opening_policy": (
                    "本测试模型不保留门洞；CAD/PDF中所有门位置均按连续墙体封闭"
                ),
                "computational_railing_segments": len(barriers),
            },
            "unverified_vertical_parameters": VERTICAL_ASSUMPTION,
            "scope_note": (
                "三个活动室及其退进窗按CAD/PDF精确折线计算；"
                "所有门洞按用户要求替换为墙体；"
                "相邻寝室、盥洗卫生/衣帽带、走廊和北侧房间同时保留；"
                "PDF补全栏杆、楼梯扶手、阳台围护、内院围护和雨棚参考；"
                "内院上空和门厅上空作为非房间留空，不产生虚假计算区"
            ),
        },
    )
    return building, selected


def _render_preview(
    building: BuildingModel,
    selected_space_ids: Sequence[str],
) -> None:
    """Render CAD vectors and the computable model side by side."""
    from PIL import Image, ImageDraw, ImageFont

    width_px, height_px = 3000, 1180
    panel_width = width_px // 2
    image = Image.new("RGB", (width_px, height_px), "white")
    draw = ImageDraw.Draw(image)
    font_path = Path(r"C:\Windows\Fonts\msyh.ttc")
    title_font = ImageFont.truetype(str(font_path), 31)
    body_font = ImageFont.truetype(str(font_path), 19)
    small_font = ImageFont.truetype(str(font_path), 13)

    scale = min(
        (panel_width - 110) / 47_000.0,
        (height_px - 185) / 31_000.0,
    )
    y_origin = height_px - 70.0

    def xy(point: Point2D, panel: int) -> tuple[float, float]:
        return (
            panel * panel_width + 55.0 + point.x * scale,
            y_origin - point.y * scale,
        )

    cad_reference = building.storeys[0].metadata["cad_reference"]
    pdf_reference = building.storeys[0].metadata["pdf_reference"]
    layer_colors = {
        "WALL": "#5b6470",
        "WINDOW": "#38bdf8",
        "COLUMN": "#8b949e",
    }
    for panel in (0, 1):
        for layer, paths in cad_reference["paths"].items():
            color = layer_colors[layer]
            if panel == 1:
                color = {
                    "WALL": "#d5dae0",
                    "WINDOW": "#bae6fd",
                    "COLUMN": "#cbd5e1",
                }[layer]
            for path in paths:
                draw.line(
                    [
                        xy(Point2D(float(x), float(y)), panel)
                        for x, y in path
                    ],
                    fill=color,
                    width=2 if layer != "COLUMN" else 3,
                )

    pdf_layer_colors = {
        "WALL": "#facc15",
        "WINDOW": "#65a30d",
        "RAILING_OR_CANOPY": "#ec4899",
        "GLAZED_CANOPY": "#22d3ee",
    }
    for panel in (0, 1):
        for layer, paths in pdf_reference["paths"].items():
            color = pdf_layer_colors[layer]
            if panel == 1:
                color = {
                    "WALL": "#fef08a",
                    "WINDOW": "#d9f99d",
                    "RAILING_OR_CANOPY": "#fbcfe8",
                    "GLAZED_CANOPY": "#cffafe",
                }[layer]
            for path in paths:
                draw.line(
                    [
                        xy(Point2D(float(x), float(y)), panel)
                        for x, y in path
                    ],
                    fill=color,
                    width=2 if panel == 0 else 1,
                )

    selected = set(selected_space_ids)
    storey = building.storeys[0]
    # Selected-room fill goes above the pale CAD reference in the right panel.
    for space in storey.spaces:
        points = space.outer_loop().points()
        polygon = [xy(point, 1) for point in points]
        if space.id in selected:
            draw.polygon(
                polygon,
                fill="#dbeafe",
                outline="#1d4ed8",
                width=4,
            )
        else:
            draw.line(
                polygon + [polygon[0]],
                fill="#94a3b8",
                width=1,
            )

    # Repaint exact model walls and openings over the selected fills.
    for space in storey.spaces:
        points = space.outer_loop().points()
        for wall in space.wall_segments():
            draw.line(
                [xy(wall.start, 1), xy(wall.end, 1)],
                fill="#eab308" if space.id in selected else "#64748b",
                width=4 if space.id in selected else 1,
            )
            for opening in wall.openings:
                start = wall.point_at(opening.offset_mm)
                end = wall.point_at(opening.end_offset_mm)
                if opening.kind == "door":
                    draw.line(
                        [xy(start, 1), xy(end, 1)],
                        fill="#ffffff",
                        width=9,
                    )
                    draw.line(
                        [xy(start, 1), xy(end, 1)],
                        fill="#84cc16",
                        width=3,
                    )
                    continue
                if opening.plane_offset_mm > 0.0:
                    ox, oy = wall.outward_normal
                    start = Point2D(
                        start.x + ox * opening.plane_offset_mm,
                        start.y + oy * opening.plane_offset_mm,
                    )
                    end = Point2D(
                        end.x + ox * opening.plane_offset_mm,
                        end.y + oy * opening.plane_offset_mm,
                    )
                draw.line(
                    [xy(start, 1), xy(end, 1)],
                    fill="#16a34a",
                    width=7,
                )
        centre_x = sum(point.x for point in points) / len(points)
        centre_y = sum(point.y for point in points) / len(points)
        label = ("★" if space.id in selected else "") + space.name
        anchor = xy(Point2D(centre_x, centre_y), 1)
        draw.text(
            anchor,
            label,
            font=small_font,
            fill="#1e3a8a" if space.id in selected else "#475569",
            anchor="mm",
        )

    unique_barriers = {}
    for space in storey.spaces:
        for barrier in space.exterior_barriers:
            unique_barriers[barrier.id] = barrier
    for barrier in unique_barriers.values():
        draw.line(
            [xy(barrier.start, 1), xy(barrier.end, 1)],
            fill="#ec4899" if barrier.kind == "railing" else "#eab308",
            width=5 if barrier.kind == "railing" else 7,
        )

    draw.line(
        [(panel_width, 0), (panel_width, height_px)],
        fill="#cbd5e1",
        width=2,
    )
    draw.text(
        (55, 18),
        "左：CAD + 标注PDF纠错矢量",
        font=title_font,
        fill="#334155",
    )
    draw.text(
        (panel_width + 55, 18),
        "右：可计算rlproj（叠加CAD/PDF底图）",
        font=title_font,
        fill="#1e3a8a",
    )
    draw.text(
        (panel_width + 55, 60),
        "黄色=墙　绿色=窗　粉色=栏杆/扶手　青色=玻璃雨棚　蓝色=默认分析活动室",
        font=body_font,
        fill="#334155",
    )
    draw.text(
        (panel_width + 55, height_px - 38),
        "所有门洞已封闭为墙；栏杆双线已合并为计算中心线；"
        "窗台/窗高/栏杆高度与雨棚竖向位置仍待立面复核",
        font=small_font,
        fill="#7c2d12",
    )
    image.save(OUTPUT_COMPARISON)
    image.crop((panel_width, 0, width_px, height_px)).save(OUTPUT_PREVIEW)


def main() -> None:
    audit = audit_source_pdf()
    building, selected = build_floor2()
    issues = [
        (space.name, issue)
        for space in building.spaces()
        for issue in validate_space(space)
    ]
    errors = [
        (name, issue)
        for name, issue in issues
        if issue.severity == "error"
    ]
    if errors:
        raise RuntimeError("\n".join(
            f"{name}: {issue.code} {issue.message}"
            for name, issue in errors
        ))
    weather = default_dataset()
    weather.location = "湖南长沙"
    weather.source = "益阳TMY近似长沙；正式论文计算前应导入长沙EPW/TMY"
    save_building_project(
        str(OUTPUT_PROJECT),
        building,
        weather=weather,
        active_space_id=selected[0],
    )
    reloaded, _weather, active_space_id, load_error = load_building_project(
        str(OUTPUT_PROJECT)
    )
    if load_error:
        raise RuntimeError(f"生成后重新读取失败：{load_error}")
    if active_space_id != selected[0] or len(reloaded.spaces()) != 26:
        raise RuntimeError("生成后空间数量或默认活动室不一致。")
    reference = reloaded.storeys[0].metadata.get("cad_reference", {})
    path_counts = reference.get("path_counts", {})
    if not path_counts.get("WALL") or not path_counts.get("WINDOW"):
        raise RuntimeError("生成后CAD墙窗参考路径丢失。")
    pdf_reference = reloaded.storeys[0].metadata.get("pdf_reference", {})
    pdf_path_counts = pdf_reference.get("path_counts", {})
    if (
        not pdf_path_counts.get("WALL")
        or not pdf_path_counts.get("RAILING_OR_CANOPY")
        or not pdf_path_counts.get("GLAZED_CANOPY")
    ):
        raise RuntimeError("生成后PDF墙体/栏杆/玻璃雨棚纠错路径丢失。")
    total_doors = sum(
        sum(opening.kind == "door" for opening in wall.openings)
        for space in reloaded.spaces()
        for wall in space.wall_segments()
    )
    if total_doors:
        raise RuntimeError(
            f"门洞封墙策略失效，重载后仍有{total_doors}个门洞。"
        )
    for room_id in selected:
        space = reloaded.get_space(room_id)
        window_count = sum(
            len(wall.windows()) for wall in space.wall_segments()
        )
        door_count = sum(
            sum(opening.kind == "door" for opening in wall.openings)
            for wall in space.wall_segments()
        )
        if window_count != 3 or door_count != 0:
            raise RuntimeError(
                f"{space.name}重载后应为3组窗+0个门洞，"
                f"实际{window_count}组窗+{door_count}个门洞。"
            )
        if len(space.exterior_barriers) < 20:
            raise RuntimeError(
                f"{space.name}重载后的PDF补全栏杆不完整："
                f"{len(space.exterior_barriers)}段。"
            )
    _render_preview(building, selected)
    windows = sum(
        len(wall.windows())
        for space in building.spaces()
        for wall in space.wall_segments()
    )
    total_area = sum(
        space_floor_area_mm2(space) / 1_000_000.0
        for space in building.spaces()
    )
    print(f"project={OUTPUT_PROJECT}")
    print(f"preview={OUTPUT_PREVIEW}")
    print(f"comparison={OUTPUT_COMPARISON}")
    print(f"pdf_vector_audit={audit}")
    print(f"cad_reference_paths={path_counts}")
    print(f"pdf_reference_paths={pdf_path_counts}")
    print(
        "computational_obstructions="
        f"{reloaded.storeys[0].metadata.get('computational_obstruction_count')}"
    )
    print(
        f"spaces={len(building.spaces())}, windows={windows}, "
        f"doors={total_doors}, area_m2={total_area:.1f}, "
        f"errors=0, issues={len(issues)}"
    )


if __name__ == "__main__":
    main()
