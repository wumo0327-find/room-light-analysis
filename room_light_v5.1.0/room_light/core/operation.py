"""Occupied-time lighting/HVAC energy and annual cost model.

The parameter experiment is a screening tool, not an hourly building-energy
simulator.  This module deliberately uses transparent monthly equations so
every number exported to a paper can be audited.  Only schedule-dependent
lighting and HVAC energy are compared; fixed multimedia/disinfection loads are
excluded because a shading retrofit does not change them.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Mapping

import numpy as np

from core.thermal import T_COMFORT_HIGH, T_COMFORT_LOW, ThermalResult


@dataclass(frozen=True)
class BuildingUseProfile:
    building_type: str = "幼儿园"
    room_use: str = "普通班级活动室"
    teaching_days_per_year: float = 200.0
    lighting_hours_per_day: float = 7.5
    hvac_hours_per_day: float = 6.0
    lighting_power_density_w_m2: float = 9.0
    electricity_tariff_yuan_kwh: float = 0.85
    cooling_cop: float = 3.2
    heating_cop: float = 3.0
    construction_service_life_years: float = 20.0
    # A 200-day kindergarten year distributed over teaching months.  User
    # changes to the annual total scale this pattern proportionally.
    monthly_teaching_days: tuple[float, ...] = (
        15, 15, 22, 21, 21, 20, 0, 0, 21, 22, 21, 22,
    )

    def to_dict(self) -> dict:
        return asdict(self)


KINDERGARTEN_CLASSROOM = BuildingUseProfile()
KINDERGARTEN_FUNCTION_ROOM = BuildingUseProfile(
    room_use="公共功能活动室",
    lighting_hours_per_day=2.5,
    hvac_hours_per_day=2.5,
)


def profile_from_mapping(values: Mapping | None) -> BuildingUseProfile:
    """Build a validated extensible use profile from GUI/exported settings."""
    source = dict(values or {})
    room_use = str(source.get("room_use", "普通班级活动室"))
    base = (
        KINDERGARTEN_FUNCTION_ROOM
        if room_use == "公共功能活动室"
        else KINDERGARTEN_CLASSROOM
    )
    allowed = set(base.to_dict())
    updates = {key: value for key, value in source.items() if key in allowed}
    if "monthly_teaching_days" in updates:
        monthly = tuple(float(value) for value in updates["monthly_teaching_days"])
        if len(monthly) != 12:
            monthly = base.monthly_teaching_days
        updates["monthly_teaching_days"] = monthly
    profile = replace(base, **updates)
    return profile


def _monthly_days(profile: BuildingUseProfile) -> np.ndarray:
    pattern = np.asarray(profile.monthly_teaching_days, dtype=float)
    pattern = np.maximum(pattern, 0.0)
    total = float(np.sum(pattern))
    if total <= 0.0:
        pattern = np.ones(12, dtype=float)
        total = 12.0
    return pattern * max(float(profile.teaching_days_per_year), 0.0) / total


def calculate_operation_metrics(
    *,
    daylight_score: float,
    thermal_result: ThermalResult,
    floor_area_m2: float,
    construction_cost_yuan: float = 0.0,
    profile: BuildingUseProfile | Mapping | None = None,
) -> dict:
    """Estimate annual lighting/HVAC electricity and comparable annual cost.

    Lighting uses the continuous daylight achievement score as the annual
    dimming/available-daylight fraction.  HVAC uses occupied degree-hours from
    monthly free-running temperatures and the calculated room heat-loss
    coefficient.  Both are constrained to the actual teaching schedule.
    """
    use = (
        profile
        if isinstance(profile, BuildingUseProfile)
        else profile_from_mapping(profile)
    )
    area = max(float(floor_area_m2), 0.0)
    days = _monthly_days(use)
    total_days = float(np.sum(days))
    full_lighting_kwh = (
        area * max(use.lighting_power_density_w_m2, 0.0) / 1000.0
        * max(use.lighting_hours_per_day, 0.0) * total_days
    )
    daylight_fraction = float(np.clip(daylight_score, 0.0, 1.0))
    lighting_kwh = full_lighting_kwh * (1.0 - daylight_fraction)

    indoor = np.asarray(thermal_result.T_in, dtype=float)
    if indoor.size != 12:
        indoor = np.resize(indoor, 12)
    heat_loss_w_k = max(
        float(thermal_result.H_envelope) + float(thermal_result.H_vent_avg),
        0.0,
    )
    occupied_hours = days * max(use.hvac_hours_per_day, 0.0)
    cooling_load_kwh = float(np.sum(
        np.maximum(indoor - T_COMFORT_HIGH, 0.0)
        * heat_loss_w_k * occupied_hours / 1000.0
    ))
    heating_load_kwh = float(np.sum(
        np.maximum(T_COMFORT_LOW - indoor, 0.0)
        * heat_loss_w_k * occupied_hours / 1000.0
    ))
    cooling_kwh = cooling_load_kwh / max(float(use.cooling_cop), 0.1)
    heating_kwh = heating_load_kwh / max(float(use.heating_cop), 0.1)
    hvac_kwh = cooling_kwh + heating_kwh
    operating_kwh = lighting_kwh + hvac_kwh
    tariff = max(float(use.electricity_tariff_yuan_kwh), 0.0)
    operating_cost = operating_kwh * tariff
    construction_cost = max(float(construction_cost_yuan), 0.0)
    life = max(float(use.construction_service_life_years), 1.0)
    annualized_construction = construction_cost / life
    annual_total = operating_cost + annualized_construction

    return {
        "building_type": use.building_type,
        "room_use": use.room_use,
        "teaching_days_per_year": total_days,
        "lighting_hours_per_day": use.lighting_hours_per_day,
        "hvac_hours_per_day": use.hvac_hours_per_day,
        "lighting_power_density_w_m2": use.lighting_power_density_w_m2,
        "electricity_tariff_yuan_kwh": tariff,
        "cooling_cop": use.cooling_cop,
        "heating_cop": use.heating_cop,
        "annual_lighting_full_kwh": full_lighting_kwh,
        "annual_lighting_kwh": lighting_kwh,
        "annual_cooling_kwh": cooling_kwh,
        "annual_heating_kwh": heating_kwh,
        "annual_hvac_kwh": hvac_kwh,
        "annual_operating_kwh": operating_kwh,
        "annual_operating_cost": operating_cost,
        "construction_cost": construction_cost,
        "construction_service_life_years": life,
        "annualized_construction_cost": annualized_construction,
        "annual_total_cost": annual_total,
        "lifecycle_total_cost": operating_cost * life + construction_cost,
        # Existing Pareto/plot code reads the generic cost column.
        "cost": annual_total,
        "operation_method": (
            "教学时段月均准稳态：照明按Cd调光折减；空调按自由运行室温"
            "超出18~26℃的占用度时及COP估算；不含不受遮阳影响的固定设备电耗"
        ),
        "cost_basis": (
            "年综合费用=年照明电费+年空调电费+遮阳工程造价/使用年限"
        ),
    }


def refresh_record_daylight_cost(
    record: Mapping,
    profile: BuildingUseProfile | Mapping | None = None,
) -> dict:
    """Refresh lighting and total cost after a higher-resolution Cd pass."""
    result = dict(record)
    use = (
        profile
        if isinstance(profile, BuildingUseProfile)
        else profile_from_mapping(profile)
    )
    full = float(result.get("annual_lighting_full_kwh", 0.0))
    daylight = float(np.clip(result.get("daylight_score", 0.0), 0.0, 1.0))
    lighting = full * (1.0 - daylight)
    hvac = float(result.get("annual_hvac_kwh", 0.0))
    operating = lighting + hvac
    operating_cost = operating * max(use.electricity_tariff_yuan_kwh, 0.0)
    annualized = float(result.get("annualized_construction_cost", 0.0))
    result.update({
        "annual_lighting_kwh": lighting,
        "annual_operating_kwh": operating,
        "annual_operating_cost": operating_cost,
        "annual_total_cost": operating_cost + annualized,
        "cost": operating_cost + annualized,
    })
    return result
