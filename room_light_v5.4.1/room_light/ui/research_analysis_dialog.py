"""Options for the V5 research-grade external-engine workflow."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from core.research_energy import find_energyplus


class ResearchAnalysisDialog(QDialog):
    def __init__(self, default_output: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle('论文精算设置')
        self.setMinimumWidth(620)
        root = QVBoxLayout(self)
        note = QLabel(
            '该入口使用官方 Radiance 计算标准全阴天逐点采光，并生成可审计的 '
            'EnergyPlus 25.2 年模拟模型。只有选择 EPW 且本机检测到 EnergyPlus '
            '时才运行年能耗；缺少引擎时不会用简化模型冒充精算。'
        )
        note.setWordWrap(True)
        note.setStyleSheet('background:#eff6ff;border:1px solid #bfdbfe;border-radius:6px;padding:9px;color:#1e3a8a;')
        root.addWidget(note)
        form = QFormLayout()
        self.output = QLineEdit(default_output)
        self.epw = QLineEdit()
        self.grid = QSpinBox(); self.grid.setRange(50,1000); self.grid.setValue(150); self.grid.setSuffix(' mm')
        self.bounces = QSpinBox(); self.bounces.setRange(1,10); self.bounces.setValue(5)
        self.divisions = QSpinBox(); self.divisions.setRange(64,32768); self.divisions.setSingleStep(256); self.divisions.setValue(4096)
        form.addRow('结果保存到', self._path_row(self.output, self._browse_output, '选择文件夹'))
        form.addRow('EPW气象文件（可选）', self._path_row(self.epw, self._browse_epw, '选择EPW'))
        form.addRow('工作面网格', self.grid)
        form.addRow('环境反弹次数', self.bounces)
        form.addRow('环境采样数', self.divisions)
        root.addLayout(form)
        energy = find_energyplus()
        status = QLabel(
            '✓ Radiance 已随 V5 配置\n' +
            ('✓ EnergyPlus：' + str(energy) if energy else
             '△ 未检测到 EnergyPlus：仍会生成有效IDF，暂不输出年能耗数值')
        )
        status.setStyleSheet('color:#166534;padding:6px;')
        root.addWidget(status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('开始论文精算')
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _path_row(self, edit, callback, text):
        widget = QWidget(); row = QHBoxLayout(widget); row.setContentsMargins(0,0,0,0)
        button = QPushButton(text); button.clicked.connect(callback)
        row.addWidget(edit,1); row.addWidget(button)
        return widget

    def _browse_output(self):
        path = QFileDialog.getExistingDirectory(self,'选择论文精算结果目录',self.output.text())
        if path: self.output.setText(path)

    def _browse_epw(self):
        path, _ = QFileDialog.getOpenFileName(self,'选择EPW气象文件','', 'EnergyPlus Weather (*.epw)')
        if path: self.epw.setText(path)

    def _accept_if_valid(self):
        output_text = self.output.text().strip()
        if not output_text:
            self.output.setFocus(); return
        try:
            output = Path(output_text).expanduser()
            if output.exists() and not output.is_dir():
                raise NotADirectoryError("所选结果位置是文件，不是文件夹。")
            output.mkdir(parents=True, exist_ok=True)
            probe = output / ".roomlight_write_test.tmp"
            probe.write_text("ok", encoding="ascii")
            probe.unlink()
            epw_text = self.epw.text().strip()
            if epw_text and not Path(epw_text).is_file():
                raise FileNotFoundError("选择的EPW气象文件不存在。")
        except Exception as exc:
            QMessageBox.warning(
                self, "无法开始论文精算",
                f"请检查保存路径或EPW文件：\n{exc}")
            return
        self.accept()

    def values(self):
        return {
            'output':self.output.text().strip(), 'epw':self.epw.text().strip(),
            'grid_mm':int(self.grid.value()), 'ambient_bounces':int(self.bounces.value()),
            'ambient_divisions':int(self.divisions.value()),
        }
