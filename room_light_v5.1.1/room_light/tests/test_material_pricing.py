import unittest

from core.models import (
    DEFAULT_INSTALL_COST_PER_WINDOW,
    DEFAULT_SUPPORT_COST_PER_M,
    INSTALL_COST_RANGE_PER_WINDOW,
    MATERIAL_PRESETS,
    SUPPORT_COST_RANGE_PER_M,
    canonical_material_name,
    get_material,
    iter_materials,
    ShadingDevice,
)


class MaterialPricingTests(unittest.TestCase):
    def test_hunan_market_recommended_prices_and_ranges(self):
        expected = {
            "深色/多孔混凝土": (250.0, (220.0, 280.0)),
            "密实混凝土": (230.0, (200.0, 260.0)),
            "白色反射涂料混凝土": (275.0, (240.0, 310.0)),
            "深色阳极氧化铝板": (430.0, (380.0, 480.0)),
            "普通/氧化铝板": (370.0, (320.0, 420.0)),
            "镜面高反射铝板": (550.0, (480.0, 620.0)),
            "深色木材(胡桃木)": (780.0, (650.0, 900.0)),
            "浅色木材(松木)": (320.0, (260.0, 380.0)),
            "基层遮阳板+深色涂层": (430.0, (380.0, 480.0)),
            "基层遮阳板+反射隔热白涂层": (400.0, (350.0, 450.0)),
        }
        actual = {
            name: (float(spec["installed_cost_per_m2"]), tuple(spec["cost_range_per_m2"]))
            for _category, name, spec in iter_materials()
        }
        self.assertEqual(actual, expected)

    def test_public_cost_defaults(self):
        self.assertEqual(DEFAULT_SUPPORT_COST_PER_M, 65.0)
        self.assertEqual(SUPPORT_COST_RANGE_PER_M, (55.0, 80.0))
        self.assertEqual(DEFAULT_INSTALL_COST_PER_WINDOW, 300.0)
        self.assertEqual(INSTALL_COST_RANGE_PER_WINDOW, (200.0, 400.0))

    def test_historical_coating_names_remain_compatible(self):
        self.assertEqual(canonical_material_name("深色涂料"), "基层遮阳板+深色涂层")
        self.assertIs(get_material("深色涂料"), get_material("基层遮阳板+深色涂层"))
        self.assertEqual(
            MATERIAL_PRESETS["反射隔热白涂料"],
            MATERIAL_PRESETS["基层遮阳板+反射隔热白涂层"],
        )

    def test_every_material_has_explicit_optical_and_thermal_properties(self):
        for _category, _name, spec in iter_materials():
            for key in (
                "visible_reflectance", "solar_reflectance",
                "thermal_emissivity", "specular_fraction",
            ):
                self.assertIn(key, spec)
                self.assertGreaterEqual(float(spec[key]), 0.0)
                self.assertLessEqual(float(spec[key]), 1.0)

    def test_tilted_overhang_uses_vertical_tip_offset(self):
        shading = ShadingDevice(
            type="horizontal_overhang",
            overhang_depth_mm=900.0,
        )
        downward = shading.beam_shade_fraction(45.0, 1800.0, tilt_deg=60.0)
        horizontal = shading.beam_shade_fraction(45.0, 1800.0, tilt_deg=90.0)
        upward = shading.beam_shade_fraction(45.0, 1800.0, tilt_deg=120.0)
        self.assertGreater(downward, horizontal)
        self.assertGreater(horizontal, upward)


if __name__ == "__main__":
    unittest.main()
