"""
io_utils/weather_data.py — Weather dataset model + Excel/CSV/manual import
默认气象数据: 湖南省益阳市 (CSWD/中国气象局)
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List
import math

MONTHS_ZH = ["1月","2月","3月","4月","5月","6月",
              "7月","8月","9月","10月","11月","12月"]

# ── 益阳 TMY 气象参考值（程序默认值） ─────────────────────────────────────────
# 湖南省益阳市  纬度: 28.59°N  经度: 112.33°E
# 数据来源: 中国气象局标准气象数据集 (CSWD)，代表性气象年 (TMY)
# 室外水平照度 = GHI月均值(W/m²) × 110 lm/W（昼光光效系数，全阴天近似）
# 月均 GHI (W/m²): 68, 72, 108, 141, 163, 166, 196, 188, 149, 120, 83, 63
# 益阳属中亚热带季风湿润气候，春夏多阴雨，7-8月伏旱辐射最强
YIYANG_TMY_LUX = [
     7480,  7920, 11880, 15510, 17930, 18260,
    21560, 20680, 16390, 13200,  9130,  6930,
]

# 北京 TMY 参考值（可选填充）  单位: lux
BEIJING_TMY_LUX = [
    20100, 27400, 36800, 48500, 55200, 58600,
    52400, 51300, 44700, 33900, 22500, 17800,
]

DEFAULT_LOCATION   = "湖南益阳"
DEFAULT_SOURCE     = "益阳 TMY (CSWD/中国气象局, 28.59°N 112.33°E)"
DEFAULT_TMY_LUX    = YIYANG_TMY_LUX


@dataclass
class WeatherDataset:
    """12个月的室外水平照度数据，单位 lux"""
    source:      str        = DEFAULT_SOURCE
    location:    str        = DEFAULT_LOCATION
    monthly_lux: List[float] = field(
        default_factory=lambda: list(DEFAULT_TMY_LUX))

    def is_valid(self) -> bool:
        return (len(self.monthly_lux) == 12
                and all(v > 0 for v in self.monthly_lux))

    @property
    def annual_avg(self) -> float:
        return sum(self.monthly_lux) / max(1, len(self.monthly_lux))

    @property
    def monthly_ghi(self) -> List[float]:
        """GHI W/m² 估算 (÷110 lm/W)"""
        return [v / 110.0 for v in self.monthly_lux]

    def summary_rows(self):
        return [
            (MONTHS_ZH[i],
             f"{self.monthly_ghi[i]:.1f}",
             f"{self.monthly_lux[i]:.0f}")
            for i in range(12)
        ]


def default_dataset() -> WeatherDataset:
    """返回益阳默认气象数据集"""
    return WeatherDataset()


# ── Excel / CSV 导入 ──────────────────────────────────────────────────────────

def load_from_excel(path: str) -> tuple[WeatherDataset, str]:
    """
    从 Excel / CSV 读取气象数据。
    格式 A: 月份, 室外照度(lux)
    格式 B: 月份, GHI(W/m²)  → 自动 ×110
    第一行文字标题自动跳过。
    返回 (dataset, error_msg)；error_msg="" 表示成功。
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

        lux_vals = _parse_rows(rows)
        if len(lux_vals) < 12:
            return WeatherDataset(), f"数据不足: 找到 {len(lux_vals)} 行，需要 12 行"

        ds = WeatherDataset()
        ds.monthly_lux = lux_vals[:12]
        ds.source      = f"文件导入: {os.path.basename(path)}"
        ds.location    = ""
        return ds, ""
    except Exception as e:
        return WeatherDataset(), str(e)


def _parse_rows(rows: list) -> List[float]:
    data_rows = []
    for row in rows:
        if not row or row[0] is None:
            continue
        try:
            float(str(row[0]).strip().replace("月","").replace("月份",""))
        except ValueError:
            continue
        vals = []
        for cell in row:
            try:
                vals.append(float(str(cell).strip()))
            except Exception:
                pass
        if vals:
            data_rows.append(vals[-1])

    if not data_rows:
        return []
    if max(data_rows) < 2000:
        data_rows = [v * 110.0 for v in data_rows]
    return data_rows
