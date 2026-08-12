"""
ui/experiment_panel.py — 参数化实验标签页  v2.11.0（v2.5.1新增，v2.11.0"运行实验"按钮改白底蓝字提升可读性）
==========================================================
GUI 内一键跑「玻璃对照 + 遮阳β扫描」批量实验并内嵌显示帕累托散点图。

设计要点（复用，不重写）：
  · 批量实验/帕累托/绘图 全部调用 core/experiments.py 已验证逻辑
  · 后台运行由 MainWindow 用现有 AnalysisWorker + ProgressDialog 编排（本面板只发信号）
  · 本面板是纯视图：收集参数 → 发 run_requested；拿到结果 DataFrame → 渲染/导出
"""
from __future__ import annotations
import os
import tempfile
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QComboBox, QSpinBox, QDoubleSpinBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QFileDialog, QMessageBox, QFrame, QDialog,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPixmap, QImage

from core.experiments import DEFAULT_GLASS_SPECS, GlassSpec, plot_experiments

# 热轴指标 → (是否越大越好, 中文标签)  —— 与 run_experiments.py 一致
_Y_META = {
    "thermal_discomfort":     (False, "热不舒适度 Σ(超温+欠温) ℃·月"),
    "overheat_degree_months": (False, "超温强度 Σ(T−26) ℃·月"),
    "comfort_ratio":          (True,  "舒适月数占比"),
}


# ── v2.11.0 新增：参数右键说明（示意图 / 文字公式）────────────────────────────
# 能画示意图的（β、H）画一张剖面示意图；不便画图的概念（步长、热轴指标、U0下限）
# 用文字+公式解释。图只画一次、缓存复用，避免每次右键都重新渲染。
_BETA_DIAGRAM_CACHE: dict = {}


