"""
ui/main_window.py — 主窗口  v3.6.0
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
import csv
import re
from datetime import datetime
from copy import deepcopy
import numpy as np

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QSplitter,
    QLabel, QStackedWidget, QPushButton, QFileDialog, QMessageBox,
)
from PyQt6.QtCore import Qt, pyqtSlot
from PyQt6.QtGui import QDragEnterEvent, QDropEvent

from core.models import (
    RoomModel, DEFAULT_SUPPORT_COST_PER_M,
    DEFAULT_INSTALL_COST_PER_WINDOW,
)
from core.complex_models import SpaceModel
from core.space_geometry import space_floor_area_mm2
from core.legacy_adapter import building_from_room
from core.daylight import DaylightResult
from core.thermal  import ThermalResult
from io_utils.weather_data import WeatherDataset
from ui.analysis_panel    import AnalysisPanel
from ui.thermal_panel     import ThermalPanel
from ui.experiment_panel  import ExperimentPanel
from ui.experiment_sidebar import ExperimentSidebar
from ui.progress_dialog   import ProgressDialog, AnalysisWorker
from ui.complex_space_editor import (
    ComplexSpaceCanvas,
    ComplexSpaceSummary,
)
from ui.bp_editor_dialog import EmbeddedBpEditorDialog
from io_utils.project_io import (
    FILE_EXT, FILE_FILTER, load_building_project, save_building_project,
)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("建筑室内采光分析工具  v3.6.0  |  益阳默认气象")
        self.resize(1200, 760)   # v2.12.0: 默认非全屏窗口调小（原1480×900）
        self.setMinimumSize(1080, 700)
        self.setAcceptDrops(True)

        self.building = building_from_room(RoomModel())
        self._active_space_id: str | None = "legacy_space"
        self._selected_space_ids: list[str] = ["legacy_space"]
        from io_utils.weather_data import default_dataset
        self.weather = default_dataset()
        self._daylight_result: DaylightResult | None = None
        self._thermal_result:  ThermalResult  | None = None
        self._daylight_results: dict[str, object] = {}
        self._thermal_results: dict[str, object] = {}
        self._current_file: str = ""
        self._worker: AnalysisWorker | None = None
        self._experiment_df = None
        self._optimal_space: SpaceModel | None = None
        self._optimal_daylight_result: DaylightResult | None = None
        self._optimal_thermal_result: ThermalResult | None = None
        self._optimal_spaces: dict[str, SpaceModel] = {}
        self._optimal_daylight_results: dict[str, object] = {}
        self._optimal_thermal_results: dict[str, object] = {}
        self._optimal_label: str = ""

        # ── 布局 ───────────────────────────────────────────────────────────
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0,0,0,0)

        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.setHandleWidth(2)
        self._splitter.setStyleSheet("QSplitter::handle{background:#d0d5e0;}")

        # v3.2: 建筑视图按楼层显示全部房间，直接在画布上多选计算范围。
        self.experiment_sidebar = ExperimentSidebar()
        self.complex_sidebar = ComplexSpaceSummary()
        self._sidebar_stack = QStackedWidget()
        self._sidebar_stack.addWidget(self.complex_sidebar)     # 0 建筑参数
        self._sidebar_stack.addWidget(self.experiment_sidebar)  # 1 实验参数
        self._splitter.addWidget(self._sidebar_stack)
        self.complex_sidebar.edit_requested.connect(
            self._open_complex_space_editor)
        self.complex_sidebar.view_changed.connect(
            self._on_complex_view_changed)
        self.complex_sidebar.weather_dialog_requested.connect(
            self._open_weather_dialog)
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
        self.complex_canvas   = ComplexSpaceCanvas()
        self.complex_canvas.selection_changed.connect(
            self._on_canvas_selection_changed)
        self.complex_canvas.shading_windows_changed.connect(
            self._on_shading_windows_changed)
        self.complex_sidebar.shading_selection_mode_changed.connect(
            self.complex_canvas.set_shading_selection_mode)
        self.canvas           = self.complex_canvas
        self.analysis_panel   = AnalysisPanel()
        self.thermal_panel    = ThermalPanel()
        self.experiment_panel = ExperimentPanel()
        self.experiment_panel.view_optimal_requested.connect(
            self._show_optimal_result)
        self._stack.addWidget(self.complex_canvas)    # 0
        self._stack.addWidget(self.analysis_panel)    # 1
        self._stack.addWidget(self.thermal_panel)     # 2
        self._stack.addWidget(self.experiment_panel)  # 3
        right_lay.addWidget(self._stack, 1)
        self.complex_canvas.set_model(
            self.building, self._active_space_id, self._selected_space_ids,
            self._effective_shading_window_keys())
        self.complex_sidebar.set_model(
            self.building, self._active_space_id, self._selected_space_ids,
            self._stored_shading_window_count())
        self.complex_sidebar.set_weather(self.weather)
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
        self._btn_run = QPushButton("▶  分析选中房间")
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
        if idx in (1, 2):
            self._sync_active_results()
        self._stack.setCurrentIndex(idx)
        # 参数化实验视图显示实验侧栏，其余视图显示建筑侧栏。
        self._sidebar_stack.setCurrentIndex(1 if idx == 3 else 0)
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
            self.building.metadata["selected_space_ids"] = list(
                self._selected_space_ids
            )
            save_building_project(
                path,
                self.building,
                self.weather if self.weather.is_valid() else None,
                self._active_space_id,
            )
            self._current_file = path
            self._update_title()
            self._status(f"项目已保存: {path}")
        except Exception as e:
            QMessageBox.critical(self, "保存失败", str(e))

    def _open_project(self):
        path, _ = QFileDialog.getOpenFileName(self, "打开项目", "", FILE_FILTER)
        if path: self._load_project_file(path)

    def _load_project_file(self, path: str):
        # 统一入口：v3 工程直接读取，v2 及更早的矩形工程自动转换为
        # BuildingModel 后再显示和计算。
        self._load_complex_project_file(path)

    def _load_complex_project_file(self, path: str):
        building, weather, active_space_id, error = load_building_project(path)
        if error:
            QMessageBox.critical(self, "打开失败", error)
            return
        self.building = building
        self._active_space_id = (
            active_space_id
            or (building.spaces()[0].id if building.spaces() else None)
        )
        stored_selection = building.metadata.get("selected_space_ids", [])
        self._selected_space_ids = [
            str(space_id)
            for space_id in stored_selection
            if building.get_space(str(space_id)) is not None
        ]
        if not self._selected_space_ids and self._active_space_id:
            self._selected_space_ids = [self._active_space_id]
        self._btn_run.setText("▶  分析选中房间")
        if weather:
            self.weather = weather
            self._wx_lbl.setText(
                f"气象: {weather.location or weather.source}  "
                f"年均 {weather.annual_avg:.0f} lux"
            )
        self.complex_canvas.set_model(
            self.building, self._active_space_id, self._selected_space_ids,
            self._effective_shading_window_keys())
        self.complex_sidebar.set_model(
            self.building, self._active_space_id, self._selected_space_ids,
            self._stored_shading_window_count())
        self.complex_sidebar.set_weather(self.weather)
        self._current_file = path
        self._daylight_result = None
        self._thermal_result = None
        self._daylight_results = {}
        self._thermal_results = {}
        self._experiment_df = None
        self._optimal_space = None
        self._optimal_daylight_result = None
        self._optimal_thermal_result = None
        self._optimal_spaces = {}
        self._optimal_daylight_results = {}
        self._optimal_thermal_results = {}
        self._optimal_label = ""
        self._btn_export.setEnabled(False)
        self.experiment_sidebar.set_export_enabled(False)
        self.experiment_panel.clear_result(
            "已打开新工程，请重新运行复杂空间参数化实验。")
        self._switch_view(0)
        self._update_title()
        space = (
            building.get_space(self._active_space_id)
            if self._active_space_id else None
        )
        self._status(
            f"已打开工程: {os.path.basename(path)}"
            + (f"  |  当前空间：{space.name}" if space else "")
        )
        self._refresh_experiment_context()

    def _open_complex_space_editor(self):
        dialog = EmbeddedBpEditorDialog(
            self.building,
            self._active_space_id,
            parent=self,
        )
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        if dialog.result_building is None:
            return
        self.building = dialog.result_building
        self._active_space_id = dialog.result_active_space_id
        stored_selection = self.building.metadata.get(
            "selected_space_ids", []
        )
        self._selected_space_ids = [
            str(space_id)
            for space_id in stored_selection
            if self.building.get_space(str(space_id)) is not None
        ]
        if not self._selected_space_ids and self._active_space_id:
            self._selected_space_ids = [self._active_space_id]
        self._btn_run.setText("▶  分析选中房间")
        self.complex_canvas.set_model(
            self.building, self._active_space_id, self._selected_space_ids,
            self._effective_shading_window_keys())
        self.complex_sidebar.set_model(
            self.building, self._active_space_id, self._selected_space_ids,
            self._stored_shading_window_count())
        self._daylight_result = None
        self._thermal_result = None
        self._daylight_results = {}
        self._thermal_results = {}
        self._experiment_df = None
        self._optimal_space = None
        self._optimal_daylight_result = None
        self._optimal_thermal_result = None
        self._optimal_spaces = {}
        self._optimal_daylight_results = {}
        self._optimal_thermal_results = {}
        self._optimal_label = ""
        self._btn_export.setEnabled(False)
        self.experiment_sidebar.set_export_enabled(False)
        self.experiment_panel.clear_result(
            "建筑模型已改变，请重新运行复杂空间参数化实验。")
        self._switch_view(0)
        self._refresh_experiment_context()
        self._status(
            "BP建筑平面已转换为RL复杂空间模型；"
            "气象数据保持不变，请保存工程并重新运行分析。"
        )

    def _update_title(self):
        fn = os.path.basename(self._current_file) if self._current_file else ""
        suf = f"  —  {fn}" if fn else ""
        self.setWindowTitle(f"建筑室内采光分析工具  v3.6.0{suf}")

    @pyqtSlot(str)
    def _on_complex_view_changed(self, view: str):
        self._switch_view(0)
        self.complex_canvas.set_view(view)

    @pyqtSlot(object, object)
    def _on_canvas_selection_changed(
        self,
        selected_space_ids,
        active_space_id,
    ):
        self._selected_space_ids = [
            str(space_id)
            for space_id in selected_space_ids
            if self.building.get_space(str(space_id)) is not None
        ]
        if active_space_id and self.building.get_space(str(active_space_id)):
            self._active_space_id = str(active_space_id)
        self.building.metadata["selected_space_ids"] = list(
            self._selected_space_ids
        )
        self.complex_sidebar.set_model(
            self.building,
            self._active_space_id,
            self._selected_space_ids,
            self._stored_shading_window_count(),
        )
        self.complex_canvas.set_shading_window_keys(
            self._effective_shading_window_keys()
        )
        self._sync_active_results()
        self._invalidate_experiment_results()
        self._refresh_experiment_context()
        active = self.building.get_space(self._active_space_id)
        self._status(
            f"已选 {len(self._selected_space_ids)} 个房间"
            + (f"｜当前：{active.name}" if active else "")
            + "｜再次点击取消，右键房间可单选"
        )

    def _selected_spaces(self) -> list[SpaceModel]:
        selected = set(self._selected_space_ids)
        return [
            space
            for space in self.building.spaces()
            if space.id in selected
        ]

    def _stored_shading_window_keys(self):
        value = self.building.metadata.get("shading_target_window_keys")
        if value is None:
            return None
        if not isinstance(value, (list, tuple, set)):
            return []
        from core.complex_experiments import (
            exterior_windows,
            window_selection_key,
        )
        valid = {
            window_selection_key(space, wall, opening)
            for space in self.building.spaces()
            for wall, opening in exterior_windows(space)
        }
        return [
            str(key) for key in value
            if str(key) in valid
        ]

    def _effective_shading_window_keys(self) -> list[str]:
        stored = self._stored_shading_window_keys()
        if stored is not None:
            return stored
        from core.complex_experiments import (
            exterior_windows,
            window_selection_key,
        )
        return [
            window_selection_key(space, wall, opening)
            for space in self._selected_spaces()
            for wall, opening in exterior_windows(space)
        ]

    def _stored_shading_window_count(self):
        stored = self._stored_shading_window_keys()
        return None if stored is None else len(stored)

    @pyqtSlot(object)
    def _on_shading_windows_changed(self, keys) -> None:
        self.building.metadata["shading_target_window_keys"] = [
            str(value) for value in keys
        ]
        self.complex_sidebar.set_model(
            self.building,
            self._active_space_id,
            self._selected_space_ids,
            len(keys),
        )
        self._invalidate_experiment_results()
        self._refresh_experiment_context()
        self._status(
            f"已指定 {len(keys)} 扇参数化遮阳窗；"
            "橙色窗将安装遮阳板并计入造价。"
        )

    def _sync_active_results(self) -> None:
        self._daylight_result = self._daylight_results.get(
            self._active_space_id
        )
        self._thermal_result = self._thermal_results.get(
            self._active_space_id
        )
        space = self.building.get_space(self._active_space_id)
        if space is None:
            return
        optimal_space = self._optimal_spaces.get(self._active_space_id)
        optimal_daylight = self._optimal_daylight_results.get(
            self._active_space_id
        )
        optimal_thermal = self._optimal_thermal_results.get(
            self._active_space_id
        )
        if self._daylight_result is not None:
            weather_info = (
                f"气象: {self.weather.location}  "
                f"Eout={self.weather.annual_avg:.0f} lux"
            )
            if optimal_space is not None and optimal_daylight is not None:
                self.analysis_panel.set_comparison(
                    self._daylight_result,
                    space,
                    optimal_daylight,
                    optimal_space,
                    weather_info=(
                        f"{weather_info}｜数值：当前 → 最优｜{self._optimal_label}"
                    ),
                )
            else:
                self.analysis_panel.update(
                    self._daylight_result,
                    space,
                    weather_info=weather_info,
                )
        if self._thermal_result is not None:
            if optimal_space is not None and optimal_thermal is not None:
                self.thermal_panel.set_comparison(
                    self._thermal_result,
                    space,
                    optimal_thermal,
                    optimal_space,
                )
            else:
                self.thermal_panel.update(self._thermal_result, space)
        self._btn_export.setEnabled(
            bool(self._daylight_results or self._thermal_results)
            or self._experiment_df is not None
        )

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
        self.complex_sidebar.set_weather(ds)
        self._daylight_results = {}
        self._thermal_results = {}
        self._daylight_result = None
        self._thermal_result = None
        self._invalidate_experiment_results()
        self._refresh_experiment_context()
        self._status(f"气象数据已设置 — 年均 {ds.annual_avg:.0f} lux")

    def _refresh_experiment_context(self):
        if not hasattr(self, "experiment_sidebar"):
            return
        from core.complex_experiments import selected_exterior_windows
        target_keys = self._effective_shading_window_keys()
        target_count = sum(
            len(selected_exterior_windows(space, target_keys))
            for space in self._selected_spaces()
        )
        self.experiment_sidebar.set_complex_project_context(
            self._selected_spaces(),
            self.weather if self.weather and self.weather.is_valid() else None,
            os.path.basename(self._current_file) if self._current_file else "未命名工程",
            target_window_count=target_count,
        )

    def _invalidate_experiment_results(self):
        """当前工程或气候改变后，旧参数实验不再允许查看或导出。"""
        if (self._experiment_df is None
                and self._optimal_daylight_result is None
                and self._optimal_thermal_result is None
                and not self._optimal_spaces):
            return
        self._experiment_df = None
        self._optimal_space = None
        self._optimal_daylight_result = None
        self._optimal_thermal_result = None
        self._optimal_spaces = {}
        self._optimal_daylight_results = {}
        self._optimal_thermal_results = {}
        self._optimal_label = ""
        self.experiment_sidebar.set_export_enabled(False)
        self.experiment_panel.clear_result()
        self._btn_export.setEnabled(
            self._daylight_result is not None or self._thermal_result is not None)
        self._sync_active_results()

    # ── 全部分析 ──────────────────────────────────────────────────────────
    def _run_all(self):
        self._run_complex_analysis()

    def _run_complex_analysis(self):
        spaces = self._selected_spaces()
        if not spaces:
            QMessageBox.warning(
                self,
                "无法计算",
                "请先在建筑平面图中点击选择至少一个房间。",
            )
            return
        outdoor = (
            self.weather.annual_avg
            if self.weather.is_valid() else 15_000.0
        )
        steps = []
        for source_space in spaces:
            space = deepcopy(source_space)
            space_id = source_space.id

            def _run_daylight(
                progress_cb,
                target=space,
                target_id=space_id,
            ):
                from core.complex_daylight import (
                    GRID_MM,
                    WIN_DIV,
                    build_workplane_grid,
                    compute_complex_daylight,
                )
                _xs, ys, _mask = build_workplane_grid(target, GRID_MM)
                total_rows = len(ys)

                def _row_callback(index):
                    progress_cb(index + 1, total_rows)

                result = compute_complex_daylight(
                    target,
                    E_out=outdoor,
                    grid_mm=GRID_MM,
                    ndiv=WIN_DIV,
                    store_components=True,
                    row_cb=_row_callback,
                )
                return "daylight", target_id, result

            def _run_thermal(
                progress_cb,
                target=space,
                target_id=space_id,
            ):
                from core.complex_thermal import compute_complex_thermal

                def _month_callback(index):
                    progress_cb(index + 1, 12)

                result = compute_complex_thermal(
                    target,
                    self.weather.monthly_ghi,
                    self.weather.monthly_temp,
                    latitude_deg=self.building.location.latitude,
                    north_angle_deg=self.building.north_angle_deg,
                    progress_cb=_month_callback,
                )
                return "thermal", target_id, result

            steps.extend([
                (f"采光｜{source_space.name}", _run_daylight),
                (f"热环境｜{source_space.name}", _run_thermal),
            ])

        step_names = [name for name, _runner in steps]
        dialog = ProgressDialog(step_names, parent=self)
        self._worker = AnalysisWorker(steps)
        dialog.bind_worker(self._worker)
        self._worker.step_done.connect(self._on_step_done)
        self._worker.all_done.connect(self._on_batch_analysis_done)
        self._worker.all_done.connect(dialog.accept)
        dialog.cancelled.connect(self._worker.cancel)
        self._btn_run.setEnabled(False)
        self._worker.finished.connect(
            lambda: self._btn_run.setEnabled(True))
        self._worker.start()
        dialog.exec()

    @pyqtSlot(int, str, object)
    def _on_step_done(self, idx: int, name: str, result):
        if (
            isinstance(result, tuple)
            and len(result) == 3
            and result[1]
        ):
            kind, space_id, payload = result
            if kind == "daylight":
                self._daylight_results[str(space_id)] = payload
            elif kind == "thermal":
                self._thermal_results[str(space_id)] = payload
        self._status(f"✓ {name} 完成")

    @pyqtSlot()
    def _on_batch_analysis_done(self):
        self._sync_active_results()
        calculated = len(
            set(self._daylight_results) | set(self._thermal_results)
        )
        if calculated:
            self._btn_export.setEnabled(True)
            self._switch_view(1)
        self._status(
            f"✓ 已完成 {calculated} 个选中房间的批量采光与热环境分析；"
            "回到建筑视图点击房间可切换结果。"
        )

    # ── 参数化实验（后台线程，复用 AnalysisWorker + ProgressDialog）─────────
    EXP_NDIV = 20   # 批量实验采光离散数（兼顾速度）

    def _run_experiment(self, params: dict):
        spaces = self._selected_spaces()
        if not spaces:
            QMessageBox.information(
                self, "提示", "请先在建筑平面图中点击选择房间。")
            return
        from core.complex_experiments import selected_exterior_windows
        target_window_keys = self._effective_shading_window_keys()
        target_count = sum(
            len(selected_exterior_windows(space, target_window_keys))
            for space in spaces
        )
        if target_count <= 0:
            QMessageBox.information(
                self,
                "提示",
                "所选房间中没有参数化遮阳位置。\n"
                "请回到建筑视图，点击“在图中选择参数化遮阳窗”，"
                "再点击需要安装遮阳板的外窗。",
            )
            return
        weather = self.weather if self.weather.is_valid() else None
        from io_utils.weather_data import default_dataset
        weather = weather or default_dataset()
        self._exp_params = dict(params)
        self._exp_params["model_type"] = "multi_complex_spaces"
        self._exp_params["application_scope"] = (
            f"选中{len(spaces)}个房间内指定的{target_count}扇外窗"
        )
        self._exp_params["target_window_keys"] = list(target_window_keys)
        self._exp_params["selected_space_ids"] = [
            space.id for space in spaces
        ]
        self._exp_params["selected_space_names"] = [
            space.name for space in spaces
        ]
        tilt_degs  = params["tilt_degs"]
        depth_mms  = params["depth_mms"]
        gap_mms    = params["gap_mms"]
        materials  = params["materials"]
        space_snaps = [deepcopy(space) for space in spaces]
        positive_depths = [v for v in depth_mms if float(v) > 0.0]
        include_zero = any(float(v) <= 0.0 for v in depth_mms)
        material_count = max(1, len(materials))
        per_room = (
            1
            + int(include_zero)
            + len(tilt_degs) * len(positive_depths)
            * len(gap_mms) * material_count
        )
        batch_total = per_room * len(space_snaps)
        total = batch_total + 2 * len(space_snaps)

        def _run_exp(progress_cb):
            from core.experiments import balanced_compromise
            from core.complex_experiments import (
                build_solution_space,
                run_multi_space_experiments,
            )
            from core.complex_daylight import (
                WIN_DIV,
                compute_complex_daylight,
            )
            from core.complex_thermal import compute_complex_thermal
            def _pcb(_tag, current, _batch_total):
                progress_cb(current, total)

            df = run_multi_space_experiments(
                spaces=space_snaps,
                weather=weather,
                ndiv=self.EXP_NDIV,
                tilt_degs=tilt_degs,
                depth_mms=depth_mms, gap_mms=gap_mms, materials=materials,
                include_baseline=True,
                progress_cb=_pcb,
                material_unit_costs=params.get("material_unit_costs"),
                support_cost_per_m=params.get(
                    "support_cost_per_m", DEFAULT_SUPPORT_COST_PER_M),
                install_cost_per_window=params.get(
                    "install_cost_per_window",
                    DEFAULT_INSTALL_COST_PER_WINDOW),
                latitude_deg=self.building.location.latitude,
                north_angle_deg=self.building.north_angle_deg,
                target_window_keys=target_window_keys,
            )
            recommended = balanced_compromise(
                df,
                thermal_col=params.get("y", "thermal_discomfort"),
                maximize_thermal=params.get("maximize_y", False),
                u0_min=params.get("u0_min", 0.0))
            package = {
                "df": df,
                "optimal_row": None,
                "optimal_spaces": {},
                "optimal_daylight_results": {},
                "optimal_thermal_results": {},
            }
            if recommended.empty:
                return package
            row = recommended.iloc[0]
            completed = batch_total
            optimal_spaces = {}
            optimal_daylights = {}
            optimal_thermals = {}
            for source_space in space_snaps:
                optimal_space = build_solution_space(
                    source_space,
                    row,
                    target_window_keys=target_window_keys,
                )
                daylight = compute_complex_daylight(
                    optimal_space,
                    E_out=weather.annual_avg,
                    ndiv=WIN_DIV,
                    store_components=True,
                )
                completed += 1
                progress_cb(completed, total)
                thermal = compute_complex_thermal(
                    optimal_space,
                    weather.monthly_ghi,
                    weather.monthly_temp,
                    latitude_deg=self.building.location.latitude,
                    north_angle_deg=self.building.north_angle_deg,
                )
                completed += 1
                progress_cb(completed, total)
                optimal_spaces[source_space.id] = optimal_space
                optimal_daylights[source_space.id] = daylight
                optimal_thermals[source_space.id] = thermal
            package.update({
                "optimal_row": row.to_dict(),
                "optimal_spaces": optimal_spaces,
                "optimal_daylight_results": optimal_daylights,
                "optimal_thermal_results": optimal_thermals,
            })
            return package

        dlg = ProgressDialog(
            [f"{len(spaces)}个选中房间参数化实验"],
            parent=self,
        )
        self._exp_worker = AnalysisWorker([
            (f"{len(spaces)}个选中房间参数化实验", _run_exp)
        ])
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
            self._optimal_spaces = package.get("optimal_spaces", {})
            self._optimal_daylight_results = package.get(
                "optimal_daylight_results", {}
            )
            self._optimal_thermal_results = package.get(
                "optimal_thermal_results", {}
            )
            self._optimal_space = self._optimal_spaces.get(
                self._active_space_id
            )
            self._optimal_daylight_result = (
                self._optimal_daylight_results.get(self._active_space_id)
            )
            self._optimal_thermal_result = (
                self._optimal_thermal_results.get(self._active_space_id)
            )
            optimal_row = package.get("optimal_row") or {}
            self._optimal_label = str(optimal_row.get("param_label", ""))
            self._sync_active_results()
            self.experiment_panel.show_result(
                df, getattr(self, "_exp_params", {}),
                has_optimal=self._optimal_space is not None)
            self.experiment_sidebar.set_export_enabled(True)
            self._btn_export.setEnabled(True)
            self._switch_view(3)
            if self._optimal_space is not None:
                detail = "已生成最优方案完整分析"
            else:
                detail = "只有改造前基准，没有可推荐的L>0遮阳方案"
            self._status(f"✓ 参数化实验完成（{len(df)} 个算例，{detail}）")

    @pyqtSlot(str)
    def _show_optimal_result(self, view: str):
        self._optimal_space = self._optimal_spaces.get(
            self._active_space_id,
            self._optimal_space,
        )
        self._optimal_daylight_result = self._optimal_daylight_results.get(
            self._active_space_id,
            self._optimal_daylight_result,
        )
        self._optimal_thermal_result = self._optimal_thermal_results.get(
            self._active_space_id,
            self._optimal_thermal_result,
        )
        if not (self._optimal_space and self._optimal_daylight_result
                and self._optimal_thermal_result):
            QMessageBox.information(self, "提示", "尚无可查看的最优方案完整分析。")
            return
        if view == "daylight":
            self._switch_view(1)
        else:
            self._switch_view(2)
        self._sync_active_results()
        self._status(
            f"正在对比当前方案与最优方案：{self._optimal_label}"
            "（原工程未被修改）"
        )

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
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        raw_prefix = re.sub(
            r'[<>:"/\\|?*]+',
            "_",
            str(dlg.export_prefix or "").strip(),
        )
        prefix = f"{raw_prefix or '建筑光热分析结果'}_{timestamp}"
        exported = []

        try:
            def safe_name(value: str) -> str:
                cleaned = re.sub(r'[<>:"/\\|?*]+', "_", str(value)).strip()
                return cleaned or "未命名房间"

            display_model = (
                self.building.get_space(self._active_space_id)
                if self._active_space_id else None
            )
            if display_model is None:
                raise ValueError("当前没有可导出的计算空间。")

            # 普通分析按当前“选中房间”逐房间导出；单房间时保持历史文件名。
            normal_entries = []
            for space_id in self._selected_space_ids:
                space = self.building.get_space(space_id)
                daylight = self._daylight_results.get(space_id)
                thermal = self._thermal_results.get(space_id)
                if space is not None and (daylight is not None or thermal is not None):
                    normal_entries.append((space, daylight, thermal))
            if not normal_entries and (
                self._daylight_result is not None or self._thermal_result is not None
            ):
                normal_entries.append(
                    (display_model, self._daylight_result, self._thermal_result)
                )

            for space, daylight, thermal in normal_entries:
                room_prefix = prefix
                if len(normal_entries) > 1:
                    room_prefix = f"{prefix}_{safe_name(space.name)}"
                normal_ap = None
                normal_tp = None
                if daylight is not None:
                    normal_ap = AnalysisPanel()
                    normal_ap.update(
                        daylight, space,
                        weather_info=f"气象: {self.weather.location}")
                if thermal is not None:
                    normal_tp = ThermalPanel()
                    normal_tp.update(thermal, space)

                if dlg.chk_heatmap.isChecked() and daylight is not None:
                    p = os.path.join(d, f"{room_prefix}_热力图.png")
                    normal_ap.save_heatmap(p, dpi=200)
                    exported.append(p)

                if dlg.chk_profile.isChecked() and daylight is not None:
                    p = os.path.join(d, f"{room_prefix}_截面分布.png")
                    normal_ap.save_profile(p, dpi=200)
                    exported.append(p)

                if dlg.chk_thermal.isChecked() and thermal is not None:
                    p = os.path.join(d, f"{room_prefix}_热环境.png")
                    normal_tp.save_figure(p, dpi=200)
                    exported.append(p)

                if (
                    dlg.chk_combined.isChecked()
                    and daylight is not None
                    and thermal is not None
                ):
                    p = os.path.join(d, f"{room_prefix}_光热综合.png")
                    self._save_combined(
                        p, analysis_panel=normal_ap, thermal_panel=normal_tp,
                        title=f"{space.name}｜原始工程光热环境综合分析")
                    exported.append(p)

            # 多房间结果额外给出一张轻量 CSV 汇总表，便于横向筛选房间。
            if len(normal_entries) > 1:
                p = os.path.join(d, f"{prefix}_多房间分析汇总.csv")
                fields = [
                    "房间ID", "房间名称", "面积(m²)", "外窗数量",
                    "平均采光系数DF(%)", "平均照度(lux)",
                    "照度均匀度U0", "采光达标面积比Ra",
                    "年均室温(℃)", "舒适月数",
                    "超温月数", "欠温月数", "热不舒适度(℃·月)",
                ]
                with open(p, "w", newline="", encoding="utf-8-sig") as stream:
                    writer = csv.DictWriter(stream, fieldnames=fields)
                    writer.writeheader()
                    for space, daylight, thermal in normal_entries:
                        discomfort = ""
                        if thermal is not None:
                            discomfort = float(np.sum(
                                np.maximum(thermal.T_in - 26.0, 0.0)
                                + np.maximum(18.0 - thermal.T_in, 0.0)
                            ))
                        writer.writerow({
                            "房间ID": space.id,
                            "房间名称": space.name,
                            "面积(m²)": round(
                                space_floor_area_mm2(space) / 1_000_000.0, 3),
                            "外窗数量": sum(
                                len(wall.windows())
                                for wall in space.wall_segments()),
                            "平均采光系数DF(%)": (
                                round(float(daylight.DF_avg), 4)
                                if daylight is not None else ""),
                            "平均照度(lux)": (
                                round(float(daylight.E_avg), 2)
                                if daylight is not None else ""),
                            "照度均匀度U0": (
                                round(float(daylight.U0), 4)
                                if daylight is not None else ""),
                            "采光达标面积比Ra": (
                                round(float(daylight.Ra), 4)
                                if daylight is not None else ""),
                            "年均室温(℃)": (
                                round(float(thermal.T_in_annual_avg), 3)
                                if thermal is not None else ""),
                            "舒适月数": (
                                thermal.comfort_months
                                if thermal is not None else ""),
                            "超温月数": (
                                thermal.overheat_months
                                if thermal is not None else ""),
                            "欠温月数": (
                                thermal.underheat_months
                                if thermal is not None else ""),
                            "热不舒适度(℃·月)": (
                                round(discomfort, 4)
                                if discomfort != "" else ""),
                        })
                exported.append(p)

            if dlg.chk_exp_plots.isChecked() and self._experiment_df is not None:
                exported.extend(self.experiment_panel.save_pngs(
                    d, prefix=f"{prefix}_参数化实验"))

            if dlg.chk_exp_csv.isChecked() and self._experiment_df is not None:
                p = os.path.join(d, f"{prefix}_参数化实验结果.csv")
                saved = self.experiment_panel.save_csv(p)
                if saved:
                    exported.append(saved)

            if dlg.chk_optimal_plots.isChecked():
                optimal_entries = []
                for space_id in self._selected_space_ids:
                    optimal_space = self._optimal_spaces.get(space_id)
                    optimal_daylight = self._optimal_daylight_results.get(space_id)
                    optimal_thermal = self._optimal_thermal_results.get(space_id)
                    if (
                        optimal_space is not None
                        and optimal_daylight is not None
                        and optimal_thermal is not None
                    ):
                        optimal_entries.append(
                            (optimal_space, optimal_daylight, optimal_thermal)
                        )
                if not optimal_entries and (
                    self._optimal_space is not None
                    and self._optimal_daylight_result is not None
                    and self._optimal_thermal_result is not None
                ):
                    optimal_entries.append((
                        self._optimal_space,
                        self._optimal_daylight_result,
                        self._optimal_thermal_result,
                    ))

                for optimal_space, optimal_daylight, optimal_thermal in optimal_entries:
                    optimal_ap = AnalysisPanel()
                    optimal_tp = ThermalPanel()
                    optimal_ap.update(
                        optimal_daylight, optimal_space,
                        weather_info=f"最优遮阳：{self._optimal_label}")
                    optimal_tp.update(optimal_thermal, optimal_space)
                    room_prefix = f"{prefix}_最优方案"
                    if len(optimal_entries) > 1:
                        room_prefix += f"_{safe_name(optimal_space.name)}"
                    optimal_files = [
                        (f"{room_prefix}_采光热力图.png",
                         lambda path: optimal_ap.save_heatmap(path, dpi=200)),
                        (f"{room_prefix}_照度截面.png",
                         lambda path: optimal_ap.save_profile(path, dpi=200)),
                        (f"{room_prefix}_热环境.png",
                         lambda path: optimal_tp.save_figure(path, dpi=200)),
                    ]
                    for filename, writer in optimal_files:
                        p = os.path.join(d, filename)
                        writer(p)
                        exported.append(p)
                    p = os.path.join(d, f"{room_prefix}_光热综合.png")
                    self._save_combined(
                        p, analysis_panel=optimal_ap, thermal_panel=optimal_tp,
                        title=(
                            f"{optimal_space.name}｜最优遮阳方案光热综合分析"
                            f"｜{self._optimal_label}"
                        ))
                    exported.append(p)

            if dlg.chk_xls.isChecked():
                p = os.path.join(d, f"{prefix}_报告.xlsx")
                from io_utils.exporter import export_excel_v2
                experiment_df = self.experiment_panel.flagged_dataframe()
                export_excel_v2(p, self._daylight_result, self._thermal_result,
                                display_model,
                                self.weather if self.weather.is_valid() else None,
                                experiment_df=experiment_df,
                                experiment_params=getattr(self, "_exp_params", None),
                                optimal_daylight_result=self._optimal_daylight_result,
                                optimal_thermal_result=self._optimal_thermal_result,
                                optimal_room=self._optimal_space,
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
