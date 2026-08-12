"""Standalone building-plan drafting main window."""
from __future__ import annotations

from pathlib import Path
import re

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QAction, QKeyEvent, QKeySequence
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from canvas import DraftCanvas
from dialogs import (
    RailingSettingsDialog,
    SelectionPropertiesDialog,
    WallSettingsDialog,
    WindowSettingsDialog,
)
from model import DraftDocument, Railing, Wall, Window
from font_utils import install_chinese_font
from rlproj_export import export_rlproj as export_rlproj_file
from rlproj_import import load_rlproj


HERE = Path(__file__).resolve().parent
FILE_FILTER = (
    "支持的工程 (*.bplan.json *.rlproj *.json);;"
    "BP建筑平面 (*.bplan.json *.json);;"
    "RoomLight工程 (*.rlproj)"
)
SAVE_FILTER = "BP建筑平面 (*.bplan.json);;JSON文件 (*.json)"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("建筑平面绘图原型 v0.1.0｜墙窗栏杆与基础编辑")
        self.resize(1450, 900)
        self.current_file: Path | None = None
        self.imported_source: Path | None = None
        self._last_command: str | None = None
        self._editing_entity_id: str | None = None
        self._editing_palette = None
        self._build_toolbar()
        self._build_central()
        self._build_parameter_palettes()
        QApplication.instance().installEventFilter(self)
        self._set_style()
        self._update_counts()
        self.statusBar().showMessage(
            "输入 HZQT绘墙、MC插窗、LG绘栏杆、L直线、M移动、CO复制、MI镜像。"
        )

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("建筑绘图工具")
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.addToolBar(toolbar)

        actions = [
            ("新建", self.new_document, QKeySequence.StandardKey.New),
            ("打开", self.open_document, QKeySequence.StandardKey.Open),
            ("保存", self.save_document, QKeySequence.StandardKey.Save),
            ("导出PNG", self.export_png, None),
            ("导出rlproj", self.export_rlproj_project, None),
            ("撤销", lambda: self.canvas.undo(), QKeySequence.StandardKey.Undo),
        ]
        for text, callback, shortcut in actions:
            action = QAction(text, self)
            action.triggered.connect(callback)
            if shortcut:
                action.setShortcut(shortcut)
            toolbar.addAction(action)
        toolbar.addSeparator()
        for text, callback, shortcut in (
            ("HZQT 绘制墙体", self.wall_command, "Alt+W"),
            ("MC 绘制窗体", self.window_command, "Alt+C"),
            ("LG 绘制栏杆", self.railing_command, "Alt+R"),
            ("L 绘制直线", self.line_command, "Alt+L"),
        ):
            action = QAction(text, self)
            action.triggered.connect(callback)
            action.setShortcut(shortcut)
            toolbar.addAction(action)
        dimension = QAction("ZDBZ 逐点标注", self)
        dimension.setShortcut("Alt+D")
        dimension.triggered.connect(self.dimension_command)
        toolbar.addAction(dimension)
        toolbar.addSeparator()
        for text, callback in (
            ("M 移动", self.move_command),
            ("CO 复制", self.copy_command),
            ("MI 镜像", self.mirror_command),
        ):
            action = QAction(text, self)
            action.triggered.connect(callback)
            toolbar.addAction(action)
        properties = QAction("特性 Ctrl+1", self)
        properties.setShortcut(QKeySequence("Ctrl+1"))
        properties.triggered.connect(self.properties_command)
        toolbar.addAction(properties)
        toolbar.addSeparator()
        toolbar.addWidget(QLabel("视图"))
        self.view_combo = QComboBox()
        for label, value in (
            ("平面图", "plan"),
            ("北立面", "north"),
            ("南立面", "south"),
            ("东立面", "east"),
            ("西立面", "west"),
        ):
            self.view_combo.addItem(label, value)
        self.view_combo.currentIndexChanged.connect(self._switch_view)
        toolbar.addWidget(self.view_combo)
        toolbar.addSeparator()
        fit = QAction("范围显示", self)
        fit.setShortcut("Ctrl+0")
        fit.triggered.connect(self._fit)
        toolbar.addAction(fit)
        ortho = QAction("F8 正交", self)
        ortho.setShortcut("F8")
        ortho.triggered.connect(self._toggle_ortho)
        toolbar.addAction(ortho)

    def _build_central(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        info = QFrame()
        info.setObjectName("infoPanel")
        info_layout = QHBoxLayout(info)
        title = QLabel("建筑视图空间编辑原型")
        title.setObjectName("title")
        self.counts = QLabel()
        self.ortho_label = QLabel("正交 F8：开")
        self.ortho_label.setObjectName("modeBadge")
        info_layout.addWidget(title)
        info_layout.addStretch()
        info_layout.addWidget(self.counts)
        info_layout.addWidget(self.ortho_label)
        root.addWidget(info)

        quick = QHBoxLayout()
        for label, callback in (
            ("HZQT 墙体", self.wall_command),
            ("MC 窗体", self.window_command),
            ("LG 栏杆", self.railing_command),
            ("L 直线", self.line_command),
            ("ZDBZ 标注", self.dimension_command),
            ("M 移动", self.move_command),
            ("CO 复制", self.copy_command),
            ("MI 镜像", self.mirror_command),
        ):
            button = QPushButton(label)
            button.clicked.connect(callback)
            button.setObjectName("commandButton")
            quick.addWidget(button)
        hint = QLabel(
            "墙体闭合区域自动识别为房间，无需命名｜左键绘制/选择｜"
            "拖框选择（左→右包含、右→左相交）｜Delete删除"
        )
        hint.setObjectName("hint")
        quick.addWidget(hint, 1)
        root.addLayout(quick)

        self.canvas = DraftCanvas()
        self.canvas.status_changed.connect(self._show_prompt)
        self.canvas.document_changed.connect(self._update_counts)
        self.canvas.document_changed.connect(
            self._refresh_selection_properties
        )
        self.canvas.entity_edit_requested.connect(
            self._edit_existing_entity
        )
        self.canvas.selection_changed.connect(
            lambda _selection: self._refresh_selection_properties()
        )
        self.canvas.room_count_changed.connect(
            lambda _count: self._update_counts()
        )
        root.addWidget(self.canvas, 1)

        command_row = QHBoxLayout()
        command_label = QLabel("命令：")
        command_label.setObjectName("commandLabel")
        self.command = QLineEdit()
        self.command.setPlaceholderText(
            "HZQT / MC / LG / L / ZDBZ / M / CO / MI；可直接键入命令"
        )
        self.command.returnPressed.connect(self._execute_command)
        self.prompt = QLabel("就绪")
        self.prompt.setMinimumWidth(480)
        self.prompt.setWordWrap(True)
        command_row.addWidget(command_label)
        command_row.addWidget(self.command, 2)
        command_row.addWidget(self.prompt, 3)
        root.addLayout(command_row)
        self.setCentralWidget(central)

    def _build_parameter_palettes(self) -> None:
        self.wall_palette = WallSettingsDialog(self.canvas.wall_params, self)
        self.window_palette = WindowSettingsDialog(
            self.canvas.window_params,
            self,
        )
        self.railing_palette = RailingSettingsDialog(
            self.canvas.railing_params,
            self,
        )
        self.selection_properties = SelectionPropertiesDialog(self)
        for palette in (
            self.wall_palette,
            self.window_palette,
            self.railing_palette,
        ):
            palette.parameters_changed.connect(
                lambda values, source=palette:
                self._parameter_values_changed(source, values)
            )
            palette.palette_hidden.connect(
                lambda source=palette:
                self._parameter_palette_hidden(source)
            )

    def _set_style(self) -> None:
        self.setStyleSheet("""
            QMainWindow, QWidget { background:#f4f6fa; color:#1f2937; }
            QToolBar {
                background:#ffffff; border-bottom:1px solid #cbd5e1;
                spacing:5px; padding:5px;
            }
            QToolButton, QPushButton {
                background:#ffffff; border:1px solid #cbd5e1;
                border-radius:5px; padding:7px 12px;
            }
            QToolButton:hover, QPushButton:hover {
                border-color:#2563eb; color:#1d4ed8; background:#eff6ff;
            }
            QPushButton#commandButton {
                font-weight:700; color:#1d4ed8; min-width:105px;
            }
            QFrame#infoPanel {
                background:#ffffff; border:1px solid #d8dee9;
                border-radius:6px;
            }
            QLabel#title { font-size:17px; font-weight:700; color:#1d4ed8; }
            QLabel#modeBadge {
                background:#dbeafe; color:#1e40af; border-radius:4px;
                padding:4px 9px; font-weight:600;
            }
            QLabel#hint { color:#64748b; padding-left:10px; }
            QLabel#commandLabel { font-weight:700; color:#334155; }
            QLineEdit {
                background:#111827; color:#f8fafc; border:1px solid #334155;
                border-radius:4px; padding:7px; font-family:Consolas;
            }
        """)

    # ------------------------------------------------------------------
    # Commands and dialogs
    def wall_command(self) -> None:
        self._finish_existing_entity_edit()
        self._last_command = "HZQT"
        self._ensure_plan_view()
        self._hide_parameter_palettes(except_palette=self.wall_palette)
        self.canvas.start_wall(self.wall_palette.values())
        self.wall_palette.show_near_canvas()
        self.canvas.setFocus()

    def window_command(self) -> None:
        self._finish_existing_entity_edit()
        self._last_command = "MC"
        self._ensure_plan_view()
        self._hide_parameter_palettes(except_palette=self.window_palette)
        self.canvas.start_window(self.window_palette.values())
        self.window_palette.show_near_canvas()
        self.canvas.setFocus()

    def railing_command(self) -> None:
        self._finish_existing_entity_edit()
        self._last_command = "LG"
        self._ensure_plan_view()
        self._hide_parameter_palettes(except_palette=self.railing_palette)
        self.canvas.start_railing(self.railing_palette.values())
        self.railing_palette.show_near_canvas()
        self.canvas.setFocus()

    def dimension_command(self) -> None:
        self._last_command = "ZDBZ"
        self._ensure_plan_view()
        self._hide_parameter_palettes()
        self.canvas.start_dimension()
        self.canvas.setFocus()

    def line_command(self) -> None:
        self._last_command = "L"
        self._ensure_plan_view()
        self._hide_parameter_palettes()
        self.canvas.start_line()
        self.canvas.setFocus()

    def move_command(self) -> None:
        self._last_command = "M"
        self._ensure_plan_view()
        self._hide_parameter_palettes()
        self.canvas.start_move()
        self.canvas.setFocus()

    def copy_command(self) -> None:
        self._last_command = "CO"
        self._ensure_plan_view()
        self._hide_parameter_palettes()
        self.canvas.start_copy()
        self.canvas.setFocus()

    def mirror_command(self) -> None:
        self._last_command = "MI"
        self._ensure_plan_view()
        self._hide_parameter_palettes()
        self.canvas.start_mirror()
        self.canvas.setFocus()

    def properties_command(self) -> None:
        if not self.canvas._selected_ids:
            self._show_prompt("请先点选、切选或框选需要查看的图元。")
            return
        self.canvas.finish_command(quiet=True)
        self._finish_existing_entity_edit()
        self._hide_parameter_palettes()
        self.selection_properties.set_selection(
            self.canvas.document,
            set(self.canvas._selected_ids),
        )
        self.selection_properties.show_near_canvas()
        self._show_prompt(
            f"特性窗口已显示当前选择的"
            f"{len(self.canvas._selected_ids)}个图元。"
        )

    def _refresh_selection_properties(self) -> None:
        if not hasattr(self, "selection_properties"):
            return
        if not self.selection_properties.isVisible():
            return
        self.selection_properties.set_selection(
            self.canvas.document,
            set(self.canvas._selected_ids),
        )

    def _hide_parameter_palettes(self, except_palette=None) -> None:
        for palette in (
            self.wall_palette,
            self.window_palette,
            self.railing_palette,
        ):
            if palette is not except_palette:
                palette.hide()

    def _finish_existing_entity_edit(self) -> None:
        if self._editing_palette is not None:
            self._editing_palette.set_edit_mode(False)
        self._editing_entity_id = None
        self._editing_palette = None

    def _parameter_palette_hidden(self, palette) -> None:
        if palette is self._editing_palette:
            self._finish_existing_entity_edit()

    @staticmethod
    def _entity_parameter_values(entity) -> dict:
        if isinstance(entity, Wall):
            return {
                "height_mm": entity.height_mm,
                "width_mm": entity.width_mm,
                "axis": entity.axis,
            }
        if isinstance(entity, Window):
            return {
                "sill_height_mm": entity.sill_height_mm,
                "height_mm": entity.height_mm,
                "width_mm": entity.width_mm,
            }
        if isinstance(entity, Railing):
            return {
                "height_mm": entity.height_mm,
                "width_mm": entity.width_mm,
                "material": entity.material,
            }
        return {}

    def _edit_existing_entity(self, entity_id: str) -> None:
        entity = self.canvas.document.entity_by_id(entity_id)
        if isinstance(entity, Wall):
            palette = self.wall_palette
        elif isinstance(entity, Window):
            palette = self.window_palette
        elif isinstance(entity, Railing):
            palette = self.railing_palette
        else:
            self._show_prompt("该图元没有可双击编辑的构件参数。")
            return

        self._finish_existing_entity_edit()
        self._hide_parameter_palettes(except_palette=palette)
        palette.set_values(self._entity_parameter_values(entity))
        palette.set_edit_mode(True)
        self._editing_entity_id = entity.id
        self._editing_palette = palette
        palette.show_near_canvas()
        self._show_prompt(
            "正在编辑已选构件；修改参数会立即更新图面，关闭参数窗完成。"
        )

    def _parameter_values_changed(self, palette, values: dict) -> None:
        if (
            self._editing_entity_id is not None
            and palette is self._editing_palette
        ):
            old_id = self._editing_entity_id
            resulting_id = self.canvas.update_existing_entity_parameters(
                old_id,
                values,
            )
            if resulting_id is None:
                entity = self.canvas.document.entity_by_id(old_id)
                if entity is not None:
                    palette.set_values(
                        self._entity_parameter_values(entity)
                    )
                return
            self._editing_entity_id = resulting_id

        if palette is self.wall_palette:
            self.canvas.update_wall_params(values)
        elif palette is self.window_palette:
            self.canvas.update_window_params(values)
        else:
            self.canvas.update_railing_params(values)

    def _ensure_plan_view(self) -> None:
        index = self.view_combo.findData("plan")
        if self.view_combo.currentIndex() != index:
            self.view_combo.setCurrentIndex(index)

    def _switch_view(self) -> None:
        self._hide_parameter_palettes()
        mode = str(self.view_combo.currentData())
        self.canvas.set_view_mode(mode)
        self.canvas.setFocus()

    def _execute_command(self) -> None:
        text = self.command.text().strip()
        self.command.clear()
        if not text:
            if self.canvas.complete_command():
                self._hide_parameter_palettes()
            return
        command = text.upper()
        if command == "HZQT":
            self.wall_command()
            return
        if command == "MC":
            self.window_command()
            return
        if command in {"LG", "HLG", "RAILING"}:
            self.railing_command()
            return
        if command in {"ZDBZ", "DIM", "DIMCONTINUE"}:
            self.dimension_command()
            return
        if command in {"L", "LINE"}:
            self.line_command()
            return
        if command in {"M", "MOVE"}:
            self.move_command()
            return
        if command in {"CO", "COPY", "CP"}:
            self.copy_command()
            return
        if command in {"MI", "MIRROR"}:
            self.mirror_command()
            return
        if command in {"F", "Z", "ZE"}:
            self._hide_parameter_palettes()
            self._fit()
            return
        if command == "F8":
            self._toggle_ortho()
            return
        number = re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", text)
        if number:
            self.canvas.apply_numeric(float(text))
            self.canvas.setFocus()
            return
        self._show_prompt(
            f"未知命令：{text}。可用 HZQT、MC、LG、L、ZDBZ、"
            "M、CO、MI、F、F8。"
        )

    def _toggle_ortho(self) -> None:
        self.canvas.toggle_ortho()
        self.ortho_label.setText(
            f"正交 F8：{'开' if self.canvas.ortho else '关'}"
        )
        self.canvas.setFocus()

    def _fit(self) -> None:
        self._last_command = "F"
        self._hide_parameter_palettes()
        self.canvas.fit_document()
        self._show_prompt("已范围显示全部图元。")
        self.canvas.setFocus()

    def _inside_parameter_editor(self, widget: QWidget | None) -> bool:
        current = widget
        while current is not None:
            if current in {
                self.wall_palette,
                self.window_palette,
                self.railing_palette,
            }:
                return True
            current = current.parentWidget()
        return False

    def eventFilter(self, watched, event) -> bool:
        """Allow CAD-style command typing without focusing the command line."""
        if event.type() != QEvent.Type.KeyPress:
            return super().eventFilter(watched, event)
        key_event = event
        if not isinstance(key_event, QKeyEvent):
            return super().eventFilter(watched, event)
        if QApplication.activeModalWidget() is not None:
            return super().eventFilter(watched, event)

        key = key_event.key()
        modifiers = key_event.modifiers()
        focus = QApplication.focusWidget()

        if key == Qt.Key.Key_Escape:
            self.command.clear()
            cancelled = self.canvas.cancel_command()
            deselected = self.canvas.clear_selection()
            self._hide_parameter_palettes()
            if not cancelled and not deselected:
                self._show_prompt("当前没有正在执行的命令。")
            self.canvas.setFocus()
            return True

        if self._inside_parameter_editor(focus):
            return super().eventFilter(watched, event)

        if modifiers & (
            Qt.KeyboardModifier.ControlModifier
            | Qt.KeyboardModifier.AltModifier
            | Qt.KeyboardModifier.MetaModifier
        ):
            return super().eventFilter(watched, event)

        if key in {Qt.Key.Key_Return, Qt.Key.Key_Enter}:
            if self.command.text().strip():
                self._execute_command()
            elif self.canvas.complete_command():
                self._hide_parameter_palettes()
            self.canvas.setFocus()
            return True

        if key == Qt.Key.Key_Space:
            if self.command.text().strip():
                self._execute_command()
            elif self.canvas.complete_command():
                self._hide_parameter_palettes()
            elif self._last_command:
                self.command.setText(self._last_command)
                self._execute_command()
            else:
                self._show_prompt("尚无可重复执行的上一个命令。")
            self.canvas.setFocus()
            return True

        if focus is self.command:
            return super().eventFilter(watched, event)

        if key == Qt.Key.Key_Backspace:
            text = self.command.text()
            self.command.setText(text[:-1])
            self._show_prompt(f"命令：{self.command.text()}")
            return True

        text = key_event.text()
        if (
            text
            and text.isprintable()
            and not text.isspace()
            and (text.isalnum() or text in ".+-_")
        ):
            self.command.setText(self.command.text() + text.upper())
            self._show_prompt(
                f"命令：{self.command.text()}（Enter或空格执行）"
            )
            return True
        return super().eventFilter(watched, event)

    # ------------------------------------------------------------------
    # Files
    def new_document(self) -> None:
        self._hide_parameter_palettes()
        self._ensure_plan_view()
        self.canvas.set_document(DraftDocument())
        self.current_file = None
        self.imported_source = None
        self._update_title()
        self._show_prompt("已新建空白图纸。")

    def open_document(self) -> None:
        self._hide_parameter_palettes()
        path, _filter = QFileDialog.getOpenFileName(
            self, "打开建筑平面", str(HERE), FILE_FILTER,
        )
        if not path:
            return
        self.load_path(Path(path))

    def load_path(self, path: Path) -> None:
        if path.suffix.lower() == ".rlproj":
            self._load_rlproj(path)
            return
        try:
            self._ensure_plan_view()
            self.canvas.set_document(DraftDocument.load(path))
        except Exception as exc:
            QMessageBox.critical(self, "打开失败", str(exc))
            return
        self.current_file = path
        self.imported_source = None
        self._update_title()
        self._show_prompt(f"已打开：{path.name}")

    def _load_rlproj(self, path: Path) -> None:
        try:
            result = load_rlproj(path)
        except Exception as exc:
            QMessageBox.critical(self, "导入rlproj失败", str(exc))
            return
        self._ensure_plan_view()
        self.canvas.set_document(result.document)
        self.current_file = None
        self.imported_source = path
        self._update_title()
        self._update_counts()
        self._show_prompt(
            f"{result.notes} 当前为可编辑副本，保存时将另存为BP文件。"
        )

    def save_document(self) -> None:
        self._hide_parameter_palettes()
        if self.current_file is None:
            path, _filter = QFileDialog.getSaveFileName(
                self,
                "保存建筑平面",
                str(HERE / "未命名.bplan.json"),
                SAVE_FILTER,
            )
            if not path:
                return
            self.current_file = Path(path)
        try:
            self.canvas.document.save(self.current_file)
        except Exception as exc:
            QMessageBox.critical(self, "保存失败", str(exc))
            return
        self._update_title()
        self._show_prompt(f"已保存：{self.current_file.name}")

    def export_png(self) -> None:
        self._hide_parameter_palettes()
        path, _filter = QFileDialog.getSaveFileName(
            self,
            "导出当前建筑视图",
            str(HERE / "建筑平面.png"),
            "PNG图片 (*.png)",
        )
        if not path:
            return
        self.canvas.grab().save(path, "PNG")
        self._show_prompt(f"已导出：{Path(path).name}")

    def export_rlproj_project(self) -> None:
        """Export all recognised rooms as a RoomLight v3.3 building project."""
        self._hide_parameter_palettes()
        source = self.current_file or self.imported_source
        project_name = source.stem if source else "BP导出建筑"
        default_path = HERE / f"{project_name}.rlproj"
        path, _filter = QFileDialog.getSaveFileName(
            self,
            "导出RoomLight工程",
            str(default_path),
            "RoomLight工程 (*.rlproj)",
        )
        if not path:
            return
        try:
            summary = export_rlproj_file(
                path,
                self.canvas.document,
                project_name=project_name,
            )
        except Exception as exc:
            QMessageBox.critical(self, "导出rlproj失败", str(exc))
            return
        self._show_prompt(
            f"已导出 {summary.path.name}：{summary.spaces}个空间、"
            f"{summary.walls}段边界墙、{summary.windows}扇窗、"
            f"{summary.railings}道栏杆。请在RoomLight中复核气象、"
            "玻璃与热工参数。"
        )

    # ------------------------------------------------------------------
    def _show_prompt(self, text: str) -> None:
        self.prompt.setText(text)
        self.statusBar().showMessage(text)

    def _update_counts(self) -> None:
        document = self.canvas.document
        rooms = len(document.recognised_rooms())
        self.counts.setText(
            f"墙体 {len(document.walls)}｜窗体 {len(document.windows)}｜"
            f"栏杆 {len(document.railings)}｜直线 {len(document.lines)}｜"
            f"尺寸 {len(document.dimensions)}｜"
            f"已识别房间 {rooms}"
        )

    def _update_title(self) -> None:
        source = self.current_file or self.imported_source
        suffix = f" — {source.name}" if source else ""
        self.setWindowTitle(
            "建筑平面绘图原型 v0.1.0｜墙窗栏杆与基础编辑" + suffix
        )

    def closeEvent(self, event) -> None:
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        super().closeEvent(event)


def run() -> int:
    app = QApplication.instance() or QApplication([])
    install_chinese_font(app)
    window = MainWindow()
    window.show()
    return app.exec()
