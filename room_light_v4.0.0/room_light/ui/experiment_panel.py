"""
ui/experiment_panel.py — 参数化实验结果显示面板  v4.0.0
========================================================
本面板只负责"显示结果"，全部输入控件位于 ui/experiment_sidebar.py。
v2.14.0：统一全局三目标帕累托语义，新增帕累托标识开关、原始数值透视3D视图，
一次导出默认3D图与三张二维投影图。
（点"参数化实验"时主窗口左侧切换成实验侧边栏）。面板腾出的空间全部给结果图。

显示两种视图（顶部按钮切换）：
  · 2D图：x=采光达标面积比Ra，y=热轴指标(或成本)；下方三个按钮直接切换三张
    两两指标的二维图（Ra×热轴 / Ra×成本 / 热轴×成本），点色=遮阳材料。
  · 3D点云：Ra × 热不舒适度 × 工程造价估算 三轴点云，可鼠标拖动旋转；点色=材料；
    "恢复默认视图"按钮把转乱的视角复位。（3D 不再点击弹二维图——二维图改由
    2D 模式下方三个按钮直接看。）

绘图逻辑全部复用 core/experiments.py，本面板不含计算。
"""
from __future__ import annotations
import os
import tempfile
from datetime import datetime
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QStackedWidget,
    QFileDialog, QMessageBox, QCheckBox, QPlainTextEdit, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas

from core.experiments import (
    plot_experiments, build_pareto3d_figure, _3D_VIEW_ELEV, _3D_VIEW_AZIM,
    _3D_FOCAL_LENGTH, annotate_global_selection, global_selection_summary,
    pareto_front, _shading_candidates,
)

# 2D 模式下方三个"面"按钮 → (x列, y列, 是否y越大越好, x轴中文名, y轴中文名, 是否用当前热轴)
# 说明：热轴那两项的 y 列/标签在运行时按当前实验的热轴指标动态填入。


