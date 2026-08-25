"""
core/complex_thermal.py — Arbitrary-space single-zone thermal engine  v4.2.2

The physical model remains the monthly lumped single-zone balance used by
``core.thermal``.  Geometry inputs are replaced with actual polygon floor area,
exterior wall segments, per-wall azimuth and wall-attached openings.
"""
from __future__ import annotations

import math
from typing import Callable, List, Optional

import numpy as np

from core import solar
from core.complex_models import SpaceModel, WallSegment
from core.space_geometry import space_floor_area_mm2, validate_space
from core.thermal import (
    CP_AIR,
    H_OUT,
    RHO_AIR,
    T_COMFORT_HIGH,
    T_COMFORT_LOW,
    ThermalResult,
    _ORIENT_FACTORS,
    _ewm_smooth,
    n_ach_to_H,
)


def wall_azimuth_deg(
    wall: WallSegment,
    north_angle_deg: float = 0.0,
) -> float:
    """
    Exterior wall-normal azimuth, degrees clockwise from true north.

    Model coordinates use +X east and +Y north when north_angle is zero.
    """
    outward_x, outward_y = wall.outward_normal
    local_azimuth = math.degrees(math.atan2(outward_x, outward_y)) % 360.0
    return (local_azimuth + north_angle_deg) % 360.0


def orientation_factors_for_azimuth(azimuth_deg: float) -> np.ndarray:
    """
    Circular piecewise-linear interpolation of the legacy cardinal factors.

    Cardinal walls remain exactly backward compatible while oblique walls vary
    continuously instead of being forced into the nearest compass category.
    """
    azimuth = azimuth_deg % 360.0
    cardinals = [
        (0.0, np.asarray(_ORIENT_FACTORS["north"], dtype=float)),
        (90.0, np.asarray(_ORIENT_FACTORS["east"], dtype=float)),
        (180.0, np.asarray(_ORIENT_FACTORS["south"], dtype=float)),
        (270.0, np.asarray(_ORIENT_FACTORS["west"], dtype=float)),
        (360.0, np.asarray(_ORIENT_FACTORS["north"], dtype=float)),
    ]
    for (start_angle, start_values), (end_angle, end_values) in zip(
        cardinals,
        cardinals[1:],
    ):
        if start_angle <= azimuth <= end_angle:
            fraction = (azimuth - start_angle) / (end_angle - start_angle)
            return start_values * (1.0 - fraction) + end_values * fraction
    return np.asarray(_ORIENT_FACTORS["north"], dtype=float)


def monthly_profile_angles(
    latitude_deg: float,
    wall_azimuth: float,
):
    values = []
    for day_of_year in solar.MID_MONTH_DOY:
        declination = solar.declination_deg(day_of_year)
        altitude = solar.solar_noon_altitude_deg(
            latitude_deg,
            declination,
        )
        sun_azimuth = solar.solar_noon_azimuth_deg(
            latitude_deg,
            declination,
        )
        values.append(solar.profile_angle_deg(
            altitude,
            sun_azimuth,
            wall_azimuth,
        ))
    return values


def exterior_corner_count(space: SpaceModel) -> int:
    """Count real exterior direction changes, not collinear BP splits."""
    count = 0
    for loop in space.boundary_loops:
        segments = loop.segments
        if len(segments) < 2:
            continue
        for index, current in enumerate(segments):
            previous = segments[index - 1]
            if (
                current.boundary_type not in {"exterior", "ground"}
                or previous.boundary_type not in {"exterior", "ground"}
                or current.analysis_length_mm <= 1e-9
                or previous.analysis_length_mm <= 1e-9
            ):
                continue
            ax, ay = previous.direction
            bx, by = current.direction
            cross = ax * by - ay * bx
            dot = ax * bx + ay * by
            if abs(cross) > 1e-8 or dot < 0.0:
                count += 1
    return count


