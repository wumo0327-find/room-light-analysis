"""
ui/experiment_panel.py — 参数化实验标签页  v2.12.0（v2.5.1新增，v2.11.0"运行实验"按钮改白底蓝字提升可读性，v2.12.0遮阳组由β/H改为θ倾斜角×L板长网格+材料预设+安装间隙h）
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
    QHeaderView, QFileDialog, QMessageBox, QFrame, QDialog, QStackedWidget,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPixmap, QImage
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas

from core.experiments import (
    DEFAULT_GLASS_SPECS, GlassSpec, plot_experiments, build_pareto3d_figure,
)
from core.models import MATERIAL_PRESETS

# 热轴指标 → (是否越大越好, 中文标签)  —— 与 run_experiments.py 一致
_Y_META = {
    "thermal_discomfort":     (False, "热不舒适度 Σ(超温+欠温) ℃·月"),
    "overheat_degree_months": (False, "超温强度 Σ(T−26) ℃·月"),
    "comfort_ratio":          (True,  "舒适月数占比"),
}


# ── v2.11.0 新增，v2.12.0 改画θ/L：参数右键说明（示意图 / 文字公式）───────────
# 能画示意图的（θ、L）画一张剖面示意图；不便画图的概念（步长、材料、h、热轴
# 指标、U0下限）用文字+公式解释。图只画一次、缓存复用，避免每次右键都重新渲染。
_TILT_DIAGRAM_CACHE: dict = {}


def _render_tilt_diagram() -> QPixmap:
    """
    渲染"倾斜角θ / 板长L"剖面示意图，返回 QPixmap（缓存一次）。
    同时画出 θ=90°(水平/虚线参照)、θ=120°(上扬30°示例)、θ=60°(下垂30°示例)
    三根从同一板根点出发的板方向，直观展示"θ=90°为水平分界、大于/小于90°
    对应上扬/下垂"这个约定，不需要额外的正负号。
    """
    if "pixmap" in _TILT_DIAGRAM_CACHE:
        return _TILT_DIAGRAM_CACHE["pixmap"]

    import math
    import numpy as np
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from matplotlib import patches

    H = 1.0     # 归一化窗高（画面比例用，不代表真实米数）
    L = 1.0     # 归一化板长

    def tip(theta_deg):
        th = math.radians(theta_deg)
        return L * math.sin(th), H - L * math.cos(th)   # (出挑s, 高度z)，根部在(0,H)

    s90, z90 = tip(90.0)     # 水平参照
    s_up, z_up = tip(120.0)  # 上扬30°示例
    s_dn, z_dn = tip(60.0)   # 下垂30°示例
    s_max = max(s90, s_up, s_dn)

    fig = Figure(figsize=(5.2, 4.0), dpi=150, facecolor="#ffffff")
    canvas = FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)
    ax.set_facecolor("#ffffff")

    # 室内/室外背景色块
    ax.axvspan(-0.62, 0, color="#f5f6f8", zorder=0)
    ax.axvspan(0, s_max + 0.6, color="#eef6ff", zorder=0)
    ax.text(-0.31, max(z_up, H) + 0.35, "室内", color="#9aa0b0", fontsize=9, ha="center")
    ax.text(s_max * 0.5 + 0.15, max(z_up, H) + 0.35, "室外", color="#60a5fa", fontsize=9, ha="center")

    # 地面 + 墙体 + 窗户
    ax.plot([-0.62, s_max + 0.6], [0, 0], color="#9aa0b0", lw=1.2, zorder=1)
    ax.plot([0, 0], [0, max(z_up, H) + 0.5], color="#5a6175", lw=3, zorder=2,
            solid_capstyle="butt")
    ax.add_patch(patches.Rectangle((-0.035, 0), 0.035, H, facecolor="#bfdbfe",
                                   edgecolor="#2563eb", lw=1.0, zorder=3))

    # 水平参照（θ=90°，虚线，代表水平分界）
    ax.plot([0, s90], [H, z90], "--", color="#9aa0b0", lw=1.3, zorder=3)
    ax.text(s90 + 0.05, z90, "θ=90°(水平)", color="#9aa0b0", fontsize=8.5, va="center")

    # 上扬示例（θ=120°=90°+30°，实心橙色板）
    ax.plot([0, s_up], [H, z_up], "-", color="#b45309", lw=3.2, zorder=4,
            solid_capstyle="butt")
    ax.text(s_up + 0.05, z_up, "θ=120°\n(上扬30°)", color="#b45309", fontsize=8.5, va="center")

    # 下垂示例（θ=60°=90°-30°，实心绿色板）
    ax.plot([0, s_dn], [H, z_dn], "-", color="#15803d", lw=3.2, zorder=4,
            solid_capstyle="butt")
    ax.text(s_dn + 0.05, z_dn - 0.08, "θ=60°\n(下垂30°)", color="#15803d", fontsize=8.5, va="center")

    # θ角弧线：分别画"水平→上扬"和"水平→下垂"两段小弧，标注偏离水平的角度
    arc_r = 0.30
    t1 = np.linspace(math.atan2(z90 - H, s90), math.atan2(z_up - H, s_up), 30)
    ax.plot(arc_r * np.cos(t1), H + arc_r * np.sin(t1), color="#b45309", lw=1.1, zorder=3)
    t2 = np.linspace(math.atan2(z_dn - H, s_dn), math.atan2(z90 - H, s90), 30)
    ax.plot(arc_r * np.cos(t2), H + arc_r * np.sin(t2), color="#15803d", lw=1.1, zorder=3)

    # L 标注（沿水平参照线标板长，L沿板自身方向，非水平投影）
    ax.annotate("", xy=(s90, z90 + 0.10), xytext=(0, H + 0.10),
                arrowprops=dict(arrowstyle="<->", color="#2563eb", lw=1.1))
    ax.text(s90 / 2, H + 0.20, "L(板长，沿板自身方向)", color="#2563eb",
            fontsize=8.5, ha="center", fontweight="bold")

    # 阳光示意
    for dx in (0.0, 0.20):
        x0, y0 = s_max + 0.55 - dx, max(z_up, H) + 0.55
        x1, y1 = x0 - 0.40, y0 - 0.40
        ax.annotate("", xy=(x1, y1), xytext=(x0, y0),
                    arrowprops=dict(arrowstyle="->", color="#f59e0b", lw=1.1, alpha=0.8))
    ax.text(s_max + 0.42, max(z_up, H) + 0.62, "阳光", color="#b45309", fontsize=8, ha="center")

    ax.set_xlim(-0.62, s_max + 0.7)
    ax.set_ylim(-0.12, max(z_up, H) + 0.85)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_title("倾斜角 θ 与板长 L 示意\n"
                "（θ=90°为水平分界，>90°上扬/<90°下垂，不需要额外正负号；"
                "图中角度仅为示意）",
                fontsize=9.5, color="#1a1e2e", pad=6)
    fig.tight_layout()

    canvas.draw()
    w, h = int(fig.bbox.width), int(fig.bbox.height)
    buf = bytes(canvas.buffer_rgba())
    qimg = QImage(buf, w, h, QImage.Format.Format_RGBA8888)
    pixmap = QPixmap.fromImage(qimg.copy())
    _TILT_DIAGRAM_CACHE["pixmap"] = pixmap
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

        # v2.12.0: 不再用抽象特征角β反算深度，直接扫描"倾斜角θ×板长L"网格
        # （θ=90°水平，>90°上扬，<90°下垂；L=板长mm，沿板自身方向）
        self._tilt_min  = self._spin(0, 180, 180, 60)
        self._tilt_max  = self._spin(0, 180, 180, 120)
        self._tilt_step = self._spin(1, 90, 90, 10)
        g.addWidget(_lbl("倾斜角θ(°,90=水平)"), 0, 0)
        g.addWidget(self._tilt_min, 0, 1)
        g.addWidget(_lbl("~"),        0, 2)
        g.addWidget(self._tilt_max, 0, 3)
        g.addWidget(_lbl("步长"),     0, 4)
        g.addWidget(self._tilt_step, 0, 5)

        self._L_min  = self._spin(0, 100000, 100000, 300)
        self._L_max  = self._spin(0, 100000, 100000, 1500)
        self._L_step = self._spin(1, 10000, 10000, 300)
        g.addWidget(_lbl("板长L(mm)"), 1, 0)
        g.addWidget(self._L_min, 1, 1)
        g.addWidget(_lbl("~"),    1, 2)
        g.addWidget(self._L_max, 1, 3)
        g.addWidget(_lbl("步长"), 1, 4)
        g.addWidget(self._L_step, 1, 5)

        # 材料（决定 diffuse_residual）+ 安装间隙h（窗顶到板根的距离，固定值不扫描）
        self._material_combo = QComboBox()
        for name, spec in MATERIAL_PRESETS.items():
            self._material_combo.addItem(f"{name}（k_diff={spec['diffuse_residual']:.2f}）",
                                         userData=spec["diffuse_residual"])
        self._material_combo.setFixedHeight(28)
        self._material_combo.setCurrentIndex(1)   # 默认"浅色/密实混凝土"
        self._h_mm = QDoubleSpinBox()
        self._h_mm.setRange(0.0, 2000.0); self._h_mm.setSingleStep(50.0)
        self._h_mm.setDecimals(0); self._h_mm.setValue(0.0)
        self._h_mm.setFixedHeight(28)
        g.addWidget(_lbl("材料"), 2, 0)
        g.addWidget(self._material_combo, 2, 1, 1, 3)
        g.addWidget(_lbl("h(mm)"), 2, 4)
        g.addWidget(self._h_mm, 2, 5)

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
        g.addWidget(_lbl("热轴指标"), 3, 0)
        g.addWidget(self._y_combo, 3, 1, 1, 3)
        g.addWidget(_lbl("U0下限"), 3, 4)
        g.addWidget(self._u0_min, 3, 5)

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
        g.addWidget(self._btn_run, 3, 6)
        g.addWidget(self._btn_png, 3, 7)
        g.addWidget(self._btn_csv, 3, 8)

        hint = QLabel("💡 不清楚某个参数是什么意思？在对应输入框上单击右键查看说明")
        hint.setStyleSheet("color:#9aa0b0;font-size:11px;background:transparent;")
        g.addWidget(hint, 4, 0, 1, 9)

        # v2.12.0: 右键参数控件查看示意图/文字说明（不懂这些数值含义时用）
        for w in (self._tilt_min, self._tilt_max, self._L_min, self._L_max):
            self._attach_diagram_help(w)
        self._attach_text_help(self._tilt_step, "步长 是什么？", (
            "<b>步长</b>决定这次实验测试得有多密（倾斜角θ和板长L各自的步长）。<br><br>"
            "程序会从「起始值」到「终止值」之间，每隔一个「步长」就测一次，"
            "每一次都要完整跑一遍采光+温度计算，θ和L是网格交叉——"
            "比如θ测7档、L测5档，总共会跑7×5=35个组合。<br><br>"
            "步长越小 → 测得越细，但要跑的次数越多、越慢；"
            "步长越大 → 跑得快，但可能错过中间更好的方案。"))
        self._attach_text_help(self._L_step, "步长 是什么？", (
            "<b>步长</b>决定这次实验测试得有多密（倾斜角θ和板长L各自的步长）。<br><br>"
            "程序会从「起始值」到「终止值」之间，每隔一个「步长」就测一次，"
            "θ和L是网格交叉——比如θ测7档、L测5档，总共会跑7×5=35个组合。<br><br>"
            "步长越小 → 测得越细但越慢；步长越大 → 跑得快但可能错过更好的方案。"))
        self._attach_text_help(self._material_combo, "材料 是什么？", (
            "遮阳板不是100%不透明黑体——即使完全挡住直射阳光，材料本身的颜色/"
            "光泽仍会把一部分散射天空光、地面反射光反弹进房间。<br><br>"
            "下拉框里的<b>k_diff</b>（残余透过比）就是描述这个效应的参数："
            "完全遮挡时仍有多少比例的散射光能透过/反射进来，数值越大代表材料"
            "越亮/越反光。<br><br>"
            "预设数值是按公开资料里几类材料的太阳反射比粗略估算的（深色混凝土"
            "反射率≈0.16~0.20、浅色/密实混凝土≈0.30~0.35、氧化铝板≈0.5、"
            "镜面高反射铝板≈0.7~0.85），<b>不是严格标定值</b>，仅供实验时"
            "参考选用；如果你有具体材料的实测反射率，建议自行标定更准确的k_diff。"))
        self._attach_text_help(self._h_mm, "h(mm) 是什么？", (
            "<b>h</b> = 窗户上沿到遮阳板板根（贴墙那一端）底面的安装间隙。"
            "h=0 表示板紧贴窗顶安装（最常见的做法）；h越大，板的位置离窗户"
            "越高，同样的倾斜角和板长，实际遮挡效果会变弱。<br><br>"
            "当前版本里 h 是<b>固定值</b>，不参与θ×L的网格扫描——先把倾斜角和"
            "板长这两个主要变量筛选出最优候选，h和材料一样，建议在筛出候选"
            "方案后再单独复核它的敏感性，避免和θ、L交叉组合数量爆炸。"))
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
        """右键弹出θ/L剖面示意图。"""
        widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        widget.customContextMenuRequested.connect(
            lambda _pos: _ParamHelpDialog(
                "倾斜角 θ 与板长 L",
                pixmap=_render_tilt_diagram(),
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
        w = QWidget()
        lay = QVBoxLayout(w); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(6)

        # v2.12.0: 2D图/3D点云 切换（3D支持拖动旋转+点击某面墙弹出对应二维图）
        toggle_row = QHBoxLayout()
        self._btn_view2d = QPushButton("2D图")
        self._btn_view3d = QPushButton("3D点云")
        for b in (self._btn_view2d, self._btn_view3d):
            b.setCheckable(True); b.setFixedHeight(26)
        self._btn_view2d.setChecked(True)
        self._btn_view2d.clicked.connect(lambda: self._switch_plot_view(0))
        self._btn_view3d.clicked.connect(lambda: self._switch_plot_view(1))
        toggle_row.addWidget(self._btn_view2d)
        toggle_row.addWidget(self._btn_view3d)
        toggle_row.addStretch()
        lay.addLayout(toggle_row)

        self._plot_stack = QStackedWidget()

        self._plot_lbl = QLabel(
            "设置参数后点「▶ 运行实验」\n"
            "完成后此处显示帕累托散点图（后台线程计算，不卡界面）")
        self._plot_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._plot_lbl.setStyleSheet(
            "color:#9aa0b0;background:#ffffff;border:1px solid #e8ebf2;"
            "border-radius:6px;")
        self._plot_lbl.setMinimumSize(480, 360)
        self._plot_stack.addWidget(self._plot_lbl)   # index 0

        self._plot3d_container = QWidget()
        c3d_lay = QVBoxLayout(self._plot3d_container)
        c3d_lay.setContentsMargins(0, 0, 0, 0)
        self._plot3d_hint = QLabel(
            "运行实验后此处显示3D帕累托点云（可拖动旋转；点击背景墙上的灰点"
            "弹出对应两个指标的二维图）")
        self._plot3d_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._plot3d_hint.setStyleSheet(
            "color:#9aa0b0;background:#ffffff;border:1px solid #e8ebf2;"
            "border-radius:6px;")
        self._plot3d_hint.setMinimumSize(480, 360)
        c3d_lay.addWidget(self._plot3d_hint)
        self._plot3d_canvas = None       # 当前3D画布（每次跑完实验重建）
        self._wall_pick_map = {}         # PathCollection -> (x_col,y_col,x_lbl,y_lbl,maximize_y)
        self._plot_stack.addWidget(self._plot3d_container)   # index 1

        lay.addWidget(self._plot_stack, 1)
        return w

    def _switch_plot_view(self, idx: int):
        self._btn_view2d.setChecked(idx == 0)
        self._btn_view3d.setChecked(idx == 1)
        self._plot_stack.setCurrentIndex(idx)

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
        tmin, tmax = self._tilt_min.value(), self._tilt_max.value()
        tstep = max(1, self._tilt_step.value())
        if tmax < tmin:
            QMessageBox.warning(self, "提示", "θ 上限不能小于下限。"); return
        tilt_degs = [float(v) for v in range(tmin, tmax + 1, tstep)]

        lmin, lmax = self._L_min.value(), self._L_max.value()
        lstep = max(1, self._L_step.value())
        if lmax < lmin:
            QMessageBox.warning(self, "提示", "L 上限不能小于下限。"); return
        depth_mms = [float(v) for v in range(lmin, lmax + 1, lstep)]

        specs = self._collect_glass_specs()
        if not specs:
            QMessageBox.warning(self, "提示", "玻璃对照组表格无有效数据。"); return
        ykey = self._y_combo.currentData()
        params = {
            "tilt_degs":  tilt_degs,
            "depth_mms":  depth_mms,
            "diffuse_residual": float(self._material_combo.currentData()),
            "overhang_gap_mm":  float(self._h_mm.value()),
            "glass_specs": specs,
            "y":          ykey,
            "maximize_y": _Y_META[ykey][0],
            "y_label":    _Y_META[ykey][1],
            "u0_min":     float(self._u0_min.value()),
        }
        self._last_params = params
        self.run_requested.emit(params)

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

        self._build_3d_view(df, p)

    # ── v2.12.0 新增：3D帕累托点云 ───────────────────────────────────────────
    def _build_3d_view(self, df, p: dict):
        """重建3D点云画布（每次跑完实验整体重建，比原地更新3D散点简单可靠）。"""
        try:
            fig, wall_map = build_pareto3d_figure(df, u0_min=p["u0_min"])
        except Exception as e:
            QMessageBox.critical(self, "3D绘图失败", str(e))
            return

        c3d_lay = self._plot3d_container.layout()
        # 清掉上一次的画布（提示文字或旧canvas）
        while c3d_lay.count():
            item = c3d_lay.takeAt(0)
            if item.widget():
                item.widget().setParent(None)
                item.widget().deleteLater()

        canvas = FigureCanvas(fig)
        canvas.setMinimumSize(480, 360)
        canvas.mpl_connect("pick_event", self._on_pick3d)
        c3d_lay.addWidget(canvas)
        self._plot3d_canvas = canvas
        self._wall_pick_map = wall_map

    def _on_pick3d(self, event):
        """3D点云上点击了某面墙的投影散点——弹出对应两个指标的二维图。"""
        info = self._wall_pick_map.get(event.artist)
        if info is None or self._df is None:
            return
        x_col, y_col, x_label, y_label, maximize_y = info
        self._show_2d_popup(x_col, y_col, x_label, y_label, maximize_y)

    def _show_2d_popup(self, x_col, y_col, x_label, y_label, maximize_y):
        tmp_png = os.path.join(tempfile.gettempdir(), "room_light_pareto_popup.png")
        try:
            plot_experiments(
                self._df, out_path=tmp_png, x=x_col, y=y_col,
                maximize_y=maximize_y, size_col="cost",
                u0_min=self._last_params.get("u0_min", 0.0),
                x_label=x_label, y_label=y_label,
                title=f"{x_label} × {y_label}", dpi=200)
        except Exception as e:
            QMessageBox.critical(self, "绘图失败", str(e)); return

        dlg = QDialog(self)
        dlg.setWindowTitle(f"{x_label} × {y_label}")
        lay = QVBoxLayout(dlg)
        img_lbl = QLabel()
        pm = QPixmap(tmp_png)
        img_lbl.setPixmap(pm.scaled(760, 560, Qt.AspectRatioMode.KeepAspectRatio,
                                    Qt.TransformationMode.SmoothTransformation))
        lay.addWidget(img_lbl)
        btn = QPushButton("关闭")
        btn.clicked.connect(dlg.accept)
        lay.addWidget(btn, 0, Qt.AlignmentFlag.AlignRight)
        dlg.exec()

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
