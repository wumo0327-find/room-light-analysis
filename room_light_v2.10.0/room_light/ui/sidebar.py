"""
ui/sidebar.py — 可折叠参数侧边栏  v2.10.0
BUG FIX:
  - _add_win_card: blockSignals 防止 setValue 在构建期触发 _upd 回调
  - _upd 闭包使用明确的 win_id 查找，避免悬空引用
  - _del_win: 先发信号再销毁 widget，避免信号槽中引用已删部件
  - CollapsibleSection._on_toggle: 改用存储 title 字符串，避免 text()[2:] 切片越界
  - _build_quick_section: lambda 捕获修正，避免闭包共享变量问题
"""
from __future__ import annotations
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QDoubleSpinBox,
    QComboBox, QPushButton, QScrollArea, QFrame, QToolButton,
    QSizePolicy, QTableWidget, QTableWidgetItem, QHeaderView,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont

from core.models import RoomModel, Window, WALL_NAMES, WALL_MAP
from io_utils.weather_data import WeatherDataset, MONTHS_ZH

# ── 仅聚焦时响应滚轮的 SpinBox ────────────────────────────────────────────────
class _NoScrollSpinBox(QDoubleSpinBox):
    """
    完全禁用滚轮调值：wheelEvent 直接 ignore，
    事件冒泡至父级 QScrollArea，实现正常页面滚动。
    数值修改只能通过点击上下箭头或直接键盘输入。
    """
    def wheelEvent(self, event):
        # 无论是否聚焦，一律不用滚轮改值，交给父级滚动
        event.ignore()


# ── 小工具函数 ────────────────────────────────────────────────────────────────

def _h_line() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setStyleSheet("color:#d0d5e0;")
    return f


def _lbl(text: str, bold=False, color="#1a1e2e", size=13) -> QLabel:
    l = QLabel(text)
    f = QFont()
    f.setBold(bold)
    f.setPointSize(size)
    l.setFont(f)
    l.setStyleSheet(f"color:{color};background:transparent;")
    return l


def _dspin(val: float, mn: float, mx: float,
           step: float = 1.0, dec: int = 0) -> _NoScrollSpinBox:
    """SpinBox 工厂：构造完成后才赋值，内部不主动 emit。仅聚焦时响应滚轮。"""
    s = _NoScrollSpinBox()
    s.setRange(mn, mx)
    s.setSingleStep(step)
    s.setDecimals(dec)
    s.setFixedHeight(30)
    # blockSignals 期间赋值，避免构造时就触发 valueChanged
    s.blockSignals(True)
    s.setValue(val)
    s.blockSignals(False)
    return s


# ── CollapsibleSection ────────────────────────────────────────────────────────

class CollapsibleSection(QWidget):
    """可折叠面板：修复了 toggle 文字切片越界 bug。"""

    def __init__(self, title: str, parent=None, expanded: bool = True):
        super().__init__(parent)
        self._title    = title          # ← 单独存 title，不再从 text() 反解析
        self._expanded = expanded

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 4)
        lay.setSpacing(2)

        # 标题行
        hdr = QWidget()
        hdr.setStyleSheet("background:#e8ebf2;border-radius:6px;border:1px solid #d0d5e0;")
        hlay = QHBoxLayout(hdr)
        hlay.setContentsMargins(8, 5, 8, 5)

        self._toggle = QToolButton()
        self._toggle.setStyleSheet(
            "QToolButton{background:transparent;border:none;"
            "color:#2563eb;font-weight:700;font-size:13px;text-align:left;}"
            "QToolButton:hover{color:#1d4ed8;}")
        self._toggle.setCheckable(True)
        self._toggle.setChecked(expanded)
        self._toggle.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._toggle.clicked.connect(self._on_toggle)
        self._refresh_toggle_text()

        hlay.addWidget(self._toggle)
        lay.addWidget(hdr)

        # 内容体
        self._body = QWidget()
        self._body.setStyleSheet("background:#ffffff;border-radius:6px;border:1px solid #e8ebf2;")
        bl = QVBoxLayout(self._body)
        bl.setContentsMargins(10, 8, 10, 10)
        bl.setSpacing(6)
        self._cl = bl
        lay.addWidget(self._body)
        self._body.setVisible(expanded)

    def _refresh_toggle_text(self):
        prefix = "▾ " if self._expanded else "▸ "
        self._toggle.setText(prefix + self._title)

    def set_title(self, title: str):
        """安全更新标题，不依赖 text()[2:] 切片。"""
        self._title = title
        self._refresh_toggle_text()

    def _on_toggle(self, checked: bool):
        self._expanded = checked
        self._body.setVisible(checked)
        self._refresh_toggle_text()

    def add_widget(self, w: QWidget):
        self._cl.addWidget(w)

    def add_layout(self, lay):
        self._cl.addLayout(lay)

    def add_row(self, label: str, widget: QWidget, unit: str = ""):
        row = QHBoxLayout()
        lbl = _lbl(label, color="#5a6175")
        lbl.setMinimumWidth(78)
        row.addWidget(lbl)
        row.addWidget(widget, 1)
        if unit:
            row.addWidget(_lbl(unit, color="#4a5270", size=11))
        self._cl.addLayout(row)


