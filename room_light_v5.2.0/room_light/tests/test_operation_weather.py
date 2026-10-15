from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from core.daylight import compute as compute_daylight
from core.experiments import _base_room
from core.operation import (
    KINDERGARTEN_CLASSROOM,
    calculate_operation_metrics,
)
from core.thermal import ThermalResult
from io_utils.exporter import _safe_color_scale_max, export_excel_v2
from io_utils.weather_data import city_weather_dataset, city_weather_names


class OperationCostTests(unittest.TestCase):
    def _thermal(self, indoor):
        result = ThermalResult()
        result.T_in = np.asarray(indoor, dtype=float)
        result.H_envelope = 180.0
        result.H_vent_avg = 45.0
        return result

    def test_kindergarten_profile_is_200_teaching_days(self):
        self.assertEqual(
            sum(KINDERGARTEN_CLASSROOM.monthly_teaching_days), 200
        )

    def test_better_daylight_reduces_lighting_energy_and_cost(self):
        thermal = self._thermal([22.0] * 12)
        poor = calculate_operation_metrics(
            daylight_score=0.2,
            thermal_result=thermal,
            floor_area_m2=90.0,
        )
        good = calculate_operation_metrics(
            daylight_score=0.8,
            thermal_result=thermal,
            floor_area_m2=90.0,
        )
        self.assertLess(good["annual_lighting_kwh"], poor["annual_lighting_kwh"])
        self.assertLess(good["annual_total_cost"], poor["annual_total_cost"])

    def test_comfortable_temperature_has_no_hvac_screening_load(self):
        metrics = calculate_operation_metrics(
            daylight_score=0.5,
            thermal_result=self._thermal([22.0] * 12),
            floor_area_m2=90.0,
        )
        self.assertAlmostEqual(metrics["annual_hvac_kwh"], 0.0)

    def test_construction_cost_is_annualized(self):
        metrics = calculate_operation_metrics(
            daylight_score=1.0,
            thermal_result=self._thermal([22.0] * 12),
            floor_area_m2=90.0,
            construction_cost_yuan=20000.0,
        )
        self.assertAlmostEqual(metrics["annualized_construction_cost"], 1000.0)
        self.assertAlmostEqual(metrics["cost"], 1000.0)


class CityWeatherTests(unittest.TestCase):
    def test_catalog_contains_capitals_and_major_cities(self):
        names = city_weather_names()
        self.assertGreaterEqual(len(names), 60)
        for city in ("北京", "长沙", "拉萨", "乌鲁木齐", "深圳", "苏州"):
            self.assertIn(city, names)

    def test_changsha_values_are_physical_and_traceable(self):
        data = city_weather_dataset("长沙")
        self.assertEqual(len(data.monthly_lux), 12)
        self.assertTrue(all(3000 < value < 50000 for value in data.monthly_lux))
        self.assertTrue(-10 < min(data.monthly_temp) < 20)
        self.assertIn("NASA POWER", data.source)
        self.assertAlmostEqual(data.latitude, 28.23, places=2)

    def test_nan_excel_scale_has_finite_fallback(self):
        self.assertEqual(_safe_color_scale_max([np.nan, np.nan]), 1)
        self.assertEqual(_safe_color_scale_max([0.1, 12.2, np.nan]), 13)

    def test_excel_export_accepts_nan_illuminance_cells(self):
        room = _base_room()
        daylight = compute_daylight(
            room, E_out=13000.0, grid_mm=1500.0, ndiv=2,
            store_components=True,
        )
        daylight.E_lux[:] = np.nan
        with TemporaryDirectory() as directory:
            path = Path(directory) / "含空值的建筑光热报告.xlsx"
            export_excel_v2(
                str(path), daylight, ThermalResult(), room,
            )
            self.assertTrue(path.is_file())
            self.assertGreater(path.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
