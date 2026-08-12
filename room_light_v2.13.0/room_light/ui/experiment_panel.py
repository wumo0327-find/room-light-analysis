"""
ui/experiment_panel.py — 参数化实验结果显示面板  v2.13.0
========================================================
v2.13.0 起本面板只负责"显示结果"，全部输入控件已移到 ui/experiment_sidebar.py
（点"参数化实验"时主窗口左侧切换成实验侧边栏）。面板腾出的空间全部给结果图。

显示两种视图（顶部按钮切换）：
  · 2D图：x=采光达标面积比Ra，y=热轴指标(或成本)；下方三个按钮直接切换三张
    两两指标的二维图（Ra×热轴 / Ra×成本 / 热轴×成本），点色=遮阳材料。
  · 3D点云：Ra × 热不舒适度 × 成本节省 三轴点云，可鼠标拖动旋转；点色=材料；
    "恢复默认视图"按钮把转乱的视角复位。（3D 不再点击弹二维图——二维图改由
    2D 模式下方三个按钮直接看。）

绘图逻辑全部复用 core/experiments.py，本面板不含计算。
"""
from __future__ import annotations
import os
import tempfile
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QStackedWidget,
    QFileDialog, QMessageBox,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas

from core.experiments import (
    plot_experiments, build_pareto3d_figure, _3D_VIEW_ELEV, _3D_VIEW_AZIM,
)

# 2D 模式下方三个"面"按钮 → (x列, y列, 是否y越大越好, x轴中文名, y轴中文名, 是否用当前热轴)
# 说明：热轴那两项的 y 列/标签在运行时按当前实验的热轴指标动态填入。


