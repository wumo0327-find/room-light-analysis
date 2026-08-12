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
from core.plan_export import (
    C_BG, C_ACCENT, C_TEXT, C_TEXTSEC, C_WIN, C_WIN_ED, C_CARD, C_FLOOR,
    C_DIM, C_COMPASS, C_OVERHANG, C_FIN, dim_arrow as _plan_dim_arrow,
    tz_leader_tick as _plan_leader_tick,
    draw_compass as _plan_draw_compass,
    draw_window_plan as _plan_draw_window,
    draw_room_plan as _plan_draw_room,
    draw_shading_elevation as _plan_draw_shading_elev,
)

# ── Colour palette — Light Academic Theme v2.2.0 ────────────────────────────
# 平面图配色统一从 core/plan_export.py 导入（GUI 交互画布与命令行静态导出共用
# 同一套颜色与绘制逻辑，v2.9.1 起不再各自维护一份）。
C_PANEL    = "#f5f6f8"     # panel background
C_BORDER   = "#d0d5e0"     # border
C_ACCENT2  = "#1d4ed8"
C_WALL     = "#e2e6ef"     # wall fill (elevation only)


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
    # v2.9.1: 绘制逻辑已抽取到 core/plan_export.py（GUI 画布与命令行静态导出共用），
    # 本方法只是薄封装，行为与旧版逐像素一致。
    def _draw_plan(self) -> None:
        _plan_draw_room(self.ax, self.room)

    def _draw_window_plan(self, ax, win: Window, wt: float) -> None:
        _plan_draw_window(ax, win, wt, self.room)

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

        # 遮阳构件（v2.9.1 新增：立面上同步显示水平挑檐/垂直翼板示意）
        has_overhang, has_fin = _plan_draw_shading_elev(ax, r, wall)
        if has_overhang or has_fin:
            import matplotlib.lines as mlines
            handles = []
            if has_overhang:
                handles.append(mlines.Line2D([], [], marker="s", linestyle="none",
                               color=C_OVERHANG, markersize=9, alpha=0.7,
                               label="水平挑檐（示意，厚度非真实值）"))
            if has_fin:
                handles.append(mlines.Line2D([], [], marker="s", linestyle="none",
                               color=C_FIN, markersize=9, alpha=0.7,
                               label="垂直翼板（示意）"))
            ax.legend(handles=handles, loc="upper left", fontsize=8,
                     frameon=True, facecolor="#ffffff", edgecolor="#d0d5e0",
                     framealpha=0.95)

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
        # sill（天正风格引出标注，取代箭头指引线）
        _plan_leader_tick(ax, wx, wy, -pad * 2, 0,
                          f"{wy/1000:.2f}m", fontsize=8, color=C_DIM)

        # Label
        ax.text(wx + ww / 2, wy + wh / 2,
                f"W{win.id}\nτ={win.tau:.2f}",
                ha="center", va="center",
                fontsize=8, color=C_WIN_ED, fontweight="bold",
                path_effects=[patheffects.withStroke(linewidth=1.5, foreground=C_BG)])

    # ── Helpers ───────────────────────────────────────────────────────────────
    # v2.9.1: 委托给 core/plan_export.py 的共享实现（elevation 视图仍复用这两个）。
    def _dim_arrow(self, ax, x1, y1, x2, y2, label,
                   direction="h", fontsize=10):
        _plan_dim_arrow(ax, x1, y1, x2, y2, label, direction, fontsize)

    def _draw_compass(self, ax, cx, cy, r):
        _plan_draw_compass(ax, cx, cy, r)
