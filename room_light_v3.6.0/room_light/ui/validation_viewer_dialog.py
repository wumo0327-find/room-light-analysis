"""
ui/validation_viewer_dialog.py — 验证结果查看器  v2.8.0（新增）
==================================================================
通用的"验证结果文件夹"浏览对话框：选一个文件夹（如 examples/ 下的教室对比
验证结果），左侧列出其中所有 PNG 图片和 CSV 数据表（递归扫描子文件夹），
右侧预览——点图片放大显示，点 CSV 表格化显示。不写死具体某次验证的路径，
后续任何新的验证结果文件夹都能用同一入口查看，方便审查。
"""
from __future__ import annotations
import os

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QWidget, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QFileDialog, QSplitter, QTableWidget,
    QTableWidgetItem, QHeaderView, QStackedWidget, QScrollArea, QMessageBox,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap

WHITE_STYLE = """
    QDialog{background:#ffffff;color:#1a1e2e;}
    QLabel{background:transparent;color:#1a1e2e;}
    QListWidget{background:#f5f6f8;border:1px solid #d0d5e0;border-radius:6px;
               color:#1a1e2e;font-size:12px;}
    QListWidget::item{padding:5px 6px;border-radius:4px;}
    QListWidget::item:selected{background:#2563eb;color:#ffffff;}
    QListWidget::item:hover{background:#eef0f4;}
    QPushButton{background:#f5f6f8;border:1px solid #d0d5e0;border-radius:5px;
                padding:6px 14px;color:#1a1e2e;font-weight:600;}
    QPushButton:hover{background:#eef0f4;border-color:#2563eb;color:#2563eb;}
    QPushButton#primary{background:#2563eb;border:none;color:#fff;font-weight:700;}
    QPushButton#primary:hover{background:#1d4ed8;}
    QTableWidget{background:#ffffff;alternate-background-color:#f5f6f8;
                 color:#1a1e2e;gridline-color:#e6e9f0;border:1px solid #d0d5e0;}
    QHeaderView::section{background:#f5f6f8;color:#5a6175;border:none;
                         border-right:1px solid #d0d5e0;border-bottom:1px solid #d0d5e0;
                         padding:5px;font-weight:600;}
"""


class ValidationViewerDialog(QDialog):
    """浏览一个验证结果文件夹：PNG 图片放大预览 + CSV 表格化预览。"""

    def __init__(self, initial_dir: str | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("验证结果查看器")
        self.resize(1180, 760)
        self.setStyleSheet(WHITE_STYLE)

        self._current_dir = initial_dir or ""

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        # ── 顶部：路径 + 打开文件夹 ──────────────────────────────────────
        top = QHBoxLayout()
        self._path_lbl = QLabel("未选择文件夹")
        self._path_lbl.setStyleSheet("color:#5a6175;font-size:12px;")
        btn_open = QPushButton("📂 打开文件夹…")
        btn_open.setObjectName("primary")
        btn_open.clicked.connect(self._browse_dir)
        top.addWidget(self._path_lbl, 1)
        top.addWidget(btn_open)
        root.addLayout(top)

        # ── 主体：左文件列表 / 右预览 ────────────────────────────────────
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self._file_list = QListWidget()
        self._file_list.setFixedWidth(320)
        self._file_list.itemClicked.connect(self._on_select_file)
        splitter.addWidget(self._file_list)

        self._preview_stack = QStackedWidget()
        self._img_scroll = QScrollArea()
        self._img_scroll.setWidgetResizable(True)
        self._img_label = QLabel("在左侧选择一个 PNG 图片或 CSV 表格查看")
        self._img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img_label.setStyleSheet("color:#9aa0b0;")
        self._img_scroll.setWidget(self._img_label)
        self._preview_stack.addWidget(self._img_scroll)      # index 0: 图片

        self._table = QTableWidget()
        self._table.setAlternatingRowColors(True)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        self._preview_stack.addWidget(self._table)            # index 1: CSV表

        splitter.addWidget(self._preview_stack)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, 1)

        self._orig_pixmap: QPixmap | None = None

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.accept)
        btn_row.addWidget(btn_close)
        root.addLayout(btn_row)

        if self._current_dir and os.path.isdir(self._current_dir):
            self._load_dir(self._current_dir)

    # ── 文件夹浏览 ────────────────────────────────────────────────────────
    def _browse_dir(self):
        start = self._current_dir or os.getcwd()
        d = QFileDialog.getExistingDirectory(self, "选择验证结果文件夹", start)
        if d:
            self._load_dir(d)

    def _load_dir(self, d: str):
        self._current_dir = d
        self._path_lbl.setText(d)
        self._file_list.clear()

        found = []
        for dirpath, _dirs, files in os.walk(d):
            for fn in files:
                if fn.lower().endswith((".png", ".csv")):
                    full = os.path.join(dirpath, fn)
                    rel = os.path.relpath(full, d)
                    found.append((rel, full))
        found.sort()

        if not found:
            self._file_list.addItem("(未找到 PNG / CSV 文件)")
            return
        for rel, full in found:
            icon = "🖼 " if full.lower().endswith(".png") else "📊 "
            item = QListWidgetItem(icon + rel)
            item.setData(Qt.ItemDataRole.UserRole, full)
            self._file_list.addItem(item)

    # ── 预览 ──────────────────────────────────────────────────────────────
    def _on_select_file(self, item: QListWidgetItem):
        path = item.data(Qt.ItemDataRole.UserRole)
        if not path:
            return
        if path.lower().endswith(".png"):
            self._show_image(path)
        elif path.lower().endswith(".csv"):
            self._show_csv(path)

    def _show_image(self, path: str):
        pm = QPixmap(path)
        if pm.isNull():
            QMessageBox.warning(self, "打开失败", f"无法读取图片:\n{path}")
            return
        self._orig_pixmap = pm
        self._rescale_image()
        self._preview_stack.setCurrentIndex(0)

    def _rescale_image(self):
        if self._orig_pixmap is None:
            return
        avail = self._img_scroll.viewport().size()
        target_w = max(avail.width() - 20, 200)
        if self._orig_pixmap.width() > target_w:
            scaled = self._orig_pixmap.scaledToWidth(
                target_w, Qt.TransformationMode.SmoothTransformation)
        else:
            scaled = self._orig_pixmap
        self._img_label.setPixmap(scaled)
        self._img_label.setText("")

    def _show_csv(self, path: str):
        try:
            import pandas as pd
            df = pd.read_csv(path, encoding="utf-8-sig")
        except Exception as e:
            QMessageBox.warning(self, "打开失败", f"无法读取 CSV:\n{path}\n\n{e}")
            return
        self._table.clear()
        self._table.setRowCount(len(df))
        self._table.setColumnCount(len(df.columns))
        self._table.setHorizontalHeaderLabels([str(c) for c in df.columns])
        for r in range(len(df)):
            for c, col in enumerate(df.columns):
                val = df.iat[r, c]
                item = QTableWidgetItem("" if pd.isna(val) else str(val))
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._table.setItem(r, c, item)
        self._preview_stack.setCurrentIndex(1)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._rescale_image()