class ExperimentPanel(QWidget):
    """参数化实验结果显示面板（纯显示，无输入控件）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._df = None
        self._params: dict = {}
        self._tmp_png = os.path.join(tempfile.gettempdir(), "room_light_pareto.png")
        self._orig_pixmap: Optional[QPixmap] = None
        self._plot3d_canvas = None
        self._plot3d_ax = None
        self._cur_face = 0   # 当前2D显示的是哪一对指标（0/1/2）

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(8)

        title = QLabel("参数化实验结果 — 采光 · 热环境 · 成本 权衡")
        title.setStyleSheet("font-size:15px;font-weight:700;color:#2563eb;")
        root.addWidget(title)

        # 顶部：2D/3D 切换
        top = QHBoxLayout()
        self._btn_view2d = QPushButton("2D图")
        self._btn_view3d = QPushButton("3D点云")
        for b in (self._btn_view2d, self._btn_view3d):
            b.setCheckable(True); b.setFixedHeight(28)
        self._btn_view2d.setChecked(True)
        self._btn_view2d.clicked.connect(lambda: self._switch_plot_view(0))
        self._btn_view3d.clicked.connect(lambda: self._switch_plot_view(1))
        top.addWidget(self._btn_view2d)
        top.addWidget(self._btn_view3d)
        top.addSpacing(16)
        # 3D 恢复默认视图按钮（仅3D模式下有意义，2D模式禁用）
        self._btn_reset3d = QPushButton("↺ 恢复默认视图")
        self._btn_reset3d.setFixedHeight(28)
        self._btn_reset3d.setEnabled(False)
        self._btn_reset3d.clicked.connect(self._reset_3d_view)
        top.addWidget(self._btn_reset3d)
        top.addStretch()
        root.addLayout(top)

        # 主体：QStackedWidget（0=2D，1=3D）
        self._plot_stack = QStackedWidget()

        # —— 2D 页 ——
        page2d = QWidget()
        p2 = QVBoxLayout(page2d); p2.setContentsMargins(0, 0, 0, 0); p2.setSpacing(6)
        self._plot_lbl = QLabel(
            "在左侧「参数化实验」侧边栏设置参数后点「▶ 运行实验」\n"
            "完成后此处显示帕累托散点图（后台线程计算，不卡界面）")
        self._plot_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._plot_lbl.setStyleSheet(
            "color:#9aa0b0;background:#ffffff;border:1px solid #e8ebf2;border-radius:6px;")
        self._plot_lbl.setMinimumSize(560, 420)
        p2.addWidget(self._plot_lbl, 1)
        # 三个"面"按钮
        face_row = QHBoxLayout()
        face_row.addWidget(QLabel("查看二维投影："))
        self._face_btns = []
        for i, name in enumerate(["采光 × 热轴", "采光 × 成本", "热轴 × 成本"]):
            b = QPushButton(name); b.setCheckable(True); b.setFixedHeight(26)
            b.setEnabled(False)
            b.clicked.connect(lambda _c, k=i: self._show_face(k))
            self._face_btns.append(b)
            face_row.addWidget(b)
        self._face_btns[0].setChecked(True)
        face_row.addStretch()
        p2.addLayout(face_row)
        self._plot_stack.addWidget(page2d)   # index 0

        # —— 3D 页 ——
        self._plot3d_container = QWidget()
        c3d = QVBoxLayout(self._plot3d_container); c3d.setContentsMargins(0, 0, 0, 0)
        self._plot3d_hint = QLabel(
            "运行实验后此处显示3D帕累托点云（鼠标拖动旋转；转乱了点上方"
            "「↺ 恢复默认视图」复位）")
        self._plot3d_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._plot3d_hint.setStyleSheet(
            "color:#9aa0b0;background:#ffffff;border:1px solid #e8ebf2;border-radius:6px;")
        self._plot3d_hint.setMinimumSize(560, 420)
        c3d.addWidget(self._plot3d_hint)
        self._plot_stack.addWidget(self._plot3d_container)   # index 1

        root.addWidget(self._plot_stack, 1)

    # ── 视图切换 ─────────────────────────────────────────────────────────
    def _switch_plot_view(self, idx: int):
        self._btn_view2d.setChecked(idx == 0)
        self._btn_view3d.setChecked(idx == 1)
        self._btn_reset3d.setEnabled(idx == 1 and self._plot3d_ax is not None)
        self._plot_stack.setCurrentIndex(idx)

    # ── 接收结果 → 渲染 ───────────────────────────────────────────────────
    _EMBED_DPI = 300

    def _face_specs(self):
        """当前实验对应的三张二维投影的绘图参数。热轴按 self._params 动态取。"""
        p = self._params
        ykey = p.get("y", "thermal_discomfort")
        ylab = p.get("y_label", "热不舒适度 Σ(超温+欠温) ℃·月")
        ymax = p.get("maximize_y", False)   # 热轴指标是否"越大越好"
        return [
            # (x, maximize_x, y, maximize_y, x_label, y_label, title)
            ("Ra", True, ykey, ymax, "采光达标面积比 Ra", ylab, f"采光 × {ylab}"),
            ("Ra", True, "cost", False, "采光达标面积比 Ra", "成本", "采光 × 成本"),
            (ykey, ymax, "cost", False, ylab, "成本", f"{ylab} × 成本"),
        ]

    def show_result(self, df, params: dict):
        """由 MainWindow 在后台线程完成后调用。df=结果表，params=实验设置。"""
        self._df = df
        self._params = params or {}
        self._cur_face = 0
        for i, b in enumerate(self._face_btns):
            b.setEnabled(True); b.setChecked(i == 0)
        self._render_face(0)
        self._build_3d_view(df, self._params)

    def _render_face(self, k: int):
        """渲染第 k 张二维投影到 2D 显示区。"""
        if self._df is None:
            return
        x, xmax, y, ymax, xlab, ylab, title = self._face_specs()[k]
        p = self._params
        try:
            plot_experiments(
                self._df, out_path=self._tmp_png, x=x, y=y,
                maximize_x=xmax, maximize_y=ymax,
                size_col="cost", u0_min=p.get("u0_min", 0.0),
                x_label=xlab, y_label=ylab, title=title, dpi=self._EMBED_DPI)
            self._orig_pixmap = QPixmap(self._tmp_png)
            dpr = self.devicePixelRatioF() or 1.0
            self._orig_pixmap.setDevicePixelRatio(dpr)
            self._rescale_plot()
        except Exception as e:
            QMessageBox.critical(self, "绘图失败", str(e))

    def _show_face(self, k: int):
        self._cur_face = k
        for i, b in enumerate(self._face_btns):
            b.setChecked(i == k)
        self._render_face(k)

    def _build_3d_view(self, df, p: dict):
        """重建3D点云画布（每次跑完实验整体重建）。"""
        try:
            fig, ax = build_pareto3d_figure(df, u0_min=p.get("u0_min", 0.0))
        except Exception as e:
            QMessageBox.critical(self, "3D绘图失败", str(e)); return
        c3d = self._plot3d_container.layout()
        while c3d.count():
            item = c3d.takeAt(0)
            if item.widget():
                item.widget().setParent(None)
                item.widget().deleteLater()
        canvas = FigureCanvas(fig)
        canvas.setMinimumSize(560, 420)
        c3d.addWidget(canvas)
        self._plot3d_canvas = canvas
        self._plot3d_ax = ax
        self._btn_reset3d.setEnabled(self._btn_view3d.isChecked())

    def _reset_3d_view(self):
        if self._plot3d_ax is not None and self._plot3d_canvas is not None:
            self._plot3d_ax.view_init(elev=_3D_VIEW_ELEV, azim=_3D_VIEW_AZIM)
            self._plot3d_canvas.draw_idle()

    def _rescale_plot(self):
        if self._orig_pixmap and not self._orig_pixmap.isNull():
            dpr = self.devicePixelRatioF() or 1.0
            target = self._plot_lbl.size() * dpr
            scaled = self._orig_pixmap.scaled(
                target, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            scaled.setDevicePixelRatio(dpr)
            self._plot_lbl.setPixmap(scaled)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._rescale_plot()

    # ── 导出（由实验侧边栏的导出按钮通过 MainWindow 调用）──────────────────
    def export_png(self):
        if self._df is None:
            QMessageBox.information(self, "提示", "还没有实验结果可导出。"); return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出当前二维图", "pareto_scatter.png", "PNG 图片 (*.png)")
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        x, xmax, y, ymax, xlab, ylab, title = self._face_specs()[self._cur_face]
        try:
            plot_experiments(
                self._df, out_path=path, x=x, y=y, maximize_x=xmax, maximize_y=ymax,
                size_col="cost", u0_min=self._params.get("u0_min", 0.0),
                x_label=xlab, y_label=ylab, title=title, dpi=200)
            QMessageBox.information(self, "导出完成", f"已保存当前二维图:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))

    def export_csv(self):
        if self._df is None:
            QMessageBox.information(self, "提示", "还没有实验结果可导出。"); return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出结果表", "experiment_results.csv", "CSV 文件 (*.csv)")
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
        try:
            # 内部绘图辅助列(_plot_color) 不导出
            out = self._df[[c for c in self._df.columns if not c.startswith("_")]]
            out.to_csv(path, index=False, encoding="utf-8-sig")
            QMessageBox.information(self, "导出完成", f"已保存:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))
