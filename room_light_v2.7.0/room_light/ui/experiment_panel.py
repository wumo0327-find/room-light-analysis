"""
ui/experiment_panel.py — 参数化实验标签页  v2.5.1（新增）
==========================================================
GUI 内一键跑「玻璃对照 + 遮阳β扫描」批量实验并内嵌显示帕累托散点图。

设计要点（复用，不重写）：
  · 批量实验/帕累托/绘图 全部调用 core/experiments.py 已验证逻辑
  · 后台运行由 MainWindow 用现有 AnalysisWorker + ProgressDialog 编排（本面板只发信号）
  · 本面板是纯视图：收集参数 → 发 run_requested；拿到结果 DataFrame → 渲染/导出
"""
from __future__ import annotations
import os
import tempfile
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QComboBox, QSpinBox, QDoubleSpinBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QFileDialog, QMessageBox, QFrame,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPixmap

from core.experiments import DEFAULT_GLASS_SPECS, GlassSpec, plot_experiments

# 热轴指标 → (是否越大越好, 中文标签)  —— 与 run_experiments.py 一致
_Y_META = {
    "thermal_discomfort":     (False, "热不舒适度 Σ(超温+欠温) ℃·月"),
    "overheat_degree_months": (False, "超温强度 Σ(T−26) ℃·月"),
    "comfort_ratio":          (True,  "舒适月数占比"),
}