# ── Sidebar 主类 ──────────────────────────────────────────────────────────────

class Sidebar(QWidget):
    room_changed             = pyqtSignal()
    window_added             = pyqtSignal(int)
    window_removed           = pyqtSignal(int)
    window_changed           = pyqtSignal(int)
    view_changed             = pyqtSignal(str)
    weather_dialog_requested = pyqtSignal()

    def __init__(self, room: RoomModel, parent=None):
        super().__init__(parent)
        self.room = room
        self.setFixedWidth(320)
        self.setObjectName("sidebar_root")
        self.setStyleSheet("background:#f0f2f6;")

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("background:transparent;border:none;")

        inner = QWidget()
        inner.setStyleSheet("background:#f0f2f6;")
        self._main = QVBoxLayout(inner)
        self._main.setContentsMargins(8, 10, 8, 20)
        self._main.setSpacing(6)
        scroll.setWidget(inner)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(scroll)

        # win_id → CollapsibleSection card
        self._win_widgets: dict[int, CollapsibleSection] = {}

        self._build_view_section()
        self._build_room_section()
        self._build_windows_section()
        self._build_shading_section()
        self._build_shading_overrides_section()
        self._build_material_section()
        self._build_location_section()
        self._build_weather_section()
        self._build_thermal_section()
        self._build_quick_section()

        self._main.addStretch()

    # ── 视图切换 ──────────────────────────────────────────────────────────

    def _build_view_section(self):
        sec = CollapsibleSection("视图", expanded=True)
        from ui.canvas import VIEWS
        self._view_btns: dict[str, QPushButton] = {}
        row1 = QHBoxLayout()
        row2 = QHBoxLayout()
        for i, v in enumerate(VIEWS):
            btn = QPushButton(v)
            btn.setObjectName("view_btn")
            btn.setProperty("active", "true" if v == "平面" else "false")
            btn.setFixedHeight(30)
            btn.clicked.connect(lambda _, vv=v: self._on_view(vv))
            self._view_btns[v] = btn
            (row1 if i < 3 else row2).addWidget(btn)
        sec.add_layout(row1)
        sec.add_layout(row2)
        self._main.addWidget(sec)

    def _on_view(self, v: str):
        for vv, b in self._view_btns.items():
            b.setProperty("active", "true" if vv == v else "false")
            b.style().unpolish(b)
            b.style().polish(b)
        self.view_changed.emit(v)

    # ── 房间几何 ──────────────────────────────────────────────────────────

    def _build_room_section(self):
        r   = self.room
        sec = CollapsibleSection("房间几何", expanded=True)
        self._sl = _dspin(r.length, 500,  50000, 100)
        self._sw = _dspin(r.width,  500,  50000, 100)
        self._sh = _dspin(r.height, 1500, 15000,  50)
        sec.add_row("长 L", self._sl, "mm")
        sec.add_row("宽 W", self._sw, "mm")
        sec.add_row("高 H", self._sh, "mm")
        # 连接信号（此时 blockSignals 已解除，安全）
        self._sl.valueChanged.connect(lambda v: self._on_room_p("length", v))
        self._sw.valueChanged.connect(lambda v: self._on_room_p("width",  v))
        self._sh.valueChanged.connect(lambda v: self._on_room_p("height", v))
        self._main.addWidget(sec)

    def _on_room_p(self, attr: str, val: float):
        setattr(self.room, attr, val)
        self.room_changed.emit()

    # ── 窗户 ──────────────────────────────────────────────────────────────

    def _build_windows_section(self):
        self._win_sec = CollapsibleSection("窗户", expanded=True)

        add_row = QHBoxLayout()
        self._combo_wall = QComboBox()
        self._combo_wall.addItems(WALL_NAMES)
        self._combo_wall.setFixedHeight(30)
        btn_add = QPushButton("+ 添加窗户")
        btn_add.setObjectName("primary_btn")
        btn_add.setFixedHeight(30)
        btn_add.clicked.connect(self._on_add_win)
        add_row.addWidget(self._combo_wall, 1)
        add_row.addWidget(btn_add)
        self._win_sec.add_layout(add_row)
        self._win_sec.add_widget(_h_line())

        self._win_cont = QWidget()
        self._win_cont.setStyleSheet("background:transparent;")
        self._win_lay = QVBoxLayout(self._win_cont)
        self._win_lay.setContentsMargins(0, 0, 0, 0)
        self._win_lay.setSpacing(4)
        self._win_sec.add_widget(self._win_cont)
        self._main.addWidget(self._win_sec)

    def _on_add_win(self):
        wall = WALL_MAP.get(self._combo_wall.currentText(), "south")
        win  = self.room.add_window(wall)
        self._add_win_card(win)
        if hasattr(self, "_ov_lay"):
            self._add_shading_override_card(win)
        self.window_added.emit(win.id)

    def _add_win_card(self, win: Window):
        """
        为一扇窗户创建编辑卡片。

        关键修复：
        1. 所有 SpinBox 使用 blockSignals=True 的 _dspin()，构建期不触发信号。
        2. 全部控件添加完毕后才统一连接 valueChanged，避免半初始化时回调。
        3. _upd 通过 win_id 重新查找窗户对象，不持有可能失效的引用。
        4. _upd 先检查 win 是否还存在于 room，防止删除后信号延迟触发崩溃。
        """
        win_id = win.id
        title  = f"{win.label()}  {win.width/1000:.1f}×{win.height/1000:.1f}m"
        card   = CollapsibleSection(title, expanded=True)

        # ── 构建所有 SpinBox（blockSignals 已在 _dspin 内处理）────────────
        sp_x = _dspin(win.x,      0,     30000, 50)
        sp_y = _dspin(win.y,      0,     15000, 50)
        sp_w = _dspin(win.width,  100,   30000, 50)
        sp_h = _dspin(win.height, 100,   15000, 50)
        sp_t = _dspin(win.tau,    0.01,  1.0,   0.01, dec=2)

        card.add_row("X 距左边", sp_x, "mm")
        card.add_row("Y 距底边", sp_y, "mm")
        card.add_row("窗宽",     sp_w, "mm")
        card.add_row("窗高",     sp_h, "mm")
        card.add_row("透射比 τ", sp_t)

        btn_del = QPushButton(f"× 删除 W{win_id}")
        btn_del.setObjectName("danger_btn")
        btn_del.setFixedHeight(28)
        btn_del.clicked.connect(
            lambda checked=False, wid=win_id, c=card: self._del_win(wid, c))
        card.add_widget(btn_del)

        # ── 全部控件到位后才连接信号 ─────────────────────────────────────
        def _upd(_val=None, _wid=win_id, _card=card,
                 _sp_x=sp_x, _sp_y=sp_y, _sp_w=sp_w,
                 _sp_h=sp_h, _sp_t=sp_t):
            """回调：安全地更新窗户属性。"""
            # 先确认窗户仍然存在（删除后信号可能延迟到达）
            _win = self.room.get_window(_wid)
            if _win is None:
                return
            _win.x      = _sp_x.value()
            _win.y      = _sp_y.value()
            _win.width  = _sp_w.value()
            _win.height = _sp_h.value()
            _win.tau    = _sp_t.value()
            new_title = f"{_win.label()}  {_win.width/1000:.1f}×{_win.height/1000:.1f}m"
            _card.set_title(new_title)
            self.window_changed.emit(_wid)

        for sp in (sp_x, sp_y, sp_w, sp_h, sp_t):
            sp.valueChanged.connect(_upd)

        # ── 插入布局并注册 ────────────────────────────────────────────────
        self._win_lay.addWidget(card)
        self._win_widgets[win_id] = card

    def _del_win(self, win_id: int, card: CollapsibleSection):
        """
        删除窗户：
        修复顺序：先从数据模型删除 → 发信号 → 再销毁 widget。
        避免信号槽中仍引用子控件时 widget 已被 deleteLater 的崩溃。
        """
        # 1. 断开该卡片内所有 SpinBox 的信号（防止 deleteLater 后延迟回调）
        for sp in card.findChildren(QDoubleSpinBox):
            try:
                sp.blockSignals(True)
            except RuntimeError:
                pass

        # 2. 从数据模型中删除
        self.room.remove_window(win_id)
        self._win_widgets.pop(win_id, None)
        if hasattr(self, "_ov_widgets"):
            self._remove_shading_override_card(win_id)

        # 3. 发出信号（此时 widget 还活着，槽函数安全访问）
        self.window_removed.emit(win_id)

        # 4. 最后销毁 widget
        card.setParent(None)
        card.deleteLater()

    # ── 遮阳构件 ──────────────────────────────────────────────────────────
    # 遮阳类型下拉文本 → 模型 type 值；仅「水平挑檐」与「无遮阳」可用
    _SHADE_TYPES = [
        ("无遮阳",           "none",                True),
        ("水平挑檐",         "horizontal_overhang", True),
        ("百叶（暂未实现）", "louver",              False),
        ("导光板（暂未实现）","light_shelf",         False),
    ]

    def _build_shading_section(self):
        s   = self.room.shading
        sec = CollapsibleSection("遮阳构件（全局默认）", expanded=False)

        # 类型下拉（未实现项置灰）
        self._sh_type = QComboBox()
        self._sh_type.setFixedHeight(30)
        for i, (txt, _val, enabled) in enumerate(self._SHADE_TYPES):
            self._sh_type.addItem(txt)
            if not enabled:
                item = self._sh_type.model().item(i)
                if item is not None:
                    item.setEnabled(False)
        # 定位当前 type
        cur_idx = next((i for i, (_t, v, _e) in enumerate(self._SHADE_TYPES)
                        if v == s.type), 0)
        self._sh_type.blockSignals(True)
        self._sh_type.setCurrentIndex(cur_idx)
        self._sh_type.blockSignals(False)
        sec.add_row("遮阳类型", self._sh_type)

        # 水平挑檐几何参数（全局默认值；单扇窗户可在下方"遮阳构件·逐窗设置"覆盖）
        self._sh_depth = _dspin(s.overhang_depth_mm,  0, 5000, 50, 0)
        self._sh_gap   = _dspin(s.overhang_height_mm, 0, 2000, 10, 0)
        self._sh_kdiff = _dspin(s.diffuse_residual,   0.0, 1.0, 0.01, 2)
        self._sh_kdiff.setToolTip(
            "k_diff：完全遮挡时仍可透过的散射（天空散射+地面反射）比例，0–1。\n"
            "越大表示挑檐挡住直射后室内仍保留越多散射得热。默认 0.30。")

        sec.add_row("出挑深度 D",   self._sh_depth, "mm")
        sec.add_row("安装间隙",     self._sh_gap,   "mm")
        sec.add_row("残余散射 k",   self._sh_kdiff)

        sec.add_widget(_h_line())

        # 垂直翼板全局开关+默认深度（v2.10 新增 GUI 入口，此前只能在 .rlproj 里手改）
        self._sh_fin_on = QComboBox()
        self._sh_fin_on.addItems(["关闭", "启用"])
        self._sh_fin_on.setFixedHeight(30)
        self._sh_fin_on.blockSignals(True)
        self._sh_fin_on.setCurrentIndex(1 if s.vertical_fin_enabled else 0)
        self._sh_fin_on.blockSignals(False)
        sec.add_row("垂直翼板", self._sh_fin_on)

        self._sh_fin_depth = _dspin(s.vertical_fin_depth_mm, 0, 5000, 50, 0)
        sec.add_row("翼板出挑深度", self._sh_fin_depth, "mm")

        hint = _lbl("挑檐仅对南向窗按「月中日正午」剖面角逐月遮阳；翼板每扇窗左右两侧\n"
                    "各一处（最左/最右窗对应端墙，其余对应窗间墙），可在下方逐窗覆盖。\n"
                    "改参数后点工具栏「▶ 全部分析」刷新结果。",
                    color="#9aa0b0", size=10)
        hint.setWordWrap(True)
        sec.add_widget(hint)

        # 信号（blockSignals 已在 _dspin / 上方处理，此处安全连接）
        self._sh_type.currentIndexChanged.connect(self._on_shade_type)
        self._sh_depth.valueChanged.connect(
            lambda v: self._on_shade_p("overhang_depth_mm",  v))
        self._sh_gap.valueChanged.connect(
            lambda v: self._on_shade_p("overhang_height_mm", v))
        self._sh_kdiff.valueChanged.connect(
            lambda v: self._on_shade_p("diffuse_residual",   v))
        self._sh_fin_on.currentIndexChanged.connect(self._on_shade_fin_enabled)
        self._sh_fin_depth.valueChanged.connect(
            lambda v: self._on_shade_p("vertical_fin_depth_mm", v))

        self._main.addWidget(sec)
        self._sync_shade_enabled()   # 按当前类型启用/禁用几何参数

    def _sync_shade_enabled(self):
        """仅当类型为水平挑檐时，几何参数可编辑。"""
        on = (self.room.shading.type == "horizontal_overhang")
        for sp in (self._sh_depth, self._sh_gap, self._sh_kdiff):
            sp.setEnabled(on)

    def _on_shade_type(self, idx: int):
        if 0 <= idx < len(self._SHADE_TYPES):
            self.room.shading.type = self._SHADE_TYPES[idx][1]
            self._sync_shade_enabled()
            self.room_changed.emit()

    def _on_shade_fin_enabled(self, idx: int):
        self.room.shading.vertical_fin_enabled = bool(idx == 1)
        self.room_changed.emit()

    def _on_shade_p(self, attr: str, val: float):
        setattr(self.room.shading, attr, val)
        self.room_changed.emit()

    # ── 遮阳构件·逐窗设置（v2.10 新增）────────────────────────────────────────
    def _build_shading_overrides_section(self):
        """
        参照"窗户"面板的列表交互：每扇窗户一张卡片，可单独覆盖水平挑檐出挑
        深度/安装间隙、垂直翼板左/右两侧出挑深度（未勾选覆盖则使用上方全局
        默认值）；每张卡片自带"应用到全部窗户"，把该窗当前的覆盖值广播给其余
        窗户（左右两侧的翼板覆盖独立广播，即左侧值给所有窗户的左侧、右侧值给
        所有窗户的右侧——不区分该位置是端墙还是窗间墙，因为覆盖机制按"窗户+
        左/右"寻址，与端墙/窗间墙的语义无关，见 core/models.py ShadingDevice）。
        """
        self._ov_sec = CollapsibleSection("遮阳构件·逐窗设置", expanded=False)
        hint = _lbl("未勾选\"覆盖\"的项使用上方全局默认值。窗户左/右两侧翼板：\n"
                    "该窗为本墙最左/最右窗时，对应侧即两端外墙位置；否则为窗间墙位置。",
                    color="#9aa0b0", size=10)
        hint.setWordWrap(True)
        self._ov_sec.add_widget(hint)

        self._ov_cont = QWidget()
        self._ov_cont.setStyleSheet("background:transparent;")
        self._ov_lay = QVBoxLayout(self._ov_cont)
        self._ov_lay.setContentsMargins(0, 0, 0, 0)
        self._ov_lay.setSpacing(4)
        self._ov_sec.add_widget(self._ov_cont)
        self._main.addWidget(self._ov_sec)

        self._ov_widgets: dict[int, CollapsibleSection] = {}
        for win in self.room.windows:
            self._add_shading_override_card(win)

    def _add_shading_override_card(self, win: Window):
        win_id = win.id
        s = self.room.shading
        card = CollapsibleSection(f"{win.label()} 遮阳覆盖", expanded=False)

        ov = s.overhang_overrides.get(str(win_id))
        cb_ov = QComboBox()
        cb_ov.addItems(["用全局默认", "覆盖"])
        cb_ov.setFixedHeight(28)
        cb_ov.blockSignals(True)
        cb_ov.setCurrentIndex(1 if ov else 0)
        cb_ov.blockSignals(False)
        card.add_row("挑檐", cb_ov)
        sp_ov_d = _dspin(float(ov.get("depth_mm", s.overhang_depth_mm)) if ov else s.overhang_depth_mm,
                        0, 5000, 50, 0)
        sp_ov_g = _dspin(float(ov.get("gap_mm", s.overhang_height_mm)) if ov else s.overhang_height_mm,
                        0, 2000, 10, 0)
        card.add_row("　出挑深度", sp_ov_d, "mm")
        card.add_row("　安装间隙", sp_ov_g, "mm")
        sp_ov_d.setEnabled(bool(ov))
        sp_ov_g.setEnabled(bool(ov))

        btn_ov_all = QPushButton("挑檐覆盖 → 应用到全部窗户")
        btn_ov_all.setFixedHeight(26)
        card.add_widget(btn_ov_all)

        card.add_widget(_h_line())

        fL = s.fin_overrides.get(f"{win_id}:L")
        cb_finL = QComboBox()
        cb_finL.addItems(["用全局默认", "覆盖"])
        cb_finL.setFixedHeight(28)
        cb_finL.blockSignals(True)
        cb_finL.setCurrentIndex(1 if fL is not None else 0)
        cb_finL.blockSignals(False)
        card.add_row("左侧翼板", cb_finL)
        sp_finL = _dspin(float(fL) if fL is not None else s.vertical_fin_depth_mm,
                        0, 5000, 50, 0)
        sp_finL.setEnabled(fL is not None)
        card.add_row("　出挑深度", sp_finL, "mm")

        fR = s.fin_overrides.get(f"{win_id}:R")
        cb_finR = QComboBox()
        cb_finR.addItems(["用全局默认", "覆盖"])
        cb_finR.setFixedHeight(28)
        cb_finR.blockSignals(True)
        cb_finR.setCurrentIndex(1 if fR is not None else 0)
        cb_finR.blockSignals(False)
        card.add_row("右侧翼板", cb_finR)
        sp_finR = _dspin(float(fR) if fR is not None else s.vertical_fin_depth_mm,
                        0, 5000, 50, 0)
        sp_finR.setEnabled(fR is not None)
        card.add_row("　出挑深度", sp_finR, "mm")

        btn_fin_all = QPushButton("翼板覆盖 → 应用到全部窗户")
        btn_fin_all.setFixedHeight(26)
        card.add_widget(btn_fin_all)

        def _upd_overhang(_val=None, _wid=win_id,
                          _cb=cb_ov, _spd=sp_ov_d, _spg=sp_ov_g):
            _sh = self.room.shading
            if _cb.currentIndex() == 1:
                _sh.overhang_overrides[str(_wid)] = {
                    "depth_mm": _spd.value(), "gap_mm": _spg.value()}
            else:
                _sh.overhang_overrides.pop(str(_wid), None)
            _spd.setEnabled(_cb.currentIndex() == 1)
            _spg.setEnabled(_cb.currentIndex() == 1)
            self.window_changed.emit(_wid)

        def _upd_finL(_val=None, _wid=win_id, _cb=cb_finL, _sp=sp_finL):
            _sh = self.room.shading
            key = f"{_wid}:L"
            if _cb.currentIndex() == 1:
                _sh.fin_overrides[key] = _sp.value()
            else:
                _sh.fin_overrides.pop(key, None)
            _sp.setEnabled(_cb.currentIndex() == 1)
            self.window_changed.emit(_wid)

        def _upd_finR(_val=None, _wid=win_id, _cb=cb_finR, _sp=sp_finR):
            _sh = self.room.shading
            key = f"{_wid}:R"
            if _cb.currentIndex() == 1:
                _sh.fin_overrides[key] = _sp.value()
            else:
                _sh.fin_overrides.pop(key, None)
            _sp.setEnabled(_cb.currentIndex() == 1)
            self.window_changed.emit(_wid)

        def _apply_overhang_all(_checked=False, _wid=win_id):
            _sh = self.room.shading
            src = _sh.overhang_overrides.get(str(_wid))
            if not src:
                return
            for w in self.room.windows:
                _sh.overhang_overrides[str(w.id)] = dict(src)
            for _wid2, _card2 in self._ov_widgets.items():
                pass  # 数值刷新见下方整体重建（避免逐卡片查找控件的复杂度）
            self.room_changed.emit()
            self._rebuild_shading_overrides()

        def _apply_fin_all(_checked=False, _wid=win_id):
            _sh = self.room.shading
            srcL = _sh.fin_overrides.get(f"{_wid}:L")
            srcR = _sh.fin_overrides.get(f"{_wid}:R")
            for w in self.room.windows:
                if srcL is not None:
                    _sh.fin_overrides[f"{w.id}:L"] = srcL
                if srcR is not None:
                    _sh.fin_overrides[f"{w.id}:R"] = srcR
            self.room_changed.emit()
            self._rebuild_shading_overrides()

        cb_ov.currentIndexChanged.connect(_upd_overhang)
        sp_ov_d.valueChanged.connect(_upd_overhang)
        sp_ov_g.valueChanged.connect(_upd_overhang)
        cb_finL.currentIndexChanged.connect(_upd_finL)
        sp_finL.valueChanged.connect(_upd_finL)
        cb_finR.currentIndexChanged.connect(_upd_finR)
        sp_finR.valueChanged.connect(_upd_finR)
        btn_ov_all.clicked.connect(_apply_overhang_all)
        btn_fin_all.clicked.connect(_apply_fin_all)

        self._ov_lay.addWidget(card)
        self._ov_widgets[win_id] = card

    def _remove_shading_override_card(self, win_id: int):
        card = self._ov_widgets.pop(win_id, None)
        if card is not None:
            card.setParent(None)
            card.deleteLater()
        # 该窗户相关的覆盖项一并清理，避免残留死数据
        s = self.room.shading
        s.overhang_overrides.pop(str(win_id), None)
        s.fin_overrides.pop(f"{win_id}:L", None)
        s.fin_overrides.pop(f"{win_id}:R", None)

    def _rebuild_shading_overrides(self):
        """整体重建逐窗遮阳覆盖卡片列表（用于"应用到全部窗户"后刷新显示数值）。"""
        for card in list(self._ov_widgets.values()):
            card.setParent(None)
            card.deleteLater()
        self._ov_widgets = {}
        for win in self.room.windows:
            self._add_shading_override_card(win)

    # ── 材料参数 ──────────────────────────────────────────────────────────

    def _build_material_section(self):
        m   = self.room.material
        sec = CollapsibleSection("材料反射率", expanded=False)
        self._rw = _dspin(m.rho_wall,    0, 1, 0.01, 2)
        self._rc = _dspin(m.rho_ceiling, 0, 1, 0.01, 2)
        self._rf = _dspin(m.rho_floor,   0, 1, 0.01, 2)
        self._rg = _dspin(m.rho_ground,  0, 1, 0.01, 2)
        sec.add_row("墙面 ρw",  self._rw)
        sec.add_row("顶棚 ρc",  self._rc)
        sec.add_row("地面 ρf",  self._rf)
        sec.add_row("室外地面", self._rg)
        self._rw.valueChanged.connect(lambda v: self._on_mat("rho_wall",    v))
        self._rc.valueChanged.connect(lambda v: self._on_mat("rho_ceiling", v))
        self._rf.valueChanged.connect(lambda v: self._on_mat("rho_floor",   v))
        self._rg.valueChanged.connect(lambda v: self._on_mat("rho_ground",  v))
        self._main.addWidget(sec)

    def _on_mat(self, attr: str, val: float):
        setattr(self.room.material, attr, val)
        self.room_changed.emit()

    # ── 地理位置 ──────────────────────────────────────────────────────────

    def _build_location_section(self):
        loc = self.room.location
        sec = CollapsibleSection("地理位置", expanded=False)
        self._lat = _dspin(loc.latitude,  -90,   90, 0.1, 4)
        self._lon = _dspin(loc.longitude, -180, 180, 0.1, 4)
        sec.add_row("北纬 Lat", self._lat, "°")
        sec.add_row("东经 Lon", self._lon, "°")
        self._lat.valueChanged.connect(
            lambda v: setattr(self.room.location, "latitude",  v))
        self._lon.valueChanged.connect(
            lambda v: setattr(self.room.location, "longitude", v))
        self._main.addWidget(sec)

    # ── 气象数据 ──────────────────────────────────────────────────────────

    def _build_weather_section(self):
        self._wx_sec = CollapsibleSection("气象数据", expanded=True)

        self._wx_status = _lbl("— 使用益阳 TMY 默认值", color="#16a34a", size=12)
        self._wx_source = _lbl("", color="#4a5270", size=11)
        self._wx_source.setWordWrap(True)
        self._wx_sec.add_widget(self._wx_status)
        self._wx_sec.add_widget(self._wx_source)

        btn = QPushButton("⚙  更改气象数据…")
        btn.setObjectName("success_btn")
        btn.setFixedHeight(32)
        btn.clicked.connect(self.weather_dialog_requested.emit)
        self._wx_sec.add_widget(btn)
        self._wx_sec.add_widget(_h_line())

        self._wx_table = QTableWidget(12, 4)
        self._wx_table.setHorizontalHeaderLabels(["月份", "GHI W/m²", "照度 lux", "温度 ℃"])
        self._wx_table.verticalHeader().setVisible(False)
        self._wx_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self._wx_table.setFixedHeight(230)
        self._wx_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._wx_table.setStyleSheet("font-size:12px;background:#ffffff;")
        self._wx_sec.add_widget(self._wx_table)
        self._main.addWidget(self._wx_sec)

        # 启动时填入益阳默认值
        from io_utils.weather_data import default_dataset
        self.on_weather_loaded(default_dataset())

    def on_weather_loaded(self, ds: WeatherDataset):
        self._wx_status.setText(
            f"✓ {ds.location or ds.source}  年均 {ds.annual_avg:.0f} lux")
        self._wx_status.setStyleSheet("color:#52c788;background:transparent;")
        self._wx_source.setText(f"来源: {ds.source}")
        for i, row in enumerate(ds.summary_rows()):
            for col, val in enumerate(row):
                it = QTableWidgetItem(val)
                it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._wx_table.setItem(i, col, it)
        self._wx_sec._toggle.setChecked(True)
        self._wx_sec._body.setVisible(True)

    # ── 快速估算 ──────────────────────────────────────────────────────────

    # ── 围护结构热工参数 ──────────────────────────────────────────────────
    def _build_thermal_section(self):
        t   = self.room.thermal
        sec = CollapsibleSection("围护结构热工参数", expanded=False)

        self._Uw  = _dspin(t.U_wall,         0.1, 5.0, 0.05, 2)
        self._Ur  = _dspin(t.U_roof,         0.1, 5.0, 0.05, 2)
        self._Uf  = _dspin(t.U_floor,        0.1, 5.0, 0.05, 2)
        self._Ug  = _dspin(t.U_win,          0.5, 7.0, 0.1,  2)
        self._SC  = _dspin(t.SC_glass,       0.1, 1.0, 0.01, 2)
        self._abs = _dspin(t.wall_solar_abs, 0.2, 0.9, 0.01, 2)
        self._ach = _dspin(t.n_ach,          0.1, 5.0, 0.1,  1)
        self._qp  = _dspin(t.q_people,       0.0,30.0, 0.5,  1)
        self._qe  = _dspin(t.q_equipment,    0.0,20.0, 0.5,  1)
        self._ql  = _dspin(t.q_lighting,     0.0,20.0, 0.5,  1)
        self._wt  = _dspin(t.wall_thickness_mm, 50, 600, 10, 0)

        sec.add_row("外墙 U_wall",    self._Uw,  "W/(m²K)")
        sec.add_row("屋顶 U_roof",    self._Ur,  "W/(m²K)")
        sec.add_row("地面 U_floor",   self._Uf,  "W/(m²K)")
        sec.add_row("外窗 U_win",     self._Ug,  "W/(m²K)")
        sec.add_row("玻璃 SC",        self._SC)
        sec.add_row("外墙吸收系数 α", self._abs)
        sec.add_row("换气次数 n",     self._ach, "次/h")
        sec.add_row("人员热扰 q",     self._qp,  "W/m²")
        sec.add_row("设备热扰 q",     self._qe,  "W/m²")
        sec.add_row("照明热扰 q",     self._ql,  "W/m²")
        sec.add_row("外墙厚度",       self._wt,  "mm")

        for sp, attr in [
            (self._Uw,  "U_wall"),
            (self._Ur,  "U_roof"),
            (self._Uf,  "U_floor"),
            (self._Ug,  "U_win"),
            (self._SC,  "SC_glass"),
            (self._abs, "wall_solar_abs"),
            (self._ach, "n_ach"),
            (self._qp,  "q_people"),
            (self._qe,  "q_equipment"),
            (self._ql,  "q_lighting"),
            (self._wt,  "wall_thickness_mm"),
        ]:
            sp.valueChanged.connect(
                lambda v, a=attr: self._on_thermal_p(a, v))
        self._main.addWidget(sec)

    def _on_thermal_p(self, attr: str, val: float):
        setattr(self.room.thermal, attr, val)
        self.room_changed.emit()

    def _build_quick_section(self):
        self._quick_sec = CollapsibleSection("快速估算 (Lynes)", expanded=True)
        self._q_eavg = _lbl("—", color="#2563eb", size=18)
        self._q_eavg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._q_df   = _lbl("DF —%", color="#5a6175", size=11)
        self._q_df.setAlignment(Qt.AlignmentFlag.AlignCenter)
        note = _lbl(
            "基于 Lynes Flux Method 解析估算\n"
            "（实时更新，精确结果点击「开始分析」）",
            color="#9aa0b0", size=10)
        note.setWordWrap(True)
        self._quick_sec.add_widget(self._q_eavg)
        self._quick_sec.add_widget(self._q_df)
        self._quick_sec.add_widget(note)
        self._main.addWidget(self._quick_sec)

        # 使用 Qt 信号而非 lambda 避免循环引用
        self.room_changed.connect(self._update_quick)
        self.window_added.connect(self._update_quick_from_signal)
        self.window_removed.connect(self._update_quick_from_signal)
        self.window_changed.connect(self._update_quick_from_signal)

    def _update_quick_from_signal(self, _: int):
        """接受 int 参数的槽（供 window_* 信号使用）。"""
        self._update_quick()

    def _update_quick(self):
        try:
            from core.daylight import _quick
            from io_utils.weather_data import YIYANG_TMY_LUX
            yy_avg = sum(YIYANG_TMY_LUX) / len(YIYANG_TMY_LUX)
            q      = _quick(self.room, yy_avg)
            E      = q.get("E_avg", 0.0)
            DF     = q.get("DF_avg", 0.0)
            WFR    = q.get("WFR", 0.0)
            color  = "#16a34a" if E >= 300 else "#dc2626"
            self._q_eavg.setText(f"{E:.0f} lux")
            self._q_eavg.setStyleSheet(
                f"color:{color};font-size:22px;font-weight:700;"
                "background:transparent;")
            self._q_df.setText(f"DF {DF:.2f}%   WFR {WFR:.3f}")
        except Exception:
            # 防御：任何计算错误不崩溃 UI
            pass
