"""In-memory bridge between the embedded BP draft and RoomLight models."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from typing import Optional

from bp_editor.model import DraftDocument
from bp_editor.rlproj_export import build_rlproj_data
from bp_editor.rlproj_import import load_rlproj_data
from core.complex_models import BuildingModel, Point2D, SpaceModel
from core.space_geometry import point_in_space, space_floor_area_mm2
from io_utils.project_io import building_from_dict, building_to_dict


BP_DRAFT_METADATA_KEY = "embedded_bp_draft_v1"


def document_from_building(
    building: BuildingModel,
) -> tuple[DraftDocument, str]:
    """Restore the exact saved BP draft or derive one from the first storey."""
    saved = building.metadata.get(BP_DRAFT_METADATA_KEY)
    if isinstance(saved, dict):
        try:
            return (
                DraftDocument.from_dict(deepcopy(saved)),
                "已恢复工程中保存的BP原始草图。",
            )
        except Exception:
            # A damaged optional draft must not make the rlproj unopenable.
            pass
    result = load_rlproj_data(
        {"building": building_to_dict(building)},
        source_name=f"{building.name or '未命名建筑'}.rlproj",
    )
    return result.document, result.notes


def _space_reference_point(space: SpaceModel) -> Point2D:
    loop = space.outer_loop()
    points = loop.points() if loop is not None else []
    if not points:
        return Point2D(0.0, 0.0)
    candidate = Point2D(
        sum(point.x for point in points) / len(points),
        sum(point.y for point in points) / len(points),
    )
    if point_in_space(candidate, space, include_boundary=True):
        return candidate
    return points[0]


def _space_match(
    new_space: SpaceModel,
    previous_spaces: list[SpaceModel],
    fallback_index: int,
) -> Optional[SpaceModel]:
    if not previous_spaces:
        return None
    point = _space_reference_point(new_space)
    containing = [
        space
        for space in previous_spaces
        if point_in_space(point, space, include_boundary=True)
    ]
    if containing:
        target_area = space_floor_area_mm2(new_space)
        return min(
            containing,
            key=lambda space: abs(
                space_floor_area_mm2(space) - target_area
            ),
        )
    if fallback_index < len(previous_spaces):
        return previous_spaces[fallback_index]
    return min(
        previous_spaces,
        key=lambda space: _space_reference_point(space).distance_to(point),
    )


def _opening_centre(wall, opening) -> Point2D:
    point = wall.point_at(opening.offset_mm + opening.width_mm / 2.0)
    if opening.plane_offset_mm <= 0.0:
        return point
    outward_x, outward_y = wall.outward_normal
    return Point2D(
        point.x + outward_x * opening.plane_offset_mm,
        point.y + outward_y * opening.plane_offset_mm,
    )


def _copy_opening_properties(
    new_space: SpaceModel,
    previous_space: SpaceModel,
) -> None:
    old_windows = [
        (wall, opening, _opening_centre(wall, opening))
        for wall in previous_space.wall_segments()
        for opening in wall.windows()
    ]
    if not old_windows:
        return
    unmatched = list(old_windows)
    for new_wall in new_space.wall_segments():
        for opening in new_wall.windows():
            centre = _opening_centre(new_wall, opening)
            candidates = unmatched or old_windows
            old_wall, old_opening, old_centre = min(
                candidates,
                key=lambda item: item[2].distance_to(centre),
            )
            tolerance = max(
                300.0,
                0.25 * max(opening.width_mm, old_opening.width_mm),
            )
            if old_centre.distance_to(centre) > tolerance:
                continue
            opening.visible_transmittance = (
                old_opening.visible_transmittance
            )
            opening.u_value = old_opening.u_value
            opening.solar_heat_gain_coefficient = (
                old_opening.solar_heat_gain_coefficient
            )
            opening.plane_offset_mm = old_opening.plane_offset_mm
            opening.name = old_opening.name or opening.name
            opening.metadata = {
                **deepcopy(old_opening.metadata),
                **opening.metadata,
            }
            if (old_wall, old_opening, old_centre) in unmatched:
                unmatched.remove((old_wall, old_opening, old_centre))


def building_from_document(
    document: DraftDocument,
    previous_building: BuildingModel,
    *,
    project_name: str,
    previous_active_space_id: Optional[str] = None,
) -> tuple[BuildingModel, str, object]:
    """
    Convert BP geometry and retain RoomLight-only climate/physics properties.

    The BP document is saved verbatim in building metadata so subsequent edits
    do not have to reconstruct dimensions, helper lines or drafting topology.
    """
    detached = DraftDocument.from_dict(document.to_dict())
    project_data, summary = build_rlproj_data(
        detached,
        project_name=project_name,
    )
    building = building_from_dict(project_data["building"])
    previous_spaces = (
        previous_building.storeys[0].spaces
        if previous_building.storeys else []
    )
    old_active = previous_building.get_space(previous_active_space_id)

    building.id = previous_building.id or building.id
    building.name = previous_building.name or project_name
    building.north_angle_deg = previous_building.north_angle_deg
    building.location = deepcopy(previous_building.location)
    building.metadata = {
        **deepcopy(previous_building.metadata),
        **building.metadata,
        BP_DRAFT_METADATA_KEY: detached.to_dict(),
    }
    building.metadata.pop("shading_target_window_keys", None)

    new_spaces = building.spaces()
    matched_by_new_id = {
        space.id: _space_match(space, previous_spaces, index)
        for index, space in enumerate(new_spaces)
    }
    match_counts = Counter(
        id(previous)
        for previous in matched_by_new_id.values()
        if previous is not None
    )
    for space in new_spaces:
        previous = matched_by_new_id[space.id]
        if previous is None:
            continue
        # Preserve a prior room name only for an unambiguous one-to-one match.
        # When one old room is subdivided into several BP faces, keep the new
        # 房间1/房间2 names instead of duplicating one label everywhere.
        if match_counts[id(previous)] == 1:
            space.name = previous.name
        space.material = deepcopy(previous.material)
        space.thermal = deepcopy(previous.thermal)
        space.shading = deepcopy(previous.shading)
        # BP regenerates analytical opening ids.  Global shading defaults stay
        # valid, but old per-window override keys would point at non-existent
        # openings and must be selected again in the building view.
        space.shading.overhang_overrides = {}
        space.shading.fin_overrides = {}
        space.metadata = {
            **deepcopy(previous.metadata),
            **space.metadata,
        }
        _copy_opening_properties(space, previous)

    if previous_building.storeys and building.storeys:
        old_storey = previous_building.storeys[0]
        building.storeys[0].name = old_storey.name
        building.storeys[0].elevation_mm = old_storey.elevation_mm
        building.storeys[0].metadata = {
            **deepcopy(old_storey.metadata),
            **building.storeys[0].metadata,
        }
        # BP v0.1 edits one plan/storey at a time.  Storeys not shown in the
        # embedded editor remain untouched instead of being silently deleted.
        building.storeys.extend(deepcopy(previous_building.storeys[1:]))

    active_space_id = None
    if old_active is not None:
        candidates = [
            new_id
            for new_id, previous in matched_by_new_id.items()
            if previous is old_active
        ]
        if candidates:
            active_space_id = candidates[0]
    if active_space_id is None and building.spaces():
        active_space_id = building.spaces()[0].id
    selected_ids = [space.id for space in building.spaces()]
    building.metadata["selected_space_ids"] = selected_ids
    return building, active_space_id or "", summary
