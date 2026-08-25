"""
ui/experiment_sidebar.py — 参数化实验专用侧边栏  v4.0.0
================================================================
点击工具栏「参数化实验」时，主窗口左侧把"房间参数侧边栏"整体切换为本侧边栏
（房间几何/窗户等参数在实验期间用不到，改为显示实验需要调的参数）。

本侧边栏收集全部实验输入：
  · 遮阳几何：倾斜角θ范围 + 板长L范围 + 安装间隙h范围（三者做 θ×L×h 网格）
  · 遮阳材料：多选列表（分类：混凝土/金属/木材/涂料…），只算勾选的材料，
    每个材料在散点/点云上用固定颜色区分
  · 当前工程基准：保留已打开rlproj的现状作改造前参照；新候选会移除原遮阳
  · 图表设置：热轴指标 + U0 合规下限（全局帕累托显示开关位于结果面板顶部）
纯视图：收集参数 → 发 run_requested(dict)；导出按钮发 export_*_requested。
计算/绘图逻辑全部在 core/experiments.py 与 ui/experiment_panel.py，本文件不含。
"""
from __future__ import annotations
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QScrollArea, QLabel,
    QPushButton, QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox, QFrame,
    QMessageBox, QDialog, QSizePolicy,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPixmap, QImage

