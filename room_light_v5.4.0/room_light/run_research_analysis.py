"""Command-line V5 research calculation for reproducible thesis runs."""
from __future__ import annotations

import argparse
from pathlib import Path

from core.operation import KINDERGARTEN_CLASSROOM
from core.research_energy import build_energyplus_model, find_energyplus, run_energyplus
from core.research_radiance import run_radiance_daylight
from io_utils.project_io import load_building_project


def main():
    parser = argparse.ArgumentParser(description='RoomLight V5 Radiance/EnergyPlus论文精算')
    parser.add_argument('project',help='RoomLight .rlproj 工程')
    parser.add_argument('--out',default='论文精算结果',help='输出目录')
    parser.add_argument('--space',action='append',help='房间ID；可重复，默认全部房间')
    parser.add_argument('--grid-mm',type=int,default=150)
    parser.add_argument('--ambient-bounces',type=int,default=5)
    parser.add_argument('--ambient-divisions',type=int,default=4096)
    parser.add_argument('--epw',help='可选EPW；需要本机EnergyPlus 25.2')
    args = parser.parse_args()
    building, _weather, _active, error = load_building_project(args.project)
    if error:
        raise SystemExit(error)
    ids = args.space or [space.id for space in building.spaces()]
    output = Path(args.out).resolve(); output.mkdir(parents=True,exist_ok=True)
    model = build_energyplus_model(building,ids,output/'EnergyPlus年模拟模型',
                                   profile=KINDERGARTEN_CLASSROOM)
    for space_id in ids:
        space = building.get_space(space_id)
        if space is None:
            raise SystemExit('房间不存在：'+space_id)
        folder = output/'Radiance采光精算'/space_id
        result = run_radiance_daylight(space,folder,grid_mm=args.grid_mm,
            ambient_bounces=args.ambient_bounces,
            ambient_divisions=args.ambient_divisions)
        print(space.name, f'Eavg={result.average_lux:.1f} lux',
              f'U0={result.uniformity_u0:.3f}',f'Ra300={result.ra_300:.3f}')
    if args.epw:
        if find_energyplus() is None:
            raise SystemExit('IDF已生成；未找到EnergyPlus 25.2，未输出年能耗数值。')
        print(run_energyplus(model,args.epw))
    print('完成：',output)


if __name__ == '__main__':
    main()