def compute_complex_thermal(
    space: SpaceModel,
    monthly_ghi: List[float],
    monthly_temp: List[float],
    latitude_deg: float = 28.59,
    north_angle_deg: float = 0.0,
    progress_cb: Optional[Callable[[int], None]] = None,
) -> ThermalResult:
    """Calculate one validated arbitrary-polygon space as a single zone."""
    errors = [
        issue for issue in validate_space(space)
        if issue.severity == "error"
    ]
    if errors:
        raise ValueError(
            "复杂空间几何未通过校验："
            + "；".join(issue.message for issue in errors)
        )
    if len(monthly_ghi) != 12 or len(monthly_temp) != 12:
        raise ValueError("热环境计算需要12个月的辐射和室外温度数据。")

    result = ThermalResult()
    result.method = (
        "12个月月均准稳态单区集总热平衡（非逐时） + 任意多边形围护结构 + "
        "实体墙内表面净面积 + 倾斜挑檐剖面阴影 v4.2.2"
    )
    result.geometry_basis = (
        "BP wall/opening host axes + physical inner-wall net floor"
        if space.analysis_floor_loops
        else "legacy boundary axes (no saved physical inner-wall floor)"
    )
    result.net_floor_area_m2 = space_floor_area_mm2(space) / 1_000_000.0
    thermal = space.thermal
    shading = space.shading
    height_m = space.height_mm / 1000.0
    floor_area = space_floor_area_mm2(space) / 1_000_000.0
    volume = floor_area * height_m

    exterior_walls = [
        wall
        for wall in space.wall_segments()
        if wall.boundary_type in {"exterior", "ground"}
    ]
    wall_records = []
    exterior_windows = []
    for wall in exterior_walls:
        gross_area = wall.analysis_length_mm * space.height_mm / 1_000_000.0
        openings = wall.openings
        opening_area = sum(opening.area_m2 for opening in openings)
        net_area = max(0.0, gross_area - opening_area)
        u_wall = thermal.U_wall if wall.u_value is None else wall.u_value
        azimuth = wall_azimuth_deg(wall, north_angle_deg)
        factors = orientation_factors_for_azimuth(azimuth)
        wall_records.append((
            wall,
            net_area,
            float(u_wall),
            azimuth,
            factors,
        ))
        exterior_windows.extend(
            (wall, opening, azimuth, factors)
            for opening in wall.windows()
        )

    wall_net_total = sum(record[1] for record in wall_records)
    window_area_total = sum(
        opening.area_m2
        for _wall, opening, _azimuth, _factors in exterior_windows
    )
    exterior_perimeter_m = sum(
        wall.analysis_length_mm / 1000.0 for wall in exterior_walls
    )
    exterior_corner_count_value = exterior_corner_count(space)
    bridge_length = (
        exterior_corner_count_value * height_m
        + 2.0 * exterior_perimeter_m
    )
    h_bridge = thermal.psi_edge * bridge_length

    ua_wall = sum(
        net_area * u_value
        for _wall, net_area, u_value, _azimuth, _factors in wall_records
    )
    ua_window = sum(
        opening.area_m2 * (
            thermal.U_win
            if opening.u_value is None
            else opening.u_value
        )
        for _wall, opening, _azimuth, _factors in exterior_windows
    )
    ua_roof = thermal.U_roof * floor_area if space.roof_exposed else 0.0
    ua_floor = thermal.U_floor * floor_area if space.floor_exposed else 0.0
    h_envelope = ua_wall + ua_window + ua_roof + ua_floor + h_bridge
    h_ventilation = n_ach_to_H(thermal.n_ach, volume)

    window_sc_monthly = {}
    for wall, opening, azimuth, _factors in exterior_windows:
        profiles = monthly_profile_angles(latitude_deg, azimuth)
        shading_key = str(
            opening.metadata.get("legacy_window_id", opening.id)
        )
        depth, gap, tilt = shading.get_overhang_for(shading_key)
        base_sc = (
            thermal.SC_glass
            if opening.solar_heat_gain_coefficient is None
            else opening.solar_heat_gain_coefficient
        )
        window_sc_monthly[opening.id] = [
            base_sc * (
                1.0
                - shading.beam_shade_fraction(
                    profiles[month],
                    opening.height_mm,
                    depth_mm=depth,
                    gap_mm=gap,
                    tilt_deg=tilt,
                )
                * (1.0 - shading.effective_shaded_solar_residual())
            )
            for month in range(12)
        ]

    sc_monthly = np.full(12, thermal.SC_glass, dtype=float)
    if window_area_total > 1e-12:
        for month in range(12):
            sc_monthly[month] = sum(
                opening.area_m2 * window_sc_monthly[opening.id][month]
                for _wall, opening, _azimuth, _factors in exterior_windows
            ) / window_area_total

    wall_mass = (
        thermal.wall_thickness_mm / 1000.0
        * wall_net_total
        * thermal.wall_density
    )
    wall_capacity = wall_mass * thermal.wall_specific_c
    tau_hours = wall_capacity / max(h_envelope * 3600.0, 1e-12)
    alpha_smooth = 1.0 - math.exp(-720.0 / max(tau_hours, 1.0))

    indoor = np.zeros(12)
    solar_gain = np.zeros(12)
    wall_solar_gain = np.zeros(12)
    internal_gain = np.zeros(12)
    ventilation_loss = np.zeros(12)
    q_internal = (
        thermal.q_people
        + thermal.q_equipment
        + thermal.q_lighting
    ) * floor_area

    for month in range(12):
        if progress_cb:
            progress_cb(month)
        outdoor_temperature = float(monthly_temp[month])
        ghi = float(monthly_ghi[month])

        q_solar = 0.0
        for _wall, opening, _azimuth, factors in exterior_windows:
            incident = ghi * factors[month]
            q_solar += (
                opening.area_m2
                * window_sc_monthly[opening.id][month]
                * incident
                * thermal.eta_frame
            )

        # Unlike the legacy fixed 0.60 multiplier, each oblique exterior wall
        # uses its actual azimuth factor.
        q_wall_solar = sum(
            u_value
            * net_area
            * thermal.wall_solar_abs
            * (ghi * factors[month])
            / H_OUT
            for _wall, net_area, u_value, _azimuth, factors in wall_records
        )

        denominator = h_envelope + h_ventilation
        if denominator <= 1e-12:
            raise ValueError("空间围护结构和通风总热导必须大于0。")
        indoor_temperature = outdoor_temperature + (
            q_solar + q_wall_solar + q_internal
        ) / denominator
        q_ventilation = h_ventilation * (
            indoor_temperature - outdoor_temperature
        )

        indoor[month] = indoor_temperature
        solar_gain[month] = q_solar
        wall_solar_gain[month] = q_wall_solar
        internal_gain[month] = q_internal
        ventilation_loss[month] = q_ventilation

    smoothed = _ewm_smooth(indoor, alpha_smooth)
    result.T_in = smoothed
    result.T_out = np.asarray(monthly_temp, dtype=float)
    result.Q_solar = solar_gain
    result.Q_wall_solar = wall_solar_gain
    result.Q_int = internal_gain
    result.Q_vent = ventilation_loss
    result.H_envelope = h_envelope
    result.H_vent_avg = h_ventilation
    result.UA_wall = ua_wall
    result.UA_win = ua_window
    result.UA_roof = ua_roof
    result.UA_floor = ua_floor
    result.H_bridge = h_bridge
    result.SC_effective_monthly = sc_monthly
    result.SC_effective = float(np.mean(sc_monthly))

    overheating = [
        temperature for temperature in smoothed
        if temperature > T_COMFORT_HIGH
    ]
    underheating = [
        temperature for temperature in smoothed
        if temperature < T_COMFORT_LOW
    ]
    result.overheat_months = len(overheating)
    result.underheat_months = len(underheating)
    result.comfort_months = 12 - len(overheating) - len(underheating)
    result.overheat_ratio = len(overheating) / 12.0
    result.underheat_ratio = len(underheating) / 12.0
    result.T_in_annual_avg = float(np.mean(smoothed))
    result.overheat_severity = (
        float(np.mean([
            temperature - T_COMFORT_HIGH for temperature in overheating
        ]))
        if overheating else 0.0
    )
    result.underheat_severity = (
        float(np.mean([
            T_COMFORT_LOW - temperature for temperature in underheating
        ]))
        if underheating else 0.0
    )
    return result



