"""Traceable BP-to-RL geometry audit used by the v4 building view."""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math

from bp_editor.model import DraftDocument
from core.complex_models import BuildingModel
from core.space_geometry import space_floor_area_mm2, validate_space
from io_utils.bp_bridge import BP_DRAFT_METADATA_KEY, document_from_building


@dataclass
class ModelAuditReport:
    source: str
    fingerprint: str
    bp_walls: int
    bp_windows: int
    bp_rooms: int
    rl_spaces: int
    rl_wall_segments: int
    mapped_bp_walls: int
    rl_windows: int
    net_area_m2: float
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues

    @property
    def status(self) -> str:
        if self.issues:
            return "错误"
        if self.warnings:
            return "需复核"
        return "通过"

    def summary(self) -> str:
        text = (
            f"模型核验：{self.status}｜指纹 {self.fingerprint}\n"
            f"BP：墙{self.bp_walls}、窗{self.bp_windows}、房间{self.bp_rooms}；"
            f"RL：墙段{self.rl_wall_segments}（映射BP墙{self.mapped_bp_walls}）、"
            f"房间{self.rl_spaces}、唯一窗{self.rl_windows}；"
            f"净计算面积 {self.net_area_m2:.2f}㎡"
        )
        details = self.issues or self.warnings
        if details:
            text += "\n" + "；".join(details[:3])
            if len(details) > 3:
                text += f"；另有{len(details) - 3}项"
        return text


def _fingerprint(document: DraftDocument) -> str:
    payload = {
        "walls": sorted(
            (
                wall.id,
                round(wall.start.x, 4), round(wall.start.y, 4),
                round(wall.end.x, 4), round(wall.end.y, 4),
                round(wall.height_mm, 4), round(wall.width_mm, 4), wall.axis,
            )
            for wall in document.walls
        ),
        "windows": sorted(
            (
                window.id, window.wall_id, round(window.offset_mm, 4),
                round(window.width_mm, 4), round(window.sill_height_mm, 4),
                round(window.height_mm, 4),
            )
            for window in document.windows
        ),
        "railings": sorted(
            (
                railing.id,
                round(railing.start.x, 4), round(railing.start.y, 4),
                round(railing.end.x, 4), round(railing.end.y, 4),
                round(railing.height_mm, 4), round(railing.width_mm, 4),
                railing.material,
            )
            for railing in document.railings
        ),
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:12].upper()


def _loop_signature(points) -> str:
    keys = [(round(float(point.x), 3), round(float(point.y), 3)) for point in points]
    if not keys:
        return ""
    variants = []
    for sequence in (keys, list(reversed(keys))):
        variants.extend(
            tuple(sequence[index:] + sequence[:index])
            for index in range(len(sequence))
        )
    return "|".join(f"{x:g},{y:g}" for x, y in min(variants))


