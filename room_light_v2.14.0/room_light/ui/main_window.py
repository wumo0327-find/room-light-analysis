"""
ui/main_window.py — 主窗口  v2.14.0
多视图: 建筑视图 / 采光分析 / 热环境 / 参数化实验
全局分析: 进度条 + 暂停 + 取消
勾选式导出: PNG(各图+综合拼图) + Excel
v2.13.0: 左侧侧边栏改为 QStackedWidget（房间参数 / 参数化实验参数），点"参数化
         实验"视图时自动切换到实验侧边栏。
v2.14.0: 参数化实验改为跨材料/几何的全局三目标帕累托，新增透视3D三面投影、
         帕累托显示开关、均衡推荐和四图批量导出。
"""
from __future__ import annotations
import os
from copy import deepcopy
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
from ui.experiment_sidebar import ExperimentSidebar
from ui.sidebar           import Sidebar
from ui.progress_dialog   import ProgressDialog, AnalysisWorker
from io_utils.project_io import FILE_EXT, FILE_FILTER, save_project, load_project


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("建筑室内采光分析工具  v2.14.0  |  益阳默认气象")
        self.resize(1200, 760)   # v2.12.0: 默认非全屏窗口调小（原1480×900）
        self.setMinimumSize(1080, 700)
        self.setAcceptDrops(True)

        self.room    = RoomModel()
        from io_utils.weather_data import default_dataset
        self.weather = default_dataset()
        self._daylight_result: DaylightResult | None = None
        self._thermal_result:  ThermalResult  | None = None
        self._current_file: str = ""
        self._worker: AnalysisWorker | None = None
        self._experiment_df = None
        self._optimal_room: RoomModel | None = None
        self._optimal_daylight_result: DaylightResult | None = None
        self._optimal_thermal_result: ThermalResult | None = None
        self._optimal_label: str = ""

        # ── 布局 ───────────────────────────────────────────────────────────
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0,0,0,0)

        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.setHandleWidth(2)
        self._splitter.setStyleSheet("QSplitter::handle{background:#d0d5e0;}")

        # v2.13.0: 左侧改为可切换的两套侧边栏——房间参数(默认) / 参数化实验参数。
        # 点"参数化实验"视图时切到实验侧边栏，其它视图切回房间侧边栏。
        self.sidebar = Sidebar(self.room)
        self.experiment_sidebar = ExperimentSidebar()
        self._sidebar_stack = QStackedWidget()
        self._sidebar_stack.addWidget(self.sidebar)             # 0 房间参数
        self._sidebar_stack.addWidget(self.experiment_sidebar)  # 1 实验参数
        self._splitter.addWidget(self._sidebar_stack)
        self.experiment_sidebar.run_requested.connect(self._run_experiment)
        self.experiment_sidebar.export_png_requested.connect(
            lambda: self.experiment_panel.export_png())
        self.experiment_sidebar.export_csv_requested.connect(
            lambda: self.experiment_panel.export_csv())

        right = QWidget(); right.setStyleSheet("background:#ffffff;")
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0,0,0,0); right_lay.setSpacing(0)
        right_lay.addWidget(self._build_toolbar())

        self._stack = QStackedWidget()
        self.canvas           = RoomCanvas(self.room)
        self.analysis_panel   = AnalysisPanel()
        self.thermal_panel    = ThermalPanel()
        self.experiment_panel = ExperimentPanel()
        self.experiment_panel.view_optimal_requested.connect(
            self._show_optimal_result)
        self._stack.addWidget(self.canvas)            # 0
        self._stack.addWidget(self.analysis_panel)    # 1
        self._stack.addWidget(self.thermal_panel)     # 2
        self._stack.addWidget(self.experiment_panel)  # 3
        right_lay.addWidget(self._stack, 1)
        self._refresh_experiment_context()

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

        # 验证结果查看器（v2.8.0 新增）
        self._btn_validation = QPushButton("🔍 验证结果")
        self._btn_validation.setFixedHeight(32)
        self._btn_validation.setToolTip("查看实测数据交叉验证的图片与数据表（教室平面图/测点、误差对比图等）")
        self._btn_validation.clicked.connect(self._open_validation_viewer)
        lay.addWidget(self._btn_validation)

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
        # v2.13.0: 参数化实验视图(3)时左侧显示实验侧边栏，其它视图显示房间侧边栏
        self._sidebar_stack.setCurrentIndex(1 if idx == 3 else 0)
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
        self._experiment_df = None
        self._optimal_room = None
        self._optimal_daylight_result = None
        self._optimal_thermal_result = None
        self._optimal_label = ""
        self.experiment_panel.clear_result(
            "已打开新工程，请运行参数化实验生成本工程的遮阳方案。")
        self.experiment_sidebar.set_export_enabled(False)
        self._btn_export.setEnabled(False)
        self._switch_view(0)
        self._update_title()
        self._status(f"已打开: {os.path.basename(path)}  |  {len(room.windows)} 扇窗")
        self._refresh_experiment_context()

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
        self._sidebar_stack.removeWidget(old)
        old.setParent(None); old.deleteLater()
        self.sidebar = Sidebar(self.room)
        self._sidebar_stack.insertWidget(0, self.sidebar)

        for win in self.room.windows:
            self.sidebar._add_win_card(win)
        if self.weather and self.weather.is_valid():
            self.sidebar.on_weather_loaded(self.weather)

        self._connect_sidebar(self.sidebar)

    def _update_title(self):
        fn = os.path.basename(self._current_file) if self._current_file else ""
        suf = f"  —  {fn}" if fn else ""
        self.setWindowTitle(f"建筑室内采光分析工具  v2.14.0{suf}")

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
        self._invalidate_experiment_results()
        self._refresh_experiment_context()
        self._status(f"房间 {self.room.length/1000:.2f}×{self.room.width/1000:.2f}×{self.room.height/1000:.2f} m")

    @pyqtSlot(int)
    def _on_window_event(self, _):
        self._redraw_timer.start()
        self._invalidate_experiment_results()
        self._refresh_experiment_context()
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
        self._invalidate_experiment_results()
        self._refresh_experiment_context()
        self._status(f"气象数据已设置 — 年均 {ds.annual_avg:.0f} lux")

    def _refresh_experiment_context(self):
        if not hasattr(self, "experiment_sidebar"):
            return
        self.experiment_sidebar.set_project_context(
            self.room,
            self.weather if self.weather and self.weather.is_valid() else None,
            os.path.basename(self._current_file) if self._current_file else "未命名工程",
        )

    def _invalidate_experiment_results(self):
        """当前工程或气候改变后，旧参数实验不再允许查看或导出。"""
        if (self._experiment_df is None
                and self._optimal_daylight_result is None
                and self._optimal_thermal_result is None):
            return
        self._experiment_df = None
        self._optimal_room = None
        self._optimal_daylight_result = None
        self._optimal_thermal_result = None
        self._optimal_label = ""
        self.experiment_sidebar.set_export_enabled(False)
        self.experiment_panel.clear_result()
        self._btn_export.setEnabled(
            self._daylight_result is not None or self._thermal_result is not None)

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
        if not self.room.windows:
            QMessageBox.information(self, "提示", "当前工程没有窗户，无法运行参数化实验。")
            return
        weather = self.weather if self.weather.is_valid() else None
        from io_utils.weather_data import default_dataset
        weather = weather or default_dataset()
        self._exp_params = params   # 供 _on_experiment_done 传给面板
        tilt_degs  = params["tilt_degs"]
        depth_mms  = params["depth_mms"]
        gap_mms    = params["gap_mms"]
        materials  = params["materials"]
        room_snap = deepcopy(self.room)
        positive_depths = [v for v in depth_mms if float(v) > 0.0]
        include_zero = any(float(v) <= 0.0 for v in depth_mms)
        from core.experiments import room_has_any_shading
        reference_steps = 1 + int(
            include_zero and room_has_any_shading(room_snap))
        batch_total = (
            len(tilt_degs) * len(positive_depths) * len(gap_mms) * len(materials)
            + reference_steps
        )
        total = batch_total + 2  # 最优方案完整采光/热环境各1步

        def _run_exp(progress_cb):
            from core.experiments import (
                run_all_experiments, balanced_compromise, build_solution_room,
            )
            from core.daylight import compute as compute_daylight, WIN_DIV
            from core.thermal import compute_thermal
            done = [0]
            def _pcb(_tag, _i, _n):
                done[0] += 1
                progress_cb(done[0], total)
            df = run_all_experiments(
                weather=weather, ndiv=self.EXP_NDIV, tilt_degs=tilt_degs,
                depth_mms=depth_mms, gap_mms=gap_mms, materials=materials,
                include_glass=False, include_baseline=True, progress_cb=_pcb,
                base_room=room_snap,
                material_unit_costs=params.get("material_unit_costs"),
                support_cost_per_m=params.get("support_cost_per_m", 180.0),
                install_cost_per_window=params.get("install_cost_per_window", 300.0))
            recommended = balanced_compromise(
                df,
                thermal_col=params.get("y", "thermal_discomfort"),
                maximize_thermal=params.get("maximize_y", False),
                u0_min=params.get("u0_min", 0.0))
            package = {"df": df, "optimal_row": None, "optimal_room": None,
                       "optimal_daylight": None, "optimal_thermal": None}
            if recommended.empty:
                return package
            row = recommended.iloc[0]
            optimal_room = build_solution_room(room_snap, row)
            progress_cb(batch_total + 1, total)
            dl = compute_daylight(
                optimal_room, E_out=weather.annual_avg,
                ndiv=WIN_DIV, store_components=True)
            progress_cb(batch_total + 2, total)
            th = compute_thermal(
                optimal_room, weather.monthly_ghi, weather.monthly_temp)
            package.update({
                "optimal_row": row.to_dict(),
                "optimal_room": optimal_room,
                "optimal_daylight": dl,
                "optimal_thermal": th,
            })
            return package

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
            package = result if isinstance(result, dict) and "df" in result else {"df": result}
            df = package["df"]
            self._experiment_df = df
            self._optimal_room = package.get("optimal_room")
            self._optimal_daylight_result = package.get("optimal_daylight")
            self._optimal_thermal_result = package.get("optimal_thermal")
            optimal_row = package.get("optimal_row") or {}
            self._optimal_label = str(optimal_row.get("param_label", ""))
            self.experiment_panel.show_result(
                df, getattr(self, "_exp_params", {}),
                has_optimal=self._optimal_room is not None)
            self.experiment_sidebar.set_export_enabled(True)
            self._btn_export.setEnabled(True)
            self._switch_view(3)
            if self._optimal_room is not None:
                detail = "已生成最优方案完整分析"
            else:
                detail = "只有改造前基准，没有可推荐的L>0遮阳方案"
            self._status(f"✓ 参数化实验完成（{len(df)} 个算例，{detail}）")

    @pyqtSlot(str)
    def _show_optimal_result(self, view: str):
        if not (self._optimal_room and self._optimal_daylight_result
                and self._optimal_thermal_result):
            QMessageBox.information(self, "提示", "尚无可查看的最优方案完整分析。")
            return
        if view == "daylight":
            self.analysis_panel.update(
                self._optimal_daylight_result, self._optimal_room,
                weather_info=f"最优遮阳：{self._optimal_label}｜{self.weather.location}")
            self._switch_view(1)
        else:
            self.thermal_panel.update(
                self._optimal_thermal_result, self._optimal_room)
            self._switch_view(2)
        self._status(f"正在查看最优方案：{self._optimal_label}（原工程未被修改）")

    # ── 验证结果查看器 ────────────────────────────────────────────────────
    def _open_validation_viewer(self):
        from ui.validation_viewer_dialog import ValidationViewerDialog
        # 默认定位到程序自带的 examples/ 目录（若存在），否则留空让用户自己选
        default_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "..", "examples")
        default_dir = os.path.normpath(default_dir)
        if not os.path.isdir(default_dir):
            default_dir = None
        dlg = ValidationViewerDialog(initial_dir=default_dir, parent=self)
        dlg.exec()

    # ── 导出 ──────────────────────────────────────────────────────────────
    def _open_export_dialog(self):
        from ui.export_dialog import ExportDialog
        dlg = ExportDialog(
            has_daylight=self._daylight_result is not None,
            has_thermal =self._thermal_result  is not None,
            has_experiment=self._experiment_df is not None,
            has_optimal=(
                self._optimal_daylight_result is not None
                and self._optimal_thermal_result is not None),
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
            normal_ap = None
            normal_tp = None
            if self._daylight_result is not None:
                normal_ap = AnalysisPanel()
                normal_ap.update(
                    self._daylight_result, self.room,
                    weather_info=f"气象: {self.weather.location}")
            if self._thermal_result is not None:
                normal_tp = ThermalPanel()
                normal_tp.update(self._thermal_result, self.room)

            if dlg.chk_heatmap.isChecked() and self._daylight_result:
                p = os.path.join(d, f"{prefix}_热力图.png")
                normal_ap.save_heatmap(p, dpi=200)
                exported.append(p)

            if dlg.chk_profile.isChecked() and self._daylight_result:
                p = os.path.join(d, f"{prefix}_截面分布.png")
                normal_ap.save_profile(p, dpi=200)
                exported.append(p)

            if dlg.chk_thermal.isChecked() and self._thermal_result:
                p = os.path.join(d, f"{prefix}_热环境.png")
                normal_tp.save_figure(p, dpi=200)
                exported.append(p)

            if dlg.chk_combined.isChecked() and self._daylight_result and self._thermal_result:
                p = os.path.join(d, f"{prefix}_光热综合.png")
                self._save_combined(
                    p, analysis_panel=normal_ap, thermal_panel=normal_tp,
                    title="原始工程光热环境综合分析")
                exported.append(p)

            if dlg.chk_exp_plots.isChecked() and self._experiment_df is not None:
                exported.extend(self.experiment_panel.save_pngs(
                    d, prefix=f"{prefix}_参数化实验"))

            if dlg.chk_exp_csv.isChecked() and self._experiment_df is not None:
                p = os.path.join(d, f"{prefix}_参数化实验结果.csv")
                saved = self.experiment_panel.save_csv(p)
                if saved:
                    exported.append(saved)

            if (dlg.chk_optimal_plots.isChecked()
                    and self._optimal_daylight_result is not None
                    and self._optimal_thermal_result is not None
                    and self._optimal_room is not None):
                optimal_ap = AnalysisPanel()
                optimal_tp = ThermalPanel()
                optimal_ap.update(
                    self._optimal_daylight_result, self._optimal_room,
                    weather_info=f"最优遮阳：{self._optimal_label}")
                optimal_tp.update(
                    self._optimal_thermal_result, self._optimal_room)
                optimal_files = [
                    (f"{prefix}_最优方案_采光热力图.png",
                     lambda path: optimal_ap.save_heatmap(path, dpi=200)),
                    (f"{prefix}_最优方案_照度截面.png",
                     lambda path: optimal_ap.save_profile(path, dpi=200)),
                    (f"{prefix}_最优方案_热环境.png",
                     lambda path: optimal_tp.save_figure(path, dpi=200)),
                ]
                for filename, writer in optimal_files:
                    p = os.path.join(d, filename)
                    writer(p)
                    exported.append(p)
                p = os.path.join(d, f"{prefix}_最优方案_光热综合.png")
                self._save_combined(
                    p, analysis_panel=optimal_ap, thermal_panel=optimal_tp,
                    title=f"最优遮阳方案光热综合分析｜{self._optimal_label}")
                exported.append(p)

            if dlg.chk_xls.isChecked():
                p = os.path.join(d, f"{prefix}_报告.xlsx")
                from io_utils.exporter import export_excel_v2
                experiment_df = self.experiment_panel.flagged_dataframe()
                export_excel_v2(p, self._daylight_result, self._thermal_result,
                                self.room,
                                self.weather if self.weather.is_valid() else None,
                                experiment_df=experiment_df,
                                experiment_params=getattr(self, "_exp_params", None),
                                optimal_daylight_result=self._optimal_daylight_result,
                                optimal_thermal_result=self._optimal_thermal_result,
                                optimal_room=self._optimal_room,
                                optimal_label=self._optimal_label)
                exported.append(p)

            self._status(f"已导出 {len(exported)} 个文件至 {d}")
            QMessageBox.information(self, "导出完成",
                f"成功导出 {len(exported)} 个文件:\n\n" +
                "\n".join(os.path.basename(p) for p in exported))
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))

    def _save_combined(self, path: str, analysis_panel=None,
                       thermal_panel=None,
                       title: str = "建筑室内光热环境综合分析"):
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

        analysis_panel = analysis_panel or self.analysis_panel
        thermal_panel = thermal_panel or self.thermal_panel
        analysis_panel._render_heatmap_ax(ax_hm, ax_cb, fig)
        analysis_panel._render_profile_ax(ax_pf)
        thermal_panel._render_line(ax_th)
        thermal_panel._render_heatflow(ax_hf)

        fig.suptitle(title, fontsize=14,
                      fontweight="bold", color="#1a1e2e", y=0.97)
        fig.savefig(path, dpi=200, facecolor="#ffffff",
                     bbox_inches="tight", edgecolor="none")

    def _status(self, msg: str):
        self._status_lbl.setText(msg)