from ui.sidebar import CollapsibleSection, _lbl, _h_line
from core.models import (
    MATERIAL_LIBRARY, DEFAULT_SELECTED_MATERIALS,
    DEFAULT_SUPPORT_COST_PER_M, SUPPORT_COST_RANGE_PER_M,
    DEFAULT_INSTALL_COST_PER_WINDOW, INSTALL_COST_RANGE_PER_WINDOW,
)

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
        self._mat_cost_spins: dict = {}  # 材料名 → 综合单价(元/m²)
        self._project_has_shading = False
        self._build_title()
        self._build_geometry_section()
        self._build_material_section()
        self._build_project_section()
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
        note = QLabel(
            "只计算勾选材料；右侧为2025—2026年湖南市场建议综合单价(元/㎡)，"
            "含材料、加工/表面处理、5%~8%损耗、运输及9%增值税，不含支撑与安装。"
            "所有材料共同参与全局筛选。")
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
                cb = QCheckBox(name)
                cb.setStyleSheet("color:#1a1e2e;font-size:11px;background:transparent;")
                cb.setChecked(name in DEFAULT_SELECTED_MATERIALS)
                cost_range = spec.get("cost_range_per_m2")
                range_text = (
                    f"；建议区间={cost_range[0]:.0f}~{cost_range[1]:.0f}元/㎡"
                    if cost_range else ""
                )
                cb.setToolTip(
                    f"k_diff={spec['k_diff']:.2f}{range_text}；"
                    f"{spec.get('price_basis', '')}；材料只影响热环境，不改变采光几何")
                price = QDoubleSpinBox()
                price.setRange(0.0, 100000.0)
                price.setDecimals(0)
                price.setSingleStep(50.0)
                price.setSuffix(" 元/㎡")
                price.setValue(float(spec.get("installed_cost_per_m2", 230.0)))
                price.setFixedWidth(92)
                price.setFixedHeight(26)
                self._mat_checks[name] = cb
                self._mat_cost_spins[name] = price
                row.addWidget(sw)
                row.addWidget(cb, 1)
                row.addWidget(price)
                sec.add_layout(row)
        sec.add_widget(_h_line())
        self._support_cost = QDoubleSpinBox()
        self._support_cost.setRange(0.0, 10000.0)
        self._support_cost.setDecimals(0)
        self._support_cost.setSingleStep(20.0)
        self._support_cost.setSuffix(" 元/m")
        self._support_cost.setValue(DEFAULT_SUPPORT_COST_PER_M)
        self._support_cost.setToolTip(
            f"2025—2026年湖南市场建议区间："
            f"{SUPPORT_COST_RANGE_PER_M[0]:.0f}~{SUPPORT_COST_RANGE_PER_M[1]:.0f}元/m")
        self._support_cost.setFixedHeight(28)
        sec.add_row("支撑构件", self._support_cost)
        self._install_cost = QDoubleSpinBox()
        self._install_cost.setRange(0.0, 100000.0)
        self._install_cost.setDecimals(0)
        self._install_cost.setSingleStep(50.0)
        self._install_cost.setSuffix(" 元/窗")
        self._install_cost.setValue(DEFAULT_INSTALL_COST_PER_WINDOW)
        self._install_cost.setToolTip(
            f"建议区间：{INSTALL_COST_RANGE_PER_WINDOW[0]:.0f}~"
            f"{INSTALL_COST_RANGE_PER_WINDOW[1]:.0f}元/窗；高层或异形窗宜取高值")
        self._install_cost.setFixedHeight(28)
        sec.add_row("安装固定费", self._install_cost)
        cost_note = QLabel(
            "总价=Σ窗宽×L×材料综合单价 + 2×Σ窗宽×支撑单价 + 窗数×安装费；"
            "CSV/Excel会导出面积、单价和各分项。默认值依据2025年下半年至2026年8月"
            "市场资料并结合湖南省2025版消耗量标准整理；论文须注明取价时点，实际工程"
            "仍应按实施时当地信息价或厂家含税报价复核。")
        cost_note.setWordWrap(True)
        cost_note.setStyleSheet(
            "color:#9aa0b0;font-size:10px;background:transparent;")
        sec.add_widget(cost_note)
        self._attach_text_help(sec, "材料 是什么？",
            "遮阳板不是100%不透明黑体——即使挡住直射，材料本身的颜色/光泽仍会把一"
            "部分散射天空光/地面反射光反弹进室内。<b>k_diff</b>(残余透过比)就是这个"
            "比例，越大代表材料越亮/越反光。<br><br>预设k_diff按公开的太阳反射比资料"
            "粗略估算（深色混凝土≈0.16、镜面铝板≈0.78等），<b>不是严格标定值</b>，"
            "仅供选用参考。")
        self._main.addWidget(sec)

    def _build_project_section(self):
        sec = CollapsibleSection("当前工程基准（自动关联rlproj）", expanded=True)
        note = QLabel(
            "改造前基准完整保留当前工程状态；新方案自动克隆房间尺寸、进深、"
            "窗户位置/尺寸、玻璃透射率、玻璃SC、围护结构和所在地气象，随后"
            "移除原有水平/垂直遮阳，再扫描新的水平遮阳。原工程本身不会被修改。")
        note.setWordWrap(True)
        note.setStyleSheet("color:#9aa0b0;font-size:10px;background:transparent;")
        sec.add_widget(note)
        self._project_context = QLabel("尚未读取工程信息")
        self._project_context.setWordWrap(True)
        self._project_context.setStyleSheet(
            "color:#1d4ed8;font-size:10px;background:#eff6ff;"
            "border:1px solid #bfdbfe;border-radius:4px;padding:5px;")
        sec.add_widget(self._project_context)
        self._main.addWidget(sec)

    def set_project_context(self, room, weather=None, filename: str = ""):
        """由主窗口同步当前rlproj和气象摘要，仅显示、不持有RoomModel。"""
        from core.experiments import room_has_any_shading
        self._project_has_shading = room_has_any_shading(room)
        taus = [float(w.tau) for w in room.windows]
        tau_text = (
            f"{min(taus):.2f}" if taus and max(taus) - min(taus) < 1e-9
            else (f"{min(taus):.2f}~{max(taus):.2f}" if taus else "—")
        )
        wx = ""
        if weather is not None:
            wx = weather.location or weather.source
        name = filename or "未命名工程"
        self._project_context.setText(
            f"{name}\n"
            f"房间 {room.length/1000:.2f}×{room.width/1000:.2f}×"
            f"{room.height/1000:.2f} m；窗户 {len(room.windows)}扇；"
            f"玻璃τ={tau_text}，SC={room.thermal.SC_glass:.2f}；"
            f"气象={wx or '默认'}")
        self._btn_run.setEnabled(True)
        self._btn_run.setToolTip("")

    def set_complex_project_context(
        self,
        spaces,
        weather=None,
        filename: str = "",
        target_window_count: int | None = None,
    ):
        """显示鼠标选中房间的批量实验范围。"""
        wx = ""
        if weather is not None:
            wx = weather.location or weather.source
        name = filename or "未命名工程"
        has_windows = False
        vertical_note = ""
        if spaces is None:
            selected_spaces = []
        elif isinstance(spaces, (list, tuple)):
            selected_spaces = list(spaces)
        else:
            selected_spaces = [spaces]
        if not selected_spaces:
            detail = "尚未在建筑平面图中选择房间"
            self._project_has_shading = False
        else:
            from core.space_geometry import space_floor_area_mm2
            from core.complex_experiments import (
                exterior_windows,
                space_has_horizontal_shading,
            )

            per_space_openings = [
                exterior_windows(space) for space in selected_spaces
            ]
            wall_openings = [
                pair
                for openings in per_space_openings
                for pair in openings
            ]
            missing = [
                space.name
                for space, openings in zip(
                    selected_spaces,
                    per_space_openings,
                )
                if not openings
            ]
            has_windows = bool(wall_openings) and not missing
            windows = [opening for _wall, opening in wall_openings]
            shaded_walls = {wall.id for wall, _opening in wall_openings}
            taus = [float(opening.visible_transmittance) for opening in windows]
            tau_text = (
                f"{min(taus):.2f}"
                if taus and max(taus) - min(taus) < 1e-9
                else (
                    f"{min(taus):.2f}~{max(taus):.2f}"
                    if taus else "—"
                )
            )
            total_area = sum(
                space_floor_area_mm2(space) / 1_000_000
                for space in selected_spaces
            )
            detail = (
                f"已选 {len(selected_spaces)} 个房间；"
                f"总面积 {total_area:.2f} ㎡；"
                f"涉及外墙 {len(shaded_walls)}面、外窗 {len(windows)}扇；"
                f"玻璃τ={tau_text}"
            )
            if target_window_count is not None:
                detail += f"；参数化遮阳位置 {target_window_count} 扇"
            if missing:
                detail += f"；无外窗房间：{'、'.join(missing)}"
            self._project_has_shading = any(
                space_has_horizontal_shading(space)
                for space in selected_spaces
            )
            if any(
                bool(space.shading.vertical_fin_enabled)
                and (
                    float(space.shading.vertical_fin_depth_mm) > 0.0
                    or any(
                        float(value) > 0.0
                        for value in (
                            space.shading.fin_overrides or {}
                        ).values()
                    )
                )
                for space in selected_spaces
            ):
                vertical_note = (
                    "\n注意：工程含垂直翼板，其数据会保留，但本版复杂空间"
                    "实验只计算水平遮阳。"
                )
        self._project_context.setText(
            f"{name}\n{detail}；气象={wx or '默认'}\n"
            "每组θ/L/h/材料只应用到建筑视图中指定的参数化遮阳窗；"
            "采光和热指标按房间面积加权，造价按工程量求和；"
            f"原模型保留为改造前基准，L=0只保留一个建筑级方案。{vertical_note}"
        )
        can_run = has_windows and (
            target_window_count is None or target_window_count > 0
        )
        self._btn_run.setEnabled(can_run)
        self._btn_run.setToolTip(
            "" if can_run else "请先选择房间，并在建筑视图指定至少一扇参数化遮阳窗。"
        )

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
        self._btn_png = QPushButton("↓ 导出4张PNG")
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

        # 组合数量提醒（采光只按几何算一次，但仍提示总点数，避免误设几百个点糊成一团）
        positive_depths = [v for v in depth_mms if v > 0.0]
        n_geom = len(tilt_degs) * len(positive_depths) * len(gap_mms)
        # 原工程现状恒为1个参照点；若现状本来带遮阳且本轮含L=0，还会有一个
        # 独立的无板候选。若原工程本来无遮阳，同一基准点直接兼任L=0候选。
        no_shade_extra = int(
            any(v <= 0.0 for v in depth_mms) and self._project_has_shading)
        n_pts = n_geom * len(materials) + 1 + no_shade_extra
        if n_pts > 200:
            r = QMessageBox.question(
                self, "组合较多",
                f"当前设置将产生约 {n_pts} 个数据点（有效遮阳几何{n_geom}×"
                f"材料{len(materials)}＋改造前基准1"
                f"{'＋L=0无板候选1' if no_shade_extra else ''}）。"
                "L=0不随角度、间隙或材料重复。\n"
                "点数过多时图会比较密、计算也更久，确定继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if r != QMessageBox.StandardButton.Yes:
                return

        ykey = self._y_combo.currentData()
        params = {
            "tilt_degs": tilt_degs, "depth_mms": depth_mms, "gap_mms": gap_mms,
            "materials": materials,
            "material_unit_costs": {
                name: float(spin.value()) for name, spin in self._mat_cost_spins.items()
                if name in materials
            },
            "support_cost_per_m": float(self._support_cost.value()),
            "install_cost_per_window": float(self._install_cost.value()),
            "cost_basis": (
                "Σ窗宽×L×材料综合单价 + 2×Σ窗宽×支撑单价 + 窗数×安装费"
            ),
            "y": ykey, "maximize_y": _Y_META[ykey][0], "y_label": _Y_META[ykey][1],
            "u0_min": float(self._u0_min.value()),
        }
        self.run_requested.emit(params)
