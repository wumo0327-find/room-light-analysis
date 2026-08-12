"""
ui/weather_dialog.py — 气象数据输入对话框  v2.11.0
两个选项卡：手动输入 / 文件导入
v2.11.0: 新增"填入宜宾 TMY"快捷按钮
"""
from __future__ import annotations
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QWidget,
    QLabel, QTableWidget, QTableWidgetItem, QPushButton,
    QFileDialog, QHeaderView, QLineEdit, QMessageBox, QFrame,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QDoubleValidator, QColor

from io_utils.weather_data import (
    WeatherDataset, MONTHS_ZH, BEIJING_TMY_LUX, load_from_excel
)

# 数据表格：白底黑字（与对话框白色学术主题一致），单元格可编辑
_TABLE_QSS = """
    QTableWidget{background:#ffffff;alternate-background-color:#f5f6f8;
                 color:#1a1e2e;gridline-color:#e6e9f0;
                 border:1px solid #d0d5e0;border-radius:4px;}
    QTableWidget::item{color:#1a1e2e;padding:2px;}
    QTableWidget::item:selected{background:#dbeafe;color:#1e3a8a;}
    QHeaderView::section{background:#f5f6f8;color:#5a6175;border:none;
                         border-right:1px solid #d0d5e0;border-bottom:1px solid #d0d5e0;
                         padding:5px;font-weight:600;}
    QTableCornerButton::section{background:#f5f6f8;border:none;}
"""


