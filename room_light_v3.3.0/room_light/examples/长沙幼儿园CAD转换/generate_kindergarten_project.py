"""由长沙幼儿园首层 CAD 墙窗信息生成可计算的 v3.3.0 工程。

原 DWG 同时包含多层平面、立面、尺寸、家具和天正对象。这里不把所有
CAD 图元直接写入工程，而是依据首层墙/窗图层、44.4m×27.4m 尺寸链和
房间文字进行拓扑清理，只保留闭合房间边界与外窗。
"""
from __future__ import annotations

import math
from pathlib import Path
import sys
from typing import Iterable, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.complex_models import (  # noqa: E402
    BoundaryLoop,
    BuildingModel,
    Point2D,
    SpaceModel,
    StoreyModel,
    WallOpening,
    WallSegment,
)
from core.models import LocationParams, MaterialParams, ThermalParams  # noqa: E402
from core.space_geometry import space_floor_area_mm2, validate_space  # noqa: E402
from io_utils.project_io import save_building_project  # noqa: E402
from io_utils.weather_data import default_dataset  # noqa: E402


HERE = Path(__file__).resolve().parent
OUTPUT_PROJECT = HERE / "长沙幼儿园_首层多房间.rlproj"
OUTPUT_PREVIEW = HERE / "长沙幼儿园_首层房间识别预览.png"

SIDE_INDEX = {"south": 0, "east": 1, "north": 2, "west": 3}


