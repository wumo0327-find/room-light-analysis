"""生成 v4.0.0 复杂空间极限性能测试工程。

本脚本只使用项目正式数据模型和保存接口，保证生成的 rlproj 与程序
实际读写路径一致。重复运行会覆盖同目录下的测试工程。
"""
from __future__ import annotations

from dataclasses import replace
import math
from pathlib import Path
import sys
from typing import Iterable, Sequence

ROOM_LIGHT_ROOT = Path(__file__).resolve().parents[2]
if str(ROOM_LIGHT_ROOT) not in sys.path:
    sys.path.insert(0, str(ROOM_LIGHT_ROOT))

from core.complex_models import (
    BoundaryLoop,
    BuildingModel,
    Point2D,
    SpaceModel,
    StoreyModel,
    WallOpening,
    WallSegment,
)
from core.models import LocationParams, MaterialParams, ShadingDevice, ThermalParams
from core.space_geometry import signed_ring_area_mm2, space_floor_area_mm2, validate_space
from io_utils.project_io import save_building_project
from io_utils.weather_data import default_dataset


OUTPUT_PATH = Path(__file__).with_name("复杂空间极限性能测试.rlproj")


def _points(coords: Iterable[tuple[float, float]]) -> list[Point2D]:
    return [Point2D(float(x), float(y)) for x, y in coords]


def _oriented(
    coords: Sequence[tuple[float, float]],
    kind: str,
) -> list[Point2D]:
    points = _points(coords)
    signed = signed_ring_area_mm2(points)
    should_be_positive = kind == "outer"
    if (signed > 0.0) != should_be_positive:
        points.reverse()
    return points


def _make_loop(
    coords: Sequence[tuple[float, float]],
    *,
    kind: str,
    prefix: str,
    boundary_pattern: Sequence[str] = ("exterior",),
) -> BoundaryLoop:
    points = _oriented(coords, kind)
    segments: list[WallSegment] = []
    for index, start in enumerate(points):
        end = points[(index + 1) % len(points)]
        boundary_type = boundary_pattern[index % len(boundary_pattern)]
        segments.append(WallSegment(
            id=f"{prefix}_wall_{index + 1:02d}",
            name=f"{prefix}墙段{index + 1:02d}",
            start=start,
            end=end,
            boundary_type=boundary_type,
            thickness_mm=(300.0 if boundary_type == "exterior" else 200.0),
            u_value=(0.75 if boundary_type == "exterior" else 1.50),
            metadata={
                "stress_test": True,
                "sequence": index + 1,
                "source": "程序生成的极限测试几何",
            },
        ))
    return BoundaryLoop(
        id=f"{prefix}_loop",
        name=f"{prefix}{'外轮廓' if kind == 'outer' else '内院空洞'}",
        kind=kind,
        segments=segments,
        metadata={"stress_test": True},
    )


