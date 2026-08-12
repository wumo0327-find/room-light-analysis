"""
core/complex_models.py — Complex building/space data models  v3.3.0

This module is deliberately independent from Qt and CAD import.  It defines the
geometry contract that both the manual editor and the future CAD/AI importer
must produce before any physical calculation is allowed to run.

All geometry units are millimetres (mm).
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
import uuid
from typing import Dict, Iterable, List, Optional, Tuple

from core.models import (
    LocationParams,
    MaterialParams,
    ShadingDevice,
    ThermalParams,
)


BOUNDARY_TYPES = {"exterior", "interior", "adiabatic", "ground"}
LOOP_KINDS = {"outer", "hole"}
OPENING_KINDS = {"window", "door"}
BARRIER_KINDS = {"wall", "railing", "parapet", "screen"}
BARRIER_RAY_SCOPES = {
    "outside_window",
    "between_point_and_window",
    "all",
}


def new_id(prefix: str) -> str:
    """Return a stable, JSON-friendly identifier for a newly created object."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@dataclass(frozen=True)
class Point2D:
    """Two-dimensional world coordinate in millimetres."""

    x: float
    y: float

    def as_tuple(self) -> Tuple[float, float]:
        return float(self.x), float(self.y)

    def distance_to(self, other: "Point2D") -> float:
        return math.hypot(other.x - self.x, other.y - self.y)


@dataclass
class WallOpening:
    """
    Rectangular opening attached to one wall segment.

    ``offset_mm`` is measured from the wall segment start point along the wall.
    Vertical dimensions are measured from the space floor.
    """

    kind: str = "window"
    offset_mm: float = 0.0
    width_mm: float = 1500.0
    sill_height_mm: float = 900.0
    height_mm: float = 1500.0
    visible_transmittance: float = 0.71
    u_value: Optional[float] = None
    solar_heat_gain_coefficient: Optional[float] = None
    # Physical glazing plane measured outwards from the host wall.  This is
    # used by recessed façades, balconies and covered exterior corridors.
    plane_offset_mm: float = 0.0
    name: str = ""
    id: str = field(default_factory=lambda: new_id("opening"))
    metadata: Dict[str, object] = field(default_factory=dict)

    @property
    def end_offset_mm(self) -> float:
        return self.offset_mm + self.width_mm

    @property
    def head_height_mm(self) -> float:
        return self.sill_height_mm + self.height_mm

    @property
    def area_m2(self) -> float:
        return self.width_mm * self.height_mm / 1_000_000.0


@dataclass
class WallSegment:
    """
    Directed vertical wall segment.

    Boundary loops use a consistent direction: the outer loop is counter-
    clockwise and hole loops are clockwise, so the space interior is always on
    the left side of each segment.
    """

    start: Point2D
    end: Point2D
    boundary_type: str = "exterior"
    thickness_mm: float = 240.0
    u_value: Optional[float] = None
    adjacent_space_id: Optional[str] = None
    name: str = ""
    id: str = field(default_factory=lambda: new_id("wall"))
    openings: List[WallOpening] = field(default_factory=list)
    metadata: Dict[str, object] = field(default_factory=dict)

    @property
    def length_mm(self) -> float:
        return self.start.distance_to(self.end)

    @property
    def direction(self) -> Tuple[float, float]:
        length = self.length_mm
        if length <= 0.0:
            return 0.0, 0.0
        return (
            (self.end.x - self.start.x) / length,
            (self.end.y - self.start.y) / length,
        )

    @property
    def inward_normal(self) -> Tuple[float, float]:
        """Unit normal pointing to the left side of the directed segment."""
        dx, dy = self.direction
        return -dy, dx

    @property
    def outward_normal(self) -> Tuple[float, float]:
        nx, ny = self.inward_normal
        return -nx, -ny

    def point_at(self, offset_mm: float) -> Point2D:
        dx, dy = self.direction
        return Point2D(
            self.start.x + dx * offset_mm,
            self.start.y + dy * offset_mm,
        )

    def windows(self) -> List[WallOpening]:
        return [opening for opening in self.openings if opening.kind == "window"]