def _room(
    room_id: str,
    name: str,
    bounds: tuple[float, float, float, float],
    exterior_sides: Iterable[str] = (),
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
    side_names = ("south", "east", "north", "west")
    walls = []
    for index, side in enumerate(side_names):
        walls.append(WallSegment(
            id=f"{room_id}_wall_{side}",
            name=f"{name}{side}",
            start=points[index],
            end=points[(index + 1) % 4],
            boundary_type="exterior" if side in exterior else "interior",
            thickness_mm=240.0 if side in exterior else 200.0,
            u_value=0.80 if side in exterior else 1.50,
            metadata={
                "source": "长沙幼儿园首层CAD墙体图层",
                "side": side,
            },
        ))
    return SpaceModel(
        id=room_id,
        name=name,
        height_mm=height_mm,
        floor_elevation_mm=0.0,
        boundary_loops=[BoundaryLoop(
            id=f"{room_id}_outer",
            name=f"{name}闭合边界",
            kind="outer",
            segments=walls,
            metadata={"recognised_as_room": True},
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
            "recognised_as_room": True,
            "source_drawing": "长沙华润住宅-幼儿园首层平面",
            "cad_cleanup": (
                "按墙中心线闭合；吸附小缝隙；忽略门扇、家具、标注、"
                "填充、轴网、立面和其他楼层"
            ),
        },
    )


def _add_even_windows(
    space: SpaceModel,
    side: str,
    count: int,
    *,
    sill_mm: float = 600.0,
    height_mm: float = 2100.0,
    margin_mm: float = 350.0,
    gap_mm: float = 260.0,
    tau: float = 0.70,
) -> None:
    wall = space.outer_loop().segments[SIDE_INDEX[side]]
    available = wall.length_mm - 2.0 * margin_mm
    width = min(1800.0, (available - gap_mm * (count - 1)) / count)
    if width <= 300.0:
        raise ValueError(f"{space.name}{side}墙无法布置{count}扇窗。")
    occupied = width * count + gap_mm * (count - 1)
    first = (wall.length_mm - occupied) / 2.0
    for index in range(count):
        opening_id = f"{space.id}_window_{side}_{index + 1}"
        wall.openings.append(WallOpening(
            id=opening_id,
            name=f"{space.name}外窗{index + 1}",
            kind="window",
            offset_mm=first + index * (width + gap_mm),
            width_mm=width,
            sill_height_mm=sill_mm,
            height_mm=min(
                height_mm,
                space.height_mm - sill_mm - 250.0,
            ),
            visible_transmittance=tau,
            u_value=2.40,
            solar_heat_gain_coefficient=0.55,
            metadata={
                "source": "CAD WINDOW图层，经房间外边界归并",
                "glass": "普通中空玻璃（待按现场门窗表复核）",
            },
        ))


def _segment_key(wall: WallSegment):
    first = (round(wall.start.x, 3), round(wall.start.y, 3))
    second = (round(wall.end.x, 3), round(wall.end.y, 3))
    return tuple(sorted((first, second)))


def _link_shared_walls(spaces: Sequence[SpaceModel]) -> None:
    """Match identical opposite room edges and record room adjacency."""
    owners = {}
    for space in spaces:
        for wall in space.wall_segments():
            owners.setdefault(_segment_key(wall), []).append((space, wall))
    for pairs in owners.values():
        if len(pairs) != 2:
            continue
        (first_space, first_wall), (second_space, second_wall) = pairs
        first_wall.boundary_type = "interior"
        second_wall.boundary_type = "interior"
        first_wall.adjacent_space_id = second_space.id
        second_wall.adjacent_space_id = first_space.id


def build_kindergarten() -> tuple[BuildingModel, list[str]]:
    """Create the cleaned first-floor room topology in millimetres."""
    specs = [
        # 南侧三组幼儿生活单元（CAD尺寸链：6m+6m+3.3m重复）
        ("sleep_1", "寝室1", (0, 0, 6000, 6600), {"south", "west"}),
        ("activity_1", "活动室1", (6000, 0, 12000, 6600), {"south"}),
        ("foyer_1", "衣帽前室1", (12000, 0, 15300, 6600), {"south"}),
        ("sleep_2", "寝室2", (15300, 0, 21300, 6600), {"south"}),
        ("activity_2", "活动室2", (21300, 0, 27300, 6600), {"south"}),
        ("foyer_2", "衣帽前室2", (27300, 0, 30600, 6600), {"south"}),
        ("activity_3", "活动室3", (30600, 0, 36600, 6600), {"south"}),
        ("sleep_3", "寝室3", (36600, 0, 42600, 6600), {"south", "east"}),
        # 各班盥洗、卫生和衣帽空间
        ("shower_1", "淋浴间1", (0, 6600, 1800, 9000), {"west"}),
        ("toilet_1", "卫生间1", (1800, 6600, 4200, 9000), set()),
        ("wash_1", "盥洗间1", (4200, 6600, 6000, 9000), set()),
        ("cloak_1", "衣帽间1", (6000, 6600, 12000, 9000), set()),
        ("shower_2", "淋浴间2", (15300, 6600, 17100, 9000), set()),
        ("toilet_2", "卫生间2", (17100, 6600, 19500, 9000), set()),
        ("wash_2", "盥洗间2", (19500, 6600, 21300, 9000), set()),
        ("cloak_2", "衣帽间2", (21300, 6600, 27300, 9000), set()),
        ("cloak_3", "衣帽间3", (30600, 6600, 36600, 9000), set()),
        ("shower_3", "淋浴间3", (36600, 6600, 38400, 9000), set()),
        ("toilet_3", "卫生间3", (38400, 6600, 40800, 9000), set()),
        ("wash_3", "盥洗间3", (40800, 6600, 42600, 9000), {"east"}),
        # 中部交通与北侧后勤空间；中央15.3m×6.5m留作露天内院
        ("corridor", "公共走廊", (0, 9000, 42600, 11500), {"west", "east"}),
        ("cold_store", "冷库", (0, 11500, 3000, 15000), {"west"}),
        ("staple_store", "主食库", (3000, 11500, 6000, 15000), set()),
        ("secondary_store", "副食库", (6000, 11500, 9000, 15000), set()),
        ("disinfection", "消毒间", (9000, 11500, 12000, 15000), set()),
        ("pantry", "配餐间", (12000, 11500, 15300, 15000), {"east"}),
        ("kitchen", "厨房", (0, 15000, 9000, 22000), {"west", "north"}),
        ("teacher_dining", "教工餐厅", (9000, 15000, 15300, 22000), {"east", "north"}),
        ("lobby", "门厅", (30600, 11500, 42600, 18000), {"west", "east"}),
        ("laundry", "洗衣房", (15300, 22000, 21300, 27400), {"south", "north"}),
        ("staff_wash", "职工盥洗间", (21300, 22000, 24300, 27400), {"north"}),
        ("staff_toilet", "职工卫生间", (24300, 22000, 27300, 27400), {"north"}),
        ("health", "保健室", (30600, 18000, 36600, 24000), {"west", "north"}),
        ("morning_check", "晨检室", (36600, 18000, 42600, 24000), {"east", "north"}),
    ]
    spaces = [
        _room(room_id, name, bounds, exterior)
        for room_id, name, bounds, exterior in specs
    ]
    by_id = {space.id: space for space in spaces}

    # CAD中明确可见的主要外窗。小服务房间只保留能在首层墙窗图层中
    # 与外边界稳定匹配的窗，避免把门扇或立面窗误识别成平面外窗。
    for room_id in (
        "sleep_1", "activity_1", "sleep_2",
        "activity_2", "activity_3", "sleep_3",
    ):
        _add_even_windows(by_id[room_id], "south", 3)
    _add_even_windows(by_id["sleep_1"], "west", 2)
    _add_even_windows(by_id["sleep_3"], "east", 2)
    _add_even_windows(
        by_id["wash_1"], "north", 1,
        sill_mm=1200.0, height_mm=1200.0,
    )
    _add_even_windows(
        by_id["wash_2"], "north", 1,
        sill_mm=1200.0, height_mm=1200.0,
    )
    _add_even_windows(
        by_id["wash_3"], "east", 1,
        sill_mm=1200.0, height_mm=1200.0,
    )
    for room_id, side in (
        ("cold_store", "west"),
        ("kitchen", "west"),
        ("kitchen", "north"),
        ("teacher_dining", "north"),
        ("laundry", "north"),
        ("staff_wash", "north"),
        ("staff_toilet", "north"),
        ("health", "north"),
        ("morning_check", "north"),
        ("morning_check", "east"),
    ):
        _add_even_windows(
            by_id[room_id],
            side,
            1 if "staff" in room_id else 2,
            sill_mm=900.0,
            height_mm=1500.0,
        )

    _link_shared_walls(spaces)
    selected = ["activity_1", "activity_2", "activity_3"]
    building = BuildingModel(
        id="changsha_kindergarten_cad",
        name="长沙幼儿园｜CAD清理模型",
        north_angle_deg=0.0,
        location=LocationParams(
            latitude=28.2282,
            longitude=112.9388,
            timezone=8,
            orientation_deg=0.0,
        ),
        storeys=[StoreyModel(
            id="kindergarten_ground_floor",
            name="首层",
            elevation_mm=0.0,
            default_height_mm=3600.0,
            spaces=spaces,
            metadata={
                "cad_floor_bounds": (
                    "-995000~-940000, 688000~724000"
                ),
            },
        )],
        metadata={
            "source": "0809-长沙华润住宅…幼儿园.dwg（AC1021）",
            "source_dxf": "长沙幼儿园原始图纸.dxf",
            "selected_space_ids": selected,
            "conversion_method": (
                "AutoCAD DXFOUT后按WALL/WINDOW图层和尺寸链人工辅助清理；"
                "闭合空间转为独立SpaceModel；共享边界记录相邻房间"
            ),
            "excluded_cad_content": (
                "门、家具、洁具、楼梯细线、柱填充、轴网、尺寸、文字、"
                "立面、剖面、其他楼层和未能可靠归属房间的短线"
            ),
            "accuracy_note": (
                "适合软件功能和遮阳方案比较；玻璃型号、窗台高度及局部"
                "墙位仍需结合门窗表和现场复核后用于正式论文数据"
            ),
        },
    )
    return building, selected


def render_preview(
    building: BuildingModel,
    selected_space_ids: Sequence[str],
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams["font.sans-serif"] = [
        "Microsoft YaHei",
        "SimHei",
        "Arial Unicode MS",
    ]
    plt.rcParams["axes.unicode_minus"] = False
    selected = set(selected_space_ids)
    figure, axis = plt.subplots(figsize=(16, 10), dpi=180)
    storey = building.storeys[0]
    for space in storey.spaces:
        outer = space.outer_loop()
        points = outer.points()
        xs = [point.x for point in points] + [points[0].x]
        ys = [point.y for point in points] + [points[0].y]
        is_selected = space.id in selected
        axis.fill(
            xs,
            ys,
            facecolor="#bfdbfe" if is_selected else "#f8fafc",
            edgecolor="#2563eb" if is_selected else "#475569",
            linewidth=1.8 if is_selected else 1.0,
            alpha=0.88,
        )
        for wall in space.wall_segments():
            axis.plot(
                [wall.start.x, wall.end.x],
                [wall.start.y, wall.end.y],
                color=(
                    "#111827"
                    if wall.boundary_type == "exterior"
                    else "#64748b"
                ),
                linewidth=2.0 if wall.boundary_type == "exterior" else 0.9,
            )
            for opening in wall.windows():
                start = wall.point_at(opening.offset_mm)
                end = wall.point_at(opening.end_offset_mm)
                axis.plot(
                    [start.x, end.x],
                    [start.y, end.y],
                    color="#06b6d4",
                    linewidth=4.0,
                    solid_capstyle="butt",
                )
        centre_x = sum(point.x for point in points) / len(points)
        centre_y = sum(point.y for point in points) / len(points)
        axis.text(
            centre_x,
            centre_y,
            ("已选 " if is_selected else "") + space.name,
            ha="center",
            va="center",
            fontsize=6.5,
            color="#1e3a8a",
        )
    axis.add_patch(plt.Rectangle(
        (15300, 11500),
        15300,
        10500,
        facecolor="#ecfccb",
        edgecolor="#65a30d",
        hatch="//",
        alpha=0.55,
    ))
    axis.text(22950, 16750, "露天内院（不计算）", ha="center", color="#3f6212")
    axis.set_aspect("equal", adjustable="box")
    axis.set_xlim(-1200, 44400)
    axis.set_ylim(-1200, 28600)
    axis.grid(True, color="#e5e7eb", linewidth=0.35)
    axis.set_xlabel("X / mm")
    axis.set_ylabel("Y / mm")
    axis.set_title(
        "长沙幼儿园首层｜CAD墙体围合房间识别结果\n"
        "蓝色为默认选中的3个活动室，青色为外窗"
    )
    figure.tight_layout()
    figure.savefig(OUTPUT_PREVIEW, bbox_inches="tight")
    plt.close(figure)


def main() -> None:
    building, selected = build_kindergarten()
    issues = [
        (space.name, issue)
        for space in building.spaces()
        for issue in validate_space(space)
    ]
    errors = [
        (name, issue) for name, issue in issues
        if issue.severity == "error"
    ]
    if errors:
        raise RuntimeError("\n".join(
            f"{name}: {issue.code} {issue.message}"
            for name, issue in errors
        ))
    weather = default_dataset()
    weather.location = "湖南长沙"
    weather.source = "益阳TMY近似长沙；正式计算前建议导入长沙EPW/TMY"
    save_building_project(
        str(OUTPUT_PROJECT),
        building,
        weather=weather,
        active_space_id=selected[0],
    )
    render_preview(building, selected)
    windows = sum(
        len(wall.windows())
        for space in building.spaces()
        for wall in space.wall_segments()
    )
    total_area = sum(
        space_floor_area_mm2(space) / 1_000_000.0
        for space in building.spaces()
    )
    print(f"工程：{OUTPUT_PROJECT}")
    print(f"预览：{OUTPUT_PREVIEW}")
    print(
        f"首层识别{len(building.spaces())}个闭合房间，"
        f"{windows}扇外窗，总房间面积{total_area:.1f}平方米，"
        f"几何错误0，警告{len(issues)}。"
    )


if __name__ == "__main__":
    main()
