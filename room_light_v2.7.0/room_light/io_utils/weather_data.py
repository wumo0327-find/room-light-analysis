"""
io_utils/weather_data.py — Weather dataset model + Excel/CSV/manual import
默认气象数据: 湖南省益阳市 (CSWD/中国气象局, 28.59°N 112.33°E)
v2.7.0: 新增 monthly_temp (月均室外干球温度)
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List

MONTHS_ZH = ["1月","2月","3月","4月","5月","6月",
              "7月","8月","9月","10月","11月","12月"]

# ── 益阳 TMY 数据（CSWD/中国气象局标准气象年）──────────────────────────────────
# 室外水平照度: GHI月均(W/m²) × 110 lm/W
YIYANG_TMY_LUX = [
     7480,  7920, 11880, 15510, 17930, 18260,
    21560, 20680, 16390, 13200,  9130,  6930,
]
# 月均 GHI (W/m²)
YIYANG_TMY_GHI = [68, 72, 108, 141, 163, 166, 196, 188, 149, 120, 83, 63]

# 月均室外干球温度 (℃)  来源: CSWD 益阳 TMY
YIYANG_TMY_TEMP = [
    5.8, 7.5, 12.0, 18.5, 23.2, 27.1,
    30.2, 29.8, 24.8, 19.0, 13.2, 7.3,
]

BEIJING_TMY_LUX  = [
    20100, 27400, 36800, 48500, 55200, 58600,
    52400, 51300, 44700, 33900, 22500, 17800,
]
BEIJING_TMY_TEMP = [
    -3.2, -0.5, 6.2, 14.3, 20.1, 25.3,
    27.9, 26.5, 20.8, 13.2, 4.7, -1.5,
]

DEFAULT_LOCATION = "湖南益阳"
DEFAULT_SOURCE   = "益阳 TMY (CSWD/中国气象局, 28.59°N 112.33°E)"


@dataclass
class WeatherDataset:
    """12个月气象数据"""
    source:       str        = DEFAULT_SOURCE
    location:     str        = DEFAULT_LOCATION
    monthly_lux:  List[float] = field(
        default_factory=lambda: list(YIYANG_TMY_LUX))
    monthly_temp: List[float] = field(
        default_factory=lambda: list(YIYANG_TMY_TEMP))  # ℃

    def is_valid(self) -> bool:
        return (len(self.monthly_lux)  == 12
                and len(self.monthly_temp) == 12
                and all(v > 0 for v in self.monthly_lux))

    @property
    def annual_avg(self) -> float:
        return sum(self.monthly_lux) / 12

    @property
    def annual_avg_temp(self) -> float:
        return sum(self.monthly_temp) / 12

    @property
    def monthly_ghi(self) -> List[float]:
        """GHI W/m² (÷110 lm/W)"""
        return [v / 110.0 for v in self.monthly_lux]

    def summary_rows(self):
        """(月份, GHI W/m², 照度 lux, 温度 ℃)"""
        return [
            (MONTHS_ZH[i],
             f"{self.monthly_ghi[i]:.1f}",
             f"{self.monthly_lux[i]:.0f}",
             f"{self.monthly_temp[i]:.1f}")
            for i in range(12)
        ]


def default_dataset() -> WeatherDataset:
    return WeatherDataset()


# ── Excel / CSV 导入 ──────────────────────────────────────────────────────────

def load_from_excel(path: str) -> tuple[WeatherDataset, str]:
    """
    支持格式:
      列A=月份, 列B=照度(lux 或 GHI W/m²), 列C=温度(℃, 可选)
    GHI自动判断: max<2000则认为W/m², ×110转换
    """
    import os
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext in (".xlsx", ".xls", ".xlsm"):
            import openpyxl
            wb = openpyxl.load_workbook(path, data_only=True)
            ws = wb.active
            rows = [[cell.value for cell in row] for row in ws.iter_rows()]
        elif ext == ".csv":
            import csv
            with open(path, newline="", encoding="utf-8-sig") as f:
                rows = list(csv.reader(f))
        else:
            return WeatherDataset(), f"不支持的格式: {ext}"

        lux_vals, temp_vals = _parse_rows_with_temp(rows)
        if len(lux_vals) < 12:
            return WeatherDataset(), f"数据不足: 找到{len(lux_vals)}行，需12行"

        ds = WeatherDataset()
        ds.monthly_lux  = lux_vals[:12]
        ds.monthly_temp = temp_vals[:12] if len(temp_vals) == 12 else list(YIYANG_TMY_TEMP)
        ds.source       = f"文件导入: {os.path.basename(path)}"
        ds.location     = ""
        return ds, ""
    except Exception as e:
        return WeatherDataset(), str(e)


def _parse_rows_with_temp(rows: list):
    lux_list  = []
    temp_list = []
    for row in rows:
        if not row or row[0] is None:
            continue
        try:
            float(str(row[0]).strip().replace("月",""))
        except ValueError:
            continue
        nums = []
        for cell in row:
            try:
                nums.append(float(str(cell).strip()))
            except Exception:
                pass
        if len(nums) >= 2:
            lux_list.append(nums[1])
            if len(nums) >= 3:
                temp_list.append(nums[2])
        elif len(nums) == 1:
            lux_list.append(nums[0])

    if lux_list and max(lux_list) < 2000:
        lux_list = [v * 110.0 for v in lux_list]
    return lux_list, temp_list
