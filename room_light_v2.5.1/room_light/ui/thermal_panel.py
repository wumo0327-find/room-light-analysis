"""
ui/thermal_panel.py — 热环境分析结果面板  v2.5.1
独立视图：逐月室温折线图 + KPI指标卡 + 热流分解条形图
"""
from __future__ import annotations
from typing import Optional

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.gridspec import GridSpec
import matplotlib.patches as patches
import matplotlib.ticker as mticker

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QSizePolicy
)
from PyQt6.QtCore import Qt

from core.thermal import ThermalResult, T_COMFORT_LOW, T_COMFORT_HIGH
from core.models import RoomModel

C_BG      = "#ffffff"
C_TEXT    = "#1a1e2e"
C_TEXTSEC = "#5a6175"
C_ACCENT  = "#2563eb"
C_BORDER  = "#d0d5e0"
C_GRID    = "#f0f2f6"
C_HOT     = "#dc2626"
C_COLD    = "#7c3aed"
C_OK      = "#16a34a"
C_WARN    = "#d97706"

MONTHS_ZH = ["1","2","3","4","5","6","7","8","9","10","11","12"]


class ThermalPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._result: Optional[ThermalResult] = None
        self._room:   Optional[RoomModel]     = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_kpi_bar())

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
        bar.setStyleSheet("background:#f5f6f8;border-bottom:1px solid #d0d5e0;")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(16, 6, 16, 6)
        lay.setSpacing(0)

        self._kpis: dict = {}
        specs = [
            ("T_avg",    "年均室温",    "℃",  None,  None),
            ("T_max",    "最高室温",    "℃",  26.0,  False),
            ("T_min",    "最低室温",    "℃",  18.0,  True),
            ("comfort",  "舒适月数",    "月",  7,     True),
            ("overheat", "超温月数",    "月",  0,     False),
            ("underheat","欠温月数",    "月",  0,     False),
        ]
        for i, (key, title, unit, thresh, hi_good) in enumerate(specs):
            if i > 0:
                sep = QFrame(); sep.setFrameShape(QFrame.Shape.VLine)
                sep.setStyleSheet("color:#d0d5e0;max-width:1px;")
                lay.addWidget(sep)

            cell = QWidget(); cell.setStyleSheet("background:transparent;")
            cl = QVBoxLayout(cell)
            cl.setContentsMargins(10,2,10,2); cl.setSpacing(0)
            cl.setAlignment(Qt.AlignmentFlag.AlignCenter)

            t = QLabel(title); t.setAlignment(Qt.AlignmentFlag.AlignCenter)
            t.setStyleSheet("color:#5a6175;font-size:10px;background:transparent;")
            v = QLabel("—");   v.setAlignment(Qt.AlignmentFlag.AlignCenter)
            v.setStyleSheet("color:#1a1e2e;font-size:17px;font-weight:700;background:transparent;")
            u = QLabel(unit);  u.setAlignment(Qt.AlignmentFlag.AlignCenter)
            u.setStyleSheet("color:#9aa0b0;font-size:9px;background:transparent;")

            cl.addWidget(t); cl.addWidget(v); cl.addWidget(u)
            lay.addWidget(cell, 1)
            self._kpis[key] = (v, thresh, hi_good)
        return bar

    def _update_kpi(self, res: ThermalResult):
        vals = {
            "T_avg":     f"{res.T_in_annual_avg:.1f}",
            "T_max":     f"{float(np.max(res.T_in)):.1f}",
            "T_min":     f"{float(np.min(res.T_in)):.1f}",
            "comfort":   str(res.comfort_months),
            "overheat":  str(res.overheat_months),
            "underheat": str(res.underheat_months),
        }
        for key, (lbl, thresh, hi_good) in self._kpis.items():
            s = vals.get(key,"—"); lbl.setText(s)
            if thresh is not None and hi_good is not None:
                try:
                    v = float(s)
                    ok = v >= thresh if hi_good else v <= thresh
                    col = C_OK if ok else C_HOT
                    lbl.setStyleSheet(
                        f"color:{col};font-size:17px;font-weight:700;background:transparent;")
                except Exception:
                    pass

    # ── 公共接口 ──────────────────────────────────────────────────────────
    def update(self, result: ThermalResult, room: RoomModel):
        self._result = result
        self._room   = room
        self._update_kpi(result)
        self._draw_charts()

    # ── 导出 ──────────────────────────────────────────────────────────────
    def save_figure(self, path: str, dpi: int = 200):
        """导出热环境分析图（白底）——与屏幕布局一致：逐月室温 + 舒适判定 + 逐月热流分解"""
        if self._result is None: return
        fig = Figure(figsize=(11, 7), facecolor=C_BG)
        FigureCanvasAgg(fig)
        gs  = GridSpec(2, 2, figure=fig,
                       width_ratios=[3, 1], height_ratios=[2.2, 1],
                       left=0.07, right=0.96, top=0.91, bottom=0.09,
                       hspace=0.42, wspace=0.14)
        ax_line = fig.add_subplot(gs[0, 0])
        ax_bar  = fig.add_subplot(gs[0, 1])
        ax_heat = fig.add_subplot(gs[1, :])
        self._render_line(ax_line)
        self._render_bar(ax_bar)
        self._render_heatflow(ax_heat)
        fig.savefig(path, dpi=dpi, facecolor=C_BG, bbox_inches="tight")

    # ── 占位图 ────────────────────────────────────────────────────────────
    def _draw_placeholder(self):
        self._fig.clear()
        ax = self._fig.add_subplot(111)
        ax.set_facecolor(C_BG)
        for sp in ax.spines.values(): sp.set_visible(False)
        ax.tick_params(left=False,bottom=False,labelleft=False,labelbottom=False)
        ax.text(0.5,0.54,"点击工具栏「▶ 全部分析」生成热环境分析",
                ha="center",va="center",fontsize=13,color="#9aa0b0",transform=ax.transAxes)
        ax.text(0.5,0.46,"包含: 逐月自然室温 / 热舒适评价 / 热流分解",
                ha="center",va="center",fontsize=10,color="#b0b8cc",transform=ax.transAxes)
        self._fig.patch.set_facecolor(C_BG)
        self._canvas.draw()

    # ── 主绘制 ────────────────────────────────────────────────────────────
    def _draw_charts(self):
        if self._result is None:
            self._draw_placeholder(); return
        self._fig.clear()
        gs = self._fig.add_gridspec(
            2, 2,
            width_ratios=[3, 1],
            height_ratios=[2.2, 1],
            left=0.07, right=0.96,
            top=0.91,  bottom=0.09,
            hspace=0.42, wspace=0.14,
        )
        ax_line = self._fig.add_subplot(gs[0, 0])
        ax_bar  = self._fig.add_subplot(gs[0, 1])
        ax_heat = self._fig.add_subplot(gs[1, :])

        self._render_line(ax_line)
        self._render_bar(ax_bar)
        self._render_heatflow(ax_heat)

        self._fig.patch.set_facecolor(C_BG)
        self._canvas.draw()

    def _render_line(self, ax):
        """逐月室温折线图"""
        res = self._result
        x   = np.arange(12)
        ax.set_facecolor(C_BG)

        # 舒适带背景
        ax.axhspan(T_COMFORT_LOW, T_COMFORT_HIGH,
                   color="#dcfce7", alpha=0.55, zorder=1, label="舒适区 18-26℃")

        # 上下限线
        ax.axhline(T_COMFORT_HIGH, color=C_HOT, lw=1.2, ls="--", alpha=0.7)
        ax.axhline(T_COMFORT_LOW,  color=C_COLD, lw=1.2, ls="--", alpha=0.7)

        # 室外温度
        ax.plot(x, res.T_out, color=C_TEXTSEC, lw=1.5, ls=":",
                marker="o", ms=4, alpha=0.8, label="室外温度 T_out")

        # 室内温度（分段着色：超温/欠温/舒适）
        T_in = res.T_in
        ax.plot(x, T_in, color=C_ACCENT, lw=2.2, marker="s", ms=5,
                zorder=4, label="自然室温 T_in")

        # 填色
        ax.fill_between(x, T_in, T_COMFORT_HIGH,
                         where=T_in > T_COMFORT_HIGH,
                         color=C_HOT, alpha=0.15, label="过热区")
        ax.fill_between(x, T_in, T_COMFORT_LOW,
                         where=T_in < T_COMFORT_LOW,
                         color=C_COLD, alpha=0.15, label="过冷区")

        # 数值标注
        for i, t in enumerate(T_in):
            ax.text(i, t + 0.8, f"{t:.1f}",
                    ha="center", va="bottom", fontsize=7.5,
                    color=C_HOT if t > T_COMFORT_HIGH else (
                        C_COLD if t < T_COMFORT_LOW else C_ACCENT),
                    fontweight="semibold")

        ax.set_xticks(x)
        ax.set_xticklabels([f"{m}月" for m in range(1,13)],
                            fontsize=8, color=C_TEXTSEC)
        ax.set_ylabel("温度 (℃)", color=C_TEXTSEC, fontsize=9)
        ax.set_title(
            f"逐月自然室温   年均 {res.T_in_annual_avg:.1f}℃   "
            f"舒适{res.comfort_months}月 / 超温{res.overheat_months}月 / "
            f"欠温{res.underheat_months}月",
            color=C_TEXT, fontsize=9, fontweight="bold", pad=5, loc="left")
        ax.legend(fontsize=7.5, loc="upper left",
                   labelcolor=C_TEXT, facecolor="#ffffff", edgecolor=C_BORDER)
        ax.tick_params(colors=C_TEXTSEC, labelsize=8, length=3)
        ax.grid(True, color=C_GRID, lw=0.5)
        for sp in ax.spines.values():
            sp.set_color(C_BORDER); sp.set_linewidth(0.6)
        ax.set_facecolor(C_BG)

    def _render_bar(self, ax):
        """月份状态条形图"""
        res = self._result
        T_in = res.T_in
        colors = []
        for t in T_in:
            if t > T_COMFORT_HIGH:   colors.append(C_HOT)
            elif t < T_COMFORT_LOW:  colors.append(C_COLD)
            else:                    colors.append(C_OK)

        x = np.arange(12)
        ax.barh(x, [1]*12, color=colors, alpha=0.85, height=0.72)
        ax.set_yticks(x)
        ax.set_yticklabels([f"{m}月" for m in range(1,13)], fontsize=8, color=C_TEXTSEC)
        ax.set_xticks([])
        ax.set_xlim(0,1)
        ax.set_title("舒适判定", fontsize=9, color=C_TEXT, fontweight="bold", pad=5)
        for sp in ax.spines.values(): sp.set_visible(False)
        ax.tick_params(length=0)
        ax.set_facecolor(C_BG)

        # 图例
        from matplotlib.patches import Patch
        legend_elements = [
            Patch(fc=C_HOT,  label=f"过热 (>{T_COMFORT_HIGH:.0f}℃)"),
            Patch(fc=C_OK,   label="舒适"),
            Patch(fc=C_COLD, label=f"过冷 (<{T_COMFORT_LOW:.0f}℃)"),
        ]
        ax.legend(handles=legend_elements, fontsize=7, loc="lower right",
                   labelcolor=C_TEXT, facecolor="#ffffff", edgecolor=C_BORDER,
                   framealpha=0.95)

    def _render_heatflow(self, ax):
        """热流分解堆积条形图"""
        res = self._result
        x   = np.arange(12)
        ax.set_facecolor(C_BG)

        w = 0.6
        ax.bar(x, res.Q_solar/1000,      width=w, label="太阳辐射得热",      color="#f59e0b", alpha=0.85)
        ax.bar(x, res.Q_wall_solar/1000, width=w, label="外墙辐射附加",
               bottom=res.Q_solar/1000,  color="#fbbf24", alpha=0.7)
        ax.bar(x, res.Q_int/1000,        width=w, label="内热扰",
               bottom=(res.Q_solar+res.Q_wall_solar)/1000, color="#6366f1", alpha=0.8)

        ax.set_xticks(x)
        ax.set_xticklabels([f"{m}月" for m in range(1,13)], fontsize=8, color=C_TEXTSEC)
        ax.set_ylabel("热流 (kW)", color=C_TEXTSEC, fontsize=8)
        ax.set_title("逐月热流分解", fontsize=9, color=C_TEXT, fontweight="bold", pad=3)
        ax.legend(fontsize=7.5, loc="upper right",
                   labelcolor=C_TEXT, facecolor="#ffffff", edgecolor=C_BORDER)
        ax.grid(True, axis="y", color=C_GRID, lw=0.5)
        for sp in ax.spines.values():
            sp.set_color(C_BORDER); sp.set_linewidth(0.6)
        ax.tick_params(colors=C_TEXTSEC, labelsize=8, length=3)