class ExperimentPanel(QWidget):
    """参数化实验视图：参数输入 + 结果散点图 + 导出。"""
    run_requested = pyqtSignal(dict)   # 收集好的实验参数

    def __init__(self, parent=None):
        super().__init__(parent)
        self._df = None                 # 最近一次结果 DataFrame
        self._last_params: dict = {}
        self._orig_pixmap: Optional[QPixmap] = None
        self._tmp_png = os.path.join(tempfile.gettempdir(),
                                     "room_light_pareto.png")

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        title = QLabel("参数化实验 — 玻璃对照 + 遮阳β扫描 → 帕累托前沿")
        title.setStyleSheet("font-size:15px;font-weight:700;color:#2563eb;")
        root.addWidget(title)

        root.addWidget(self._build_controls())

        # 主体：左=玻璃表格 / 右=帕累托图
        body = QHBoxLayout(); body.setSpacing(12)
        body.addWidget(self._build_glass_table(), 0)
        body.addWidget(self._build_plot_area(), 1)
        root.addLayout(body, 1)

    # ── 控制区 ────────────────────────────────────────────────────────────
    def _build_controls(self) -> QWidget:
        box = QFrame()
        box.setStyleSheet("QFrame{background:#f5f6f8;border:1px solid #d0d5e0;"
                          "border-radius:6px;}")
        g = QGridLayout(box); g.setContentsMargins(12, 10, 12, 10)
        g.setHorizontalSpacing(8); g.setVerticalSpacing(8)

        def _lbl(t):
            l = QLabel(t); l.setStyleSheet("color:#5a6175;background:transparent;")
            return l

        # β 扫描范围
        self._beta_min  = self._spin(0, 0, 89, 0)
        self._beta_max  = self._spin(0, 0, 89, 60)
        self._beta_step = self._spin(1, 1, 30, 5)
        self._H_mm      = self._spin(100, 5000, 100000, 1500)  # 基准高度 H(mm)
        g.addWidget(_lbl("遮阳β范围(°)"), 0, 0)
        g.addWidget(self._beta_min, 0, 1)
        g.addWidget(_lbl("~"),        0, 2)
        g.addWidget(self._beta_max, 0, 3)
        g.addWidget(_lbl("步长"),     0, 4)
        g.addWidget(self._beta_step, 0, 5)
        g.addWidget(_lbl("H(mm)"),    0, 6)
        g.addWidget(self._H_mm, 0, 7)

        # 热轴指标 + U0 下限
        self._y_combo = QComboBox()
        for k, (_mx, lab) in _Y_META.items():
            self._y_combo.addItem(lab, userData=k)
        self._y_combo.setFixedHeight(28)
        self._u0_min = QDoubleSpinBox()
        self._u0_min.setRange(0.0, 1.0); self._u0_min.setSingleStep(0.05)
        self._u0_min.setDecimals(2); self._u0_min.setValue(0.0)
        self._u0_min.setFixedHeight(28)
        self._u0_min.setToolTip("合规筛选 U0 下限。侧窗深房间 U0 常<0.70，"
                                "默认 0 以显示全部点；调高则滤除不合规点。")
        g.addWidget(_lbl("热轴指标"), 1, 0)
        g.addWidget(self._y_combo, 1, 1, 1, 3)
        g.addWidget(_lbl("U0下限"), 1, 4)
        g.addWidget(self._u0_min, 1, 5)

        # 按钮
        self._btn_run = QPushButton("▶  运行实验")
        self._btn_run.setObjectName("primary_btn"); self._btn_run.setFixedHeight(30)
        self._btn_run.clicked.connect(self._on_run)
        self._btn_png = QPushButton("↓ 导出PNG")
        self._btn_csv = QPushButton("↓ 导出CSV")
        for b in (self._btn_png, self._btn_csv):
            b.setFixedHeight(30); b.setEnabled(False)
        self._btn_png.clicked.connect(self._export_png)
        self._btn_csv.clicked.connect(self._export_csv)
        g.addWidget(self._btn_run, 1, 6)
        g.addWidget(self._btn_png, 1, 7)
        g.addWidget(self._btn_csv, 1, 8)
        return box

    def _spin(self, mn, mx, big, val) -> QSpinBox:
        s = QSpinBox(); s.setRange(int(mn), int(big)); s.setValue(int(val))
        s.setFixedHeight(28)
        # mx 参数保留以兼容调用（此处上限用 big）
        return s

    # ── 玻璃对照组表格 ────────────────────────────────────────────────────
    def _build_glass_table(self) -> QWidget:
        w = QWidget(); w.setFixedWidth(340)
        lay = QVBoxLayout(w); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(6)
        cap = QLabel("玻璃对照组（可编辑；成本为占位值）")
        cap.setStyleSheet("color:#5a6175;font-size:12px;")
        lay.addWidget(cap)

        self._glass_tbl = QTableWidget(len(DEFAULT_GLASS_SPECS), 4)
        self._glass_tbl.setHorizontalHeaderLabels(["玻璃类型", "Tvis", "SC", "成本"])
        self._glass_tbl.verticalHeader().setVisible(False)
        self._glass_tbl.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        for c in (1, 2, 3):
            self._glass_tbl.horizontalHeader().setSectionResizeMode(
                c, QHeaderView.ResizeMode.ResizeToContents)
        for i, gspec in enumerate(DEFAULT_GLASS_SPECS):
            for c, val in enumerate([gspec.label, f"{gspec.tvis:.2f}",
                                     f"{gspec.sc:.2f}", f"{gspec.cost:.0f}"]):
                it = QTableWidgetItem(val)
                if c != 0:
                    it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._glass_tbl.setItem(i, c, it)
        self._glass_tbl.setStyleSheet("font-size:12px;background:#ffffff;")
        lay.addWidget(self._glass_tbl, 1)
        return w

    # ── 绘图区 ────────────────────────────────────────────────────────────
    def _build_plot_area(self) -> QWidget:
        self._plot_lbl = QLabel(
            "设置参数后点「▶ 运行实验」\n"
            "完成后此处显示帕累托散点图（后台线程计算，不卡界面）")
        self._plot_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._plot_lbl.setStyleSheet(
            "color:#9aa0b0;background:#ffffff;border:1px solid #e8ebf2;"
            "border-radius:6px;")
        self._plot_lbl.setMinimumSize(480, 360)
        return self._plot_lbl

    # ── 收集参数 → 发信号 ─────────────────────────────────────────────────
    def _collect_glass_specs(self):
        specs = []
        for i in range(self._glass_tbl.rowCount()):
            try:
                label = self._glass_tbl.item(i, 0).text().strip()
                tvis  = float(self._glass_tbl.item(i, 1).text())
                sc    = float(self._glass_tbl.item(i, 2).text())
                cost  = float(self._glass_tbl.item(i, 3).text())
                if not label:
                    continue
                specs.append(GlassSpec(label=label, tvis=tvis, sc=sc, cost=cost))
            except (AttributeError, ValueError):
                continue
        return specs

    def _on_run(self):
        bmin, bmax = self._beta_min.value(), self._beta_max.value()
        step = max(1, self._beta_step.value())
        if bmax < bmin:
            QMessageBox.warning(self, "提示", "β 上限不能小于下限。"); return
        beta_degs = list(range(bmin, bmax + 1, step))
        specs = self._collect_glass_specs()
        if not specs:
            QMessageBox.warning(self, "提示", "玻璃对照组表格无有效数据。"); return
        ykey = self._y_combo.currentData()
        params = {
            "beta_degs":  beta_degs,
            "H_mm":       float(self._H_mm.value()),
            "glass_specs": specs,
            "y":          ykey,
            "maximize_y": _Y_META[ykey][0],
            "y_label":    _Y_META[ykey][1],
            "u0_min":     float(self._u0_min.value()),
        }
        self._last_params = params
        self.run_requested.emit(params)

    # ── 接收结果 → 渲染 ───────────────────────────────────────────────────
    # 内嵌预览用超采样 dpi：远高于常见屏幕物理分辨率，缩小显示时保持清晰
    _EMBED_DPI = 300

    def show_result(self, df):
        """由 MainWindow 在后台线程完成后调用，df 为结果 DataFrame。"""
        self._df = df
        p = self._last_params
        try:
            plot_experiments(
                df, out_path=self._tmp_png, x="Ra", y=p["y"],
                maximize_y=p["maximize_y"], size_col="cost",
                u0_min=p["u0_min"], y_label=p["y_label"], dpi=self._EMBED_DPI)
            self._orig_pixmap = QPixmap(self._tmp_png)
            # 按屏幕物理像素比标记该 pixmap，避免 Qt 在高分屏下把已缩放的图再拉伸一次导致模糊
            dpr = self.devicePixelRatioF() or 1.0
            self._orig_pixmap.setDevicePixelRatio(dpr)
            self._rescale_plot()
            self._btn_png.setEnabled(True)
            self._btn_csv.setEnabled(True)
        except Exception as e:
            QMessageBox.critical(self, "绘图失败", str(e))

    def _rescale_plot(self):
        if self._orig_pixmap and not self._orig_pixmap.isNull():
            dpr = self.devicePixelRatioF() or 1.0
            target = self._plot_lbl.size() * dpr
            scaled = self._orig_pixmap.scaled(
                target,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            scaled.setDevicePixelRatio(dpr)
            self._plot_lbl.setPixmap(scaled)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._rescale_plot()

    # ── 导出 ──────────────────────────────────────────────────────────────
    def _export_png(self):
        if self._df is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出帕累托图", "pareto_scatter.png", "PNG 图片 (*.png)")
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        p = self._last_params
        try:
            plot_experiments(
                self._df, out_path=path, x="Ra", y=p["y"],
                maximize_y=p["maximize_y"], size_col="cost",
                u0_min=p["u0_min"], y_label=p["y_label"], dpi=200)
            QMessageBox.information(self, "导出完成", f"已保存:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))

    def _export_csv(self):
        if self._df is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出结果表", "experiment_results.csv", "CSV 文件 (*.csv)")
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
        try:
            self._df.to_csv(path, index=False, encoding="utf-8-sig")
            QMessageBox.information(self, "导出完成", f"已保存:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))
