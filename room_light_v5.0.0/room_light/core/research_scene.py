"""Validated SI-unit surface scene shared by research engine adapters."""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
from dataclasses import asdict

import numpy as np
from shapely.geometry import Polygon, box
from shapely.ops import triangulate

from core.space_geometry import validate_space


@dataclass
class Surface:
    name: str
    vertices: list
    kind: str
    reflectance: float = 0.5
    transmittance: float = 0.0
    specularity: float = 0.0


@dataclass
class Scene:
    surfaces: list[Surface] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def fingerprint(self):
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True,
            allow_nan=False).encode()).hexdigest()


def pieces(polygon):
    """Triangulate only covered triangles; preserve actual holes."""
    if polygon.is_empty:
        return
    if polygon.geom_type == 'MultiPolygon':
        for p in polygon.geoms:
            yield from pieces(p)
    elif polygon.geom_type == 'Polygon':
        for t in triangulate(polygon):
            if polygon.covers(t):
                yield list(t.exterior.coords)[:-1]


def space_scene(space, *, include_devices=True):
    errors = [i.message for i in validate_space(space) if i.severity == 'error']
    if errors:
        raise ValueError('; '.join(errors))
    scene = Scene()
    height = space.height_mm / 1000
    floor_z = space.floor_elevation_mm / 1000
    def add(name, vertices, kind, rho=.5, tau=0., spec=0.):
        values = np.asarray(vertices, dtype=float)
        if not np.isfinite(values).all() or not 0 <= rho < 1 or not 0 <= tau <= 1:
            raise ValueError('Invalid surface geometry/material: ' + name)
        scene.surfaces.append(Surface(name, values.tolist(), kind, rho, tau, spec))

    # Wall axes host windows. Reveals span the physical wall depth; glazing is
    # placed on its declared plane, not snapped/clipped away from a corner.
    for wi, wall in enumerate(space.wall_segments()):
        ox, oy = wall.outward_normal
        dx, dy = wall.direction
        inner_start_data = wall.metadata.get('analysis_inner_start') or {}
        inner_end_data = wall.metadata.get('analysis_inner_end') or {}
        try:
            inner_start = (float(inner_start_data['x']) / 1000,
                           float(inner_start_data['y']) / 1000)
            inner_end = (float(inner_end_data['x']) / 1000,
                         float(inner_end_data['y']) / 1000)
        except (KeyError, TypeError, ValueError):
            inner_start = (wall.start.x / 1000, wall.start.y / 1000)
            inner_end = (wall.end.x / 1000, wall.end.y / 1000)
        # BP keeps topological wall axes and the physical inner faces separately.
        # Research geometry is built on the physical face while glazing remains
        # on the exact saved plane, with explicit reveals spanning the distance.
        length = math.hypot(inner_end[0] - inner_start[0], inner_end[1] - inner_start[1])
        axis_to_inner_outward = (
            (wall.start.x / 1000 - inner_start[0]) * ox
            + (wall.start.y / 1000 - inner_start[1]) * oy
        )
        def xyz(u, z, offset=0.):
            return (inner_start[0] + dx*u + ox*offset,
                    inner_start[1] + dy*u + oy*offset, floor_z+z)
        holes = []
        for oi, opening in enumerate(wall.openings):
            if opening.kind != 'window':
                continue
            axis_opening_start = wall.point_at(opening.offset_mm)
            a = ((axis_opening_start.x / 1000 - inner_start[0]) * dx
                 + (axis_opening_start.y / 1000 - inner_start[1]) * dy)
            b = a + opening.width_mm / 1000
            c, d = opening.sill_height_mm/1000, opening.head_height_mm/1000
            hole = box(a, c, b, d)
            if not box(0, 0, length, height).covers(hole):
                raise ValueError(f'{space.name}: window outside host wall: {opening.id}')
            if any(hole.intersection(h).area > 1e-9 for h in holes):
                raise ValueError('Overlapping windows: ' + opening.id)
            holes.append(hole)
            offset = axis_to_inner_outward + opening.plane_offset_mm/1000
            add(f'w{wi}_glass{oi}', [xyz(a,c,offset),xyz(b,c,offset),
                xyz(b,d,offset),xyz(a,d,offset)], 'glass', tau=opening.visible_transmittance)
            # The boundary wall is the host reference plane. Explicit reveal
            # depth defaults to host->glazing distance (no invented wall offset).
            if abs(offset) > 1e-8:
                for j, (p,q) in enumerate([((a,c),(b,c)),((b,c),(b,d)),
                                            ((b,d),(a,d)),((a,d),(a,c))]):
                    add(f'w{wi}_reveal{oi}_{j}', [xyz(*p),xyz(*q),
                        xyz(*q,offset),xyz(*p,offset)], 'wall', space.material.rho_wall)
            if include_devices:
                u_shift = a - opening.offset_mm / 1000
                for name, coords, rho, spec in device_faces(
                        space, wall, opening, u_shift=u_shift):
                    add(f'w{wi}_{oi}_{name}', [xyz(u,z,o+offset) for u,z,o in coords],
                        'shade',rho,spec=spec)
        solid = box(0,0,length,height)
        for hole in holes:
            solid = solid.difference(hole)
        for j, coords in enumerate(pieces(solid)):
            add(f'w{wi}_{j}', [xyz(u,z) for u,z in coords], 'wall',space.material.rho_wall)
    outer = [(p.x/1000,p.y/1000) for p in space.analysis_outer_points()]
    holes = [[(p.x/1000,p.y/1000) for p in loop]
             for loop in space.analysis_hole_points()]
    polygon = Polygon(outer,holes)
    for j, triangle in enumerate(pieces(polygon)):
        add(f'floor{j}',[(x,y,floor_z) for x,y in triangle], 'floor',space.material.rho_floor)
        add(f'roof{j}',[(x,y,floor_z+height) for x,y in triangle], 'roof',space.material.rho_ceiling)
    for j,b in enumerate(space.exterior_barriers):
        add(f'barrier{j}', [(b.start.x/1000,b.start.y/1000,floor_z+b.bottom_height_mm/1000),
            (b.end.x/1000,b.end.y/1000,floor_z+b.bottom_height_mm/1000),
            (b.end.x/1000,b.end.y/1000,floor_z+b.top_height_mm/1000),
            (b.start.x/1000,b.start.y/1000,floor_z+b.top_height_mm/1000)],
            'barrier',tau=b.visible_transmittance)
    return scene


