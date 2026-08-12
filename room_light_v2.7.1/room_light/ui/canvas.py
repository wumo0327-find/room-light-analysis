"""
ui/canvas.py  —  Visualization canvas (floor plan + 4 elevations)
Renders via matplotlib embedded in a QWidget.
"""
from __future__ import annotations
import math
from typing import Optional

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from matplotlib import patches, patheffects
import matplotlib.pyplot as plt

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QSizePolicy
from PyQt6.QtCore import Qt

from core.models import RoomModel, Window, WALL_MAP_R


# ── Colour palette — Light Academic Theme v2.2.0 ────────────────────────────
C_BG       = "#ffffff"     # main background (white)
C_PANEL    = "#f5f6f8"     # panel background
C_CARD     = "#eef0f4"     # card background
C_BORDER   = "#d0d5e0"     # border
C_ACCENT   = "#2563eb"     # academic blue
C_ACCENT2  = "#1d4ed8"
C_TEXT     = "#1a1e2e"     # near-black text
C_TEXTSEC  = "#5a6175"     # secondary text
C_WIN      = "#bfdbfe80"   # window fill (light blue semi-transparent)
C_WIN_ED   = "#2563eb"     # window edge
C_WALL     = "#e2e6ef"     # wall fill
C_FLOOR    = "#f8f9fb"     # floor fill
C_DIM      = "#d97706"     # dimension annotation (amber)
C_COMPASS  = "#16a34a"     # compass / north arrow (green)


VIEWS = ["平面", "南立面", "北立面", "东立面", "西立面"]


