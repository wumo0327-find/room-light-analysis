"""Production engine settings, shared by analysis and experiments."""
from dataclasses import asdict
from pathlib import Path
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QLabel, QLineEdit,
    QPushButton, QHBoxLayout, QDialogButtonBox, QFileDialog, QMessageBox, QSpinBox)
from core.engine_service import EngineSettings, validate_settings
from core.research_energy import find_energyplus


class EngineSettingsDialog(QDialog):
    def __init__(self, value, parent=None):
        super().__init__(parent)
        self.setWindowTitle('V5.2 计算设置 — 全部分析与实验共用')
        self.resize(720, 340)
        self.value = value
        root = QVBoxLayout(self)
        note = QLabel('采光：Radiance 多次反射；热环境：EnergyPlus 逐时模拟。\n'
                      '参数实验每个方案均调用上述引擎，首次计算较慢；相同方案会自动复用结果。\n'
                      '请选择项目所在地 EPW。所有方案采用相同精度与幼儿园作息。')
        note.setWordWrap(True); root.addWidget(note)
        limits = QLabel('测试版边界：全年照明采用 EnergyPlus 单参考点调光；围护结构为等效均质层。'
                        '地面接地时仍需核实地温（当前默认18℃）。室内反光板及室内/中置百叶暂不支持联合光热实验。'
                        '更换引擎不等于已完成实测验证。')
        limits.setWordWrap(True)
        limits.setStyleSheet('color:#a65300;')
        root.addWidget(limits)
        form = QFormLayout(); root.addLayout(form)
        bundled_epw = next((Path(__file__).resolve().parents[2]/'weather').rglob('*.epw'), None)
        self.epw = QLineEdit(value.epw_path or str(bundled_epw or ''))
        self.epw.setToolTip('内置文件为长沙CSWD；其他城市必须重新选择相应EPW。')
        self.engine = QLineEdit(value.energyplus_path or str(find_energyplus() or ''))
        for label, edit, pattern in [('逐时气象 EPW',self.epw,'EPW (*.epw)'),
                                     ('EnergyPlus 25.2',self.engine,'程序 (*.exe)')]:
            row = QHBoxLayout(); row.addWidget(edit)
            button = QPushButton('选择'); row.addWidget(button)
            def browse(_checked=False, target=edit, filt=pattern):
                filename, _ = QFileDialog.getOpenFileName(self,'选择文件',target.text(),filt)
                if filename: target.setText(filename)
            button.clicked.connect(browse); form.addRow(label,row)
        self.grid = QSpinBox(); self.grid.setRange(50,1000); self.grid.setValue(int(value.grid_mm))
        self.bounces = QSpinBox(); self.bounces.setRange(3,10); self.bounces.setValue(value.ambient_bounces)
        self.samples = QSpinBox(); self.samples.setRange(256,32768); self.samples.setValue(value.ambient_divisions)
        for label, widget in [('工作面网格 mm',self.grid),('环境反射次数',self.bounces),('环境采样数',self.samples)]:
            form.addRow(label, widget)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save); buttons.rejected.connect(self.reject); root.addWidget(buttons)

    def save(self):
        value=EngineSettings(**asdict(self.value))
        value.epw_path=self.epw.text().strip(); value.energyplus_path=self.engine.text().strip()
        value.grid_mm=self.grid.value(); value.ambient_bounces=self.bounces.value()
        value.ambient_divisions=self.samples.value()
        try:
            validate_settings(value)
        except Exception as exc:
            QMessageBox.warning(self,'计算设置未完成',str(exc)); return
        self.value=value; self.accept()
