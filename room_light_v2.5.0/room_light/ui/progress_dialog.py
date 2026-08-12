"""
ui/progress_dialog.py — 全局分析进度条对话框  v2.5.0
支持: 实时进度 / 步骤状态显示 / 暂停 / 取消
"""
from __future__ import annotations
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QProgressBar,
    QPushButton, QFrame, QWidget,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QMutex, QMutexLocker
from PyQt6.QtGui import QFont
from typing import Callable, List, Tuple, Optional
import traceback


class AnalysisWorker(QThread):
    """
    后台分析线程。
    按步骤顺序执行，每步内部可逐行 emit 进度。
    支持暂停和取消。
    """
    # 信号
    step_started  = pyqtSignal(int, str)          # (step_idx, name)
    row_progress  = pyqtSignal(int, int, int)      # (step_idx, current, total)
    step_done     = pyqtSignal(int, str, object)   # (step_idx, name, result)
    step_error    = pyqtSignal(int, str, str)      # (step_idx, name, traceback)
    all_done      = pyqtSignal()

    def __init__(self, steps: List[Tuple[str, Callable]], parent=None):
        """
        steps: [(名称, 可调用函数)]
        每个函数接收 progress_cb(current, total) 作为参数
        """
        super().__init__(parent)
        self._steps   = steps
        self._mutex   = QMutex()
        self._paused  = False
        self._cancel  = False

    def pause(self):
        with QMutexLocker(self._mutex):
            self._paused = True

    def resume(self):
        with QMutexLocker(self._mutex):
            self._paused = False

    def cancel(self):
        with QMutexLocker(self._mutex):
            self._cancel = True
            self._paused = False

    def _check_pause(self):
        while True:
            with QMutexLocker(self._mutex):
                if self._cancel: return False
                if not self._paused: return True
            self.msleep(100)

    def run(self):
        n = len(self._steps)
        for i, (name, fn) in enumerate(self._steps):
            with QMutexLocker(self._mutex):
                if self._cancel: break
            self.step_started.emit(i, name)

            def _progress_cb(current, total, _i=i):
                if not self._check_pause():
                    return
                self.row_progress.emit(_i, current, total)

            try:
                result = fn(_progress_cb)
                self.step_done.emit(i, name, result)
            except Exception:
                self.step_error.emit(i, name, traceback.format_exc())

        self.all_done.emit()