class RoomCanvas(QWidget):
    """Embeds a matplotlib figure for room visualization."""

    def __init__(self, room: RoomModel, parent=None):
        super().__init__(parent)
        self.room = room
        self._view = "平面"

        self.fig = Figure(facecolor=C_BG, tight_layout=False)
        self.canvas = FigureCanvas(self.fig)
        self.canvas.setSizePolicy(QSizePolicy.Policy.Expanding,
                                  QSizePolicy.Policy.Expanding)
        self.canvas.setStyleSheet("background: transparent;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.canvas)

        self.ax: Optional[plt.Axes] = None
        self.draw()

    # ── Public API ────────────────────────────────────────────────────────────
    def set_view(self, view: str) -> None:
        if view in VIEWS:
            self._view = view
            self.draw()

    def refresh(self) -> None:
        self.draw()

    # ── Draw dispatcher ───────────────────────────────────────────────────────
    def draw(self) -> None:
        self.fig.clear()
        self.ax = self.fig.add_subplot(111)
        self.ax.set_facecolor(C_BG)
        for spine in self.ax.spines.values():
            spine.set_visible(False)
        self.ax.tick_params(left=False, bottom=False,
                            labelleft=False, labelbottom=False)

        if self._view == "平面":
            self._draw_plan()
        else:
            wall_key = {
                "南立面": "south", "北立面": "north",
                "东立面": "east",  "西立面": "west",
            }[self._view]
            self._draw_elevation(wall_key)

        self.canvas.draw()

    # ── Floor-plan view ───────────────────────────────────────────────────────
    def _draw_plan(self) -> None:
        ax = self.ax
        r  = self.room
        W, L, H = r.width, r.length, r.height   # mm

        # Room outline
        room_rect = patches.FancyBboxPatch(
            (0, 0), W, L,
            boxstyle="square,pad=0",
            linewidth=2, edgecolor=C_ACCENT, facecolor=C_FLOOR,
        )
        ax.add_patch(room_rect)

        # Wall fills (thick border representation)
        wt = max(W, L) * 0.018   # wall thickness in mm-units
        for (bx, by, bw, bh) in [
            (0, 0, W, wt),          # south
            (0, L - wt, W, wt),     # north
            (0, 0, wt, L),          # west (left)
            (W - wt, 0, wt, L),     # east (right)
        ]:
            ax.add_patch(patches.Rectangle(
                (bx, by), bw, bh,
                linewidth=0, facecolor=C_CARD,
            ))

        # Windows on plan (drawn as notch cuts in the wall)
        for win in r.windows:
            self._draw_window_plan(ax, win, wt)

        # Dimensions
        self._dim_arrow(ax, 0, -L * 0.08, W, -L * 0.08,
                        f"宽 {W/1000:.2f} m", "h")
        self._dim_arrow(ax, -W * 0.1, 0, -W * 0.1, L,
                        f"长 {L/1000:.2f} m", "v")

        # Compass
        self._draw_compass(ax, W * 0.92, L * 0.92, L * 0.05)

        # Title
        ax.set_title(f"平面图    层高 {H/1000:.2f} m",
                     color=C_TEXT, fontsize=13, fontweight="bold", pad=8)

        margin = max(W, L) * 0.18
        ax.set_xlim(-margin, W + margin * 0.5)
        ax.set_ylim(-margin, L + margin * 0.5)
        ax.set_aspect("equal")

    def _draw_window_plan(self, ax, win: Window, wt: float) -> None:
        r = self.room
        # Map window to world (x, y) in plan
        # South wall: y=0, x spans [win.x, win.x+win.width]
        # North wall: y=L
        # East  wall: x=W, span along y
        # West  wall: x=0, span along y
        gap = wt * 1.05
        w   = win.wall
        if w == "south":
            bx, by = win.x, 0
            bw, bh = win.width, gap
        elif w == "north":
            bx, by = win.x, r.length - gap
            bw, bh = win.width, gap
        elif w == "east":
            bx, by = r.width - gap, win.x
            bw, bh = gap, win.width
        else:  # west
            bx, by = 0, win.x
            bw, bh = gap, win.width

        # White cut (erase wall)
        ax.add_patch(patches.Rectangle(
            (bx, by), bw, bh,
            linewidth=0, facecolor=C_BG, zorder=3,
        ))
        # Window pane
        ax.add_patch(patches.Rectangle(
            (bx, by), bw, bh,
            linewidth=1.5, edgecolor=C_WIN_ED,
            facecolor=C_WIN, zorder=4,
        ))
        # Label
        cx = bx + bw / 2
        cy = by + bh / 2
        ax.text(cx, cy, f"W{win.id}",
                ha="center", va="center",
                fontsize=8, color=C_WIN_ED, fontweight="bold", zorder=5,
                path_effects=[patheffects.withStroke(linewidth=1.5, foreground=C_BG)])

    # ── Elevation view ────────────────────────────────────────────────────────
    def _draw_elevation(self, wall: str) -> None:
        ax = self.ax
        r  = self.room
        H  = r.height
        W  = r.wall_length(wall)   # horizontal span of this wall

        # Background sky gradient (simple rectangle)
        sky = patches.Rectangle(
            (0, H), W, H * 0.3,
            linewidth=0, facecolor="#e0eaff",
        )
        ax.add_patch(sky)

        # Ground
        ax.add_patch(patches.Rectangle(
            (0, -H * 0.05), W, H * 0.05,
            linewidth=0, facecolor="#e8ebe0",
        ))

        # Wall surface
        ax.add_patch(patches.FancyBboxPatch(
            (0, 0), W, H,
            boxstyle="square,pad=0",
            linewidth=2, edgecolor=C_ACCENT, facecolor=C_WALL,
        ))

        # Windows
        for win in r.windows_on(wall):
            self._draw_window_elevation(ax, win, W, H)

        # Dimensions
        self._dim_arrow(ax, 0, -H * 0.08, W, -H * 0.08,
                        f"{W/1000:.2f} m", "h")
        self._dim_arrow(ax, -W * 0.08, 0, -W * 0.08, H,
                        f"H = {H/1000:.2f} m", "v")

        wall_zh = WALL_MAP_R.get(wall, wall)
        ax.set_title(f"{wall_zh}立面图",
                     color=C_TEXT, fontsize=13, fontweight="bold", pad=8)

        margin = max(W, H) * 0.18
        ax.set_xlim(-margin, W + margin * 0.5)
        ax.set_ylim(-margin * 0.6, H + H * 0.35)
        ax.set_aspect("equal")

    def _draw_window_elevation(self, ax, win: Window, wall_W: float, H: float) -> None:
        # win.x = left edge from wall left, win.y = sill height
        wx, wy = win.x, win.y
        ww, wh = win.width, win.height

        # Frame
        ax.add_patch(patches.Rectangle(
            (wx, wy), ww, wh,
            linewidth=2.5, edgecolor=C_WIN_ED, facecolor=C_WIN, zorder=4,
        ))

        # Glazing bars (cross)
        cx, cy = wx + ww / 2, wy + wh / 2
        ax.plot([cx, cx], [wy, wy + wh],
                color=C_WIN_ED, lw=1.2, alpha=0.7, zorder=5)
        ax.plot([wx, wx + ww], [cy, cy],
                color=C_WIN_ED, lw=1.2, alpha=0.7, zorder=5)

        # Dimension annotations
        pad = wall_W * 0.012
        # width
        self._dim_arrow(ax, wx, wy - H * 0.06, wx + ww, wy - H * 0.06,
                        f"{ww/1000:.2f}m", "h", fontsize=9)
        # height
        self._dim_arrow(ax, wx + ww + pad, wy, wx + ww + pad, wy + wh,
                        f"{wh/1000:.2f}m", "v", fontsize=9)
        # sill
        ax.annotate("", xy=(wx, wy),
                    xytext=(wx - pad * 2, wy),
                    arrowprops=dict(arrowstyle="-|>", color=C_DIM, lw=0.8))
        ax.text(wx - pad * 2.2, wy,
                f"↑{wy/1000:.2f}m",
                ha="right", va="center", fontsize=8, color=C_DIM)

        # Label
        ax.text(wx + ww / 2, wy + wh / 2,
                f"W{win.id}\nτ={win.tau:.2f}",
                ha="center", va="center",
                fontsize=8, color=C_WIN_ED, fontweight="bold",
                path_effects=[patheffects.withStroke(linewidth=1.5, foreground=C_BG)])

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _dim_arrow(self, ax, x1, y1, x2, y2, label,
                   direction="h", fontsize=10):
        ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                    arrowprops=dict(arrowstyle="<->", color=C_DIM, lw=1.2))
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        rot = 0 if direction == "h" else 90
        ax.text(mx, my, label,
                ha="center", va="center",
                fontsize=fontsize, color=C_DIM,
                rotation=rot, fontweight="bold",
                path_effects=[patheffects.withStroke(linewidth=1.5, foreground=C_BG)])

    def _draw_compass(self, ax, cx, cy, r):
        # North arrow
        ax.annotate("", xy=(cx, cy + r), xytext=(cx, cy),
                    arrowprops=dict(arrowstyle="-|>", color=C_COMPASS,
                                   lw=2, mutation_scale=14))
        ax.text(cx, cy + r * 1.3, "N",
                ha="center", va="bottom",
                fontsize=11, color=C_COMPASS, fontweight="900")
        # Circle
        circle = plt.Circle((cx, cy), r * 0.35,
                             fill=False, edgecolor=C_COMPASS,
                             linewidth=1.0, alpha=0.5)
        ax.add_patch(circle)
