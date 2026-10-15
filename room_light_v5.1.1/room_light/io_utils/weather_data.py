"""
io_utils/weather_data.py — Weather dataset model + Excel/CSV/manual import  v2.11.0
默认气象数据: 湖南省益阳市 (CSWD/中国气象局, 28.59°N 112.33°E)
v2.4.0: 新增 monthly_temp (月均室外干球温度)
v2.11.0: 新增四川宜宾（夏热冬冷地区代表城市，日照数据为估算值，见下方说明）
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List
import json
from pathlib import Path

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

# ── 四川宜宾（v2.11.0 新增）──────────────────────────────────────────────────
# 宜宾属中亚热带湿润季风气候（夏热冬冷地区），年平均气温约17.5~18.9℃，
# 1月最冷月均7.8℃、7-8月最热月均26.8℃，年日照时数仅约1050~1128小时
# （四川盆地"日照少"的代表性城市，明显低于益阳/北京）。
# 月均温度直接取自公开气候资料（中国天气网城市概况），月均照度/GHI因缺乏
# 逐月实测TMY数据，按年日照时数比例（相对益阳TMY缩放，夏季6月因梅雨/对流
# 云雨偏多做适度下修）估算得到，非逐月实测值，仅用于本程序的参数化实验/
# 对比分析，不代表精确气象台站数据；如需更严谨的结果，建议后续替换为
# 中国气象局或EnergyPlus官方EPW的宜宾逐时数据。
YIBIN_TMY_LUX = [
     4900,  5150,  8100, 10900, 12200, 11000,
    15500, 15500, 10650,  9000,  5950,  4500,
]
YIBIN_TMY_GHI = [45, 47, 74, 99, 111, 100, 141, 141, 97, 82, 54, 41]
YIBIN_TMY_TEMP = [
    7.8, 10.5, 15.0, 19.5, 23.0, 25.5,
   26.8, 26.8, 22.5, 18.0, 13.5,  9.0,
]

DEFAULT_LOCATION = "湖南益阳"
DEFAULT_SOURCE   = "益阳 TMY (CSWD/中国气象局, 28.59°N 112.33°E)"


@dataclass
class WeatherDataset:
    """12个月气象数据"""
    source:       str        = DEFAULT_SOURCE
    location:     str        = DEFAULT_LOCATION
    latitude:     float | None = None
    longitude:    float | None = None
    data_period:  str = ""
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


_CITY_WEATHER_CACHE: dict | None = None


def _city_weather_payload() -> dict:
    global _CITY_WEATHER_CACHE
    if _CITY_WEATHER_CACHE is None:
        path = Path(__file__).with_name("city_weather_presets.json")
        _CITY_WEATHER_CACHE = json.loads(path.read_text(encoding="utf-8"))
    return _CITY_WEATHER_CACHE


def city_weather_names() -> List[str]:
    """Return packaged provincial-capital and major-city names."""
    return list(_city_weather_payload().get("cities", {}).keys())


def city_weather_metadata() -> dict:
    return dict(_city_weather_payload().get("metadata", {}))


def city_weather_dataset(city: str) -> WeatherDataset:
    """Create a dataset from the packaged NASA POWER climatology."""
    record = _city_weather_payload().get("cities", {}).get(str(city))
    if record is None:
        raise KeyError(f"城市气象库中没有：{city}")
    return WeatherDataset(
        source=str(record.get(
            "source", "NASA POWER 月气候平均值（2001—2020）"
        )),
        location=str(record.get("city", city)),
        monthly_lux=[float(value) for value in record["monthly_lux"]],
        monthly_temp=[float(value) for value in record["monthly_temp_c"]],
        latitude=float(record["latitude"]),
        longitude=float(record["longitude"]),
        data_period="2001—2020月气候平均值（非逐时TMY）",
    )


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