@dataclass
class ExteriorBarrier:
    """
    One vertical line-screen that can obstruct a room-to-window daylight ray.

    The plan segment is stored in building world coordinates. Vertical heights
    are relative to the room floor. ``visible_transmittance`` is the effective
    open-light ratio: 0 = solid wall, 1 = no daylight loss. This compact form
    covers adjacent wall returns, balcony parapets and open railings.
    ``ray_scope`` states whether the segment lies beyond the glazing plane,
    between the work point and glazing, or may be encountered in either part.
    """

    start: Point2D
    end: Point2D
    bottom_height_mm: float = 0.0
    top_height_mm: float = 1100.0
    visible_transmittance: float = 0.0
    kind: str = "wall"
    ray_scope: str = "outside_window"
    name: str = ""
    id: str = field(default_factory=lambda: new_id("barrier"))
    metadata: Dict[str, object] = field(default_factory=dict)

    @property
    def length_mm(self) -> float:
        return self.start.distance_to(self.end)


@dataclass
class BoundaryLoop:
    """Ordered wall segments forming one closed outer or hole boundary."""

    segments: List[WallSegment] = field(default_factory=list)
    kind: str = "outer"
    name: str = ""
    id: str = field(default_factory=lambda: new_id("loop"))
    metadata: Dict[str, object] = field(default_factory=dict)

    def points(self) -> List[Point2D]:
        return [segment.start for segment in self.segments]

    def is_closed(self, tolerance_mm: float = 1e-6) -> bool:
        if len(self.segments) < 3:
            return False
        for index, segment in enumerate(self.segments):
            following = self.segments[(index + 1) % len(self.segments)]
            if segment.end.distance_to(following.start) > tolerance_mm:
                return False
        return True

    def __iter__(self) -> Iterable[WallSegment]:
        return iter(self.segments)


@dataclass
class SpaceModel:
    """One independently calculable thermal/daylight zone."""

    name: str = "未命名空间"
    height_mm: float = 3000.0
    floor_elevation_mm: float = 0.0
    boundary_loops: List[BoundaryLoop] = field(default_factory=list)
    material: MaterialParams = field(default_factory=MaterialParams)
    thermal: ThermalParams = field(default_factory=ThermalParams)
    shading: ShadingDevice = field(default_factory=ShadingDevice)
    exterior_barriers: List[ExteriorBarrier] = field(default_factory=list)
    id: str = field(default_factory=lambda: new_id("space"))
    metadata: Dict[str, object] = field(default_factory=dict)

    def outer_loop(self) -> Optional[BoundaryLoop]:
        return next((loop for loop in self.boundary_loops if loop.kind == "outer"), None)

    def hole_loops(self) -> List[BoundaryLoop]:
        return [loop for loop in self.boundary_loops if loop.kind == "hole"]

    def wall_segments(self) -> List[WallSegment]:
        return [
            segment
            for loop in self.boundary_loops
            for segment in loop.segments
        ]

    def get_wall(self, wall_id: str) -> Optional[WallSegment]:
        return next(
            (wall for wall in self.wall_segments() if wall.id == wall_id),
            None,
        )

    def get_opening(self, opening_id: str) -> Optional[WallOpening]:
        for wall in self.wall_segments():
            for opening in wall.openings:
                if opening.id == opening_id:
                    return opening
        return None

    @property
    def volume_m3(self) -> float:
        # Imported lazily to keep data models free from geometry dependencies.
        from core.space_geometry import space_floor_area_mm2

        return space_floor_area_mm2(self) * self.height_mm / 1_000_000_000.0


@dataclass
class StoreyModel:
    """One building storey containing independently selectable spaces."""

    name: str = "首层"
    elevation_mm: float = 0.0
    default_height_mm: float = 3000.0
    spaces: List[SpaceModel] = field(default_factory=list)
    id: str = field(default_factory=lambda: new_id("storey"))
    metadata: Dict[str, object] = field(default_factory=dict)

    def get_space(self, space_id: str) -> Optional[SpaceModel]:
        return next((space for space in self.spaces if space.id == space_id), None)


@dataclass
class BuildingModel:
    """Root model for a multi-storey, multi-space project."""

    name: str = "未命名建筑"
    north_angle_deg: float = 0.0
    location: LocationParams = field(default_factory=LocationParams)
    storeys: List[StoreyModel] = field(default_factory=list)
    id: str = field(default_factory=lambda: new_id("building"))
    metadata: Dict[str, object] = field(default_factory=dict)

    def spaces(self) -> List[SpaceModel]:
        return [space for storey in self.storeys for space in storey.spaces]

    def get_storey(self, storey_id: str) -> Optional[StoreyModel]:
        return next((storey for storey in self.storeys if storey.id == storey_id), None)

    def get_space(self, space_id: str) -> Optional[SpaceModel]:
        return next((space for space in self.spaces() if space.id == space_id), None)
