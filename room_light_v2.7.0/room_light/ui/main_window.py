"""
ui/main_window.py — 主窗口  v2.7.0
多视图: 建筑视图 / 采光分析 / 热环境
全局分析: 进度条 + 暂停 + 取消
勾选式导出: PNG(各图+综合拼图) + Excel
"""
from __future__ import annotations
import os
import numpy as np

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QSplitter,
    QLabel, QStackedWidget, QPushButton, QFileDialog, QMessageBox,
)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QDragEnterEvent, QDropEvent

from core.models import RoomModel
from core.daylight import DaylightResult
from core.thermal  import ThermalResult
from io_utils.weather_data import WeatherDataset
from ui.canvas            import RoomCanvas
from ui.analysis_panel    import AnalysisPanel
from ui.thermal_panel     import ThermalPanel
from ui.experiment_panel  import ExperimentPanel
from ui.sidebar           import Sidebar
from ui.progress_dialog   import ProgressDialog, AnalysisWorker
from io_utils.project_io import FILE_EXT, FILE_FILTER, save_project, load_project


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("建筑室内采光分析工具  v2.7.0  |  益阳默认气象")
        self.resize(1480, 900)
        self.setMinimumSize(1080, 700)
        self.setAcceptDrops(True)

        self.room    = RoomModel()
        from io_utils.weather_data import default_dataset
        self.weather = default_dataset()
        self._daylight_result: DaylightResult | None = None
        self._thermal_result:  ThermalResult  | None = None
        self._current_file: str = ""
        self._worker: AnalysisWorker | None = None

        # ── 布局 ───────────────────────────────────────────────────────────
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0,0,0,0)

        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.setHandleWidth(2)
        self._splitter.setStyleSheet("QSplitter::handle{background:#d0d5e0;}")

        self.sidebar = Sidebar(self.room)
        self._splitter.addWidget(self.sidebar)

        right = QWidget(); right.setStyleSheet("background:#ffffff;")
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0,0,0,0); right_lay.setSpacing(0)
        right_lay.addWidget(self._build_toolbar())

        self._stack = QStackedWidget()
        self.canvas           = RoomCanvas(self.room)
        self.analysis_panel   = AnalysisPanel()
        self.thermal_panel    = ThermalPanel()
        self.experiment_panel = ExperimentPanel()
        self._stack.addWidget(self.canvas)            # 0
        self._stack.addWidget(self.analysis_panel)    # 1
        self._stack.addWidget(self.thermal_panel)     # 2
        self._stack.addWidget(self.experiment_panel)  # 3
        right_lay.addWidget(self._stack, 1)
        self.experiment_panel.run_requested.connect(self._run_experiment)

        self._splitter.addWidget(right)
        self._splitter.setStretchFactor(0,0)
        self._splitter.setStretchFactor(1,1)
        self._splitter.setSizes([320, 1160])
        root.addWidget(self._splitter)

        # ── 状态栏 ─────────────────────────────────────────────────────────
        self._status_lbl = QLabel("就绪 — 可将 .rlproj 文件拖入窗口打开")
        self._status_lbl.setStyleSheet("color:#5a6175;padding:0 8px;")
        from io_utils.weather_data import DEFAULT_LOCATION
        self._wx_lbl = QLabel(f"气象: {DEFAULT_LOCATION} (TMY默认)")
        self._wx_lbl.setStyleSheet("color:#16a34a;padding:0 8px;font-weight:600;")
        self.statusBar().setStyleSheet("background:#f5f6f8;border-top:1px solid #d0d5e0;")
        self.statusBar().addWidget(self._status_lbl)
        self.statusBar().addPermanentWidget(self._wx_lbl)

        # ── 信号 ───────────────────────────────────────────────────────────
        self._connect_sidebar(self.sidebar)
        self._redraw_timer = QTimer()
        self._redraw_timer.setSingleShot(True)
        self._redraw_timer.setInterval(150)
        self._redraw_timer.timeout.connect(self.canvas.refresh)

    # ── 工具栏 ────────────────────────────────────────────────────────────
    def _build_toolbar(self) -> QWidget:
        bar = QWidget(); bar.setFixedHeight(46)
        bar.setStyleSheet("background:#f5f6f8;border-bottom:1px solid #d0d5e0;")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(12,6,12,6); lay.setSpacing(6)

        # 视图按钮
        self._btn_arch = QPushButton("建筑视图")
        self._btn_anal = QPushButton("采光分析")
        self._btn_thml = QPushButton("热环境")
        self._btn_exp  = QPushButton("参数化实验")
        for b in (self._btn_arch, self._btn_anal, self._btn_thml, self._btn_exp):
            b.setObjectName("view_btn"); b.setFixedHeight(32)
        self._btn_arch.setProperty("active","true")
        self._btn_arch.clicked.connect(lambda: self._switch_view(0))
        self._btn_anal.clicked.connect(lambda: self._switch_view(1))
        self._btn_thml.clicked.connect(lambda: self._switch_view(2))
        self._btn_exp.clicked.connect(lambda: self._switch_view(3))
        lay.addWidget(self._btn_arch)
        lay.addWidget(self._btn_anal)
        lay.addWidget(self._btn_thml)
        lay.addWidget(self._btn_exp)

        lay.addWidget(self._make_sep())

        # 全部分析
        self._btn_run = QPushButton("▶  全部分析")
        self._btn_run.setObjectName("primary_btn")
        self._btn_run.setFixedHeight(32)
        self._btn_run.clicked.connect(self._run_all)
        lay.addWidget(self._btn_run)

        lay.addWidget(self._make_sep())

        # 项目文件
        self._btn_open   = QPushButton("📂 打开")
        self._btn_save   = QPushButton("💾 保存")
        self._btn_saveas = QPushButton("另存为")
        for b in (self._btn_open, self._btn_save, self._btn_saveas):
            b.setFixedHeight(32)
        self._btn_open.setToolTip("打开 .rlproj 项目（也可拖拽）")
        self._btn_save.setToolTip("保存（已有路径直接覆盖）")
        self._btn_open.clicked.connect(self._open_project)
        self._btn_save.clicked.connect(self._save_project)
        self._btn_saveas.clicked.connect(self._save_project_as)
        lay.addWidget(self._btn_open)
        lay.addWidget(self._btn_save)
        lay.addWidget(self._btn_saveas)

        lay.addStretch()

        # 导出
        self._btn_export = QPushButton("↓ 导出结果")
        self._btn_export.setFixedHeight(32)
        self._btn_export.setEnabled(False)
        self._btn_export.clicked.connect(self._open_export_dialog)
        lay.addWidget(self._btn_export)
        return bar

    def _make_sep(self) -> QWidget:
        s = QWidget(); s.setFixedWidth(1)
        s.setStyleSheet("background:#d0d5e0;"); return s

    def _view_buttons(self): return (self._btn_arch, self._btn_anal, self._btn_thml, self._btn_exp)

    def _switch_view(self, idx: int):
        self._stack.setCurrentIndex(idx)
        labels = ["建筑视图", "采光分析", "热环境", "参数化实验"]
        for i, b in enumerate(self._view_buttons()):
            b.setProperty("active","true" if i==idx else "false")
            b.style().unpolish(b); b.style().polish(b)

    # ── 拖拽 ──────────────────────────────────────────────────────────────
    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            if any(u.toLocalFile().endswith(FILE_EXT) for u in e.mimeData().urls()):
                e.acceptProposedAction(); return
        e.ignore()

    def dropEvent(self, e: QDropEvent):
        for url in e.mimeData().urls():
            p = url.toLocalFile()
            if p.endswith(FILE_EXT):
                self._load_project_file(p)
                e.acceptProposedAction(); return
        e.ignore()

    # ── 项目 IO ───────────────────────────────────────────────────────────
    def _save_project(self):
        self._do_save(self._current_file) if self._current_file else self._save_project_as()

    def _save_project_as(self):
        default = (os.path.splitext(os.path.basename(self._current_file))[0]
                   if self._current_file else "untitled") + FILE_EXT
        path, _ = QFileDialog.getSaveFileName(self, "保存项目", default, FILE_FILTER)
        if path:
            if not path.endswith(FILE_EXT): path += FILE_EXT
            self._do_save(path)

    def _do_save(self, path: str):
        try:
            save_project(path, self.room,
                         self.weather if self.weather.is_valid() else None)
            self._current_file = path
            self._update_title()
            self._status(f"项目已保存: {path}")
        except Exception as e:
            QMessageBox.critical(self, "保存失败", str(e))

    def _open_project(self):
        path, _ = QFileDialog.getOpenFileName(self, "打开项目", "", FILE_FILTER)
        if path: self._load_project_file(path)

    def _load_project_file(self, path: str):
        room, weather, err = load_project(path)
        if err:
            QMessageBox.critical(self, "打开失败", err); return
        self.room = room
        if weather: self.weather = weather
        self._rebuild_sidebar()
        if weather:
            self._wx_lbl.setText(
                f"气象: {weather.location or weather.source}  年均 {weather.annual_avg:.0f} lux")
        self.canvas.room = self.room
        self.canvas.refresh()
        self._current_file = path
        self._daylight_result = None
        self._thermal_result  = None
        self._btn_export.setEnabled(False)
        self._switch_view(0)
        self._update_title()
        self._status(f"已打开: {os.path.basename(path)}  |  {len(room.windows)} 扇窗")

    def _rebuild_sidebar(self):
        try:
            self.sidebar.room_changed.disconnect()
            self.sidebar.window_added.disconnect()
            self.sidebar.window_removed.disconnect()
            self.sidebar.window_changed.disconnect()
            self.sidebar.view_changed.disconnect()
            self.sidebar.weather_dialog_requested.disconnect()
        except Exception: pass

        old = self.sidebar
        old.setParent(None); old.deleteLater()
        self.sidebar = Sidebar(self.room)
        self._splitter.insertWidget(0, self.sidebar)
        self._splitter.setSizes([320, 1160])

        for win in self.room.windows:
            self.sidebar._add_win_card(win)
        if self.weather and self.weather.is_valid():
            self.sidebar.on_weather_loaded(self.weather)

        self._connect_sidebar(self.sidebar)

    def _update_title(self):
        fn = os.path.basename(self._current_file) if self._current_file else ""
        suf = f"  —  {fn}" if fn else ""
        self.setWindowTitle(f"建筑室内采光分析工具  v2.7.0{suf}")

    # ── 信号连接 ──────────────────────────────────────────────────────────
    def _connect_sidebar(self, sb: Sidebar):
        sb.room_changed.connect(self._on_room_changed)
        sb.window_added.connect(self._on_window_event)
        sb.window_removed.connect(self._on_window_event)
        sb.window_changed.connect(self._on_window_event)
        sb.view_changed.connect(self._on_view_changed)
        sb.weather_dialog_requested.connect(self._open_weather_dialog)

    @pyqtSlot()
    def _on_room_changed(self):
        self._redraw_timer.start()
        self._status(f"房间 {self.room.length/1000:.2f}×{self.room.width/1000:.2f}×{self.room.height/1000:.2f} m")

    @pyqtSlot(int)
    def _on_window_event(self, _):
        self._redraw_timer.start()
        self._status(f"窗户已更新，共 {len(self.room.windows)} 扇")

    @pyqtSlot(str)
    def _on_view_changed(self, view: str):
        self._switch_view(0)
        self.canvas.set_view(view)

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
        self._wx_lbl.setStyleSheet("color:#16a34a;padding:0 8px;font-weight:600;")
        self.sidebar.on_weather_loaded(ds)
        self._status(f"气象数据已设置 — 年均 {ds.annual_avg:.0f} lux")

    # ── 全部分析 ──────────────────────────────────────────────────────────
    def _run_all(self):
        if not self.room.windows:
            QMessageBox.information(self, "提示", "请先添加至少一扇窗户。"); return

        E_out   = self.weather.annual_avg if self.weather.is_valid() else 15000.0
        ghi_12  = self.weather.monthly_ghi
        temp_12 = self.weather.monthly_temp

        room_snap = self.room   # 计算期间不变

        def _run_daylight(progress_cb):
            from core.daylight import compute, GRID_MM, WIN_DIV
            ny_total = None
            def _row_cb(iy):
                nonlocal ny_total
                if ny_total is None:
                    import math
                    margin = 500.0
                    ny_total = max(2, round(
                        (room_snap.length - 2*margin) / GRID_MM) + 1)
                progress_cb(iy, ny_total or 1)
            return compute(room_snap, E_out,
                            store_components=True, row_cb=_row_cb)

        def _run_thermal(progress_cb):
            from core.thermal import compute_thermal
            def _month_cb(m):
                progress_cb(m, 12)
            return compute_thermal(room_snap, ghi_12, temp_12,
                                    progress_cb=_month_cb)

        step_names = ["采光分析 (DF网格)", "热环境分析 (月均热平衡)"]
        step_fns   = [_run_daylight, _run_thermal]

        dlg = ProgressDialog(step_names, parent=self)
        self._worker = AnalysisWorker(list(zip(step_names, step_fns)))
        dlg.bind_worker(self._worker)

        self._worker.step_done.connect(self._on_step_done)
        self._worker.all_done.connect(dlg.accept)
        dlg.cancelled.connect(self._worker.cancel)

        self._btn_run.setEnabled(False)
        self._worker.finished.connect(lambda: self._btn_run.setEnabled(True))

        self._worker.start()
        dlg.exec()

    @pyqtSlot(int, str, object)
    def _on_step_done(self, idx: int, name: str, result):
        if "采光" in name and result is not None:
            self._daylight_result = result
            self.analysis_panel.update(result, self.room,
                weather_info=f"气象: {self.weather.location}  Eout={self.weather.annual_avg:.0f} lux")
        elif "热环境" in name and result is not None:
            self._thermal_result = result
            self.thermal_panel.update(result, self.room)

        if self._daylight_result or self._thermal_result:
            self._btn_export.setEnabled(True)

        # 自动跳到最新完成的结果视图
        if "热环境" in name:
            self._switch_view(2)
        elif "采光" in name:
            self._switch_view(1)

        self._status(f"✓ {name} 完成")

    # ── 参数化实验（后台线程，复用 AnalysisWorker + ProgressDialog）─────────
    EXP_NDIV = 20   # 批量实验采光离散数（兼顾速度）

    def _run_experiment(self, params: dict):
        weather = self.weather if self.weather.is_valid() else None
        from io_utils.weather_data import default_dataset
        weather = weather or default_dataset()
        specs     = params["glass_specs"]
        beta_degs = params["beta_degs"]
        H_mm      = params["H_mm"]
        total     = len(specs) + len(beta_degs)

        def _run_exp(progress_cb):
            from core.experiments import run_all_experiments
            done = [0]
            def _pcb(_tag, _i, _n):
                done[0] += 1
                progress_cb(done[0], total)
            return run_all_experiments(
                weather=weather, ndiv=self.EXP_NDIV, beta_degs=beta_degs,
                H_mm=H_mm, glass_specs=specs, progress_cb=_pcb)

        dlg = ProgressDialog(["参数化实验批量运行"], parent=self)
        self._exp_worker = AnalysisWorker([("参数化实验批量运行", _run_exp)])
        dlg.bind_worker(self._exp_worker)
        self._exp_worker.step_done.connect(self._on_experiment_done)
        self._exp_worker.all_done.connect(dlg.accept)
        dlg.cancelled.connect(self._exp_worker.cancel)
        self._exp_worker.start()
        dlg.exec()

    @pyqtSlot(int, str, object)
    def _on_experiment_done(self, idx: int, name: str, result):
        if result is not None:
            self.experiment_panel.show_result(result)
            self._switch_view(3)
            self._status(f"✓ 参数化实验完成（{len(result)} 个算例）")

    # ── 导出 ──────────────────────────────────────────────────────────────
    def _open_export_dialog(self):
        from ui.export_dialog import ExportDialog
        dlg = ExportDialog(
            has_daylight=self._daylight_result is not None,
            has_thermal =self._thermal_result  is not None,
            parent=self,
        )
        if dlg.exec() != dlg.DialogCode.Accepted:
            return
        self._do_export(dlg)

    def _do_export(self, dlg):
        d      = dlg.export_dir
        prefix = dlg.export_prefix
        exported = []

        try:
            if dlg.chk_heatmap.isChecked() and self._daylight_result:
                p = os.path.join(d, f"{prefix}_热力图.png")
                self.analysis_panel.save_heatmap(p, dpi=200)
                exported.append(p)

            if dlg.chk_profile.isChecked() and self._daylight_result:
                p = os.path.join(d, f"{prefix}_截面分布.png")
                self.analysis_panel.save_profile(p, dpi=200)
                exported.append(p)

            if dlg.chk_thermal.isChecked() and self._thermal_result:
                p = os.path.join(d, f"{prefix}_热环境.png")
                self.thermal_panel.save_figure(p, dpi=200)
                exported.append(p)

            if dlg.chk_combined.isChecked() and self._daylight_result and self._thermal_result:
                p = os.path.join(d, f"{prefix}_光热综合.png")
                self._save_combined(p)
                exported.append(p)

            if dlg.chk_xls.isChecked():
                p = os.path.join(d, f"{prefix}_报告.xlsx")
                from io_utils.exporter import export_excel_v2
                export_excel_v2(p, self._daylight_result, self._thermal_result,
                                self.room,
                                self.weather if self.weather.is_valid() else None)
                exported.append(p)

            self._status(f"已导出 {len(exported)} 个文件至 {d}")
            QMessageBox.information(self, "导出完成",
                f"成功导出 {len(exported)} 个文件:\n\n" +
                "\n".join(os.path.basename(p) for p in exported))
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))

    def _save_combined(self, path: str):
        """光热综合拼合图"""
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.gridspec import GridSpec

        fig = Figure(figsize=(16, 10), facecolor="#ffffff")
        FigureCanvasAgg(fig)
        gs  = GridSpec(2, 3,
                        width_ratios=[1, 0.04, 1.2],
                        height_ratios=[2.5, 1],
                        figure=fig,
                        left=0.06, right=0.97,
                        top=0.92,  bottom=0.08,
                        hspace=0.40, wspace=0.08)

        ax_hm  = fig.add_subplot(gs[0, 0])
        ax_cb  = fig.add_subplot(gs[0, 1])
        ax_th  = fig.add_subplot(gs[0, 2])
        ax_pf  = fig.add_subplot(gs[1, 0])
        ax_hf  = fig.add_subplot(gs[1, 2])
        fig.add_subplot(gs[1, 1]).set_visible(False)

        self.analysis_panel._render_heatmap_ax(ax_hm, ax_cb, fig)
        self.analysis_panel._render_profile_ax(ax_pf)
        self.thermal_panel._render_line(ax_th)
        self.thermal_panel._render_heatflow(ax_hf)

        fig.suptitle("建筑室内光热环境综合分析", fontsize=14,
                      fontweight="bold", color="#1a1e2e", y=0.97)
        fig.savefig(path, dpi=200, facecolor="#ffffff",
                     bbox_inches="tight", edgecolor="none")

    def _status(self, msg: str):
        self._status_lbl.setText(msg)
