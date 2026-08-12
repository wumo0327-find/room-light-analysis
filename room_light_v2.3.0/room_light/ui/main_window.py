"""
ui/main_window.py — 主窗口  v2.3.0
新增:
  - 项目保存/打开 (.rlproj JSON 格式)
  - 拖拽 .rlproj 文件到窗口直接加载
  - 加载后完整重建侧边栏控件
修复:
  - view_btn active 文字改为深蓝（白底清晰可见）
"""
from __future__ import annotations
import os

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QSplitter,
    QLabel, QStackedWidget, QPushButton, QFileDialog, QMessageBox,
)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QDragEnterEvent, QDropEvent

from core.models import RoomModel
from core.daylight import DaylightResult
from io_utils.weather_data import WeatherDataset
from ui.canvas import RoomCanvas
from ui.analysis_panel import AnalysisPanel
from ui.sidebar import Sidebar
from io_utils.project_io import FILE_EXT, FILE_FILTER, save_project, load_project


class CalcThread(QThread):
    finished = pyqtSignal(object)
    error    = pyqtSignal(str)

    def __init__(self, room: RoomModel, E_out: float):
        super().__init__()
        self.room  = room
        self.E_out = E_out

    def run(self):
        try:
            from core.daylight import compute
            res = compute(self.room, self.E_out, store_components=True)
            self.finished.emit(res)
        except Exception:
            import traceback
            self.error.emit(traceback.format_exc())


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("建筑室内采光分析工具  v2.3.0  |  益阳默认气象")
        self.resize(1440, 880)
        self.setMinimumSize(1024, 680)
        self.setAcceptDrops(True)   # 启用拖拽

        self.room = RoomModel()
        from io_utils.weather_data import default_dataset
        self.weather = default_dataset()
        self._result: DaylightResult | None = None
        self._calc_thread: CalcThread | None = None
        self._current_file: str = ""

        # ── 布局 ───────────────────────────────────────────────────────────
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)

        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.setHandleWidth(2)
        self._splitter.setStyleSheet("QSplitter::handle{background:#d0d5e0;}")

        self.sidebar = Sidebar(self.room)
        self._splitter.addWidget(self.sidebar)

        right = QWidget()
        right.setStyleSheet("background:#ffffff;")
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(0)
        right_lay.addWidget(self._build_toolbar())

        self._stack = QStackedWidget()
        self.canvas   = RoomCanvas(self.room)
        self.analysis = AnalysisPanel()
        self._stack.addWidget(self.canvas)
        self._stack.addWidget(self.analysis)
        right_lay.addWidget(self._stack, 1)

        self._splitter.addWidget(right)
        self._splitter.setStretchFactor(0, 0)
        self._splitter.setStretchFactor(1, 1)
        self._splitter.setSizes([320, 1120])
        root.addWidget(self._splitter)

        # ── 状态栏 ─────────────────────────────────────────────────────────
        self._status_lbl = QLabel("就绪 — 可将 .rlproj 文件拖入窗口打开")
        self._status_lbl.setStyleSheet("color:#5a6175;padding:0 8px;")
        from io_utils.weather_data import DEFAULT_LOCATION
        self._wx_lbl = QLabel(f"气象: {DEFAULT_LOCATION}  (TMY默认)")
        self._wx_lbl.setStyleSheet(
            "color:#16a34a;padding:0 8px;font-weight:600;")
        self.statusBar().setStyleSheet(
            "background:#f5f6f8;border-top:1px solid #d0d5e0;")
        self.statusBar().addWidget(self._status_lbl)
        self.statusBar().addPermanentWidget(self._wx_lbl)

        # ── 信号连接 ───────────────────────────────────────────────────────
        self._connect_sidebar(self.sidebar)

        self._redraw_timer = QTimer()
        self._redraw_timer.setSingleShot(True)
        self._redraw_timer.setInterval(150)
        self._redraw_timer.timeout.connect(self.canvas.refresh)

    def _connect_sidebar(self, sb: Sidebar):
        sb.room_changed.connect(self._on_room_changed)
        sb.window_added.connect(self._on_window_event)
        sb.window_removed.connect(self._on_window_event)
        sb.window_changed.connect(self._on_window_event)
        sb.view_changed.connect(self._on_view_changed)
        sb.weather_dialog_requested.connect(self._open_weather_dialog)

    # ── 工具栏 ────────────────────────────────────────────────────────────
    def _build_toolbar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(46)
        bar.setStyleSheet(
            "background:#f5f6f8;border-bottom:1px solid #d0d5e0;")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(12, 6, 12, 6)
        lay.setSpacing(6)

        # 视图切换
        self._btn_arch = QPushButton("建筑视图")
        self._btn_anal = QPushButton("采光分析")
        for b in (self._btn_arch, self._btn_anal):
            b.setObjectName("view_btn")
            b.setFixedHeight(32)
        self._btn_arch.setProperty("active", "true")
        self._btn_arch.clicked.connect(lambda: self._switch_view(0))
        self._btn_anal.clicked.connect(lambda: self._switch_view(1))
        lay.addWidget(self._btn_arch)
        lay.addWidget(self._btn_anal)

        def _sep():
            s = QWidget(); s.setFixedWidth(1)
            s.setStyleSheet("background:#d0d5e0;")
            return s

        lay.addWidget(_sep())

        # 分析
        self._btn_run = QPushButton("▶  开始分析")
        self._btn_run.setObjectName("primary_btn")
        self._btn_run.setFixedHeight(32)
        self._btn_run.clicked.connect(self._run_analysis)
        lay.addWidget(self._btn_run)

        lay.addWidget(_sep())

        # 项目文件
        self._btn_open   = QPushButton("📂 打开")
        self._btn_save   = QPushButton("💾 保存")
        self._btn_saveas = QPushButton("另存为")
        for b in (self._btn_open, self._btn_save, self._btn_saveas):
            b.setFixedHeight(32)
        self._btn_open.setToolTip(
            "打开 .rlproj 项目文件（也可直接拖拽文件到窗口）")
        self._btn_save.setToolTip(
            "保存当前项目（已有文件直接覆盖，否则弹出路径选择）")
        self._btn_saveas.setToolTip("另存为新的 .rlproj 文件")
        self._btn_open.clicked.connect(self._open_project)
        self._btn_save.clicked.connect(self._save_project)
        self._btn_saveas.clicked.connect(self._save_project_as)
        lay.addWidget(self._btn_open)
        lay.addWidget(self._btn_save)
        lay.addWidget(self._btn_saveas)

        lay.addStretch()

        # 导出
        self._btn_png = QPushButton("↓ 导出图片")
        self._btn_xls = QPushButton("↓ 导出 Excel")
        for b in (self._btn_png, self._btn_xls):
            b.setFixedHeight(32)
            b.setEnabled(False)
        self._btn_png.clicked.connect(self._export_png)
        self._btn_xls.clicked.connect(self._export_excel)
        lay.addWidget(self._btn_png)
        lay.addWidget(self._btn_xls)
        return bar

    def _switch_view(self, idx: int):
        self._stack.setCurrentIndex(idx)
        self._btn_arch.setProperty("active", "true" if idx == 0 else "false")
        self._btn_anal.setProperty("active", "true" if idx == 1 else "false")
        for b in (self._btn_arch, self._btn_anal):
            b.style().unpolish(b); b.style().polish(b)

    # ── 拖拽 ──────────────────────────────────────────────────────────────
    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            if any(u.toLocalFile().endswith(FILE_EXT)
                   for u in event.mimeData().urls()):
                event.acceptProposedAction()
                return
        event.ignore()

    def dropEvent(self, event: QDropEvent):
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.endswith(FILE_EXT):
                self._load_project_file(path)
                event.acceptProposedAction()
                return
        event.ignore()

    # ── 项目 保存 ─────────────────────────────────────────────────────────
    def _save_project(self):
        if self._current_file:
            self._do_save(self._current_file)
        else:
            self._save_project_as()

    def _save_project_as(self):
        default = (
            os.path.splitext(os.path.basename(self._current_file))[0]
            if self._current_file else "untitled"
        ) + FILE_EXT
        path, _ = QFileDialog.getSaveFileName(
            self, "保存项目文件", default, FILE_FILTER)
        if path:
            if not path.endswith(FILE_EXT):
                path += FILE_EXT
            self._do_save(path)

    def _do_save(self, path: str):
        try:
            save_project(
                path, self.room,
                self.weather if self.weather.is_valid() else None)
            self._current_file = path
            self._update_title()
            self._status(f"项目已保存: {path}")
        except Exception as e:
            QMessageBox.critical(self, "保存失败", str(e))

    # ── 项目 打开 ─────────────────────────────────────────────────────────
    def _open_project(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "打开项目文件", "", FILE_FILTER)
        if path:
            self._load_project_file(path)

    def _load_project_file(self, path: str):
        room, weather, err = load_project(path)
        if err:
            QMessageBox.critical(self, "打开失败", err)
            return

        self.room    = room
        if weather:
            self.weather = weather

        # 重建侧边栏（刷新所有控件数值）
        self._rebuild_sidebar()

        # 更新气象标签
        if weather:
            self._wx_lbl.setText(
                f"气象: {weather.location or weather.source}"
                f"  年均 {weather.annual_avg:.0f} lux")
        else:
            from io_utils.weather_data import DEFAULT_LOCATION
            self._wx_lbl.setText(f"气象: {DEFAULT_LOCATION}  (TMY默认)")

        # 刷新画布
        self.canvas.room = self.room
        self.canvas.refresh()

        self._current_file = path
        self._result       = None
        self._btn_png.setEnabled(False)
        self._btn_xls.setEnabled(False)
        self._switch_view(0)
        self._update_title()
        self._status(
            f"已打开: {os.path.basename(path)}"
            f"  |  {len(room.windows)} 扇窗  "
            f"  {room.length/1000:.1f}×{room.width/1000:.1f}×{room.height/1000:.1f} m")

    def _rebuild_sidebar(self):
        """用新的 room 对象重新创建侧边栏，确保所有控件显示正确数值。"""
        # 断开旧信号
        try:
            self.sidebar.room_changed.disconnect()
            self.sidebar.window_added.disconnect()
            self.sidebar.window_removed.disconnect()
            self.sidebar.window_changed.disconnect()
            self.sidebar.view_changed.disconnect()
            self.sidebar.weather_dialog_requested.disconnect()
        except Exception:
            pass

        old = self.sidebar
        old.setParent(None)
        old.deleteLater()

        self.sidebar = Sidebar(self.room)
        self._splitter.insertWidget(0, self.sidebar)
        self._splitter.setSizes([320, 1120])

        # 加载窗户卡片
        for win in self.room.windows:
            self.sidebar._add_win_card(win)

        # 气象
        if self.weather and self.weather.is_valid():
            self.sidebar.on_weather_loaded(self.weather)

        self._connect_sidebar(self.sidebar)

    def _update_title(self):
        fname = os.path.basename(self._current_file) if self._current_file else ""
        suffix = f"  —  {fname}" if fname else ""
        self.setWindowTitle(
            f"建筑室内采光分析工具  v2.3.0{suffix}")

    # ── 信号槽 ────────────────────────────────────────────────────────────
    @pyqtSlot()
    def _on_room_changed(self):
        self._redraw_timer.start()
        self._status(
            f"房间  {self.room.length/1000:.2f}m × "
            f"{self.room.width/1000:.2f}m × "
            f"{self.room.height/1000:.2f}m")

    @pyqtSlot(int)
    def _on_window_event(self, _):
        self._redraw_timer.start()
        self._status(f"窗户已更新，共 {len(self.room.windows)} 扇")

    @pyqtSlot(str)
    def _on_view_changed(self, view: str):
        self._switch_view(0)
        self.canvas.set_view(view)
        self._status(f"视图: {view}")

    @pyqtSlot()
    def _open_weather_dialog(self):
        from ui.weather_dialog import WeatherDialog
        dlg = WeatherDialog(self.weather, parent=self)
        dlg.accepted_data.connect(self._on_weather_set)
        dlg.exec()

    def _on_weather_set(self, ds: WeatherDataset):
        self.weather = ds
        self._wx_lbl.setText(
            f"气象: {ds.location or ds.source}  年均 {ds.annual_avg:.0f} lux")
        self._wx_lbl.setStyleSheet(
            "color:#16a34a;padding:0 8px;font-weight:600;")
        self._status(f"气象数据已设置 — 年均照度 {ds.annual_avg:.0f} lux")
        self.sidebar.on_weather_loaded(ds)

    # ── 计算 ──────────────────────────────────────────────────────────────
    def _run_analysis(self):
        if not self.room.windows:
            QMessageBox.information(
                self, "提示", "请先添加至少一扇窗户再进行采光分析。")
            return
        E_out = (self.weather.annual_avg
                 if self.weather.is_valid() else 15000.0)
        self._btn_run.setEnabled(False)
        self._btn_run.setText("计算中…")
        self._status(f"正在计算采光系数网格（室外照度 {E_out:.0f} lux）…")
        self._calc_thread = CalcThread(self.room, E_out)
        self._calc_thread.finished.connect(self._on_calc_done)
        self._calc_thread.error.connect(self._on_calc_error)
        self._calc_thread.start()

    @pyqtSlot(object)
    def _on_calc_done(self, result: DaylightResult):
        self._result = result
        self._btn_run.setEnabled(True)
        self._btn_run.setText("▶  开始分析")
        self._btn_png.setEnabled(True)
        self._btn_xls.setEnabled(True)
        wx_info = (f"气象: {self.weather.location or self.weather.source}  "
                   f"Eout={result.E_out:.0f} lux")
        self.analysis.update(result, self.room, weather_info=wx_info)
        self._switch_view(1)
        self._status(
            f"分析完成  Eavg={result.E_avg:.0f} lux  "
            f"U0={result.U0:.4f}  DF_avg={result.DF_avg:.4f}%  "
            f"{'✓ 合格' if result.compliant_300 else '✗ 照度不足'}")

    @pyqtSlot(str)
    def _on_calc_error(self, msg: str):
        self._btn_run.setEnabled(True)
        self._btn_run.setText("▶  开始分析")
        QMessageBox.critical(self, "计算错误", msg[:600])

    # ── 导出 ──────────────────────────────────────────────────────────────
    def _export_png(self):
        if self._result is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "保存图片（将生成两张）", "daylight.png",
            "PNG 图像 (*.png);;所有文件 (*)")
        if not path:
            return
        base, ext = os.path.splitext(path)
        ext = ext or ".png"
        path_hm = base + "_热力图" + ext
        path_pf = base + "_截面分布" + ext
        try:
            self.analysis.save_heatmap(path_hm, dpi=200)
            self.analysis.save_profile(path_pf, dpi=200)
            self._status(
                f"已导出: {os.path.basename(path_hm)}  &  "
                f"{os.path.basename(path_pf)}")
            QMessageBox.information(
                self, "导出完成",
                "已成功导出两张图片（白底，适合论文插入）：\n\n"
                f"① {path_hm}\n\n"
                f"② {path_pf}")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))

    def _export_excel(self):
        if self._result is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "保存 Excel 报告", "daylight_report.xlsx",
            "Excel 文件 (*.xlsx);;所有文件 (*)")
        if not path:
            return
        try:
            from io_utils.exporter import export_excel
            export_excel(
                path, self._result, self.room,
                self.weather if self.weather.is_valid() else None)
            self._status(f"Excel 报告已保存: {path}")
            reply = QMessageBox.question(
                self, "导出完成",
                f"报告已保存至:\n{path}\n\n是否立即打开？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply == QMessageBox.StandardButton.Yes:
                import subprocess, sys
                if sys.platform == "win32":
                    os.startfile(path)
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", path])
                else:
                    subprocess.Popen(["xdg-open", path])
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))

    def _status(self, msg: str):
        self._status_lbl.setText(msg)
