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
        self.setWindowTitle('V5.4 计算设置 — 全部分析与实验共用')
        self.resize(760, 390)
        self.value = value
        root = QVBoxLayout(self)
        note = QLabel(
            '采光：Radiance 多次反射；热环境：EnergyPlus 逐时模拟。请选择项目所在地 EPW。\n'
            '普通采光/热环境分析使用下方精度；参数化实验会自动分级：'
            '固定250 mm测点及面积权重，ab3/ad512 → ab4/ad2048 → ab6/ad8192，'
            '未收敛方案再用ab7/ad16384复核。\n'
            '同一物理方案的EnergyPlus结果跨阶段复用；所有构件共用无附加构件基准。'
        )
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
        self.height = QSpinBox(); self.height.setRange(100,2000); self.height.setValue(int(value.workplane_height_mm))
        self.bounces = QSpinBox(); self.bounces.setRange(3,10); self.bounces.setValue(value.ambient_bounces)
        self.samples = QSpinBox(); self.samples.setRange(256,32768); self.samples.setValue(value.ambient_divisions)
        for label, widget in [
            ('普通分析网格 mm（实验固定250）', self.grid),
            ('普通分析/最终复核反射次数', self.bounces),
            ('普通分析/最终复核采样数', self.samples),
            ('统一工作面高度 mm', self.height),
        ]:
            form.addRow(label, widget)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save); buttons.rejected.connect(self.reject); root.addWidget(buttons)

    def save(self):
        value=EngineSettings(**asdict(self.value))
        value.epw_path=self.epw.text().strip(); value.energyplus_path=self.engine.text().strip()
        value.grid_mm=self.grid.value(); value.ambient_bounces=self.bounces.value()
        value.workplane_height_mm=self.height.value()
        value.ambient_divisions=self.samples.value()
        try:
            validate_settings(value)
        except Exception as exc:
            QMessageBox.warning(self,'计算设置未完成',str(exc)); return
        self.value=value; self.accept()