def _render_beta_h_diagram(H_mm: Optional[float] = None) -> QPixmap:
    """
    渲染"遮阳特征角β / 基准高度H"剖面示意图，返回 QPixmap。
    H_mm 传入时直接显示实际数值（与建筑视图窗高联动时用），不传则仅显示归一化示意。
    结果按 H_mm 是否给出分别缓存，避免每次都重新渲染。
    """
    cache_key = "with_H" if H_mm else "generic"
    if cache_key in _BETA_DIAGRAM_CACHE:
        return _BETA_DIAGRAM_CACHE[cache_key]

    import math
    import numpy as np
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from matplotlib import patches

    H = 1.0            # 归一化窗高（画面比例用，不代表真实米数）
    beta_demo = 30.0    # 示意用的角度（不代表实际取值）
    D = H * math.tan(math.radians(beta_demo))

    fig = Figure(figsize=(5.0, 3.8), dpi=150, facecolor="#ffffff")
    canvas = FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)
    ax.set_facecolor("#ffffff")

    # 室内/室外背景色块（以墙体 x=0 为界），先画在最底层，帮助辨认方位
    ax.axvspan(-0.62, 0, color="#f5f6f8", zorder=0)
    ax.axvspan(0, D + 0.75, color="#eef6ff", zorder=0)
    ax.text(-0.31, H + 0.55, "室内", color="#9aa0b0", fontsize=9, ha="center")
    ax.text(D * 0.5 + 0.2, H + 0.55, "室外", color="#60a5fa", fontsize=9, ha="center")

    # 地面（贯穿室内外，同一条线，不留断口）
    ax.plot([-0.62, D + 0.75], [0, 0], color="#9aa0b0", lw=1.2, zorder=1)
    # 墙体（竖线，从地面到顶，窗户和挑檐都贴着它）
    ax.plot([0, 0], [0, H + 0.35], color="#5a6175", lw=3, zorder=2,
            solid_capstyle="butt")
    # 窗户（贴墙的浅蓝竖向色块，底=地面，高度=H，与地面/墙体完全对齐无空隙）
    ax.add_patch(patches.Rectangle((-0.035, 0), 0.035, H, facecolor="#bfdbfe",
                                   edgecolor="#2563eb", lw=1.0, zorder=3))
    # 挑檐（顶部水平深色条，从墙面出挑到 D）
    ax.add_patch(patches.Rectangle((0, H), D, 0.035, facecolor="#f59e0b",
                                   edgecolor="#b45309", lw=1.0, zorder=4))
    # 视线：窗底(0,0) → 挑檐外沿(D,H)
    ax.plot([0, D], [0, H], "--", color="#dc2626", lw=1.4, zorder=3)
    # β 角弧线（在原点，介于地面与视线之间）
    arc_r = 0.32
    t = np.linspace(0, math.radians(beta_demo), 40)
    ax.plot(arc_r * np.cos(t), arc_r * np.sin(t), color="#dc2626", lw=1.2, zorder=3)
    ax.text(arc_r * 1.3, 0.10, "β", color="#dc2626", fontsize=13,
            fontweight="bold", ha="center", va="center")

    # H 标注（窗高，竖向双箭头，画在窗户左侧；文字一律水平书写，不用旋转文字，
    # 避免旋转的中文/公式混排看着"乱"）
    ax.annotate("", xy=(-0.16, H), xytext=(-0.16, 0),
                arrowprops=dict(arrowstyle="<->", color="#2563eb", lw=1.2))
    h_label = f"H = {H_mm:.0f}mm" if H_mm else "H"
    ax.text(-0.24, H * 0.55, h_label, color="#2563eb", fontsize=10,
            fontweight="bold", ha="right", va="center")
    ax.text(-0.24, H * 0.55 - 0.16, "(窗高/基准高度)", color="#2563eb",
            fontsize=8, ha="right", va="center")

    # D 标注（出挑深度，水平双箭头，画在挑檐上方；文字与箭头留足垂直间距，
    # 且整体右移避免"D"字形被墙体竖线切穿看着像"0"）
    ax.annotate("", xy=(D, H + 0.06), xytext=(0, H + 0.06),
                arrowprops=dict(arrowstyle="<->", color="#b45309", lw=1.1))
    ax.text(0.08, H + 0.26, "D = H·tan(β)", color="#b45309", fontsize=10,
            fontweight="bold", ha="left", va="center")

    # 阳光示意（2条平行斜线，从右上方射向挑檐外沿，位置避开 D 标注文字）
    for dx in (0.0, 0.20):
        x0, y0 = D + 0.55 - dx, H + 0.62
        x1, y1 = x0 - 0.42, y0 - 0.42
        ax.annotate("", xy=(x1, y1), xytext=(x0, y0),
                    arrowprops=dict(arrowstyle="->", color="#f59e0b", lw=1.1, alpha=0.8))
    ax.text(D + 0.42, H + 0.68, "阳光", color="#b45309", fontsize=8, ha="center")

    ax.set_xlim(-0.62, D + 0.75)
    ax.set_ylim(-0.12, H + 0.85)
    ax.set_aspect("equal")
    ax.axis("off")
    sub = f"（当前 H={H_mm:.0f}mm 取自建筑视图窗高；β=30°仅为示意角度）" if H_mm \
        else "（β=30°、H 均为示意，不代表实际取值）"
    ax.set_title("遮阳特征角 β 与基准高度 H 示意\n" + sub,
                 fontsize=10, color="#1a1e2e", pad=6)
    fig.tight_layout()

    canvas.draw()
    w, h = int(fig.bbox.width), int(fig.bbox.height)
    buf = bytes(canvas.buffer_rgba())
    qimg = QImage(buf, w, h, QImage.Format.Format_RGBA8888)
    pixmap = QPixmap.fromImage(qimg.copy())
    _BETA_DIAGRAM_CACHE[cache_key] = pixmap
    return pixmap


