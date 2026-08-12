"""
core/models.py  —  Data models shared across the whole application
All internal units: millimetres (mm)
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional


WALL_NAMES = ["南", "北", "东", "西"]
WALL_KEYS  = ["south", "north", "east", "west"]
WALL_MAP   = dict(zip(WALL_NAMES, WALL_KEYS))
WALL_MAP_R = dict(zip(WALL_KEYS, WALL_NAMES))


@dataclass
class Window:
    """A rectangular window on one wall of the room.

    origin: wall's bottom-left corner  (looking at the wall from outside)
    x      : distance from left edge   (mm)
    y      : distance from bottom edge / floor  (mm)
    width  : horizontal dimension       (mm)
    height : vertical dimension         (mm)
    wall   : "south" | "north" | "east" | "west"
    tau    : visible-light transmittance  (0-1)
    """
    wall:   str   = "south"
    x:      float = 300.0     # mm from left of wall
    y:      float = 900.0     # mm from floor  (sill height)
    width:  float = 1500.0    # mm
    height: float = 1500.0    # mm
    tau:    float = 0.71
    id:     int   = 0         # unique id assigned by RoomModel

    # ── derived helpers ──────────────────────────────────────────────────────
    @property
    def sill(self) -> float:
        return self.y

    @property
    def head(self) -> float:
        return self.y + self.height

    @property
    def right(self) -> float:
        return self.x + self.width

    def wall_length(self, room: "RoomModel") -> float:
        """Horizontal extent of the wall this window sits on."""
        if self.wall in ("south", "north"):
            return room.width
        return room.length

    def label(self) -> str:
        return f"{WALL_MAP_R.get(self.wall, self.wall)}窗 #{self.id}"


@dataclass
class MaterialParams:
    rho_wall:    float = 0.50   # wall reflectance
    rho_ceiling: float = 0.70   # ceiling reflectance
    rho_floor:   float = 0.20   # floor reflectance
    rho_ground:  float = 0.20   # outdoor ground reflectance


@dataclass
class LocationParams:
    latitude:  float = 39.9    # degrees N
    longitude: float = 116.4   # degrees E
    timezone:  int   = 8       # UTC+8


@dataclass
class RoomModel:
    """Master model that owns all room state.
    All dimensions in mm.
    """
    length:   float = 6000.0   # Y direction (南北 depth)
    width:    float = 4000.0   # X direction (东西 width)
    height:   float = 3000.0   # Z direction
    windows:  List[Window] = field(default_factory=list)
    material: MaterialParams  = field(default_factory=MaterialParams)
    location: LocationParams  = field(default_factory=LocationParams)

    _next_win_id: int = field(default=1, init=False, repr=False)

    # ── window CRUD ──────────────────────────────────────────────────────────
    def add_window(self, wall: str = "south") -> Window:
        w = Window(wall=wall, id=self._next_win_id)
        self._next_win_id += 1
        self.windows.append(w)
        return w

    def remove_window(self, win_id: int) -> None:
        self.windows = [w for w in self.windows if w.id != win_id]

    def get_window(self, win_id: int) -> Optional[Window]:
        for w in self.windows:
            if w.id == win_id:
                return w
        return None

    # ── geometry helpers ─────────────────────────────────────────────────────
    def wall_length(self, wall: str) -> float:
        """Horizontal span of a named wall (mm)."""
        if wall in ("south", "north"):
            return self.width
        return self.length

    def windows_on(self, wall: str) -> List[Window]:
        return [w for w in self.windows if w.wall == wall]