class ProgressDialog(QDialog):
    """分析进度对话框"""
    cancelled = pyqtSignal()

    def __init__(self, step_names: List[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("正在分析…")
        self.setMinimumWidth(440)
        self.setModal(True)
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
        self.setStyleSheet("""
            QDialog{background:#ffffff;color:#1a1e2e;}
            QLabel{background:transparent;color:#1a1e2e;}
            QProgressBar{background:#eef0f4;border:none;border-radius:4px;height:8px;}
            QProgressBar::chunk{background:#2563eb;border-radius:4px;}
            QPushButton{background:#f5f6f8;border:1px solid #d0d5e0;border-radius:6px;
                        padding:6px 16px;color:#1a1e2e;font-weight:600;}
            QPushButton:hover{background:#eef0f4;border-color:#2563eb;color:#2563eb;}
            QPushButton#cancel_btn{background:#fef2f2;border-color:#fca5a5;color:#dc2626;}
            QPushButton#cancel_btn:hover{background:#dc2626;color:#fff;}
        """)

        root = QVBoxLayout(self)
        root.setSpacing(12)
        root.setContentsMargins(20, 20, 20, 20)

        self._header = QLabel("正在分析，请稍候…")
        self._header.setStyleSheet("font-size:14px;font-weight:700;color:#1a1e2e;")
        root.addWidget(self._header)

        # 全局进度条
        self._prog_global = QProgressBar()
        self._prog_global.setRange(0, len(step_names))
        self._prog_global.setValue(0)
        root.addWidget(self._prog_global)

        self._prog_lbl = QLabel("等待中…")
        self._prog_lbl.setStyleSheet("color:#5a6175;font-size:12px;")
        root.addWidget(self._prog_lbl)

        # 当前步骤细节进度条
        self._prog_detail = QProgressBar()
        self._prog_detail.setRange(0, 100)
        self._prog_detail.setValue(0)
        self._prog_detail.setStyleSheet(
            "QProgressBar::chunk{background:#16a34a;border-radius:4px;}")
        root.addWidget(self._prog_detail)

        root.addWidget(self._build_step_list(step_names))

        # 按钮行
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._btn_pause  = QPushButton("⏸ 暂停")
        self._btn_cancel = QPushButton("✕ 取消")
        self._btn_cancel.setObjectName("cancel_btn")
        self._btn_pause.clicked.connect(self._on_pause)
        self._btn_cancel.clicked.connect(self._on_cancel)
        btn_row.addWidget(self._btn_pause)
        btn_row.addWidget(self._btn_cancel)
        root.addLayout(btn_row)

        self._step_names   = step_names
        self._step_labels  = {}
        self._paused       = False
        self._steps_done   = 0
        self._worker: Optional[AnalysisWorker] = None

        self._build_step_widgets(step_names)

    def _build_step_list(self, names: List[str]) -> QWidget:
        w   = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(4)
        lay.setContentsMargins(0,0,0,0)
        self._step_label_widgets: dict[int,QLabel] = {}
        for i, name in enumerate(names):
            lbl = QLabel(f"○  {name}")
            lbl.setStyleSheet("color:#9aa0b0;font-size:12px;")
            lay.addWidget(lbl)
            self._step_label_widgets[i] = lbl
        return w

    def _build_step_widgets(self, names):
        pass   # already built in _build_step_list

    def bind_worker(self, worker: AnalysisWorker):
        self._worker = worker
        worker.step_started.connect(self._on_step_started)
        worker.row_progress.connect(self._on_row_progress)
        worker.step_done.connect(self._on_step_done)
        worker.step_error.connect(self._on_step_error)
        worker.all_done.connect(self._on_all_done)

    # ── 槽 ────────────────────────────────────────────────────────────────
    def _on_step_started(self, idx: int, name: str):
        self._prog_lbl.setText(f"正在计算: {name}…")
        self._prog_detail.setValue(0)
        lbl = self._step_label_widgets.get(idx)
        if lbl:
            lbl.setStyleSheet("color:#2563eb;font-size:12px;font-weight:600;")
            lbl.setText(f"→  {self._step_names[idx]}")

    def _on_row_progress(self, step_idx: int, current: int, total: int):
        if total > 0:
            pct = int(current / total * 100)
            self._prog_detail.setValue(pct)
            self._prog_lbl.setText(
                f"正在计算: {self._step_names[step_idx]}… "
                f"({current}/{total} 行)")

    def _on_step_done(self, idx: int, name: str, _result):
        self._steps_done += 1
        self._prog_global.setValue(self._steps_done)
        lbl = self._step_label_widgets.get(idx)
        if lbl:
            lbl.setStyleSheet("color:#16a34a;font-size:12px;font-weight:600;")
            lbl.setText(f"✓  {self._step_names[idx]}")
        self._prog_detail.setValue(100)

    def _on_step_error(self, idx: int, name: str, tb: str):
        lbl = self._step_label_widgets.get(idx)
        if lbl:
            lbl.setStyleSheet("color:#dc2626;font-size:12px;")
            lbl.setText(f"✗  {self._step_names[idx]}（错误）")

    def _on_all_done(self):
        self._header.setText("✓ 分析完成")
        self._header.setStyleSheet("font-size:14px;font-weight:700;color:#16a34a;")
        self._prog_lbl.setText("所有模块计算完毕")
        self._btn_pause.setEnabled(False)
        self._btn_cancel.setText("关闭")
        self._btn_cancel.setObjectName("")
        self._btn_cancel.setStyleSheet("")
        self._btn_cancel.clicked.disconnect()
        self._btn_cancel.clicked.connect(self.accept)

    def _on_pause(self):
        if not self._worker: return
        if self._paused:
            self._worker.resume()
            self._paused = False
            self._btn_pause.setText("⏸ 暂停")
            self._prog_lbl.setText("继续计算…")
        else:
            self._worker.pause()
            self._paused = True
            self._btn_pause.setText("▶ 继续")
            self._prog_lbl.setText("已暂停")

    def _on_cancel(self):
        if self._worker:
            self._worker.cancel()
        self.cancelled.emit()
        self.reject()