class _ParamHelpDialog(QDialog):
    """右键弹出的参数说明窗口：可放示意图（QLabel+QPixmap），也可放文字/公式。"""

    def __init__(self, title: str, pixmap: Optional[QPixmap] = None,
                html: Optional[str] = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        if pixmap is not None:
            img_lbl = QLabel()
            img_lbl.setPixmap(pixmap)
            img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lay.addWidget(img_lbl)
        if html is not None:
            txt_lbl = QLabel(html)
            txt_lbl.setTextFormat(Qt.TextFormat.RichText)
            txt_lbl.setWordWrap(True)
            txt_lbl.setStyleSheet("color:#1a1e2e;font-size:12px;")
            txt_lbl.setMinimumWidth(360)
            lay.addWidget(txt_lbl)
        btn = QPushButton("知道了")
        btn.setFixedHeight(28)
        btn.clicked.connect(self.accept)
        lay.addWidget(btn, 0, Qt.AlignmentFlag.AlignRight)


class ExperimentPanel(QWidget):
    """参数化实验视图：参数输入 + 结果散点图 + 导出。"""
    run_requested = pyqtSignal(dict)   # 收集好的实验参数

    def __init__(self, parent=None):
        super().__init__(parent)
        self._df = None                 # 最近一次结果 DataFrame
        self._last_params: dict = {}
        self._orig_pixmap: Optional[QPixmap] = None
        self._tmp_png = os.path.join(tempfile.gettempdir(),
                                     "room_light_pareto.png")

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        title = QLabel("参数化实验 — 玻璃对照 + 遮阳β扫描 → 帕累托前沿")
        title.setStyleSheet("font-size:15px;font-weight:700;color:#2563eb;")
        root.addWidget(title)

        root.addWidget(self._build_controls())

        # 主体：左=玻璃表格 / 右=帕累托图
        body = QHBoxLayout(); body.setSpacing(12)
        body.addWidget(self._build_glass_table(), 0)
        body.addWidget(self._build_plot_area(), 1)
        root.addLayout(body, 1)

    # ── 控制区 ────────────────────────────────────────────────────────────
    def _build_controls(self) -> QWidget:
        box = QFrame()
        box.setStyleSheet("QFrame{background:#f5f6f8;border:1px solid #d0d5e0;"
                          "border-radius:6px;}")
        g = QGridLayout(box); g.setContentsMargins(12, 10, 12, 10)
        g.setHorizontalSpacing(8); g.setVerticalSpacing(8)

        def _lbl(t):
            l = QLabel(t); l.setStyleSheet("color:#5a6175;background:transparent;")
            return l

        # β 扫描范围
        self._beta_min  = self._spin(0, 0, 89, 0)
        self._beta_max  = self._spin(0, 0, 89, 60)
        self._beta_step = self._spin(1, 1, 30, 5)
        self._H_mm      = self._spin(100, 5000, 100000, 1500)  # 基准高度 H(mm)
        self._H_mm.setToolTip("默认与「建筑视图」当前窗户的窗高联动同步（每次切换到本"
                              "标签页会自动刷新），也可手动改成别的值试算；手动改动只在"
                              "本次停留期间保留，切走再切回会被重新同步覆盖。")
        g.addWidget(_lbl("遮阳β范围(°)"), 0, 0)
        g.addWidget(self._beta_min, 0, 1)
        g.addWidget(_lbl("~"),        0, 2)
        g.addWidget(self._beta_max, 0, 3)
        g.addWidget(_lbl("步长"),     0, 4)
        g.addWidget(self._beta_step, 0, 5)
        g.addWidget(_lbl("H(mm)"),    0, 6)
        g.addWidget(self._H_mm, 0, 7)

        # 热轴指标 + U0 下限
        self._y_combo = QComboBox()
        for k, (_mx, lab) in _Y_META.items():
            self._y_combo.addItem(lab, userData=k)
        self._y_combo.setFixedHeight(28)
        self._u0_min = QDoubleSpinBox()
        self._u0_min.setRange(0.0, 1.0); self._u0_min.setSingleStep(0.05)
        self._u0_min.setDecimals(2); self._u0_min.setValue(0.0)
        self._u0_min.setFixedHeight(28)
        self._u0_min.setToolTip("合规筛选 U0 下限。侧窗深房间 U0 常<0.70，"
                                "默认 0 以显示全部点；调高则滤除不合规点。")
        g.addWidget(_lbl("热轴指标"), 1, 0)
        g.addWidget(self._y_combo, 1, 1, 1, 3)
        g.addWidget(_lbl("U0下限"), 1, 4)
        g.addWidget(self._u0_min, 1, 5)

        # 按钮
        self._btn_run = QPushButton("▶  运行实验")
        self._btn_run.setFixedHeight(30)
        # v2.11.0: 白底蓝字，与其余控件一致的白色学术主题，避免深色底配色看不清
        self._btn_run.setStyleSheet(
            "QPushButton{background-color:#ffffff;color:#2563eb;"
            "border:1px solid #2563eb;border-radius:4px;font-weight:700;}"
            "QPushButton:hover{background-color:#eff6ff;}"
            "QPushButton:pressed{background-color:#dbeafe;}")
        self._btn_run.clicked.connect(self._on_run)
        self._btn_png = QPushButton("↓ 导出PNG")
        self._btn_csv = QPushButton("↓ 导出CSV")
        for b in (self._btn_png, self._btn_csv):
            b.setFixedHeight(30); b.setEnabled(False)
        self._btn_png.clicked.connect(self._export_png)
        self._btn_csv.clicked.connect(self._export_csv)
        g.addWidget(self._btn_run, 1, 6)
        g.addWidget(self._btn_png, 1, 7)
        g.addWidget(self._btn_csv, 1, 8)

        hint = QLabel("💡 不清楚某个参数是什么意思？在对应输入框上单击右键查看说明")
        hint.setStyleSheet("color:#9aa0b0;font-size:11px;background:transparent;")
        g.addWidget(hint, 2, 0, 1, 9)

        # v2.11.0: 右键参数控件查看示意图/文字说明（不懂这些数值含义时用）
        for w in (self._beta_min, self._beta_max, self._H_mm):
            self._attach_diagram_help(w)
        self._attach_text_help(self._beta_step, "步长 是什么？", (
            "<b>步长</b>决定这次实验测试得有多密。<br><br>"
            "程序会从「起始角度」到「终止角度」之间，每隔一个「步长」就测一次，"
            "每一次都要完整跑一遍采光+温度计算。<br><br>"
            "例如范围 0°~60°、步长 5°，就会测：<br>"
            "0°, 5°, 10°, 15°, …, 60°，共 13 种遮阳深度。<br><br>"
            "步长越小 → 测得越细，但要跑的次数越多、越慢；"
            "步长越大 → 跑得快，但可能错过中间更好的方案。"))
        self._attach_text_help(self._y_combo, "热轴指标 是什么？", (
            "<b>热轴指标</b>决定帕累托图纵轴用什么数字衡量"
            "「这个方案冬冷夏热的严重程度」，数值越小代表全年住着越舒服。"
            "下拉框里的几个选项：<br><br>"
            "· <b>热不舒适度 Σ(超温+欠温) ℃·月</b><br>"
            "&nbsp;&nbsp;= Σmax(室温−26,0) + Σmax(18−室温,0)<br>"
            "&nbsp;&nbsp;把一年12个月「超过26℃多几度」和「低于18℃少几度」全部加总。<br><br>"
            "· <b>超温强度 Σ(T−26) ℃·月</b><br>"
            "&nbsp;&nbsp;= Σmax(室温−26,0)，只算"
            "过热部分，不考虑冬天偏冷。<br><br>"
            "· <b>舒适月数占比</b><br>"
            "&nbsp;&nbsp;= 全年温度落在18~26℃之间的月数 ÷ 12<br>"
            "&nbsp;&nbsp;是一个0~1之间、越大越好的比例（离散指标，方案接近时区分度较差）。"))
        self._attach_text_help(self._u0_min, "U0下限 是什么？", (
            "<b>U0（采光均匀度）</b> = 房间里最暗处的照度 ÷ 房间平均照度，"
            "取值 0~1，越接近 1 说明房间各处亮度越均匀（没有明显的暗角）。"
            "侧窗房间靠窗亮、进深处暗是常态，U0 天生不容易很高。<br><br>"
            "<b>U0下限</b>是一个筛选门槛：只有 U0 达到这个数值以上的方案才算"
            "「合规」、才会被算进帕累托前沿。设为 0 表示不筛选，"
            "所有测试结果都保留显示。"))
        return box

    def _attach_diagram_help(self, widget) -> None:
        """右键弹出β/H剖面示意图（图中H会显示当前H(mm)输入框的实际数值）。"""
        widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        widget.customContextMenuRequested.connect(
            lambda _pos: _ParamHelpDialog(
                "遮阳特征角 β 与基准高度 H",
                pixmap=_render_beta_h_diagram(self._H_mm.value()),
                parent=self).exec())

    def _attach_text_help(self, widget, title: str, html: str) -> None:
        """右键弹出文字/公式说明（不便画示意图的概念）。"""
        widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        widget.customContextMenuRequested.connect(
            lambda _pos, t=title, h=html: _ParamHelpDialog(
                t, html=h, parent=self).exec())

    def _spin(self, mn, mx, big, val) -> QSpinBox:
        s = QSpinBox(); s.setRange(int(mn), int(big)); s.setValue(int(val))
        s.setFixedHeight(28)
        # mx 参数保留以兼容调用（此处上限用 big）
        return s

    # ── 玻璃对照组表格 ────────────────────────────────────────────────────
    def _build_glass_table(self) -> QWidget:
        w = QWidget(); w.setFixedWidth(340)
        lay = QVBoxLayout(w); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(6)
        cap = QLabel("玻璃对照组（可编辑；成本为占位值）")
        cap.setStyleSheet("color:#5a6175;font-size:12px;")
        lay.addWidget(cap)

        self._glass_tbl = QTableWidget(len(DEFAULT_GLASS_SPECS), 4)
        self._glass_tbl.setHorizontalHeaderLabels(["玻璃类型", "Tvis", "SC", "成本"])
        self._glass_tbl.verticalHeader().setVisible(False)
        self._glass_tbl.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        for c in (1, 2, 3):
            self._glass_tbl.horizontalHeader().setSectionResizeMode(
                c, QHeaderView.ResizeMode.ResizeToContents)
        for i, gspec in enumerate(DEFAULT_GLASS_SPECS):
            for c, val in enumerate([gspec.label, f"{gspec.tvis:.2f}",
                                     f"{gspec.sc:.2f}", f"{gspec.cost:.0f}"]):
                it = QTableWidgetItem(val)
                if c != 0:
                    it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._glass_tbl.setItem(i, c, it)
        self._glass_tbl.setStyleSheet("font-size:12px;background:#ffffff;")
        lay.addWidget(self._glass_tbl, 1)
        return w

    # ── 绘图区 ────────────────────────────────────────────────────────────
    def _build_plot_area(self) -> QWidget:
        self._plot_lbl = QLabel(
            "设置参数后点「▶ 运行实验」\n"
            "完成后此处显示帕累托散点图（后台线程计算，不卡界面）")
        self._plot_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._plot_lbl.setStyleSheet(
            "color:#9aa0b0;background:#ffffff;border:1px solid #e8ebf2;"
            "border-radius:6px;")
        self._plot_lbl.setMinimumSize(480, 360)
        return self._plot_lbl

    # ── 收集参数 → 发信号 ─────────────────────────────────────────────────
    def _collect_glass_specs(self):
        specs = []
        for i in range(self._glass_tbl.rowCount()):
            try:
                label = self._glass_tbl.item(i, 0).text().strip()
                tvis  = float(self._glass_tbl.item(i, 1).text())
                sc    = float(self._glass_tbl.item(i, 2).text())
                cost  = float(self._glass_tbl.item(i, 3).text())
                if not label:
                    continue
                specs.append(GlassSpec(label=label, tvis=tvis, sc=sc, cost=cost))
            except (AttributeError, ValueError):
                continue
        return specs

    def _on_run(self):
        bmin, bmax = self._beta_min.value(), self._beta_max.value()
        step = max(1, self._beta_step.value())
        if bmax < bmin:
            QMessageBox.warning(self, "提示", "β 上限不能小于下限。"); return
        beta_degs = list(range(bmin, bmax + 1, step))
        specs = self._collect_glass_specs()
        if not specs:
            QMessageBox.warning(self, "提示", "玻璃对照组表格无有效数据。"); return
        ykey = self._y_combo.currentData()
        params = {
            "beta_degs":  beta_degs,
            "H_mm":       float(self._H_mm.value()),
            "glass_specs": specs,
            "y":          ykey,
            "maximize_y": _Y_META[ykey][0],
            "y_label":    _Y_META[ykey][1],
            "u0_min":     float(self._u0_min.value()),
        }
        self._last_params = params
        self.run_requested.emit(params)

    # ── 与建筑视图窗户联动（v2.11.0 新增）──────────────────────────────────
    def sync_H_from_room(self, room) -> None:
        """把 H(mm) 同步为建筑视图当前窗户的窗高。找不到窗户则保持原值不变。
        由 MainWindow 在切换到本标签页 / 窗户改动时调用。"""
        if room is None or not room.windows:
            return
        h = int(room.windows[0].height)
        self._H_mm.blockSignals(True)
        self._H_mm.setValue(h)
        self._H_mm.blockSignals(False)

    # ── 接收结果 → 渲染 ───────────────────────────────────────────────────
    # 内嵌预览用超采样 dpi：远高于常见屏幕物理分辨率，缩小显示时保持清晰
    _EMBED_DPI = 300

    def show_result(self, df):
        """由 MainWindow 在后台线程完成后调用，df 为结果 DataFrame。"""
        self._df = df
        p = self._last_params
        try:
            plot_experiments(
                df, out_path=self._tmp_png, x="Ra", y=p["y"],
                maximize_y=p["maximize_y"], size_col="cost",
                u0_min=p["u0_min"], y_label=p["y_label"], dpi=self._EMBED_DPI)
            self._orig_pixmap = QPixmap(self._tmp_png)
            # 按屏幕物理像素比标记该 pixmap，避免 Qt 在高分屏下把已缩放的图再拉伸一次导致模糊
            dpr = self.devicePixelRatioF() or 1.0
            self._orig_pixmap.setDevicePixelRatio(dpr)
            self._rescale_plot()
            self._btn_png.setEnabled(True)
            self._btn_csv.setEnabled(True)
        except Exception as e:
            QMessageBox.critical(self, "绘图失败", str(e))

    def _rescale_plot(self):
        if self._orig_pixmap and not self._orig_pixmap.isNull():
            dpr = self.devicePixelRatioF() or 1.0
            target = self._plot_lbl.size() * dpr
            scaled = self._orig_pixmap.scaled(
                target,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            scaled.setDevicePixelRatio(dpr)
            self._plot_lbl.setPixmap(scaled)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._rescale_plot()

    # ── 导出 ──────────────────────────────────────────────────────────────
    def _export_png(self):
        if self._df is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出帕累托图", "pareto_scatter.png", "PNG 图片 (*.png)")
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        p = self._last_params
        try:
            plot_experiments(
                self._df, out_path=path, x="Ra", y=p["y"],
                maximize_y=p["maximize_y"], size_col="cost",
                u0_min=p["u0_min"], y_label=p["y_label"], dpi=200)
            QMessageBox.information(self, "导出完成", f"已保存:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))

    def _export_csv(self):
        if self._df is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出结果表", "experiment_results.csv", "CSV 文件 (*.csv)")
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
        try:
            self._df.to_csv(path, index=False, encoding="utf-8-sig")
            QMessageBox.information(self, "导出完成", f"已保存:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))
