"""
ui/analysis_panel.py — 采光分析热力图面板  v4.3.0
修复:
  - save_heatmap: 用 GridSpec + make_axes 精确控制颜色条宽度，不再布局错乱
  - save_profile: 独立渲染，格式正确
  - 所有文字颜色确保深色，白底可见
"""
from __future__ import annotations
from typing import Optional

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib import patches, colors as mcolors
from matplotlib.gridspec import GridSpec
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QSizePolicy, QCheckBox,
)
from PyQt6.QtCore import Qt

from core.daylight import DaylightResult
from core.models import RoomModel
from core.complex_models import SpaceModel

# ── 学术白色主题 ──────────────────────────────────────────────────────────────
C_BG      = "#ffffff"
C_TEXT    = "#1a1e2e"
C_TEXTSEC = "#5a6175"
C_ACCENT  = "#2563eb"
C_BORDER  = "#d0d5e0"
C_GRID    = "#eef0f4"

THRESH_E  = 300.0
THRESH_U0 = 0.70


def _model_bounds(room: RoomModel | SpaceModel):
    if isinstance(room, SpaceModel):
        outer = room.outer_loop()
        if outer is None or not outer.points():
            return 0.0, 1.0, 0.0, 1.0
        points = outer.points()
        return (
            min(point.x for point in points),
            max(point.x for point in points),
            min(point.y for point in points),
            max(point.y for point in points),
        )
    return 0.0, float(room.width), 0.0, float(room.length)


def _draw_model_outline_and_windows(ax, room, width_mm, length_mm):
    if isinstance(room, SpaceModel):
        for loop in room.boundary_loops:
            points = loop.points()
            if not points:
                continue
            xs = [point.x for point in points] + [points[0].x]
            ys = [point.y for point in points] + [points[0].y]
            ax.plot(
                xs,
                ys,
                color="#1a1e2e",
                linewidth=1.6,
                zorder=5,
            )
            for wall in loop.segments:
                for opening in wall.windows():
                    start = wall.point_at(opening.offset_mm)
                    end = wall.point_at(opening.end_offset_mm)
                    ax.plot(
                        [start.x, end.x],
                        [start.y, end.y],
                        color=C_ACCENT,
                        linewidth=6.0,
                        solid_capstyle="butt",
                        zorder=6,
                    )
        return

    ax.add_patch(patches.Rectangle(
        (0, 0), width_mm, length_mm,
        fill=False, edgecolor="#1a1e2e", linewidth=1.5, zorder=5,
    ))
    thickness = max(width_mm, length_mm) * 0.014
    for window in room.windows:
        wall = window.wall
        if wall == "south":
            bx, by, bw, bh = window.x, 0, window.width, thickness
        elif wall == "north":
            bx, by, bw, bh = (
                window.x, length_mm - thickness, window.width, thickness
            )
        elif wall == "east":
            bx, by, bw, bh = (
                width_mm - thickness, window.x, thickness, window.width
            )
        else:
            bx, by, bw, bh = 0, window.x, thickness, window.width
        ax.add_patch(patches.Rectangle(
            (bx, by), bw, bh,
            facecolor="#bfdbfe", edgecolor=C_ACCENT,
            linewidth=1.0, zorder=6, alpha=0.9,
        ))


class AnalysisPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._result: Optional[DaylightResult] = None
        self._room:   Optional[RoomModel | SpaceModel] = None
        self._comparison_result: Optional[DaylightResult] = None
        self._comparison_room: Optional[RoomModel | SpaceModel] = None
        self._show_labels = True

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_kpi_bar())
        root.addWidget(self._build_control_bar())

        self._fig = Figure(facecolor=C_BG)
        self._fig.set_tight_layout(False)
        self._canvas = FigureCanvas(self._fig)
        self._canvas.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._canvas.setStyleSheet("background:white;")
        root.addWidget(self._canvas, 1)

        self._draw_placeholder()

    # ── KPI 栏 ────────────────────────────────────────────────────────────
    def _build_kpi_bar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(66)
        bar.setStyleSheet(
            "background:#f5f6f8;border-bottom:1px solid #d0d5e0;")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(16, 6, 16, 6)
        lay.setSpacing(0)

        self._kpis: dict = {}
        specs = [
            ("E_avg",  "平均照度",   "lux", THRESH_E,  True),
            ("E_min",  "最低照度",   "lux", None,      None),
            ("E_max",  "最高照度",   "lux", None,      None),
            ("U0",     "均匀度 U₀",  "",    THRESH_U0, True),
            ("DF_avg", "DF avg",     "%",   2.0,       True),
            ("WFR",    "窗地比 WFR", "",    None,      None),
        ]
        for i, (key, title, unit, thresh, hi_good) in enumerate(specs):
            if i > 0:
                sep = QFrame()
                sep.setFrameShape(QFrame.Shape.VLine)
                sep.setStyleSheet("color:#d0d5e0;max-width:1px;")
                lay.addWidget(sep)

            cell = QWidget()
            cell.setStyleSheet("background:transparent;")
            cl = QVBoxLayout(cell)
            cl.setContentsMargins(10, 2, 10, 2)
            cl.setSpacing(0)
            cl.setAlignment(Qt.AlignmentFlag.AlignCenter)

            t = QLabel(title)
            t.setAlignment(Qt.AlignmentFlag.AlignCenter)
            t.setStyleSheet(
                "color:#5a6175;font-size:10px;background:transparent;")

            v = QLabel("—")
            v.setAlignment(Qt.AlignmentFlag.AlignCenter)
            v.setStyleSheet(
                "color:#1a1e2e;font-size:17px;font-weight:700;"
                "background:transparent;")

            u = QLabel(unit)
            u.setAlignment(Qt.AlignmentFlag.AlignCenter)
            u.setStyleSheet(
                "color:#9aa0b0;font-size:9px;background:transparent;")

            cl.addWidget(t); cl.addWidget(v); cl.addWidget(u)
            lay.addWidget(cell, 1)
            self._kpis[key] = (v, thresh, hi_good)
        return bar

    def _update_kpi(
        self,
        res: DaylightResult,
        comparison: Optional[DaylightResult] = None,
    ):
        q = res.quick or {}
        vals = {
            "E_avg":  f"{res.E_avg:.1f}",
            "E_min":  f"{res.E_min:.1f}",
            "E_max":  f"{res.E_max:.1f}",
            "U0":     f"{res.U0:.4f}",
            "DF_avg": f"{res.DF_avg:.4f}",
            "WFR":    f"{q.get('WFR',0):.4f}",
        }
        comparison_vals = None
        if comparison is not None:
            cq = comparison.quick or {}
            comparison_vals = {
                "E_avg": f"{comparison.E_avg:.1f}",
                "E_min": f"{comparison.E_min:.1f}",
                "E_max": f"{comparison.E_max:.1f}",
                "U0": f"{comparison.U0:.4f}",
                "DF_avg": f"{comparison.DF_avg:.4f}",
                "WFR": f"{cq.get('WFR', 0):.4f}",
            }
        for key, (lbl, thresh, hi_good) in self._kpis.items():
            s = vals.get(key, "—")
            shown = (
                f"{s} → {comparison_vals.get(key, '—')}"
                if comparison_vals is not None else s
            )
            lbl.setText(shown)
            if thresh is not None:
                try:
                    judged = (
                        comparison_vals.get(key, s)
                        if comparison_vals is not None else s
                    )
                    ok  = float(judged) >= thresh
                    col = "#16a34a" if ok else "#dc2626"
                    lbl.setStyleSheet(
                        f"color:{col};font-size:{13 if comparison else 17}px;font-weight:700;"
                        "background:transparent;")
                except Exception:
                    pass
            else:
                lbl.setStyleSheet(
                    f"color:#1a1e2e;font-size:{13 if comparison else 17}px;font-weight:700;"
                    "background:transparent;")

    # ── 控制栏 ────────────────────────────────────────────────────────────
    def _build_control_bar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(32)
        bar.setStyleSheet(
            "background:#ffffff;border-bottom:1px solid #eef0f4;")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(14, 4, 14, 4)
        lay.setSpacing(14)

        self._chk_labels = QCheckBox("显示网格数值")
        self._chk_labels.setChecked(True)
        self._chk_labels.stateChanged.connect(self._on_label_toggle)
        lay.addWidget(self._chk_labels)

        hint = QLabel("精度: WIN_DIV=40 · GRID=250mm")
        hint.setStyleSheet(
            "color:#b0b8cc;font-size:11px;background:transparent;")
        lay.addWidget(hint)
        lay.addStretch()

        self._wx_info = QLabel("")
        self._wx_info.setStyleSheet(
            "color:#5a6175;font-size:11px;background:transparent;")
        lay.addWidget(self._wx_info)
        return bar

    def set_weather_info(self, text: str):
        self._wx_info.setText(text)

    def _on_label_toggle(self, state):
        self._show_labels = bool(state)
        if self._result is not None:
            self._draw_heatmap()

    # ── 公共接口 ──────────────────────────────────────────────────────────
    def update(self, result, room: RoomModel | SpaceModel,
               weather_info: str = ""):
        self._result = result
        self._room   = room
        self._comparison_result = None
        self._comparison_room = None
        self._update_kpi(result)
        if weather_info:
            self.set_weather_info(weather_info)
        self._draw_heatmap()

    def set_comparison(
        self,
        current_result,
        current_room: RoomModel | SpaceModel,
        optimal_result,
        optimal_room: RoomModel | SpaceModel,
        weather_info: str = "",
    ):
        """Show current and recommended daylight results side by side."""
        self._result = current_result
        self._room = current_room
        self._comparison_result = optimal_result
        self._comparison_room = optimal_room
        self._update_kpi(current_result, optimal_result)
        self.set_weather_info(
            weather_info or "数值格式：当前方案 → 参数化最优方案"
        )
        self._draw_heatmap()

    def clear_result(self, message: str = ""):
        """Invalidate displayed results after geometry, weather, or inputs change."""
        self._result = None
        self._room = None
        self._comparison_result = None
        self._comparison_room = None
        for label, _threshold, _higher_is_better in self._kpis.values():
            label.setText("—")
            label.setStyleSheet(
                "color:#1a1e2e;font-size:17px;font-weight:700;"
                "background:transparent;"
            )
        self.set_weather_info(message)
        self._draw_placeholder()

    # ── 导出：热力图（独立正确布局）─────────────────────────────────────
    def save_heatmap(self, path: str, dpi: int = 200):
        """
        导出热力图 PNG（白底，适合论文）。
        使用 GridSpec 精确控制颜色条宽度，避免比例错乱。
        """
        if self._result is None or self._room is None:
            return

        # 根据房间长宽比计算合适的图纸尺寸
        min_x, max_x, min_y, max_y = _model_bounds(self._room)
        aspect = (max_y - min_y) / max(max_x - min_x, 1.0)
        fig_w  = 8.0
        fig_h  = max(4.5, min(10.0, fig_w * aspect * 0.72 + 1.5))

        fig = Figure(figsize=(fig_w, fig_h), facecolor=C_BG)
        FigureCanvasAgg(fig)   # 必须 attach canvas 才能 savefig

        # GridSpec：主图占 92%宽，颜色条占 4%，间距 4%
        gs = GridSpec(
            1, 2,
            figure=fig,
            width_ratios=[1, 0.045],
            left=0.10, right=0.95,
            top=0.88,  bottom=0.10,
            wspace=0.05,
        )
        ax_m  = fig.add_subplot(gs[0, 0])
        ax_cb = fig.add_subplot(gs[0, 1])

        self._render_heatmap_ax(ax_m, ax_cb, fig)
        fig.savefig(path, dpi=dpi, facecolor=C_BG,
                    bbox_inches="tight", edgecolor="none")

    # ── 导出：截面分布图 ──────────────────────────────────────────────────
    def save_profile(self, path: str, dpi: int = 200):
        """导出截面分布图 PNG（白底，适合论文）。"""
        if self._result is None or self._room is None:
            return
        fig = Figure(figsize=(8, 4), facecolor=C_BG)
        FigureCanvasAgg(fig)
        ax = fig.add_subplot(111)
        fig.subplots_adjust(left=0.10, right=0.96, top=0.86, bottom=0.14)
        self._render_profile_ax(ax)
        fig.savefig(path, dpi=dpi, facecolor=C_BG,
                    bbox_inches="tight", edgecolor="none")

    # ── 占位图 ────────────────────────────────────────────────────────────
    def _draw_placeholder(self):
        self._fig.clear()
        ax = self._fig.add_subplot(111)
        ax.set_facecolor(C_BG)
        for sp in ax.spines.values():
            sp.set_visible(False)
        ax.tick_params(left=False, bottom=False,
                       labelleft=False, labelbottom=False)
        ax.text(0.5, 0.54,
                "在建筑视图选择房间后，点击「▶ 分析选中房间」生成采光热力图",
                ha="center", va="center", fontsize=13,
                color="#9aa0b0", transform=ax.transAxes)
        ax.text(0.5, 0.46,
                "默认气象数据：湖南益阳 TMY  年均 13 906 lux",
                ha="center", va="center", fontsize=10,
                color="#b0b8cc", transform=ax.transAxes)
        self._fig.patch.set_facecolor(C_BG)
        self._canvas.draw()

    # ── 主绘制（屏幕预览）────────────────────────────────────────────────
    def _draw_heatmap(self):
        if self._result is None or self._room is None:
            self._draw_placeholder(); return

        self._fig.clear()

        if (
            self._comparison_result is not None
            and self._comparison_room is not None
        ):
            gs = self._fig.add_gridspec(
                1, 4,
                width_ratios=[1, 0.03, 1, 0.03],
                left=0.055, right=0.97,
                top=0.90, bottom=0.10,
                wspace=0.16,
            )
            ax_current = self._fig.add_subplot(gs[0, 0])
            cb_current = self._fig.add_subplot(gs[0, 1])
            ax_optimal = self._fig.add_subplot(gs[0, 2])
            cb_optimal = self._fig.add_subplot(gs[0, 3])
            all_values = np.concatenate([
                np.asarray(self._result.E_lux)[
                    np.isfinite(self._result.E_lux)
                ],
                np.asarray(self._comparison_result.E_lux)[
                    np.isfinite(self._comparison_result.E_lux)
                ],
            ])
            shared_vmax = max(
                float(all_values.max()) * 1.05 if all_values.size else 1.0,
                THRESH_E * 2.5,
            )
            self._render_heatmap_ax(
                ax_current, cb_current, self._fig,
                result=self._result, room=self._room,
                title_prefix="当前方案｜",
                vmax_override=shared_vmax,
            )
            self._render_heatmap_ax(
                ax_optimal, cb_optimal, self._fig,
                result=self._comparison_result,
                room=self._comparison_room,
                title_prefix="参数化最优方案｜",
                vmax_override=shared_vmax,
            )
            self._fig.patch.set_facecolor(C_BG)
            self._canvas.draw()
            return

        gs = self._fig.add_gridspec(
            2, 2,
            width_ratios=[1, 0.032],
            height_ratios=[2.6, 1],
            left=0.07, right=0.94,
            top=0.91,  bottom=0.08,
            hspace=0.44, wspace=0.04,
        )
        ax_m  = self._fig.add_subplot(gs[0, 0])
        ax_cb = self._fig.add_subplot(gs[0, 1])
        ax_p  = self._fig.add_subplot(gs[1, 0])
        self._fig.add_subplot(gs[1, 1]).set_visible(False)

        self._render_heatmap_ax(ax_m, ax_cb, self._fig)
        self._render_profile_ax(ax_p)

        self._fig.patch.set_facecolor(C_BG)
        self._canvas.draw()

    # ── 热力图核心（屏幕+导出共用）──────────────────────────────────────
    def _render_heatmap_ax(
        self,
        ax_m,
        ax_cb,
        fig,
        result=None,
        room=None,
        title_prefix: str = "",
        vmax_override: Optional[float] = None,
    ):
        res = self._result if result is None else result
        room = self._room if room is None else room
        xs, ys = res.grid_x, res.grid_y
        E      = np.ma.masked_invalid(res.E_lux)
        min_x, max_x, min_y, max_y = _model_bounds(room)
        W_mm = max_x - min_x
        L_mm = max_y - min_y

        cmap = plt.get_cmap("RdYlGn").reversed()
        finite = np.asarray(res.E_lux)[np.isfinite(res.E_lux)]
        vmax = (
            float(vmax_override)
            if vmax_override is not None
            else max(
                float(finite.max()) * 1.05 if finite.size else 1.0,
                THRESH_E * 2.5,
            )
        )
        norm = mcolors.Normalize(vmin=0., vmax=vmax)

        def _edges(arr):
            if len(arr) < 2:
                h = arr[0] * 0.5 if len(arr) else 250.
                return np.array([arr[0] - h, arr[0] + h])
            d = arr[1] - arr[0]
            return np.concatenate([[arr[0] - d/2], arr + d/2])

        XE, YE = np.meshgrid(_edges(xs), _edges(ys))
        pcm = ax_m.pcolormesh(XE, YE, E, cmap=cmap, norm=norm,
                               shading="flat", zorder=2, rasterized=True)

        XC, YC  = np.meshgrid(xs, ys)
        has_c   = E.shape[0] >= 3 and E.shape[1] >= 3

        # 等值线
        if has_c:
            lvls = [l for l in [50,100,150,200,300,500,750,1000,1500,2000]
                    if 0 < l < vmax]
            if lvls:
                cs = ax_m.contour(XC, YC, E, levels=lvls,
                                   colors=["#555555"], linewidths=0.4,
                                   alpha=0.4, zorder=3)
                ax_m.clabel(cs, fmt="%g", fontsize=6., colors="#333333",
                             inline_spacing=2)

        # 300 lux 阈值线
        if has_c and THRESH_E < vmax:
            ax_m.contour(XC, YC, E, levels=[THRESH_E],
                          colors=["#d97706"], linewidths=1.6,
                          linestyles=["--"], zorder=4)

        _draw_model_outline_and_windows(ax_m, room, W_mm, L_mm)

        # 网格数值标注（可关闭）
        if self._show_labels and E.size <= 600:
            n  = E.size
            fs = max(4.5, min(7., 8.0 - n * 0.005))
            for iy, y in enumerate(ys):
                for ix, x in enumerate(xs):
                    v = E[iy, ix]
                    if np.ma.is_masked(v) or not np.isfinite(float(v)):
                        continue
                    # 背景偏深时用白字，偏浅时用深字
                    bright = norm(v) > 0.52
                    tc = "#1a1e2e" if bright else "#ffffff"
                    ax_m.text(x, y, f"{v:.0f}",
                               ha="center", va="center",
                               fontsize=fs, color=tc,
                               fontweight="light", alpha=0.90, zorder=7)

        # 颜色条（绑定到 ax_cb）
        cb = fig.colorbar(pcm, cax=ax_cb)
        cb.set_label("照度 (lux)", color=C_TEXTSEC, fontsize=9, labelpad=4)
        cb.ax.yaxis.set_tick_params(
            color=C_TEXTSEC, labelcolor=C_TEXT, labelsize=8)
        cb.ax.tick_params(length=3, width=0.6)
        ax_cb.axhline(THRESH_E, color="#d97706", lw=1.2, ls="--")
        ax_cb.text(2.8, THRESH_E, "300 lux", color="#d97706",
                   fontsize=7, va="center", fontweight="bold")

        # 合规徽章
        ok_e  = res.compliant_300
        ok_u0 = res.compliant_u0
        badges = [
            (f"{'✓' if ok_e  else '✗'} Eavg {'≥' if ok_e  else '<'} 300 lux",
             "#16a34a" if ok_e  else "#dc2626"),
            (f"{'✓' if ok_u0 else '✗'} U₀ {'≥' if ok_u0 else '<'} 0.70",
             "#16a34a" if ok_u0 else "#dc2626"),
        ]
        for i, (txt, col) in enumerate(badges):
            ax_m.text(
                0.012 + i * 0.36, 0.978, txt,
                transform=ax_m.transAxes,
                fontsize=8.5, color=col, fontweight="bold", va="top",
                bbox=dict(fc="#ffffffee", ec=col,
                          boxstyle="round,pad=0.28", lw=0.9),
                zorder=8)

        # 标题（深色，白底清晰）
        ax_m.set_title(
            f"{title_prefix}室内照度分布热力图   "
            f"$E_{{avg}}$ = {res.E_avg:.1f} lux   "
            f"$U_0$ = {res.U0:.4f}   "
            f"$DF_{{avg}}$ = {res.DF_avg:.4f}%\n"
            f"$E_{{out}}$ = {res.E_out:.0f} lux   "
            f"$\\bar{{\\rho}}$ = {res.rho_bar:.3f}   "
            f"网格 {res.grid_mm:.0f} mm",
            color=C_TEXT, fontsize=9, fontweight="bold",
            pad=6, loc="left")

        ax_m.set_xlim(min_x, max_x)
        ax_m.set_ylim(min_y, max_y)
        ax_m.set_aspect("equal", adjustable="box")
        ax_m.set_xlabel("宽度 X (mm)", color=C_TEXTSEC, fontsize=9, labelpad=3)
        ax_m.set_ylabel("长度 Y (mm)", color=C_TEXTSEC, fontsize=9, labelpad=3)
        ax_m.tick_params(colors=C_TEXTSEC, labelsize=8, length=3, width=0.6)
        for sp in ax_m.spines.values():
            sp.set_color(C_BORDER); sp.set_linewidth(0.6)
        ax_m.set_facecolor(C_BG)

    # ── 截面图核心（屏幕+导出共用）──────────────────────────────────────
    def _render_profile_ax(self, ax):
        res = self._result
        xs  = res.grid_x
        ys  = res.grid_y
        E   = np.asarray(res.E_lux)
        mid_x = E.shape[1] // 2
        mid_y = E.shape[0] // 2

        ax.set_facecolor(C_BG)
        ax.plot(ys, E[:, mid_x],
                color=C_ACCENT, lw=1.8, alpha=0.9,
                label=f"纵向 X={xs[mid_x]:.0f} mm")
        ax.plot(xs, E[mid_y, :],
                color="#d97706", lw=1.8, ls="--", alpha=0.9,
                label=f"横向 Y={ys[mid_y]:.0f} mm")
        ax.axhline(THRESH_E, color="#16a34a", lw=1.2, ls=":",
                    alpha=0.9, label="300 lux 标准线")
        ax.axhline(res.E_avg, color=C_ACCENT, lw=0.8, ls="-.",
                    alpha=0.5, label=f"均值 {res.E_avg:.1f} lux")

        ax.fill_between(ys, E[:, mid_x], alpha=0.07, color=C_ACCENT)
        ax.fill_between(xs, E[mid_y, :], alpha=0.06, color="#d97706")

        ax.set_xlabel("位置 (mm)", color=C_TEXTSEC, fontsize=9, labelpad=3)
        ax.set_ylabel("照度 (lux)", color=C_TEXTSEC, fontsize=9, labelpad=3)
        ax.set_title("中心截面照度分布",
                     color=C_TEXT, fontsize=9, fontweight="bold", pad=4)
        ax.legend(fontsize=8, loc="upper right",
                   handlelength=1.5, handletextpad=0.4,
                   borderpad=0.4, labelspacing=0.3,
                   labelcolor=C_TEXT)
        ax.yaxis.set_minor_locator(mticker.AutoMinorLocator())
        for sp in ax.spines.values():
            sp.set_color(C_BORDER); sp.set_linewidth(0.6)
        ax.tick_params(colors=C_TEXTSEC, labelsize=8, length=3, width=0.6)
        ax.grid(True, color=C_GRID, linewidth=0.5, alpha=0.8)



