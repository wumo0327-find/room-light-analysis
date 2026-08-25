"""Generate the packaged China city climatology from NASA POWER.

This development utility is not used at application runtime.  It keeps the
embedded dataset reproducible and records the exact source period/endpoint.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path


CITIES = {
    # Provincial-level capitals / seats
    "北京": (39.90, 116.41), "天津": (39.08, 117.20),
    "上海": (31.23, 121.47), "重庆": (29.56, 106.55),
    "石家庄": (38.04, 114.51), "太原": (37.87, 112.55),
    "呼和浩特": (40.84, 111.75), "沈阳": (41.80, 123.43),
    "长春": (43.82, 125.32), "哈尔滨": (45.80, 126.53),
    "南京": (32.06, 118.80), "杭州": (30.27, 120.15),
    "合肥": (31.82, 117.23), "福州": (26.08, 119.30),
    "南昌": (28.68, 115.86), "济南": (36.65, 117.12),
    "郑州": (34.75, 113.62), "武汉": (30.59, 114.30),
    "长沙": (28.23, 112.94), "广州": (23.13, 113.26),
    "南宁": (22.82, 108.37), "海口": (20.04, 110.20),
    "成都": (30.57, 104.07), "贵阳": (26.65, 106.63),
    "昆明": (25.04, 102.71), "拉萨": (29.65, 91.17),
    "西安": (34.34, 108.94), "兰州": (36.06, 103.83),
    "西宁": (36.62, 101.78), "银川": (38.49, 106.23),
    "乌鲁木齐": (43.83, 87.62), "香港": (22.32, 114.17),
    "澳门": (22.20, 113.54), "台北": (25.03, 121.57),
    # Major non-capital tier-1/tier-2 and regional cities
    "深圳": (22.54, 114.06), "苏州": (31.30, 120.58),
    "宁波": (29.87, 121.55), "青岛": (36.07, 120.38),
    "大连": (38.91, 121.61), "厦门": (24.48, 118.09),
    "无锡": (31.49, 120.31), "佛山": (23.02, 113.12),
    "东莞": (23.02, 113.75), "珠海": (22.27, 113.58),
    "温州": (27.99, 120.70), "泉州": (24.87, 118.68),
    "烟台": (37.46, 121.45), "南通": (31.98, 120.89),
    "常州": (31.81, 119.97), "徐州": (34.26, 117.20),
    "金华": (29.08, 119.65), "绍兴": (30.00, 120.58),
    "惠州": (23.11, 114.42), "中山": (22.52, 113.39),
    "三亚": (18.25, 109.51), "桂林": (25.27, 110.29),
    "洛阳": (34.62, 112.45), "唐山": (39.63, 118.18),
    "保定": (38.87, 115.48), "临沂": (35.10, 118.36),
    "潍坊": (36.71, 119.16), "嘉兴": (30.75, 120.75),
    "盐城": (33.35, 120.16), "扬州": (32.39, 119.41),
    "宜宾": (28.75, 104.64), "益阳": (28.59, 112.33),
}

MONTH_KEYS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN",
              "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
ENDPOINT = (
    "https://power.larc.nasa.gov/api/temporal/climatology/point"
)


def fetch_city(name: str, latitude: float, longitude: float) -> dict:
    query = urllib.parse.urlencode({
        "parameters": "T2M,ALLSKY_SFC_SW_DWN",
        "community": "SB", "longitude": longitude, "latitude": latitude,
        "format": "JSON", "start": 2001, "end": 2020,
    })
    request = urllib.request.Request(
        f"{ENDPOINT}?{query}", headers={"User-Agent": "RoomLight/4.4.1"}
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.load(response)
    parameters = payload["properties"]["parameter"]
    temperatures = [float(parameters["T2M"][key]) for key in MONTH_KEYS]
    solar_daily = [
        float(parameters["ALLSKY_SFC_SW_DWN"][key]) for key in MONTH_KEYS
    ]
    # For the Sustainable Buildings (SB) community, the climatology endpoint
    # returns ALLSKY_SFC_SW_DWN directly in W/m².
    ghi = solar_daily
    return {
        "city": name, "latitude": latitude, "longitude": longitude,
        "monthly_temp_c": [round(value, 2) for value in temperatures],
        "monthly_ghi_w_m2": [round(value, 2) for value in ghi],
        "monthly_lux": [round(value * 110.0) for value in ghi],
        "source": "NASA POWER 月气候平均值（2001—2020）",
    }


def main() -> None:
    result = {}
    for index, (name, (latitude, longitude)) in enumerate(CITIES.items(), 1):
        print(f"[{index}/{len(CITIES)}] {name}", flush=True)
        for attempt in range(3):
            try:
                result[name] = fetch_city(name, latitude, longitude)
                break
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(2.0 * (attempt + 1))
        time.sleep(0.15)
    output = Path(__file__).resolve().parents[1] / "io_utils" / "city_weather_presets.json"
    output.write_text(
        json.dumps({
            "metadata": {
                "provider": "NASA POWER",
                "period": "2001-2020",
                "endpoint": ENDPOINT,
                "solar_parameter": "ALLSKY_SFC_SW_DWN",
                "temperature_parameter": "T2M",
                "lux_conversion": "GHI W/m² × 110 lm/W",
                "note": "月气候平均值，用于本程序月均筛选；不是逐时EPW/TMY。",
            },
            "cities": result,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(output)


if __name__ == "__main__":
    main()