def audit_building_geometry(building: BuildingModel) -> ModelAuditReport:
    exact = isinstance(building.metadata.get(BP_DRAFT_METADATA_KEY), dict)
    document, _note = document_from_building(building)
    detached = DraftDocument.from_dict(document.to_dict())
    issues: list[str] = []
    warnings: list[str] = []
    if not exact:
        warnings.append("工程未保存BP原始草图，当前画面由RL几何反向重建")

    for wall in detached.walls:
        values = (
            wall.start.x, wall.start.y, wall.end.x, wall.end.y,
            wall.height_mm, wall.width_mm,
        )
        if not all(math.isfinite(float(value)) for value in values):
            issues.append(f"墙体{wall.id}含非有限数值")
        if wall.length_mm <= 1.0 or wall.width_mm <= 0.0 or wall.height_mm <= 0.0:
            issues.append(f"墙体{wall.id}尺寸无效")
    for window in detached.windows:
        wall = detached.wall_by_id(window.wall_id)
        if wall is None:
            issues.append(f"窗体{window.id}丢失宿主墙")
            continue
        if (
            window.offset_mm < -1e-6
            or window.end_offset_mm > wall.length_mm + 1e-6
        ):
            issues.append(f"窗体{window.id}越出宿主墙")
        if window.sill_height_mm < 0.0 or window.height_mm <= 0.0:
            issues.append(f"窗体{window.id}竖向尺寸无效")

    try:
        detached.normalise_wall_topology()
        faces = detached.recognised_rooms()
    except Exception as exc:
        faces = []
        issues.append(f"BP拓扑无法标准化：{exc}")

    spaces = building.spaces()
    for space in spaces:
        for item in validate_space(space):
            target = issues if item.severity == "error" else warnings
            target.append(f"{space.name}：{item.message}")
        if not space.analysis_floor_loops:
            warnings.append(f"{space.name}尚未保存实体墙内表面净空间")
        if space.metadata.get("boundary_conditions_explicit") is False:
            warnings.append(
                f"{space.name}尚未确认屋面/下部地面热边界条件"
            )

    face_signatures = [_loop_signature(face.points) for face in faces]
    for space in spaces:
        source_index = space.metadata.get("source_face_index")
        if source_index is None:
            continue
        try:
            source_index = int(source_index)
        except (TypeError, ValueError):
            issues.append(f"{space.name}的BP房间索引无效")
            continue
        if source_index < 0 or source_index >= len(face_signatures):
            issues.append(f"{space.name}指向不存在的BP房间{source_index}")
            continue
        outer = space.outer_loop()
        rl_signature = _loop_signature(outer.points() if outer else [])
        if rl_signature != face_signatures[source_index]:
            issues.append(f"{space.name}的RL墙轴线边界与BP房间不一致")
        saved_signature = str(space.metadata.get("source_face_signature", ""))
        if saved_signature and saved_signature != face_signatures[source_index]:
            issues.append(f"{space.name}保存的BP房间签名已失效")

    rl_walls = [wall for space in spaces for wall in space.wall_segments()]
    source_wall_ids = {
        str(wall.metadata.get("source_bp_wall_id"))
        for wall in rl_walls
        if wall.metadata.get("source_bp_wall_id")
    }
    bp_wall_ids = {wall.id for wall in detached.walls}
    unknown_wall_ids = source_wall_ids - bp_wall_ids
    if unknown_wall_ids:
        issues.append(f"RL有{len(unknown_wall_ids)}个墙段指向不存在的BP墙")
    unmapped_wall_ids = bp_wall_ids - source_wall_ids
    if exact and unmapped_wall_ids:
        unmapped_walls = [
            wall for wall in detached.walls if wall.id in unmapped_wall_ids
        ]
        preview = "、".join(
            f"{wall.id}[({wall.start.x:.0f},{wall.start.y:.0f})→"
            f"({wall.end.x:.0f},{wall.end.y:.0f})]"
            for wall in unmapped_walls[:3]
        )
        if len(unmapped_walls) > 3:
            preview += f"等{len(unmapped_walls)}段"
        issues.append(
            f"BP有{len(unmapped_wall_ids)}段墙未形成任何可计算房间边界；"
            f"位置：{preview}；请删除、补全围合或改为明确的栏杆/遮挡构件"
        )

    source_window_ids = {
        str(opening.metadata.get("source_bp_window_id"))
        for space in spaces
        for wall in space.wall_segments()
        for opening in wall.windows()
        if opening.metadata.get("source_bp_window_id")
    }
    if len(faces) != len(spaces):
        issues.append(f"BP识别{len(faces)}个房间，但RL记录{len(spaces)}个房间")
    if source_window_ids and len(source_window_ids) != len(detached.windows):
        issues.append(
            f"BP有{len(detached.windows)}扇窗，但RL仅映射"
            f"{len(source_window_ids)}扇唯一窗"
        )
    bp_windows = {window.id: window for window in detached.windows}
    for wall in rl_walls:
        for opening in wall.windows():
            source_id = opening.metadata.get("source_bp_window_id")
            if not source_id:
                continue
            source_window = bp_windows.get(str(source_id))
            if source_window is None:
                issues.append(f"RL窗{opening.id}指向不存在的BP窗{source_id}")
                continue
            source_wall = detached.wall_by_id(source_window.wall_id)
            if source_wall is None:
                issues.append(f"BP窗{source_id}丢失宿主墙")
                continue
            bp_centre = source_wall.point_at(
                source_window.offset_mm + source_window.width_mm / 2.0
            )
            rl_centre = wall.point_at(opening.offset_mm + opening.width_mm / 2.0)
            if bp_centre.distance_to(type(bp_centre)(rl_centre.x, rl_centre.y)) > 1.0:
                issues.append(f"RL窗{opening.id}与BP窗{source_id}平面位置不一致")
            if (
                abs(opening.width_mm - source_window.width_mm) > 1.0
                or abs(opening.sill_height_mm - source_window.sill_height_mm) > 1.0
                or abs(opening.height_mm - source_window.height_mm) > 1.0
            ):
                issues.append(f"RL窗{opening.id}与BP窗{source_id}尺寸不一致")

    return ModelAuditReport(
        source="BP原始草图" if exact else "RL反向重建草图",
        fingerprint=_fingerprint(document),
        bp_walls=len(document.walls),
        bp_windows=len(document.windows),
        bp_rooms=len(faces),
        rl_spaces=len(spaces),
        rl_wall_segments=len(rl_walls),
        mapped_bp_walls=len(source_wall_ids),
        rl_windows=len(source_window_ids) if source_window_ids else sum(
            len(wall.windows()) for space in spaces for wall in space.wall_segments()
        ),
        net_area_m2=sum(space_floor_area_mm2(space) for space in spaces) / 1_000_000.0,
        issues=list(dict.fromkeys(issues)),
        warnings=list(dict.fromkeys(warnings)),
    )