class WeatherDialog(QDialog):
    """模态对话框，关闭时发出 accepted_data 信号。"""
    accepted_data = pyqtSignal(object)   # WeatherDataset

    def __init__(self, current: WeatherDataset | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("气象数据设置")
        self.setMinimumWidth(480)
        self.setMinimumHeight(500)
        self.setModal(True)
        self.setStyleSheet("""
            QDialog{background:#ffffff;color:#1a1e2e;}
            QTabWidget::pane{border:1px solid #d0d5e0;border-radius:6px;background:#f5f6f8;}
            QTabBar::tab{background:#eef0f4;border:1px solid #d0d5e0;
                         border-bottom:none;padding:6px 14px;color:#5a6175;border-radius:5px 5px 0 0;}
            QTabBar::tab:selected{background:#f5f6f8;color:#2563eb;font-weight:600;}
            QTabBar::tab:hover{color:#2563eb;background:#f0f5ff;}
            QTableWidget{background:#ffffff;gridline-color:#eef0f4;color:#1a1e2e;border:1px solid #d0d5e0;}
            QTableWidget::item{color:#1a1e2e;}
            QTableWidget::item:selected{background:#dbeafe;color:#1e3a8a;}
            QHeaderView::section{background:#f5f6f8;color:#5a6175;border:none;
                                  border-right:1px solid #d0d5e0;padding:4px;font-weight:600;}
            QLabel{background:transparent;color:#1a1e2e;}
            QPushButton{background:#f5f6f8;border:1px solid #d0d5e0;border-radius:5px;
                        padding:6px 14px;color:#1a1e2e;font-weight:600;}
            QPushButton:hover{background:#eef0f4;border-color:#2563eb;color:#2563eb;}
            QPushButton#primary{background:#2563eb;border:none;color:#fff;font-weight:700;}
            QPushButton#primary:hover{background:#1d4ed8;}
            QPushButton#success{background:#f0fdf4;border:1px solid #86efac;color:#16a34a;font-weight:700;}
            QPushButton#success:hover{background:#16a34a;color:#fff;}
            QLineEdit{background:#ffffff;border:1px solid #d0d5e0;border-radius:5px;
                      padding:4px 8px;color:#1a1e2e;}
            QLineEdit:focus{border-color:#2563eb;background:#f0f5ff;}
            QCheckBox{color:#1a1e2e;}
        """)

        self._dataset = current or WeatherDataset()

        root = QVBoxLayout(self)
        root.setSpacing(10)
        root.setContentsMargins(16, 16, 16, 16)

        # ── Header ────────────────────────────────────────────────────────────
        hdr = QLabel("气象数据 — 室外水平照度（逐月均值）")
        hdr.setStyleSheet("font-size:14px;font-weight:700;color:#2563eb;")
        root.addWidget(hdr)

        hint = QLabel("单位：lux（室外水平照度）。若手头为 GHI (W/m²)，填入后程序自动 ×110 换算。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#8b95aa;font-size:12px;")
        root.addWidget(hint)

        # ── Tabs ──────────────────────────────────────────────────────────────
        tabs = QTabWidget()
        root.addWidget(tabs, 1)

        tabs.addTab(self._build_manual_tab(), "✏  手动输入")
        tabs.addTab(self._build_file_tab(),   "📂  导入文件")

        # ── Buttons ───────────────────────────────────────────────────────────
        line = QFrame(); line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color:#353d55;")
        root.addWidget(line)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        btn_cancel = QPushButton("取消")
        btn_ok     = QPushButton("确定")
        btn_ok.setObjectName("primary")

        btn_cancel.clicked.connect(self.reject)
        btn_ok.clicked.connect(self._on_ok)

        btn_row.addStretch()
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_ok)
        root.addLayout(btn_row)

    # ── Manual tab ────────────────────────────────────────────────────────────
    def _build_manual_tab(self) -> QWidget:
        w = QWidget(); lay = QVBoxLayout(w); lay.setSpacing(8)

        # Location name
        loc_row = QHBoxLayout()
        loc_row.addWidget(QLabel("地点名称（可选）:"))
        self._loc_edit = QLineEdit(self._dataset.location)
        self._loc_edit.setPlaceholderText("例：北京")
        loc_row.addWidget(self._loc_edit, 1)
        lay.addLayout(loc_row)

        # Table: 12 months × 1 column editable
        self._manual_table = QTableWidget(12, 3)
        self._manual_table.setHorizontalHeaderLabels(["月份", "室外照度 (lux)", "月均温度 (℃)"])
        self._manual_table.verticalHeader().setVisible(False)
        self._manual_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Fixed)
        self._manual_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch)
        self._manual_table.setColumnWidth(0, 55)
        self._manual_table.setColumnWidth(1, 140)
        self._manual_table.setAlternatingRowColors(True)
        self._manual_table.setStyleSheet(_TABLE_QSS)

        from io_utils.weather_data import YIYANG_TMY_LUX, YIYANG_TMY_TEMP
        for i, month in enumerate(MONTHS_ZH):
            # 月份标签（只读，浅灰）
            item_m = QTableWidgetItem(month)
            item_m.setFlags(Qt.ItemFlag.ItemIsEnabled)
            item_m.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_m.setForeground(QColor("#5a6175"))
            self._manual_table.setItem(i, 0, item_m)

            # 室外照度（可编辑，白字）
            val = self._dataset.monthly_lux[i] if self._dataset.monthly_lux else YIYANG_TMY_LUX[i]
            item_v = QTableWidgetItem(f"{val:.0f}")
            item_v.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._manual_table.setItem(i, 1, item_v)

            # 月均温度（可编辑，白字）
            tval = (self._dataset.monthly_temp[i]
                    if getattr(self._dataset, "monthly_temp", None) else YIYANG_TMY_TEMP[i])
            item_t = QTableWidgetItem(f"{tval:.1f}")
            item_t.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._manual_table.setItem(i, 2, item_t)

        lay.addWidget(self._manual_table, 1)

        # Quick-fill buttons
        btn_row = QHBoxLayout(); btn_row.setSpacing(6)
        btn_yy = QPushButton("填入益阳 TMY（默认）")
        btn_yy.setObjectName("success")
        btn_yy.clicked.connect(self._fill_yiyang)
        btn_row.addWidget(btn_yy)

        btn_bj = QPushButton("填入北京 TMY")
        btn_bj.clicked.connect(self._fill_beijing)
        btn_row.addWidget(btn_bj)

        btn_yb = QPushButton("填入宜宾 TMY")
        btn_yb.setToolTip("四川宜宾（夏热冬冷地区），月均温度取自公开气候资料，\n"
                          "月均照度按年日照时数比例估算，非逐月实测值。")
        btn_yb.clicked.connect(self._fill_yibin)
        btn_row.addWidget(btn_yb)

        btn_clear = QPushButton("全部清零")
        btn_clear.clicked.connect(self._clear_table)
        btn_row.addWidget(btn_clear)
        btn_row.addStretch()
        lay.addLayout(btn_row)

        note = QLabel(
            "参考：CIE 全阴天典型值约 15 000 lux；北京晴天夏季可达 60 000 lux。\n"
            "若需按月逐月精确分析，建议填写 12 个月实测或气象站数据。"
        )
        note.setWordWrap(True)
        note.setStyleSheet("color:#9aa0b0;font-size:11px;")
        lay.addWidget(note)
        return w

    def _fill_yiyang(self):
        from io_utils.weather_data import YIYANG_TMY_LUX, YIYANG_TMY_TEMP
        for i, v in enumerate(YIYANG_TMY_LUX):
            self._manual_table.item(i, 1).setText(str(v))
        for i, v in enumerate(YIYANG_TMY_TEMP):
            item = self._manual_table.item(i, 2)
            if item: item.setText(f"{v:.1f}")
        self._loc_edit.setText("湖南益阳")

    def _fill_beijing(self):
        for i, v in enumerate(BEIJING_TMY_LUX):
            self._manual_table.item(i, 1).setText(str(v))

    def _fill_yibin(self):
        from io_utils.weather_data import YIBIN_TMY_LUX, YIBIN_TMY_TEMP
        for i, v in enumerate(YIBIN_TMY_LUX):
            self._manual_table.item(i, 1).setText(str(v))
        for i, v in enumerate(YIBIN_TMY_TEMP):
            item = self._manual_table.item(i, 2)
            if item: item.setText(f"{v:.1f}")
        self._loc_edit.setText("四川宜宾")

    def _clear_table(self):
        for i in range(12):
            self._manual_table.item(i, 1).setText("15000")

    # ── File tab ──────────────────────────────────────────────────────────────
    def _build_file_tab(self) -> QWidget:
        w = QWidget(); lay = QVBoxLayout(w); lay.setSpacing(10)

        desc = QLabel(
            "支持 Excel (.xlsx / .xls) 和 CSV (.csv) 文件。\n\n"
            "文件格式要求（两种均可自动识别）：\n"
            "  • 格式 A：第一列=月份，第二列=室外照度 (lux)\n"
            "  • 格式 B：第一列=月份，第二列=GHI (W/m²)  ← 自动 ×110 转换\n\n"
            "第一行若为文字标题，程序自动跳过。"
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color:#8b95aa;font-size:12px;line-height:160%;")
        lay.addWidget(desc)

        # File picker row
        row = QHBoxLayout()
        self._path_edit = QLineEdit()
        self._path_edit.setPlaceholderText("点击「浏览」选择文件…")
        self._path_edit.setReadOnly(True)
        btn_browse = QPushButton("浏览…")
        btn_browse.clicked.connect(self._browse_file)
        row.addWidget(self._path_edit, 1)
        row.addWidget(btn_browse)
        lay.addLayout(row)

        # Status label
        self._file_status = QLabel("—")
        self._file_status.setWordWrap(True)
        self._file_status.setStyleSheet("color:#8b95aa;font-size:12px;")
        lay.addWidget(self._file_status)

        # Preview table
        self._preview_table = QTableWidget(12, 4)
        self._preview_table.setHorizontalHeaderLabels(
            ["月份", "GHI (W/m²)", "照度 (lux)", "温度 (℃)"])
        self._preview_table.verticalHeader().setVisible(False)
        self._preview_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self._preview_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers)
        self._preview_table.setAlternatingRowColors(True)
        self._preview_table.setStyleSheet(_TABLE_QSS)
        lay.addWidget(self._preview_table, 1)

        # Download template button
        btn_tmpl = QPushButton("下载模板 Excel")
        btn_tmpl.clicked.connect(self._download_template)
        lay.addWidget(btn_tmpl, 0, Qt.AlignmentFlag.AlignLeft)

        self._file_dataset: WeatherDataset | None = None
        return w

    def _browse_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择气象数据文件", "",
            "Excel/CSV 文件 (*.xlsx *.xls *.csv);;所有文件 (*)"
        )
        if not path:
            return
        self._path_edit.setText(path)
        ds, err = load_from_excel(path)
        if err:
            self._file_status.setText(f"⚠ {err}")
            self._file_status.setStyleSheet("color:#dc2626;font-size:12px;")
            self._file_dataset = None
        else:
            self._file_dataset = ds
            self._file_status.setText(
                f"✓ 读取成功  年均照度: {ds.annual_avg:.0f} lux")
            self._file_status.setStyleSheet("color:#16a34a;font-size:12px;font-weight:600;")
            for i, (m, ghi, lux, tmp) in enumerate(ds.summary_rows()):
                for col, val in enumerate([m, ghi, lux, tmp]):
                    it = QTableWidgetItem(val)
                    it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    self._preview_table.setItem(i, col, it)

    def _download_template(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "保存模板文件", "weather_template.xlsx",
            "Excel 文件 (*.xlsx)"
        )
        if not path:
            return
        try:
            import openpyxl
            from io_utils.weather_data import MONTHS_ZH, BEIJING_TMY_LUX
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "气象数据"
            ws.append(["月份", "室外照度(lux)", "GHI(W/m²) 参考"])
            for i, m in enumerate(MONTHS_ZH):
                lux = BEIJING_TMY_LUX[i]
                ws.append([m, lux, round(lux/110, 1)])
            wb.save(path)
            QMessageBox.information(self, "模板下载", f"模板已保存至:\n{path}")
        except Exception as e:
            QMessageBox.warning(self, "错误", str(e))

    # ── OK handler ────────────────────────────────────────────────────────────
    def _on_ok(self):
        # Determine which tab is active
        # Tab 0 = manual, Tab 1 = file
        tabs = self.findChild(QTabWidget)
        idx  = tabs.currentIndex() if tabs else 0

        if idx == 1:
            # File tab
            if self._file_dataset is None:
                QMessageBox.warning(self, "提示", "请先导入气象数据文件。")
                return
            ds = self._file_dataset
        else:
            # Manual tab
            vals = []
            for i in range(12):
                item = self._manual_table.item(i, 1)
                try:
                    v = float(item.text().strip())
                    if v <= 0: raise ValueError
                    vals.append(v)
                except Exception:
                    QMessageBox.warning(self,"输入错误",
                        f"第{i+1}行（{MONTHS_ZH[i]}）照度值无效，请输入正数。")
                    return
            # 读取温度列
            from io_utils.weather_data import YIYANG_TMY_TEMP
            temps = []
            for i in range(12):
                item_t = self._manual_table.item(i, 2)
                try:
                    temps.append(float(item_t.text().strip()))
                except Exception:
                    temps.append(YIYANG_TMY_TEMP[i])
            ds = WeatherDataset()
            ds.monthly_lux  = vals
            ds.monthly_temp = temps
            ds.location     = self._loc_edit.text().strip()
            ds.source       = "手动输入"

        self._dataset = ds
        self.accepted_data.emit(ds)
        self.accept()

    @property
    def dataset(self) -> WeatherDataset:
        return self._dataset
