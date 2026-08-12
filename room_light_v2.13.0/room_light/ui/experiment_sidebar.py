"""
ui/experiment_sidebar.py — 参数化实验专用侧边栏  v2.13.0（新增）
================================================================
点击工具栏「参数化实验」时，主窗口左侧把"房间参数侧边栏"整体切换为本侧边栏
（房间几何/窗户等参数在实验期间用不到，改为显示实验需要调的参数）。

本侧边栏收集全部实验输入：
  · 遮阳几何：倾斜角θ范围 + 板长L范围 + 安装间隙h范围（三者做 θ×L×h 网格）
  · 遮阳材料：多选列表（分类：混凝土/金属/木材/涂料…），只算勾选的材料，
    每个材料在散点/点云上用固定颜色区分
  · 玻璃对照组：可选是否纳入 + 玻璃参数表
  · 图表设置：热轴指标 + U0 合规下限
纯视图：收集参数 → 发 run_requested(dict)；导出按钮发 export_*_requested。
计算/绘图逻辑全部在 core/experiments.py 与 ui/experiment_panel.py，本文件不含。
"""
from __future__ import annotations
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QScrollArea, QLabel,
    QPushButton, QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox, QFrame,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox, QDialog,
    QSizePolicy,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPixmap, QImage

from ui.sidebar import CollapsibleSection, _lbl, _h_line
from core.models import MATERIAL_LIBRARY, DEFAULT_SELECTED_MATERIALS
from core.experiments import DEFAULT_GLASS_SPECS, GlassSpec

# 热轴指标 → (是否越大越好, 中文标签)
_Y_META = {
    "thermal_discomfort":     (False, "热不舒适度 Σ(超温+欠温) ℃·月"),
    "overheat_degree_months": (False, "超温强度 Σ(T−26) ℃·月"),
    "comfort_ratio":          (True,  "舒适月数占比"),
}


# ── 参数右键说明（θ/L 剖面示意图 + 文字/公式）────────────────────────────────
_TILT_DIAGRAM_CACHE: dict = {}


def _render_tilt_diagram() -> QPixmap:
    """渲染"倾斜角θ / 板长L"剖面示意图（θ=90°水平参照 + 上扬/下垂两例）。"""
    if "pixmap" in _TILT_DIAGRAM_CACHE:
        return _TILT_DIAGRAM_CACHE["pixmap"]
    import math
    import numpy as np
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from matplotlib import patches

    H = 1.0
    L = 1.0

    def tip(theta_deg):
        th = math.radians(theta_deg)
        return L * math.sin(th), H - L * math.cos(th)

    s90, z90 = tip(90.0)
    s_up, z_up = tip(120.0)
    s_dn, z_dn = tip(60.0)
    s_max = max(s90, s_up, s_dn)

    fig = Figure(figsize=(5.2, 4.0), dpi=150, facecolor="#ffffff")
    canvas = FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)
    ax.set_facecolor("#ffffff")
    ax.axvspan(-0.62, 0, color="#f5f6f8", zorder=0)
    ax.axvspan(0, s_max + 0.6, color="#eef6ff", zorder=0)
    ax.text(-0.31, max(z_up, H) + 0.35, "室内", color="#9aa0b0", fontsize=9, ha="center")
    ax.text(s_max * 0.5 + 0.15, max(z_up, H) + 0.35, "室外", color="#60a5fa", fontsize=9, ha="center")
    ax.plot([-0.62, s_max + 0.6], [0, 0], color="#9aa0b0", lw=1.2, zorder=1)
    ax.plot([0, 0], [0, max(z_up, H) + 0.5], color="#5a6175", lw=3, zorder=2, solid_capstyle="butt")
    ax.add_patch(patches.Rectangle((-0.035, 0), 0.035, H, facecolor="#bfdbfe",
                                   edgecolor="#2563eb", lw=1.0, zorder=3))
    ax.plot([0, s90], [H, z90], "--", color="#9aa0b0", lw=1.3, zorder=3)
    ax.text(s90 + 0.05, z90, "θ=90°(水平)", color="#9aa0b0", fontsize=8.5, va="center")
    ax.plot([0, s_up], [H, z_up], "-", color="#b45309", lw=3.2, zorder=4, solid_capstyle="butt")
    ax.text(s_up + 0.05, z_up, "θ=120°\n(上扬30°)", color="#b45309", fontsize=8.5, va="center")
    ax.plot([0, s_dn], [H, z_dn], "-", color="#15803d", lw=3.2, zorder=4, solid_capstyle="butt")
    ax.text(s_dn + 0.05, z_dn - 0.08, "θ=60°\n(下垂30°)", color="#15803d", fontsize=8.5, va="center")
    arc_r = 0.30
    t1 = np.linspace(math.atan2(z90 - H, s90), math.atan2(z_up - H, s_up), 30)
    ax.plot(arc_r * np.cos(t1), H + arc_r * np.sin(t1), color="#b45309", lw=1.1, zorder=3)
    t2 = np.linspace(math.atan2(z_dn - H, s_dn), math.atan2(z90 - H, s90), 30)
    ax.plot(arc_r * np.cos(t2), H + arc_r * np.sin(t2), color="#15803d", lw=1.1, zorder=3)
    ax.annotate("", xy=(s90, z90 + 0.10), xytext=(0, H + 0.10),
                arrowprops=dict(arrowstyle="<->", color="#2563eb", lw=1.1))
    ax.text(s90 / 2, H + 0.20, "L(板长，沿板自身方向)", color="#2563eb",
            fontsize=8.5, ha="center", fontweight="bold")
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
                "（θ=90°为水平分界，>90°上扬/<90°下垂，不需要额外正负号；图中角度仅为示意）",
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
    """右键弹出的参数说明窗口：示意图（QPixmap）或文字/公式（HTML）。"""

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


