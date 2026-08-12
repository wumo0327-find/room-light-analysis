"""
core/legacy_adapter.py — v2 rectangle RoomModel to v3 complex model adapter.

Keeping this conversion explicit allows the existing UI/calculators to remain
operational while v3 modules are migrated one at a time.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Dict

from core.complex_models import (
    BoundaryLoop,
    BuildingModel,
    Point2D,
    SpaceModel,
    StoreyModel,
    WallOpening,
    WallSegment,
)
from core.models import RoomModel


def building_from_room(
    room: RoomModel,
    building_name: str = "旧版矩形工程",
    storey_name: str = "首层",
    space_name: str = "房间",
) -> BuildingModel:
    """Convert one legacy rectangle into a valid v3 building hierarchy."""
    width = float(room.width)
    length = float(room.length)
    points = [
        Point2D(0.0, 0.0),
        Point2D(width, 0.0),
        Point2D(width, length),
        Point2D(0.0, length),
    ]
    wall_keys = ["south", "east", "north", "west"]
    wall_names = ["南墙", "东墙", "北墙", "西墙"]
    walls = [
        WallSegment(
            start=points[index],
            end=points[(index + 1) % 4],
            boundary_type="exterior",
            thickness_mm=float(room.thermal.wall_thickness_mm),
            u_value=float(room.thermal.U_wall),
            name=wall_names[index],
            id=f"legacy_wall_{wall_keys[index]}",
        )
        for index in range(4)
    ]
    wall_by_key: Dict[str, WallSegment] = dict(zip(wall_keys, walls))

    for window in room.windows:
        wall = wall_by_key[window.wall]
        # The old north direction already matches the v3 CCW loop.  Old east
        # and west local axes run opposite to the corresponding CCW segments.
        if window.wall in {"east", "west"}:
            offset = wall.length_mm - (window.x + window.width)
        else:
            offset = window.x
        wall.openings.append(WallOpening(
            kind="window",
            offset_mm=float(offset),
            width_mm=float(window.width),
            sill_height_mm=float(window.y),
            height_mm=float(window.height),
            visible_transmittance=float(window.tau),
            u_value=float(room.thermal.U_win),
            solar_heat_gain_coefficient=float(room.thermal.SC_glass),
            name=window.label(),
            id=f"legacy_window_{window.id}",
            metadata={
                "legacy_window_id": window.id,
                "legacy_wall": window.wall,
            },
        ))

    space = SpaceModel(
        name=space_name,
        height_mm=float(room.height),
        boundary_loops=[BoundaryLoop(
            segments=walls,
            kind="outer",
            name="房间外边界",
            id="legacy_outer_loop",
        )],
        material=deepcopy(room.material),
        thermal=deepcopy(room.thermal),
        shading=deepcopy(room.shading),
        id="legacy_space",
        metadata={
            "legacy_room_length_mm": length,
            "legacy_room_width_mm": width,
            "legacy_orientation_deg": room.location.orientation_deg,
        },
    )
    storey = StoreyModel(
        name=storey_name,
        elevation_mm=0.0,
        default_height_mm=float(room.height),
        spaces=[space],
        id="legacy_storey",
    )
    return BuildingModel(
        name=building_name,
        north_angle_deg=float(room.location.orientation_deg),
        location=deepcopy(room.location),
        storeys=[storey],
        id="legacy_building",
    )