class ExperimentPanel(QWidget):
    """参数化实验结果显示面板（纯显示，无输入控件）。"""
    view_optimal_requested = pyqtSignal(str)

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
        top.addSpacing(12)
        self._show_pareto = QCheckBox("显示帕累托边缘与标识")
        self._show_pareto.setChecked(True)
        self._show_pareto.setStyleSheet(
            "color:#1a1e2e;font-size:11px;background:transparent;")
        self._show_pareto.toggled.connect(self._on_pareto_toggled)
        top.addWidget(self._show_pareto)
        top.addSpacing(12)
        self._show_projections = QCheckBox("3D辅助投影")
        self._show_projections.setChecked(False)
        self._show_projections.setToolTip(
            "默认关闭以减少重复点；开启后把三张二维图投到3D三个网格面。")
        self._show_projections.toggled.connect(self._on_pareto_toggled)
        top.addWidget(self._show_projections)
        top.addSpacing(12)
        self._btn_opt_daylight = QPushButton("查看最优采光")
        self._btn_opt_thermal = QPushButton("查看最优热环境")
        for button in (self._btn_opt_daylight, self._btn_opt_thermal):
            button.setFixedHeight(28)
            button.setEnabled(False)
        self._btn_opt_daylight.clicked.connect(
            lambda: self.view_optimal_requested.emit("daylight"))
        self._btn_opt_thermal.clicked.connect(
            lambda: self.view_optimal_requested.emit("thermal"))
        top.addWidget(self._btn_opt_daylight)
        top.addWidget(self._btn_opt_thermal)
        top.addStretch()
        root.addLayout(top)

        self._selection_note = QPlainTextEdit(
            "运行实验后，将在这里说明全局筛选标准、材料入选分布和推荐方案。")
        self._selection_note.setReadOnly(True)
        self._selection_note.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._selection_note.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self._selection_note.setStyleSheet(
            "color:#4b5563;background:#f8fafc;border:1px solid #d9dee8;"
            "border-radius:5px;padding:6px 8px;font-size:10px;")
        self._selection_note.setFixedHeight(108)
        root.addWidget(self._selection_note)

        self._result_table = QTableWidget(0, 10)
        self._result_table.setHorizontalHeaderLabels([
            "方案", "材料", "倾角", "板长", "间隙", "Ra", "热指标", "造价", "精度", "结论",
        ])
        self._result_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._result_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self._result_table.setAlternatingRowColors(True)
        self._result_table.verticalHeader().setVisible(False)
        self._result_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        self._result_table.horizontalHeader().setStretchLastSection(True)
        self._result_table.setFixedHeight(150)
        self._result_table.setToolTip(
            "优先列出三目标帕累托方案，再列综合推荐排名靠前的方案；完整数据见CSV。")
        root.addWidget(self._result_table)

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
        self._update_selection_note()
        self._update_result_table()

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
            ("Ra", True, "cost", False, "采光达标面积比 Ra", "工程造价估算（元）",
             "采光 × 工程造价"),
            (ykey, ymax, "cost", False, ylab, "工程造价估算（元）",
             f"{ylab} × 工程造价"),
        ]

    def show_result(self, df, params: dict, has_optimal: bool = False):
        """由 MainWindow 在后台线程完成后调用。df=结果表，params=实验设置。"""
        self._df = df
        self._params = params or {}
        self._cur_face = 0
        self._btn_opt_daylight.setEnabled(has_optimal)
        self._btn_opt_thermal.setEnabled(has_optimal)
        self._update_selection_note()
        self._update_result_table()
        for i, b in enumerate(self._face_btns):
            b.setEnabled(True); b.setChecked(i == 0)
        self._render_face(0)
        self._build_3d_view(df, self._params)

    def clear_result(self, message: str = "工程参数已改变，请重新运行参数化实验。"):
        """清除已失效的实验图和最优方案入口，防止导出旧工程结果。"""
        self._df = None
        self._params = {}
        self._orig_pixmap = None
        self._plot_lbl.clear()
        self._plot_lbl.setText(message)
        self._plot_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._selection_note.setPlainText(message)
        self._result_table.setRowCount(0)
        self._btn_opt_daylight.setEnabled(False)
        self._btn_opt_thermal.setEnabled(False)
        for button in self._face_btns:
            button.setEnabled(False)
        layout = self._plot3d_container.layout()
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        hint = QLabel(message)
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet(
            "color:#9aa0b0;background:#ffffff;border:1px solid #e8ebf2;"
            "border-radius:6px;")
        hint.setMinimumSize(560, 420)
        layout.addWidget(hint)
        self._plot3d_canvas = None
        self._plot3d_ax = None
        self._btn_reset3d.setEnabled(False)

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
                x_label=xlab, y_label=ylab, title=title, dpi=self._EMBED_DPI,
                show_pareto=self._show_pareto.isChecked(),
                global_thermal_col=p.get("y", "thermal_discomfort"),
                maximize_global_thermal=p.get("maximize_y", False))
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
        self._update_selection_note()

    def _update_selection_note(self):
        """按当前2D面或3D模式显示与红圈完全一致的筛选口径。"""
        if self._df is None:
            return
        p = self._params
        if self._plot_stack.currentIndex() == 1:
            summary = global_selection_summary(
                self._df,
                thermal_col=p.get("y", "thermal_discomfort"),
                maximize_thermal=p.get("maximize_y", False),
                thermal_label=p.get("y_label", "热不舒适度"),
                u0_min=p.get("u0_min", 0.0),
            )
            self._selection_note.setPlainText(summary + self._precision_summary())
            return

        x, maximize_x, y, maximize_y, x_label, y_label, _title = \
            self._face_specs()[self._cur_face]
        candidates = _shading_candidates(
            self._df, u0_min=p.get("u0_min", 0.0))
        front = pareto_front(
            candidates, x=x, y=y,
            maximize_x=maximize_x, maximize_y=maximize_y,
            u0_min=p.get("u0_min", 0.0))
        ignored = (
            "工程造价不参与本图帕累托判断"
            if self._cur_face == 0 else
            f"{p.get('y_label', '热指标')}不参与本图帕累托判断"
            if self._cur_face == 1 else
            "采光达标面积比Ra不参与本图帕累托判断"
        )
        if "material" in front.columns and not front.empty:
            counts = front["material"].astype(str).value_counts(sort=False)
            materials = "、".join(
                f"{name} {int(count)}个" for name, count in counts.items())
        else:
            materials = "无"
        x_rule = "越大越好" if maximize_x else "越小越好"
        y_rule = "越大越好" if maximize_y else "越小越好"
        self._selection_note.setPlainText(
            f"当前二维图只使用两个指标：{x_label}（{x_rule}）与"
            f"{y_label}（{y_rule}）；{ignored}。若另一方案在这两项都不差，"
            "且至少一项严格更好，本方案才被淘汰。\n"
            f"本图共有{len(candidates)}个合规候选，圈出{len(front)}个二维"
            f"帕累托点；入选材料：{materials}。红圈/虚线均按这两个指标产生。"
            "金色星标仍是采光、热、造价三目标等权的综合推荐，不等同于本图红圈。"
            + self._precision_summary()
        )

    def _precision_summary(self) -> str:
        if self._df is None or "precision_note" not in self._df.columns:
            return ""
        verified = self._df["precision_note"].astype(str).str.contains("已收敛")
        needs_more = self._df["precision_note"].astype(str).str.contains("继续细化")
        capped = self._df["precision_note"].astype(str).str.contains("复核上限")
        text = f"\n数值精度：{int(verified.sum())} 个关键方案经高积分密度复核后已收敛。"
        if needs_more.any():
            text += f"有 {int(needs_more.sum())} 个关键方案尚未收敛，不应直接作为论文定案。"
        if capped.any():
            text += f"另有 {int(capped.sum())} 个关键方案仅完成参数扫描，请在论文定案前缩小参数范围重算。"
        return text

    def _build_3d_view(self, df, p: dict):
        """重建3D点云画布（每次跑完实验整体重建）。"""
        try:
            fig, ax = build_pareto3d_figure(
                df, u0_min=p.get("u0_min", 0.0),
                thermal_col=p.get("y", "thermal_discomfort"),
                maximize_thermal=p.get("maximize_y", False),
                thermal_label=p.get("y_label", "热舒适优度"),
                show_pareto=self._show_pareto.isChecked(),
                show_projections=self._show_projections.isChecked())
        except Exception as e:
            QMessageBox.critical(self, "3D绘图失败", str(e)); return
        c3d = self._plot3d_container.layout()
        while c3d.count():
            item = c3d.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        canvas = FigureCanvas(fig)
        canvas.setMinimumSize(560, 420)
        c3d.addWidget(canvas)
        self._plot3d_canvas = canvas
        self._plot3d_ax = ax
        self._btn_reset3d.setEnabled(self._btn_view3d.isChecked())

    def _reset_3d_view(self):
        if self._plot3d_ax is not None and self._plot3d_canvas is not None:
            self._plot3d_ax.set_proj_type("persp", focal_length=_3D_FOCAL_LENGTH)
            self._plot3d_ax.view_init(elev=_3D_VIEW_ELEV, azim=_3D_VIEW_AZIM)
            self._plot3d_canvas.draw_idle()

    def _on_pareto_toggled(self, _checked: bool):
        """同步刷新当前二维图和3D图；普通点与均衡推荐星标不受开关影响。"""
        if self._df is None:
            return
        self._render_face(self._cur_face)
        self._build_3d_view(self._df, self._params)

    def _update_result_table(self):
        if self._df is None:
            self._result_table.setRowCount(0)
            return
        p = self._params
        thermal_col = p.get("y", "thermal_discomfort")
        flagged = annotate_global_selection(
            self._df,
            thermal_col=thermal_col,
            maximize_thermal=p.get("maximize_y", False),
            u0_min=p.get("u0_min", 0.0),
        )
        candidates = flagged[
            flagged["solution_id"].astype(str).str.startswith("S")
        ].copy()
        if candidates.empty:
            self._result_table.setRowCount(0)
            return
        candidates = candidates.sort_values(
            ["global_pareto", "recommendation_rank"],
            ascending=[False, True],
            kind="mergesort",
        ).head(24)
        self._result_table.setRowCount(len(candidates))
        for row_number, (_index, row) in enumerate(candidates.iterrows()):
            conclusion = []
            if bool(row.get("balanced_recommended", False)):
                conclusion.append("综合推荐")
            if bool(row.get("global_pareto", False)):
                conclusion.append("三目标帕累托")
            values = [
                row.get("solution_id", ""),
                row.get("material", ""),
                f"{float(row.get('tilt_deg', 0.0)):.0f}°",
                f"{float(row.get('L_mm', 0.0)):.0f}",
                f"{float(row.get('gap_mm', 0.0)):.0f}",
                f"{float(row.get('Ra', 0.0)):.4f}",
                f"{float(row.get(thermal_col, 0.0)):.3f}",
                f"{float(row.get('cost', 0.0)):.0f}",
                row.get("precision_note", "参数扫描"),
                "、".join(conclusion) or f"推荐排名{int(row.get('recommendation_rank', 0))}",
            ]
            for column, value in enumerate(values):
                self._result_table.setItem(
                    row_number, column, QTableWidgetItem(str(value)))

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
    def save_pngs(self, out_dir: str, prefix: str = "参数化实验") -> list[str]:
        """无对话框地保存默认3D图和三张二维投影，供统一导出复用。"""
        if self._df is None:
            return []
        filenames = [
            f"{prefix}_采光_热舒适.png",
            f"{prefix}_采光_工程造价.png",
            f"{prefix}_热舒适_工程造价.png",
        ]
        p = self._params
        written = []
        for spec, filename in zip(self._face_specs(), filenames):
            x, xmax, y, ymax, xlab, ylab, title = spec
            path = os.path.join(out_dir, filename)
            plot_experiments(
                self._df, out_path=path, x=x, y=y,
                maximize_x=xmax, maximize_y=ymax,
                size_col="cost", u0_min=p.get("u0_min", 0.0),
                x_label=xlab, y_label=ylab, title=title, dpi=240,
                show_pareto=self._show_pareto.isChecked(),
                global_thermal_col=p.get("y", "thermal_discomfort"),
                maximize_global_thermal=p.get("maximize_y", False))
            written.append(path)

        fig3d, ax3d = build_pareto3d_figure(
            self._df, u0_min=p.get("u0_min", 0.0),
            thermal_col=p.get("y", "thermal_discomfort"),
            maximize_thermal=p.get("maximize_y", False),
            thermal_label=p.get("y_label", "热舒适优度"),
            show_pareto=self._show_pareto.isChecked(),
            show_projections=self._show_projections.isChecked())
        ax3d.set_proj_type("persp", focal_length=_3D_FOCAL_LENGTH)
        ax3d.view_init(elev=_3D_VIEW_ELEV, azim=_3D_VIEW_AZIM)
        path3d = os.path.join(out_dir, f"{prefix}_3D全局帕累托.png")
        fig3d.savefig(path3d, dpi=240, facecolor="white", bbox_inches="tight")
        written.insert(0, path3d)
        return written

    def export_png(self):
        if self._df is None:
            QMessageBox.information(self, "提示", "还没有实验结果可导出。"); return
        out_dir = QFileDialog.getExistingDirectory(self, "选择四张图的导出文件夹")
        if not out_dir:
            return
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
            written = self.save_pngs(
                out_dir,
                prefix=f"参数化实验图表_{timestamp}",
            )
            QMessageBox.information(
                self, "导出完成",
                "已按默认透视视角导出4张图：\n" + "\n".join(written))
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))

    def flagged_dataframe(self):
        """返回带全局帕累托/均衡推荐标记且移除内部绘图列的结果表。"""
        if self._df is None:
            return None
        p = self._params
        flagged = annotate_global_selection(
            self._df,
            thermal_col=p.get("y", "thermal_discomfort"),
            maximize_thermal=p.get("maximize_y", False),
            u0_min=p.get("u0_min", 0.0))
        return flagged[[c for c in flagged.columns if not c.startswith("_")]]

    def save_csv(self, path: str) -> str:
        """无对话框保存实验CSV，供统一导出复用。"""
        out = self.flagged_dataframe()
        if out is None:
            return ""
        out.to_csv(path, index=False, encoding="utf-8-sig")
        return path

    def export_csv(self):
        if self._df is None:
            QMessageBox.information(self, "提示", "还没有实验结果可导出。"); return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出结果表",
            (
                "参数化实验结果_"
                f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')[:-3]}.csv"
            ),
            "CSV 文件 (*.csv)",
        )
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
        try:
            self.save_csv(path)
            QMessageBox.information(self, "导出完成", f"已保存:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))
