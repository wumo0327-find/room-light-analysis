"""
io_utils/weather_fetcher.py
Fetch representative climate / irradiance data for a given location.

Strategy (in order of preference):
  1. Open-Meteo public API  – free, no key, JSON, good coverage
  2. Fallback: approximate CIE overcast illuminance from latitude

Units returned: W/m²  (global horizontal irradiance, monthly average)
"""
from __future__ import annotations
import json
import math
import threading
from typing import Callable, Dict, List, Optional

try:
    import requests
except ImportError:  # Offline geometry/project tools do not need networking.
    requests = None


# ── Open-Meteo endpoint ───────────────────────────────────────────────────────
_OM_CLIMATE_URL = (
    "https://climate-api.open-meteo.com/v1/climate"
    "?latitude={lat}&longitude={lon}"
    "&models=EC_Earth3P_HR"
    "&variables=shortwave_radiation_sum"
    "&start_date=2000-01-01&end_date=2009-12-31"
    "&disable_bias_correction=false"
)

_OM_GEOCODE_URL = (
    "https://geocoding-api.open-meteo.com/v1/search"
    "?name={city}&count=1&language=zh&format=json"
)

MONTHS_ZH = ["1月","2月","3月","4月","5月","6月",
             "7月","8月","9月","10月","11月","12月"]


# ── Public interface ──────────────────────────────────────────────────────────

class WeatherData:
    """Container for fetched weather info."""
    def __init__(self):
        self.location_name: str = ""
        self.latitude:  float = 0.0
        self.longitude: float = 0.0
        self.monthly_ghi: List[float] = []          # W/m²  (12 values)
        self.monthly_illuminance: List[float] = []  # klux  (12 values)
        self.annual_avg_ghi: float = 0.0
        self.source: str = ""
        self.raw: dict = {}

    def is_valid(self) -> bool:
        return len(self.monthly_ghi) == 12

    def summary_rows(self) -> List[tuple]:
        """Return list of (month, GHI W/m², Illuminance klux) for display."""
        rows = []
        for i, m in enumerate(MONTHS_ZH):
            ghi  = self.monthly_ghi[i] if self.monthly_ghi else 0
            illm = self.monthly_illuminance[i] if self.monthly_illuminance else 0
            rows.append((m, f"{ghi:.1f}", f"{illm:.1f}"))
        return rows


def fetch_weather_async(
    lat: float,
    lon: float,
    callback: Callable[[WeatherData, Optional[str]], None],
) -> None:
    """
    Start a background thread to fetch weather data.
    callback(data, error_msg) is called on completion (from the thread).
    """
    def _worker():
        data, err = _fetch_weather(lat, lon)
        callback(data, err)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()


def fetch_weather(lat: float, lon: float) -> tuple[WeatherData, Optional[str]]:
    """Synchronous version (blocks caller thread)."""
    return _fetch_weather(lat, lon)


# ── Internal implementation ───────────────────────────────────────────────────

def _fetch_weather(lat: float, lon: float) -> tuple[WeatherData, Optional[str]]:
    data = WeatherData()
    data.latitude  = lat
    data.longitude = lon

    try:
        if requests is None:
            raise RuntimeError("requests未安装，无法在线获取气象数据")
        url = _OM_CLIMATE_URL.format(lat=lat, lon=lon)
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        raw = resp.json()
        data.raw = raw

        # Open-Meteo returns daily sums in MJ/m²; convert to daily mean W/m²
        daily = raw.get("daily", {})
        sums  = daily.get("shortwave_radiation_sum", [])   # MJ/m²/day
        times = daily.get("time", [])

        if not sums:
            raise ValueError("No irradiance data returned")

        # Aggregate by calendar month
        monthly_sum   = [0.0] * 12
        monthly_count = [0]   * 12
        for t_str, val in zip(times, sums):
            if val is None:
                continue
            month_idx = int(t_str[5:7]) - 1          # "2000-01-15" → 0
            wh = val * 1000 / 24                      # MJ→Wh, daily mean W/m²
            monthly_sum[month_idx]   += wh
            monthly_count[month_idx] += 1

        data.monthly_ghi = [
            monthly_sum[i] / monthly_count[i] if monthly_count[i] else 0
            for i in range(12)
        ]
        # Convert GHI (W/m²) → horizontal illuminance (klux)
        # Using luminous efficacy ≈ 110 lm/W for daylight
        data.monthly_illuminance = [g * 110 / 1000 for g in data.monthly_ghi]
        data.annual_avg_ghi = sum(data.monthly_ghi) / 12
        data.source = "Open-Meteo Climate API (EC-Earth3P-HR, 2000-2009)"

        # Reverse-geocode city name
        data.location_name = _reverse_geocode(lat, lon)
        return data, None

    except Exception as exc:
        # Fallback: analytical estimate from latitude
        data = _fallback_estimate(lat, lon)
        err  = f"在线获取失败，使用估算值。原因：{exc}"
        return data, err


def _reverse_geocode(lat: float, lon: float) -> str:
    """Return a human-readable location name via Open-Meteo geocoding."""
    try:
        if requests is None:
            raise RuntimeError("requests未安装")
        # Use reverse geocoding approach via latitude/longitude search
        url  = (
            f"https://nominatim.openstreetmap.org/reverse"
            f"?lat={lat}&lon={lon}&format=json&accept-language=zh"
        )
        resp = requests.get(url, timeout=8,
                            headers={"User-Agent": "RoomLightApp/1.0"})
        resp.raise_for_status()
        j = resp.json()
        addr = j.get("address", {})
        city = addr.get("city") or addr.get("town") or addr.get("county", "")
        country = addr.get("country", "")
        return f"{city} {country}".strip()
    except Exception:
        return f"({lat:.2f}°N, {lon:.2f}°E)"


def _fallback_estimate(lat: float, lon: float) -> WeatherData:
    """
    Estimate monthly mean GHI from latitude using a simple sinusoidal model.
    Reference: Collares-Pereira & Rabl (1979) simplified.
    """
    data = WeatherData()
    data.latitude  = lat
    data.longitude = lon
    data.source    = "纬度估算 (离线模式)"
    data.location_name = f"({lat:.2f}°N, {lon:.2f}°E)"

    lat_r = math.radians(lat)
    ghis  = []
    # Day-of-year for mid-month
    mid_days = [15, 45, 74, 105, 135, 162, 198, 228, 258, 288, 318, 344]
    for d in mid_days:
        # Solar declination
        decl = math.radians(23.45 * math.sin(math.radians(360 * (284 + d) / 365)))
        # Sunrise hour angle
        cos_ws = -math.tan(lat_r) * math.tan(decl)
        cos_ws = max(-1.0, min(1.0, cos_ws))
        ws = math.acos(cos_ws)
        # Daily extraterrestrial radiation H0 (J/m²/day)
        Gsc = 1367.0  # W/m²
        H0  = (24 * 3600 / math.pi) * Gsc * (
            math.cos(lat_r) * math.cos(decl) * math.sin(ws)
            + ws * math.sin(lat_r) * math.sin(decl)
        )
        # Mean W/m² over 24 h, then apply clearness index Kt ≈ 0.55
        Kt  = 0.55
        H   = Kt * H0 / (24 * 3600)   # W/m² daily mean GHI
        ghis.append(H)

    data.monthly_ghi = ghis
    data.monthly_illuminance = [g * 110 / 1000 for g in ghis]
    data.annual_avg_ghi = sum(ghis) / 12
    return data