# ── 实验侧边栏 ────────────────────────────────────────────────────────────────
class ExperimentSidebar(QWidget):
    """参数化实验参数输入侧边栏（可滚动）。"""
    run_requested        = pyqtSignal(dict)
    export_png_requested = pyqtSignal()
    export_csv_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(260)
        self.setStyleSheet("background:#f0f2f6;")

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("background:transparent;border:none;")
        inner = QWidget(); inner.setStyleSheet("background:#f0f2f6;")
        self._main = QVBoxLayout(inner)
        self._main.setContentsMargins(8, 10, 8, 20)
        self._main.setSpacing(6)
        scroll.setWidget(inner)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(scroll)

        self._mat_checks: dict = {}   # 材料名 → QCheckBox
        self._build_title()
        self._build_geometry_section()
        self._build_material_section()
        self._build_glass_section()
        self._build_chart_section()
        self._build_actions()
        self._main.addStretch()

    # ── 各区块 ───────────────────────────────────────────────────────────
    def _build_title(self):
        t = QLabel("参数化实验")
        t.setStyleSheet("font-size:15px;font-weight:700;color:#2563eb;"
                        "background:transparent;padding:2px 4px;")
        self._main.addWidget(t)
        hint = QLabel("💡 θ/L/h 三者做网格交叉，材料是第4维（只改热环境，不改采光）。"
                      "\n在 θ/L/h 输入框上右键可看示意图。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#9aa0b0;font-size:10px;background:transparent;padding:0 4px;")
        self._main.addWidget(hint)

    def _spin(self, mn, mx, val, step=1) -> QSpinBox:
        s = QSpinBox(); s.setRange(int(mn), int(mx)); s.setSingleStep(int(step))
        s.setValue(int(val)); s.setFixedHeight(28)
        return s

    def _range_row(self, sec, label, sp_min, sp_max, sp_step, unit=""):
        row = QHBoxLayout()
        lb = _lbl(label, color="#5a6175"); lb.setMinimumWidth(70)
        row.addWidget(lb)
        row.addWidget(sp_min, 1)
        row.addWidget(_lbl("~", color="#9aa0b0"))
        row.addWidget(sp_max, 1)
        sec.add_layout(row)
        row2 = QHBoxLayout()
        lb2 = _lbl("　步长", color="#9aa0b0", size=11); lb2.setMinimumWidth(70)
        row2.addWidget(lb2)
        row2.addWidget(sp_step, 1)
        if unit:
            row2.addWidget(_lbl(unit, color="#9aa0b0", size=11))
        row2.addStretch()
        sec.add_layout(row2)

    def _build_geometry_section(self):
        sec = CollapsibleSection("遮阳几何（θ × L × h 网格）", expanded=True)
        self._tilt_min  = self._spin(0, 180, 60)
        self._tilt_max  = self._spin(0, 180, 120)
        self._tilt_step = self._spin(1, 90, 10)
        self._range_row(sec, "倾斜角θ(°)", self._tilt_min, self._tilt_max, self._tilt_step, "°(90=水平)")
        sec.add_widget(_h_line())
        self._L_min  = self._spin(0, 100000, 300)
        self._L_max  = self._spin(0, 100000, 1500)
        self._L_step = self._spin(1, 10000, 300)
        self._range_row(sec, "板长L(mm)", self._L_min, self._L_max, self._L_step, "mm")
        sec.add_widget(_h_line())
        self._h_min  = self._spin(0, 2000, 0)
        self._h_max  = self._spin(0, 2000, 0)
        self._h_step = self._spin(1, 2000, 100)
        self._range_row(sec, "间隙h(mm)", self._h_min, self._h_max, self._h_step, "mm")
        note = QLabel("h=窗顶到板根的安装间隙；起止相同则h不扫描(单值)。")
        note.setWordWrap(True)
        note.setStyleSheet("color:#9aa0b0;font-size:10px;background:transparent;")
        sec.add_widget(note)
        self._main.addWidget(sec)

        # 右键说明
        for w in (self._tilt_min, self._tilt_max, self._L_min, self._L_max):
            self._attach_diagram_help(w)
        self._attach_text_help(self._tilt_step, "步长 是什么？",
            "<b>步长</b>决定这次实验测试得有多密（θ/L/h 各自的步长）。<br><br>"
            "程序从「起始值」到「终止值」之间，每隔一个「步长」测一次，θ、L、h "
            "三者做网格交叉——比如θ测7档、L测5档、h测1档，就跑 7×5×1=35 组几何。<br><br>"
            "步长越小→测得越细但越慢；越大→跑得快但可能错过更好的方案。")
        self._attach_text_help(self._L_step, "步长 是什么？",
            "<b>步长</b>决定测试密度（θ/L/h 各自的步长），三者网格交叉。步长越小"
            "越细越慢，越大越快但可能错过更好方案。")
        self._attach_text_help(self._h_step, "h(mm) 是什么？",
            "<b>h</b> = 窗户上沿到遮阳板板根（贴墙那端）底面的安装间隙。"
            "h=0 表示板紧贴窗顶；h越大板位置越高、同样θ/L的遮挡效果越弱。<br><br>"
            "v2.13.0 起 h 也是扫描维度之一（θ×L×h 网格）。若把 h 的起止设成相同值，"
            "h 就只取单值、不参与扫描。")

    def _build_material_section(self):
        sec = CollapsibleSection("遮阳材料（多选，按材料着色）", expanded=True)
        note = QLabel("只计算勾选的材料；每个材料在散点/点云上用固定颜色区分。"
                      "材料只影响热环境(残余透过比k_diff)，不影响采光。")
        note.setWordWrap(True)
        note.setStyleSheet("color:#9aa0b0;font-size:10px;background:transparent;")
        sec.add_widget(note)
        for cat, mats in MATERIAL_LIBRARY.items():
            cat_lbl = _lbl(cat, bold=True, color="#5a6175", size=12)
            sec.add_widget(cat_lbl)
            for name, spec in mats.items():
                row = QHBoxLayout()
                sw = QLabel()
                sw.setFixedSize(14, 14)
                sw.setStyleSheet(f"background:{spec['color']};border:1px solid #d0d5e0;"
                                 "border-radius:3px;")
                cb = QCheckBox(f"{name}  (k_diff={spec['k_diff']:.2f})")
                cb.setStyleSheet("color:#1a1e2e;font-size:11px;background:transparent;")
                cb.setChecked(name in DEFAULT_SELECTED_MATERIALS)
                self._mat_checks[name] = cb
                row.addWidget(sw)
                row.addWidget(cb, 1)
                sec.add_layout(row)
        self._attach_text_help(sec, "材料 是什么？",
            "遮阳板不是100%不透明黑体——即使挡住直射，材料本身的颜色/光泽仍会把一"
            "部分散射天空光/地面反射光反弹进室内。<b>k_diff</b>(残余透过比)就是这个"
            "比例，越大代表材料越亮/越反光。<br><br>预设k_diff按公开的太阳反射比资料"
            "粗略估算（深色混凝土≈0.16、镜面铝板≈0.78等），<b>不是严格标定值</b>，"
            "仅供选用参考。")
        self._main.addWidget(sec)

    def _build_glass_section(self):
        sec = CollapsibleSection("玻璃对照组（可选）", expanded=False)
        self._glass_on = QCheckBox("纳入玻璃对照组（绿色圆点）")
        self._glass_on.setChecked(True)
        self._glass_on.setStyleSheet("color:#1a1e2e;font-size:12px;background:transparent;")
        sec.add_widget(self._glass_on)
        note = QLabel("对照组：固定不装遮阳、只换玻璃(Tvis/SC)，用来对比"
                      "\"调遮阳\"和\"换玻璃\"谁更划算。成本为占位值，可编辑。")
        note.setWordWrap(True)
        note.setStyleSheet("color:#9aa0b0;font-size:10px;background:transparent;")
        sec.add_widget(note)
        self._glass_tbl = QTableWidget(len(DEFAULT_GLASS_SPECS), 4)
        self._glass_tbl.setHorizontalHeaderLabels(["玻璃类型", "Tvis", "SC", "成本"])
        self._glass_tbl.verticalHeader().setVisible(False)
        self._glass_tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in (1, 2, 3):
            self._glass_tbl.horizontalHeader().setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        for i, g in enumerate(DEFAULT_GLASS_SPECS):
            for c, val in enumerate([g.label, f"{g.tvis:.2f}", f"{g.sc:.2f}", f"{g.cost:.0f}"]):
                it = QTableWidgetItem(val)
                if c != 0:
                    it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._glass_tbl.setItem(i, c, it)
        self._glass_tbl.setStyleSheet("font-size:11px;background:#ffffff;")
        self._glass_tbl.setFixedHeight(180)
        sec.add_widget(self._glass_tbl)
        self._main.addWidget(sec)

    def _build_chart_section(self):
        sec = CollapsibleSection("图表设置", expanded=True)
        self._y_combo = QComboBox()
        for k, (_mx, lab) in _Y_META.items():
            self._y_combo.addItem(lab, userData=k)
        self._y_combo.setFixedHeight(28)
        sec.add_row("热轴指标", self._y_combo)
        self._u0_min = QDoubleSpinBox()
        self._u0_min.setRange(0.0, 1.0); self._u0_min.setSingleStep(0.05)
        self._u0_min.setDecimals(2); self._u0_min.setValue(0.0)
        self._u0_min.setFixedHeight(28)
        sec.add_row("U0下限", self._u0_min)
        self._main.addWidget(sec)
        self._attach_text_help(self._y_combo, "热轴指标 是什么？",
            "<b>热轴指标</b>决定2D图纵轴/3D图一根轴用什么数字衡量方案冬冷夏热的严重"
            "程度：<br><br>· <b>热不舒适度 Σ(超温+欠温) ℃·月</b> = Σmax(室温−26,0)+"
            "Σmax(18−室温,0)，越小越好（区分度好，推荐）<br>· <b>超温强度</b> 只算过热"
            "部分<br>· <b>舒适月数占比</b> 18~26℃月数÷12，越大越好（离散、区分度差）")
        self._attach_text_help(self._u0_min, "U0下限 是什么？",
            "<b>U0(采光均匀度)</b>=最暗处照度÷平均照度，0~1，越接近1越均匀。<br><br>"
            "<b>U0下限</b>是筛选门槛：只有U0≥该值的方案才算合规、才进帕累托前沿。"
            "设为0=不筛选，全部显示（侧窗房间U0天生偏低，默认0）。")

    def _build_actions(self):
        box = QFrame()
        box.setStyleSheet("QFrame{background:#f5f6f8;border:1px solid #d0d5e0;border-radius:6px;}")
        lay = QVBoxLayout(box); lay.setContentsMargins(10, 10, 10, 10); lay.setSpacing(6)
        self._btn_run = QPushButton("▶  运行实验")
        self._btn_run.setFixedHeight(32)
        self._btn_run.setStyleSheet(
            "QPushButton{background-color:#ffffff;color:#2563eb;border:1px solid #2563eb;"
            "border-radius:4px;font-weight:700;}"
            "QPushButton:hover{background-color:#eff6ff;}"
            "QPushButton:pressed{background-color:#dbeafe;}")
        self._btn_run.clicked.connect(self._on_run)
        lay.addWidget(self._btn_run)
        exp_row = QHBoxLayout()
        self._btn_png = QPushButton("↓ 导出PNG")
        self._btn_csv = QPushButton("↓ 导出CSV")
        for b in (self._btn_png, self._btn_csv):
            b.setFixedHeight(30); b.setEnabled(False)
        self._btn_png.clicked.connect(self.export_png_requested.emit)
        self._btn_csv.clicked.connect(self.export_csv_requested.emit)
        exp_row.addWidget(self._btn_png); exp_row.addWidget(self._btn_csv)
        lay.addLayout(exp_row)
        self._main.addWidget(box)

    def set_export_enabled(self, on: bool):
        self._btn_png.setEnabled(on)
        self._btn_csv.setEnabled(on)

    # ── 右键说明挂载 ─────────────────────────────────────────────────────
    def _attach_diagram_help(self, widget):
        widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        widget.customContextMenuRequested.connect(
            lambda _pos: _ParamHelpDialog("倾斜角 θ 与板长 L",
                pixmap=_render_tilt_diagram(), parent=self).exec())

    def _attach_text_help(self, widget, title, html):
        widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        widget.customContextMenuRequested.connect(
            lambda _pos, t=title, h=html: _ParamHelpDialog(t, html=h, parent=self).exec())

    # ── 收集参数 → 发信号 ───────────────────────────────────────────────
    def _collect_glass_specs(self):
        specs = []
        for i in range(self._glass_tbl.rowCount()):
            try:
                label = self._glass_tbl.item(i, 0).text().strip()
                tvis  = float(self._glass_tbl.item(i, 1).text())
                sc    = float(self._glass_tbl.item(i, 2).text())
                cost  = float(self._glass_tbl.item(i, 3).text())
                if label:
                    specs.append(GlassSpec(label=label, tvis=tvis, sc=sc, cost=cost))
            except (AttributeError, ValueError):
                continue
        return specs

    @staticmethod
    def _range_list(v_min, v_max, v_step):
        step = max(1, v_step)
        return [float(v) for v in range(v_min, v_max + 1, step)]

    def _on_run(self):
        if self._tilt_max.value() < self._tilt_min.value():
            QMessageBox.warning(self, "提示", "θ 上限不能小于下限。"); return
        if self._L_max.value() < self._L_min.value():
            QMessageBox.warning(self, "提示", "L 上限不能小于下限。"); return
        if self._h_max.value() < self._h_min.value():
            QMessageBox.warning(self, "提示", "h 上限不能小于下限。"); return
        tilt_degs = self._range_list(self._tilt_min.value(), self._tilt_max.value(), self._tilt_step.value())
        depth_mms = self._range_list(self._L_min.value(), self._L_max.value(), self._L_step.value())
        gap_mms   = self._range_list(self._h_min.value(), self._h_max.value(), self._h_step.value())
        materials = [name for name, cb in self._mat_checks.items() if cb.isChecked()]
        if not materials:
            QMessageBox.warning(self, "提示", "请至少勾选一种遮阳材料。"); return

        include_glass = self._glass_on.isChecked()
        glass_specs = self._collect_glass_specs()
        if include_glass and not glass_specs:
            QMessageBox.warning(self, "提示", "玻璃对照组表格无有效数据（或取消勾选纳入）。"); return

        # 组合数量提醒（采光只按几何算一次，但仍提示总点数，避免误设几百个点糊成一团）
        n_geom = len(tilt_degs) * len(depth_mms) * len(gap_mms)
        n_pts = n_geom * len(materials) + (len(glass_specs) if include_glass else 0)
        if n_pts > 200:
            r = QMessageBox.question(
                self, "组合较多",
                f"当前设置将产生约 {n_pts} 个数据点（几何组合{n_geom}×材料{len(materials)}"
                f"{'＋玻璃'+str(len(glass_specs)) if include_glass else ''}）。\n"
                "点数过多时图会比较密、计算也更久，确定继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if r != QMessageBox.StandardButton.Yes:
                return

        ykey = self._y_combo.currentData()
        params = {
            "tilt_degs": tilt_degs, "depth_mms": depth_mms, "gap_mms": gap_mms,
            "materials": materials, "glass_specs": glass_specs,
            "include_glass": include_glass,
            "y": ykey, "maximize_y": _Y_META[ykey][0], "y_label": _Y_META[ykey][1],
            "u0_min": float(self._u0_min.value()),
        }
        self.run_requested.emit(params)
