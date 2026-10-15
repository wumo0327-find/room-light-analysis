"""V5.4 CLI: same Radiance/EnergyPlus pipeline as ordinary GUI analysis."""
import argparse
from pathlib import Path
from datetime import datetime
from core.engine_service import EngineSettings, configure, validate_settings
from core.complex_experiments import run_multi_space_experiments
from io_utils.project_io import load_building_project


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True)
    parser.add_argument('--epw', required=True)
    parser.add_argument('--space', action='append')
    parser.add_argument('--out', default='建筑光热分析结果')
    parser.add_argument('--grid-mm', type=float, default=250, help='实验统一250mm；此参数仅兼容旧命令')
    parser.add_argument('--samples', type=int, default=4096)
    parser.add_argument('--tilts', type=float, nargs='+', default=[90])
    parser.add_argument('--depths', type=float, nargs='+', default=[300])
    parser.add_argument('--gaps', type=float, nargs='+', default=[0])
    parser.add_argument('--materials', nargs='+', default=['密实混凝土'])
    args = parser.parse_args()
    building, weather, active, error = load_building_project(args.project)
    if error:
        parser.error(str(error))
    config = EngineSettings(epw_path=str(Path(args.epw).resolve()),
        grid_mm=args.grid_mm, ambient_divisions=args.samples,
        north_angle_deg=building.north_angle_deg)
    validate_settings(config)
    configure(config)
    spaces = [building.get_space(key) for key in (args.space or [active])]
    if not spaces or any(s is None for s in spaces):
        parser.error('请选择有效房间ID（--space）')
    result = run_multi_space_experiments(spaces, weather=weather,
        tilt_degs=args.tilts, depth_mms=args.depths, gap_mms=args.gaps,
        materials=args.materials, enable_combinations=False,
        progress_cb=lambda tag, i, n: print(f'{tag} {i}/{n}', flush=True))
    out = Path(args.out)/datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    out.mkdir(parents=True, exist_ok=False)
    from core.experiment_contract import export_experiment_records
    export_experiment_records(result,out/'逐方案实验结果.csv')
    print(out.resolve())


if __name__ == '__main__':
    main()
