"""
ui/export_dialog.py — 勾选式导出对话框  v2.5.1
"""
from __future__ import annotations
import os
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QCheckBox,
    QPushButton, QFileDialog, QLineEdit, QFrame, QGroupBox,
    QMessageBox,
)
from PyQt6.QtCore import Qt

WHITE_STYLE = """
    QDialog{background:#ffffff;color:#1a1e2e;}
    QLabel{background:transparent;color:#1a1e2e;}
    QGroupBox{background:#f5f6f8;border:1px solid #d0d5e0;border-radius:6px;
              margin-top:14px;padding:6px 10px 10px 10px;font-weight:600;color:#1a1e2e;}
    QGroupBox::title{subcontrol-origin:margin;subcontrol-position:top left;
                     left:10px;top:-2px;padding:0 4px;color:#2563eb;}
    QCheckBox{color:#1a1e2e;spacing:5px;}
    QCheckBox::indicator{width:13px;height:13px;border-radius:3px;
                         border:1px solid #d0d5e0;background:#ffffff;}
    QCheckBox::indicator:checked{background:#2563eb;border-color:#2563eb;}
    QPushButton{background:#f5f6f8;border:1px solid #d0d5e0;border-radius:5px;
                padding:6px 14px;color:#1a1e2e;font-weight:600;}
    QPushButton:hover{background:#eef0f4;border-color:#2563eb;color:#2563eb;}
    QPushButton#primary{background:#2563eb;border:none;color:#fff;font-weight:700;}
    QPushButton#primary:hover{background:#1d4ed8;}
    QLineEdit{background:#ffffff;border:1px solid #d0d5e0;border-radius:5px;
              padding:4px 8px;color:#1a1e2e;}
    QLineEdit:focus{border-color:#2563eb;}
"""


class ExportDialog(QDialog):
    def __init__(self, has_daylight: bool, has_thermal: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle("导出结果")
        self.setMinimumWidth(420)
        self.setModal(True)
        self.setStyleSheet(WHITE_STYLE)

        root = QVBoxLayout(self)
        root.setSpacing(10)
        root.setContentsMargins(18, 18, 18, 18)

        # ── 标题 ──────────────────────────────────────────────────────────
        hdr = QLabel("选择导出内容")
        hdr.setStyleSheet("font-size:14px;font-weight:700;color:#2563eb;")
        root.addWidget(hdr)

        # ── 图片组 ────────────────────────────────────────────────────────
        grp_img = QGroupBox("图片 (PNG, 白底, 200 dpi)")
        img_lay = QVBoxLayout(grp_img)

        self.chk_heatmap = QCheckBox("采光热力图")
        self.chk_profile = QCheckBox("照度截面分布图")
        self.chk_thermal = QCheckBox("热环境分析图")
        self.chk_combined= QCheckBox("光热综合对比图（拼合）")

        self.chk_heatmap.setChecked(has_daylight)
        self.chk_profile.setChecked(has_daylight)
        self.chk_thermal.setChecked(has_thermal)
        self.chk_combined.setChecked(has_daylight and has_thermal)

        for chk in (self.chk_heatmap, self.chk_profile,
                    self.chk_thermal, self.chk_combined):
            img_lay.addWidget(chk)
            if not has_daylight and chk in (self.chk_heatmap, self.chk_profile):
                chk.setEnabled(False)
            if not has_thermal and chk == self.chk_thermal:
                chk.setEnabled(False)
            if not (has_daylight and has_thermal) and chk == self.chk_combined:
                chk.setEnabled(False)

        root.addWidget(grp_img)

        # ── Excel 组 ──────────────────────────────────────────────────────
        grp_xls = QGroupBox("Excel 报告 (.xlsx)")
        xls_lay = QVBoxLayout(grp_xls)

        self.chk_xls = QCheckBox("导出 Excel（含所有已分析数据）")
        self.chk_xls.setChecked(True)
        xls_lay.addWidget(self.chk_xls)
        root.addWidget(grp_xls)

        # ── 保存路径 ──────────────────────────────────────────────────────
        grp_path = QGroupBox("保存路径")
        path_lay = QVBoxLayout(grp_path)

        row1 = QHBoxLayout()
        self._dir_edit = QLineEdit()
        self._dir_edit.setPlaceholderText("选择保存文件夹…")
        btn_browse = QPushButton("浏览…")
        btn_browse.clicked.connect(self._browse_dir)
        row1.addWidget(self._dir_edit, 1)
        row1.addWidget(btn_browse)
        path_lay.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("文件名前缀:"))
        self._prefix_edit = QLineEdit("daylight_result")
        row2.addWidget(self._prefix_edit, 1)
        path_lay.addLayout(row2)

        root.addWidget(grp_path)

        # ── 底部按钮 ──────────────────────────────────────────────────────
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color:#d0d5e0;")
        root.addWidget(sep)

        btn_row = QHBoxLayout()
        btn_all   = QPushButton("全选")
        btn_none  = QPushButton("全不选")
        btn_cancel= QPushButton("取消")
        btn_ok    = QPushButton("导出")
        btn_ok.setObjectName("primary")

        btn_all.clicked.connect(self._select_all)
        btn_none.clicked.connect(self._select_none)
        btn_cancel.clicked.connect(self.reject)
        btn_ok.clicked.connect(self._on_ok)

        btn_row.addWidget(btn_all)
        btn_row.addWidget(btn_none)
        btn_row.addStretch()
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_ok)
        root.addLayout(btn_row)

        # 结果
        self.result_paths: list[str] = []

    def _browse_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择保存文件夹", "")
        if d:
            self._dir_edit.setText(d)

    def _select_all(self):
        for chk in (self.chk_heatmap, self.chk_profile,
                    self.chk_thermal, self.chk_combined, self.chk_xls):
            if chk.isEnabled(): chk.setChecked(True)

    def _select_none(self):
        for chk in (self.chk_heatmap, self.chk_profile,
                    self.chk_thermal, self.chk_combined, self.chk_xls):
            chk.setChecked(False)

    def _on_ok(self):
        d = self._dir_edit.text().strip()
        if not d:
            QMessageBox.warning(self, "提示", "请先选择保存文件夹。")
            return
        prefix = self._prefix_edit.text().strip() or "result"
        self._export_dir    = d
        self._export_prefix = prefix
        self.accept()

    @property
    def export_dir(self) -> str:
        return getattr(self, "_export_dir", "")

    @property
    def export_prefix(self) -> str:
        return getattr(self, "_export_prefix", "result")
