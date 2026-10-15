"""Engine migration regression; real engine integration is explicitly opt-in."""
import os
from pathlib import Path
import sys
import unittest
import numpy as np
from core.engine_process import checkpoint, run_process
from core.engine_service import (EngineSettings, configure, settings, _key,
    compute_complex_daylight, compute_complex_thermal)
from tests.test_v510_kindergarten_regression import _building


class EngineContractTests(unittest.TestCase):
    def tearDown(self):
        configure(EngineSettings())

    def test_cancellation_prevents_start(self):
        token = checkpoint.set(lambda: False)
        try:
            with self.assertRaises(InterruptedError):
                run_process([sys.executable, '-c', 'print(1)'])
        finally:
            checkpoint.reset(token)

    def test_failed_process_is_not_success(self):
        with self.assertRaises(RuntimeError):
            run_process([sys.executable, '-c', 'raise RuntimeError("failure")'])

    def test_cache_changes_with_material_and_precision(self):
        b, _ = _building(); s = b.get_space('bp_space_2')
        a = _key(s, 'radiance', {})
        s.material.rho_wall = .42
        self.assertNotEqual(a, _key(s, 'radiance', {}))
        a = _key(s, 'radiance', {})
        config = settings(); config.ambient_divisions *= 2; configure(config)
        self.assertNotEqual(a, _key(s, 'radiance', {}))

    @unittest.skipUnless(os.getenv('ROOMLIGHT_ENGINE_TESTS') == '1', 'real engines opt-in')
    def test_kindergarten_real_engines_and_plotting(self):
        from PyQt6.QtWidgets import QApplication
        from ui.mpl_font import setup_font
        from ui.analysis_panel import AnalysisPanel
        from ui.thermal_panel import ThermalPanel
        epw = next((Path(__file__).resolve().parents[2]/'weather').rglob('*.epw'))
        configure(EngineSettings(epw_path=str(epw), grid_mm=1000,
                                 ambient_bounces=3, ambient_divisions=256))
        b, w = _building(); s = b.get_space('bp_space_2')
        light = compute_complex_daylight(s, E_out=w.annual_avg)
        heat = compute_complex_thermal(s)
        self.assertTrue(np.isfinite(light.E_lux[light.valid_mask]).all())
        self.assertGreater(light.E_min, 0)
        self.assertEqual(len(heat.hourly_temperature), 8760)
        self.assertGreater(heat.annual_lighting_kwh, 0)
        self.assertEqual(light.engine, 'Radiance')
        self.assertEqual(heat.engine, 'EnergyPlus')
        app = QApplication.instance() or QApplication([])
        setup_font()
        daylight_panel = AnalysisPanel(); daylight_panel.update(light, s)
        thermal_panel = ThermalPanel(); thermal_panel.update(heat, s)
        daylight_panel.close(); thermal_panel.close()


if __name__ == '__main__':
    unittest.main()
