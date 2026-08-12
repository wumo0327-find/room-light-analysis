"""
ui/analysis_panel.py — 采光分析热力图面板  v2.2.0
白色学术主题 + 热力图/截面图分离导出
"""
from __future__ import annotations
from typing import Optional

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from matplotlib import patches, colors as mcolors
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QSizePolicy, QCheckBox,
)
from PyQt6.QtCore import Qt

from core.daylight import DaylightResult
from core.models import RoomModel

# ── 学术白色主题颜色 ──────────────────────────────────────────────────────────
C_BG      = "#ffffff"
C_PANEL   = "#f5f6f8"
C_TEXT    = "#1a1e2e"
C_TEXTSEC = "#5a6175"
C_ACCENT  = "#2563eb"
C_BORDER  = "#d0d5e0"
C_GRID    = "#eef0f4"

THRESH_E  = 300.0
THRESH_U0 = 0.70

# matplotlib 全局字体设置（白色主题下统一）
_MPL_PARAMS = {
    "axes.facecolor":    C_BG,
    "figure.facecolor":  C_BG,
    "axes.edgecolor":    C_BORDER,
    "axes.labelcolor":   C_TEXTSEC,
    "xtick.color":       C_TEXTSEC,
    "ytick.color":       C_TEXTSEC,
    "text.color":        C_TEXT,
    "axes.spines.top":   False,
    "axes.spines.right": False,
    "axes.grid":         True,
    "grid.color":        C_GRID,
    "grid.linewidth":    0.5,
    "legend.framealpha": 0.92,
    "legend.edgecolor":  C_BORDER,
    "legend.facecolor":  "#ffffff",
    "legend.labelcolor": C_TEXT,
}


class AnalysisPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._result: Optional[DaylightResult] = None
        self._room:   Optional[RoomModel]      = None
        self._show_labels = True

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_kpi_bar())
        root.addWidget(self._build_control_bar())

        # ── 主画布（热力图 + 截面图） ──────────────────────────────────────
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
            "background:#f5f6f8;"
            "border-bottom:1px solid #d0d5e0;")
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
                sep.setStyleSheet("color:#d0d5e0; max-width:1px;")
                lay.addWidget(sep)

            cell = QWidget()
            cell.setStyleSheet("background:transparent;")
            cl = QVBoxLayout(cell)
            cl.setContentsMargins(10, 2, 10, 2)
            cl.setSpacing(0)
            cl.setAlignment(Qt.AlignmentFlag.AlignCenter)

            t = QLabel(title)
            t.setAlignment(Qt.AlignmentFlag.AlignCenter)
            t.setStyleSheet("color:#9aa0b0;font-size:10px;background:transparent;")

            v = QLabel("—")
            v.setAlignment(Qt.AlignmentFlag.AlignCenter)
            v.setStyleSheet(
                "color:#1a1e2e;font-size:17px;font-weight:700;"
                "background:transparent;")

            u = QLabel(unit)
            u.setAlignment(Qt.AlignmentFlag.AlignCenter)
            u.setStyleSheet("color:#c0c5d0;font-size:9px;background:transparent;")

            cl.addWidget(t); cl.addWidget(v); cl.addWidget(u)
            lay.addWidget(cell, 1)
            self._kpis[key] = (v, thresh, hi_good)
        return bar

    def _update_kpi(self, res: DaylightResult):
        q = res.quick or {}
        vals = {
            "E_avg":  f"{res.E_avg:.1f}",
            "E_min":  f"{res.E_min:.1f}",
            "E_max":  f"{res.E_max:.1f}",
            "U0":     f"{res.U0:.4f}",
            "DF_avg": f"{res.DF_avg:.4f}",
            "WFR":    f"{q.get('WFR',0):.4f}",
        }
        for key, (lbl, thresh, hi_good) in self._kpis.items():
            s = vals.get(key, "—")
            lbl.setText(s)
            if thresh is not None:
                try:
                    ok = float(s) >= thresh
                    col = "#16a34a" if ok else "#dc2626"
                    lbl.setStyleSheet(
                        f"color:{col};font-size:17px;font-weight:700;"
                        "background:transparent;")
                except Exception:
                    pass
            else:
                lbl.setStyleSheet(
                    "color:#1a1e2e;font-size:17px;font-weight:700;"
                    "background:transparent;")

    # ── 控制栏 ────────────────────────────────────────────────────────────
    def _build_control_bar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(32)
        bar.setStyleSheet(
            "background:#ffffff;"
            "border-bottom:1px solid #eef0f4;")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(14, 4, 14, 4)
        lay.setSpacing(14)

        self._chk_labels = QCheckBox("显示网格数值")
        self._chk_labels.setChecked(True)
        self._chk_labels.stateChanged.connect(self._on_label_toggle)
        lay.addWidget(self._chk_labels)

        hint = QLabel(f"精度: WIN_DIV=40 · GRID=250mm")
        hint.setStyleSheet("color:#b0b8cc;font-size:11px;background:transparent;")
        lay.addWidget(hint)
        lay.addStretch()

        self._wx_info = QLabel("")
        self._wx_info.setStyleSheet(
            "color:#9aa0b0;font-size:11px;background:transparent;")
        lay.addWidget(self._wx_info)
        return bar

    def set_weather_info(self, text: str):
        self._wx_info.setText(text)

    def _on_label_toggle(self, state):
        self._show_labels = bool(state)
        if self._result is not None:
            self._draw_heatmap()

    # ── 公共接口 ──────────────────────────────────────────────────────────
    def update(self, result: DaylightResult, room: RoomModel,
               weather_info: str = ""):
        self._result = result
        self._room   = room
        self._update_kpi(result)
        if weather_info:
            self.set_weather_info(weather_info)
        self._draw_heatmap()

    # ── 导出接口（分为两张图） ────────────────────────────────────────────
    def save_heatmap(self, path: str, dpi: int = 200):
        """仅导出热力图部分（论文插图用）"""
        if self._result is None or self._room is None:
            return
        fig = Figure(figsize=(8, 6), facecolor=C_BG)
        ax_m  = fig.add_subplot(121)
        ax_cb = fig.add_subplot(122)
        fig.subplots_adjust(left=0.08, right=0.88, top=0.88, bottom=0.10,
                            wspace=0.06)
        self._render_heatmap_ax(ax_m, ax_cb, fig)
        fig.savefig(path, dpi=dpi, facecolor=C_BG,
                    bbox_inches="tight", edgecolor="none")

    def save_profile(self, path: str, dpi: int = 200):
        """仅导出截面分布图部分（论文插图用）"""
        if self._result is None or self._room is None:
            return
        fig = Figure(figsize=(8, 4), facecolor=C_BG)
        ax = fig.add_subplot(111)
        fig.subplots_adjust(left=0.10, right=0.96, top=0.88, bottom=0.14)
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
                "点击工具栏「▶ 开始分析」生成采光热力图",
                ha="center", va="center", fontsize=13,
                color="#9aa0b0", transform=ax.transAxes)
        ax.text(0.5, 0.46,
                "默认气象数据：湖南益阳 TMY  年均 13 906 lux",
                ha="center", va="center", fontsize=10,
                color="#c0c5d0", transform=ax.transAxes)
        self._fig.patch.set_facecolor(C_BG)
        self._canvas.draw()

    # ── 主绘制 ────────────────────────────────────────────────────────────
    def _draw_heatmap(self):
        if self._result is None or self._room is None:
            self._draw_placeholder(); return

        self._fig.clear()

        # 2列×2行：热力图(大) | 颜色条(细) / 截面图(跨两列)
        gs = self._fig.add_gridspec(
            2, 2,
            width_ratios=[1, 0.032],
            height_ratios=[2.6, 1],
            left=0.07, right=0.94,
            top=0.91, bottom=0.08,
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

    # ── 热力图核心绘制（可复用于独立导出） ──────────────────────────────
    def _render_heatmap_ax(self, ax_m, ax_cb, fig):
        res  = self._result
        room = self._room
        xs   = res.grid_x
        ys   = res.grid_y
        E    = res.E_lux
        W_mm = room.width
        L_mm = room.length

        # ── 色图：学术风格，蓝→黄→红（Plasma系） ─────────────────────────
        cmap = plt.get_cmap("RdYlGn").reversed()
        vmax = max(float(E.max())*1.05 if E.size else 1., THRESH_E*2.5)
        norm = mcolors.Normalize(vmin=0., vmax=vmax)

        def _edges(arr):
            if len(arr) < 2:
                h = arr[0]*0.5 if len(arr) else 250.
                return np.array([arr[0]-h, arr[0]+h])
            d = arr[1]-arr[0]
            return np.concatenate([[arr[0]-d/2], arr+d/2])

        XE, YE = np.meshgrid(_edges(xs), _edges(ys))
        pcm = ax_m.pcolormesh(XE, YE, E, cmap=cmap, norm=norm,
                               shading="flat", zorder=2, rasterized=True)

        # 等值线
        XC, YC = np.meshgrid(xs, ys)
        has_c  = E.shape[0] >= 3 and E.shape[1] >= 3
        if has_c:
            lvls = [l for l in [50,100,150,200,300,500,750,1000,1500,2000]
                    if 0 < l < vmax]
            if lvls:
                cs = ax_m.contour(XC, YC, E, levels=lvls,
                                   colors=["#555555"], linewidths=0.4,
                                   alpha=0.4, zorder=3)
                ax_m.clabel(cs, fmt="%g", fontsize=6., colors="#444444",
                             inline_spacing=2)

        # 300 lux 阈值线
        if has_c and THRESH_E < vmax:
            ax_m.contour(XC, YC, E, levels=[THRESH_E],
                          colors=["#d97706"], linewidths=1.6,
                          linestyles=["--"], zorder=4)

        # 房间轮廓
        ax_m.add_patch(patches.Rectangle(
            (0,0), W_mm, L_mm,
            fill=False, edgecolor="#1a1e2e",
            linewidth=1.5, zorder=5))

        # 窗户
        wt = max(W_mm, L_mm) * 0.014
        for win in room.windows:
            w = win.wall
            if   w == "south": bx,by,bw,bh = win.x,0,       win.width,wt
            elif w == "north": bx,by,bw,bh = win.x,L_mm-wt, win.width,wt
            elif w == "east":  bx,by,bw,bh = W_mm-wt,win.x, wt,win.width
            else:              bx,by,bw,bh = 0,win.x,        wt,win.width
            ax_m.add_patch(patches.Rectangle(
                (bx,by), bw, bh,
                facecolor="#bfdbfe", edgecolor=C_ACCENT,
                linewidth=1., zorder=6, alpha=0.9))

        # 网格数值标注
        if self._show_labels and E.size <= 600:
            n  = E.size
            fs = max(4.5, min(7., 8. - n*0.005))
            for iy, y in enumerate(ys):
                for ix, x in enumerate(xs):
                    v = E[iy, ix]
                    bright = norm(v) > 0.52
                    tc = "#1a1e2e" if bright else "#ffffff"
                    ax_m.text(x, y, f"{v:.0f}",
                               ha="center", va="center",
                               fontsize=fs, color=tc,
                               fontweight="light", alpha=0.88, zorder=7)

        # 颜色条
        cb = fig.colorbar(pcm, cax=ax_cb)
        cb.set_label("照度 (lux)", color=C_TEXTSEC, fontsize=9, labelpad=4)
        cb.ax.yaxis.set_tick_params(color=C_TEXTSEC, labelcolor=C_TEXTSEC,
                                     labelsize=7.5)
        cb.ax.tick_params(length=3, width=0.6)
        ax_cb.axhline(THRESH_E, color="#d97706", lw=1.2, ls="--")
        ax_cb.text(2.5, THRESH_E, "300", color="#d97706",
                   fontsize=7, va="center", fontweight="bold")

        # 合规徽章（白底）
        ok_e  = res.compliant_300
        ok_u0 = res.compliant_u0
        for i, (txt, col) in enumerate([
            (f"{'✓' if ok_e  else '✗'} Eavg {'≥' if ok_e  else '<'} 300 lux",
             "#16a34a" if ok_e  else "#dc2626"),
            (f"{'✓' if ok_u0 else '✗'} U₀ {'≥' if ok_u0 else '<'} 0.70",
             "#16a34a" if ok_u0 else "#dc2626"),
        ]):
            ax_m.text(0.012+i*0.33, 0.978, txt,
                       transform=ax_m.transAxes,
                       fontsize=8.5, color=col, fontweight="bold", va="top",
                       bbox=dict(fc="#ffffffdd", ec=col,
                                 boxstyle="round,pad=0.28", lw=0.9),
                       zorder=8)

        # 标题与轴
        ax_m.set_title(
            f"室内照度分布热力图   "
            f"$E_{{avg}}$ = {res.E_avg:.1f} lux   "
            f"$U_0$ = {res.U0:.4f}   "
            f"$DF_{{avg}}$ = {res.DF_avg:.4f}%\n"
            f"$E_{{out}}$ = {res.E_out:.0f} lux   "
            f"$\\bar{{\\rho}}$ = {res.rho_bar:.3f}   "
            f"网格 {res.grid_mm:.0f} mm",
            color=C_TEXT, fontsize=9, fontweight="bold",
            pad=6, loc="left")
        ax_m.set_xlim(0., W_mm); ax_m.set_ylim(0., L_mm)
        ax_m.set_aspect("equal", adjustable="box")
        ax_m.set_xlabel("宽度 X (mm)", color=C_TEXTSEC, fontsize=9, labelpad=3)
        ax_m.set_ylabel("长度 Y (mm)", color=C_TEXTSEC, fontsize=9, labelpad=3)
        ax_m.tick_params(colors=C_TEXTSEC, labelsize=8, length=3, width=0.6)
        for sp in ax_m.spines.values():
            sp.set_color(C_BORDER); sp.set_linewidth(0.6)
        ax_m.set_facecolor(C_BG)

    # ── 截面图核心绘制（可复用于独立导出） ──────────────────────────────
    def _render_profile_ax(self, ax):
        res  = self._result
        xs   = res.grid_x
        ys   = res.grid_y
        E    = res.E_lux
        mid_x = E.shape[1] // 2
        mid_y = E.shape[0] // 2

        ax.set_facecolor(C_BG)
        ax.plot(ys, E[:, mid_x],
                color=C_ACCENT, lw=1.8, alpha=0.9,
                label=f"纵向截面 X={xs[mid_x]:.0f}mm")
        ax.plot(xs, E[mid_y, :],
                color="#d97706", lw=1.8, ls="--", alpha=0.9,
                label=f"横向截面 Y={ys[mid_y]:.0f}mm")
        ax.axhline(THRESH_E, color="#16a34a", lw=1.2, ls=":",
                    alpha=0.9, label="300 lux 标准线")
        ax.axhline(res.E_avg, color=C_ACCENT, lw=0.8, ls="-.",
                    alpha=0.5, label=f"均值 {res.E_avg:.1f} lux")

        ax.fill_between(ys, E[:, mid_x], alpha=0.07, color=C_ACCENT)
        ax.fill_between(xs, E[mid_y, :], alpha=0.06, color="#d97706")

        ax.set_xlabel("位置 (mm)", color=C_TEXTSEC, fontsize=9, labelpad=3)
        ax.set_ylabel("照度 (lux)", color=C_TEXTSEC, fontsize=9, labelpad=3)
        ax.set_title("中心截面照度分布", color=C_TEXT, fontsize=9,
                     fontweight="bold", pad=4)
        ax.legend(fontsize=7.5, loc="upper right",
                   handlelength=1.5, handletextpad=0.4,
                   borderpad=0.4, labelspacing=0.3)
        ax.yaxis.set_minor_locator(mticker.AutoMinorLocator())
        for sp in ax.spines.values():
            sp.set_color(C_BORDER); sp.set_linewidth(0.6)
        ax.tick_params(colors=C_TEXTSEC, labelsize=8, length=3, width=0.6)
        ax.grid(True, color=C_GRID, linewidth=0.5, alpha=0.8)