def _add_openings(
    space: SpaceModel,
    *,
    max_windows_per_wall: int,
    include_hole_windows: bool = True,
) -> None:
    """在合适墙段上生成不重叠的窗和门，并加入逐窗玻璃参数。"""
    window_number = 0
    door_number = 0
    for loop_index, loop in enumerate(space.boundary_loops):
        if loop.kind == "hole" and not include_hole_windows:
            continue
        for wall_index, wall in enumerate(loop.segments):
            length = wall.length_mm
            if wall.boundary_type in {"exterior", "ground"} and length >= 1800.0:
                margin = 300.0
                gap = 260.0
                usable = length - 2.0 * margin
                requested = max(
                    1,
                    min(max_windows_per_wall, int((usable + gap) // 1760.0)),
                )
                width = min(
                    1800.0,
                    (usable - gap * (requested - 1)) / requested,
                )
                while requested > 1 and width < 850.0:
                    requested -= 1
                    width = min(
                        1800.0,
                        (usable - gap * (requested - 1)) / requested,
                    )
                if width < 700.0:
                    continue
                occupied = requested * width + (requested - 1) * gap
                first_offset = (length - occupied) / 2.0
                for slot in range(requested):
                    window_number += 1
                    sill = 450.0 + 150.0 * ((window_number + wall_index) % 4)
                    height = min(
                        2500.0 - 100.0 * (window_number % 3),
                        space.height_mm - sill - 250.0,
                    )
                    wall.openings.append(WallOpening(
                        id=f"{space.id}_window_{window_number:03d}",
                        name=f"{space.name}外窗{window_number:03d}",
                        kind="window",
                        offset_mm=first_offset + slot * (width + gap),
                        width_mm=width,
                        sill_height_mm=sill,
                        height_mm=height,
                        visible_transmittance=(0.55, 0.62, 0.71, 0.78)[
                            window_number % 4
                        ],
                        u_value=(1.8, 2.1, 2.4, 2.7)[window_number % 4],
                        solar_heat_gain_coefficient=(0.38, 0.45, 0.52, 0.60)[
                            window_number % 4
                        ],
                        metadata={
                            "glass_type": (
                                "三银Low-E",
                                "双银Low-E",
                                "普通中空",
                                "高透中空",
                            )[window_number % 4],
                            "loop_index": loop_index,
                            "wall_index": wall_index,
                        },
                    ))
            elif wall.boundary_type == "interior" and length >= 1400.0:
                door_number += 1
                width = min(1200.0, length - 400.0)
                wall.openings.append(WallOpening(
                    id=f"{space.id}_door_{door_number:03d}",
                    name=f"{space.name}内门{door_number:03d}",
                    kind="door",
                    offset_mm=(length - width) / 2.0,
                    width_mm=width,
                    sill_height_mm=0.0,
                    height_mm=min(2400.0, space.height_mm - 200.0),
                    visible_transmittance=0.0,
                    metadata={"door_type": "测试用内门"},
                ))


def _make_space(
    *,
    space_id: str,
    name: str,
    outer: Sequence[tuple[float, float]],
    holes: Sequence[Sequence[tuple[float, float]]] = (),
    height_mm: float,
    elevation_mm: float,
    boundary_pattern: Sequence[str] = ("exterior",),
    max_windows_per_wall: int = 2,
    include_hole_windows: bool = True,
    material: MaterialParams | None = None,
    thermal: ThermalParams | None = None,
    shading: ShadingDevice | None = None,
    purpose: str = "几何与性能压力测试",
) -> SpaceModel:
    loops = [
        _make_loop(
            outer,
            kind="outer",
            prefix=f"{space_id}_outer",
            boundary_pattern=boundary_pattern,
        )
    ]
    for index, hole in enumerate(holes, start=1):
        loops.append(_make_loop(
            hole,
            kind="hole",
            prefix=f"{space_id}_hole_{index:02d}",
            boundary_pattern=("exterior",),
        ))
    space = SpaceModel(
        id=space_id,
        name=name,
        height_mm=height_mm,
        floor_elevation_mm=elevation_mm,
        boundary_loops=loops,
        material=material or MaterialParams(),
        thermal=thermal or ThermalParams(),
        shading=shading or ShadingDevice(),
        metadata={
            "stress_test": True,
            "purpose": purpose,
            "geometry_note": "仅用于软件极限与回归测试，不代表真实建筑设计",
        },
    )
    _add_openings(
        space,
        max_windows_per_wall=max_windows_per_wall,
        include_hole_windows=include_hole_windows,
    )
    return space


def _translate(
    coords: Sequence[tuple[float, float]],
    dx: float,
    dy: float,
) -> list[tuple[float, float]]:
    return [(x + dx, y + dy) for x, y in coords]


def _regular_polygon(
    cx: float,
    cy: float,
    radius: float,
    count: int,
    rotation_deg: float = 0.0,
) -> list[tuple[float, float]]:
    return [
        (
            cx + radius * math.cos(math.radians(rotation_deg + i * 360.0 / count)),
            cy + radius * math.sin(math.radians(rotation_deg + i * 360.0 / count)),
        )
        for i in range(count)
    ]


def build_stress_building() -> tuple[BuildingModel, str]:
    rich_material = MaterialParams(
        rho_wall=0.58,
        rho_ceiling=0.82,
        rho_floor=0.24,
        rho_ground=0.22,
    )
    heavy_thermal = ThermalParams(
        U_wall=0.72,
        U_roof=0.48,
        U_floor=0.65,
        U_win=2.10,
        wall_thickness_mm=300.0,
        wall_density=1900.0,
        wall_specific_c=1050.0,
        wall_solar_abs=0.55,
        SC_glass=0.52,
        eta_frame=0.72,
        q_people=8.0,
        q_equipment=7.0,
        q_lighting=5.0,
        n_ach=0.8,
        psi_edge=0.08,
    )
    active_shading = ShadingDevice(
        type="horizontal_overhang",
        overhang_depth_mm=1200.0,
        overhang_height_mm=150.0,
        overhang_tilt_deg=105.0,
        diffuse_residual=0.30,
        vertical_fin_enabled=True,
        vertical_fin_depth_mm=650.0,
        fin_column_width_mm=420.0,
    )

    active = _make_space(
        space_id="space_extreme_active",
        name="极限综合测试空间（默认计算）",
        outer=[
            (0, 0), (28000, 0), (28000, 4000), (24000, 4000),
            (24000, 7000), (30000, 7000), (30000, 13000),
            (26000, 13000), (26000, 18000), (19000, 18000),
            (19000, 22000), (12000, 22000), (12000, 19000),
            (7000, 19000), (7000, 24000), (0, 24000),
            (0, 16000), (3000, 16000), (3000, 12000),
            (-3000, 12000), (-3000, 6000), (0, 6000),
        ],
        holes=[
            [(4000, 4000), (4000, 8500), (8500, 8500), (8500, 4000)],
            [
                (14500, 9500), (14500, 14500), (18000, 14500),
                (18000, 12000), (21500, 12000), (21500, 9500),
            ],
        ],
        height_mm=4200.0,
        elevation_mm=0.0,
        boundary_pattern=(
            "exterior", "exterior", "interior", "exterior",
            "adiabatic", "exterior", "exterior", "ground",
        ),
        max_windows_per_wall=3,
        material=rich_material,
        thermal=heavy_thermal,
        shading=active_shading,
        purpose="默认活动空间；重点压测复杂遮挡、密集外窗、采光积分和参数化遮阳实验",
    )

    active_windows = [
        opening
        for wall in active.wall_segments()
        for opening in wall.windows()
    ]
    for index, opening in enumerate(active_windows):
        if index % 3 == 0:
            active.shading.overhang_overrides[opening.id] = {
                "depth_mm": 600.0 + 150.0 * (index % 7),
                "gap_mm": 50.0 * (index % 5),
                "tilt_deg": 75.0 + 5.0 * (index % 10),
            }
        if index % 4 == 0:
            active.shading.fin_overrides[f"{opening.id}:L"] = (
                300.0 + 100.0 * (index % 6)
            )
        if index % 5 == 0:
            active.shading.fin_overrides[f"{opening.id}:R"] = (
                350.0 + 80.0 * (index % 7)
            )

    u_shape = _make_space(
        space_id="space_u_shape",
        name="U形连通大厅",
        outer=_translate([
            (0, 0), (16000, 0), (16000, 14000), (11000, 14000),
            (11000, 5000), (5000, 5000), (5000, 14000), (0, 14000),
        ], 38000, 0),
        height_mm=5400.0,
        elevation_mm=0.0,
        boundary_pattern=("exterior", "exterior", "interior", "exterior"),
        max_windows_per_wall=3,
        shading=replace(active_shading, overhang_depth_mm=900.0),
        purpose="压测深凹口、长短墙混合及高大空间",
    )
    trapezoid = _make_space(
        space_id="space_trapezoid",
        name="斜墙梯形报告厅",
        outer=[(58000, 0), (76000, 2500), (73000, 15000), (60000, 13000)],
        height_mm=7200.0,
        elevation_mm=0.0,
        max_windows_per_wall=3,
        purpose="压测非正交墙体、连续方位角热工与超高空间",
    )
    ring = _make_space(
        space_id="space_ring",
        name="双内院环形展厅",
        outer=[(80000, 0), (104000, 0), (106000, 15000), (82000, 17000)],
        holes=[
            [(85000, 4000), (85000, 10500), (91000, 10500), (91000, 4000)],
            [(96000, 5000), (96000, 12000), (102000, 11500), (101500, 4500)],
        ],
        height_mm=4800.0,
        elevation_mm=0.0,
        max_windows_per_wall=3,
        purpose="压测多个内院空洞、内院窗和斜向边界",
    )

    serrated = _make_space(
        space_id="space_serrated",
        name="锯齿形开放工作室",
        outer=[
            (0, 0), (5000, 0), (6500, 1800), (8000, 0),
            (9500, 1800), (11000, 0), (16000, 0), (16000, 12000),
            (12000, 12000), (10500, 10200), (9000, 12000),
            (7500, 10200), (6000, 12000), (0, 12000),
        ],
        height_mm=3900.0,
        elevation_mm=4500.0,
        boundary_pattern=("exterior", "exterior", "adiabatic"),
        max_windows_per_wall=2,
        purpose="压测大量短斜墙和锯齿边界",
    )
    circular = _make_space(
        space_id="space_24_gon",
        name="24边形多功能厅",
        outer=_regular_polygon(30000, 8000, 8000, 24, 7.5),
        height_mm=6500.0,
        elevation_mm=4500.0,
        max_windows_per_wall=1,
        purpose="以24边形近似圆弧，压测密集斜墙方位",
    )
    cross = _make_space(
        space_id="space_cross",
        name="十字形共享空间",
        outer=_translate([
            (4000, 0), (10000, 0), (10000, 4000), (14000, 4000),
            (14000, 10000), (10000, 10000), (10000, 14000),
            (4000, 14000), (4000, 10000), (0, 10000),
            (0, 4000), (4000, 4000),
        ], 45000, 0),
        height_mm=4200.0,
        elevation_mm=4500.0,
        boundary_pattern=("exterior", "interior", "exterior", "ground"),
        max_windows_per_wall=2,
        purpose="压测多凹角、混合边界和十字形网格裁切",
    )
    zigzag = _make_space(
        space_id="space_zigzag",
        name="狭长折线画廊",
        outer=[
            (65000, 0), (85000, 0), (85000, 3000), (78000, 3000),
            (78000, 5000), (87000, 5000), (87000, 9000),
            (72000, 9000), (72000, 7000), (65000, 7000),
        ],
        height_mm=3600.0,
        elevation_mm=4500.0,
        boundary_pattern=("exterior", "adiabatic", "interior"),
        max_windows_per_wall=2,
        purpose="压测狭长空间、重复折返边界和窄区域采样",
    )

    stepped = _make_space(
        space_id="space_stepped",
        name="阶梯退台会议中心",
        outer=[
            (0, 0), (18000, 0), (18000, 4000), (15000, 4000),
            (15000, 7500), (12000, 7500), (12000, 11000),
            (9000, 11000), (9000, 14500), (0, 14500),
        ],
        height_mm=4000.0,
        elevation_mm=9000.0,
        max_windows_per_wall=3,
        purpose="压测多级退台、不同墙长和连续凹角",
    )
    octagon = _make_space(
        space_id="space_octagon",
        name="不规则八角教室",
        outer=[
            (26000, 1000), (34000, 0), (40000, 5000), (39000, 12000),
            (33000, 15000), (26000, 13000), (23000, 8000), (24000, 3000),
        ],
        height_mm=3800.0,
        elevation_mm=9000.0,
        max_windows_per_wall=2,
        purpose="压测不规则凸多边形及八向外窗",
    )
    l_shape = _make_space(
        space_id="space_l_shape",
        name="L形复式阅读区",
        outer=[
            (46000, 0), (62000, 0), (62000, 6000),
            (54000, 6000), (54000, 15000), (46000, 15000),
        ],
        height_mm=5800.0,
        elevation_mm=9000.0,
        boundary_pattern=("exterior", "exterior", "interior", "exterior"),
        max_windows_per_wall=3,
        purpose="压测单凹角、复式层高与局部内墙",
    )
    slanted_courtyard = _make_space(
        space_id="space_slanted_courtyard",
        name="斜边内院实验室",
        outer=[
            (68000, 1000), (90000, 0), (94000, 10000),
            (86000, 17000), (70000, 14500), (65000, 7000),
        ],
        holes=[
            [(73500, 4500), (72500, 9500), (79500, 12000), (81500, 5500)],
        ],
        height_mm=4600.0,
        elevation_mm=9000.0,
        max_windows_per_wall=3,
        purpose="压测全斜边外轮廓、旋转内院和斜向遮阳投影",
    )

    building = BuildingModel(
        id="building_extreme_stress_v310",
        name="复杂空间极限性能测试建筑",
        north_angle_deg=23.5,
        location=LocationParams(
            latitude=28.2282,
            longitude=112.9388,
            timezone=8,
            orientation_deg=23.5,
        ),
        storeys=[
            StoreyModel(
                id="storey_ground",
                name="首层｜超大空间与多内院",
                elevation_mm=0.0,
                default_height_mm=4200.0,
                spaces=[active, u_shape, trapezoid, ring],
                metadata={"stress_test": True, "index": 1},
            ),
            StoreyModel(
                id="storey_second",
                name="二层｜密集折线与多边形",
                elevation_mm=4500.0,
                default_height_mm=3900.0,
                spaces=[serrated, circular, cross, zigzag],
                metadata={"stress_test": True, "index": 2},
            ),
            StoreyModel(
                id="storey_third",
                name="三层｜退台、斜边与高空间",
                elevation_mm=9000.0,
                default_height_mm=4200.0,
                spaces=[stepped, octagon, l_shape, slanted_courtyard],
                metadata={"stress_test": True, "index": 3},
            ),
        ],
        metadata={
            "generator": Path(__file__).name,
            "target_version": "4.0.0",
            "stress_test": True,
            "warning": "测试工程，不可作为真实建筑设计或工程计算结论",
        },
    )
    return building, active.id


def _statistics(building: BuildingModel) -> dict[str, float]:
    spaces = building.spaces()
    walls = [wall for space in spaces for wall in space.wall_segments()]
    openings = [opening for wall in walls for opening in wall.openings]
    return {
        "storeys": len(building.storeys),
        "spaces": len(spaces),
        "loops": sum(len(space.boundary_loops) for space in spaces),
        "walls": len(walls),
        "openings": len(openings),
        "windows": sum(opening.kind == "window" for opening in openings),
        "doors": sum(opening.kind == "door" for opening in openings),
        "floor_area_m2": sum(
            space_floor_area_mm2(space) / 1_000_000.0 for space in spaces
        ),
    }


def main() -> None:
    building, active_space_id = build_stress_building()
    errors = []
    warnings = []
    for space in building.spaces():
        for issue in validate_space(space):
            (errors if issue.severity == "error" else warnings).append(
                (space.name, issue.code, issue.message)
            )
    if errors:
        formatted = "\n".join(f"{name}: {code} - {message}" for name, code, message in errors)
        raise RuntimeError(f"生成模型未通过几何校验：\n{formatted}")

    weather = default_dataset()
    weather.location = "湖南长沙（极限性能测试）"
    weather.source = "基于程序默认益阳TMY；仅用于软件性能与回归测试"
    save_building_project(
        str(OUTPUT_PATH),
        building,
        weather=weather,
        active_space_id=active_space_id,
    )

    stats = _statistics(building)
    print(f"已生成：{OUTPUT_PATH.resolve()}")
    print(f"默认活动空间：{active_space_id}")
    print(
        "规模："
        f"{int(stats['storeys'])}层，{int(stats['spaces'])}空间，"
        f"{int(stats['loops'])}个闭合环，{int(stats['walls'])}段墙，"
        f"{int(stats['windows'])}扇窗，{int(stats['doors'])}樘门，"
        f"总楼面面积{stats['floor_area_m2']:.1f}平方米"
    )
    print(f"几何错误：0；方向类警告：{len(warnings)}")


if __name__ == "__main__":
    main()