def device_faces(space, wall, opening, *, u_shift=0.0):
    """Generate physical overhang, fixed slats and two-sided light-shelf faces."""
    s = space.shading
    key = str(opening.metadata.get('legacy_window_id',opening.id))
    if s.target_window_ids and key not in s.target_window_ids:
        return
    a = opening.offset_mm/1000 + u_shift
    b = opening.end_offset_mm/1000 + u_shift
    def plate(name,z,o1,o2,dz,rho,spec=0.):
        return name,[(a,z,o1),(b,z,o1),(b,z+dz,o2),(a,z+dz,o2)],rho,spec
    if s.has_component('horizontal_overhang'):
        length,gap,tilt = s.get_overhang_for(key)
        if length>0:
            theta=math.radians(tilt)
            yield plate('overhang',(opening.head_height_mm+gap)/1000,0,
                length*math.sin(theta)/1000,-length*math.cos(theta)/1000,
                s.visible_reflectance,s.specular_fraction)
    if s.has_component('louver'):
        if s.louver_spacing_mm <= 0:
            raise ValueError('百叶间距必须大于0')
        if s.louver_control_mode != 'fixed':
            raise ValueError('研究计算目前要求固定百叶；季节控制须拆分状态后计算')
        if s.louver_position == 'between':
            raise ValueError('中置百叶需要完整玻璃层/BSDF数据，不能按外置百叶替代')
        theta=math.radians(s.louver_angle_deg)
        width=s.louver_width_mm/1000
        sign=-1 if s.louver_position=='interior' else 1
        for j,z in enumerate(np.arange(opening.sill_height_mm,
                opening.head_height_mm+1e-6,s.louver_spacing_mm)/1000):
            yield plate(f'slat{j}',z,sign*.02,sign*(.02+width*math.cos(theta)),
                        -width*math.sin(theta),s.louver_reflect,s.specular_fraction)
    if s.has_component('light_shelf'):
        z=(s.light_shelf_height_mm or (opening.head_height_mm-
            opening.height_mm*s.light_shelf_upper_window_ratio))/1000
        slope=math.tan(math.radians(s.light_shelf_tilt_deg))
        outside=max(s.light_shelf_outside_depth_mm,s.light_shelf_depth_mm)/1000
        inside=s.light_shelf_inside_depth_mm/1000
        if outside>0:
            yield plate('shelf_out',z,0,outside,-outside*slope,s.light_shelf_reflect,s.specular_fraction)
        if inside>0:
            yield plate('shelf_in',z,0,-inside,inside*slope,s.light_shelf_reflect,s.specular_fraction)
    if s.vertical_fin_enabled:
        for side,u in [('L',a),('R',b)]:
            depth=s.get_fin_depth_for(key,side)/1000
            if depth>0:
                c,d=opening.sill_height_mm/1000,opening.head_height_mm/1000
                yield 'fin'+side,[(u,c,0),(u,c,depth),(u,d,depth),(u,d,0)],s.visible_reflectance,s.specular_fraction
