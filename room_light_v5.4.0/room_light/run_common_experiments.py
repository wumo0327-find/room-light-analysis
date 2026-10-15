"""Small acceptance first; opt-in three-device rerun with identical inputs."""
import argparse
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import tempfile

import numpy as np
import pandas as pd

from core.engine_service import EngineSettings, configure, validate_settings
from core.complex_experiments import run_multi_space_experiments, run_complex_project_baseline
from core.experiment_contract import attach_baseline, digest, export_experiment_records, fresh_pareto, stage_record
from io_utils.project_io import load_building_project

KINDS = ('horizontal_overhang','louver','light_shelf')
CHECK_METRICS = ('sensor_count','sensor_fingerprint','Ra','daylight_score','U0','DF_avg',
                 'E_avg','thermal_discomfort','annual_operating_cost','baseline_model_fingerprint')


def same_baseline(left, right):
    for key in CHECK_METRICS:
        if isinstance(left[key], str):
            if left[key] != right[key]: raise AssertionError('基准不一致：'+key)
        elif not np.isclose(float(left[key]), float(right[key]), rtol=1e-10, atol=1e-10):
            raise AssertionError(f'基准不一致：{key} {left[key]} / {right[key]}')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',required=True,help='三批实验的实际完整rlproj（必须明确指定）')
    parser.add_argument('--config',default='统一实验配置.json')
    parser.add_argument('--out',default='建筑光热分析结果_统一基准')
    parser.add_argument('--full',action='store_true')
    parser.add_argument('--acceptance',help='本项目/同一配置的小批量验收.json；全量必填')
    args=parser.parse_args()
    configuration=json.loads(Path(args.config).read_text(encoding='utf-8-sig'))
    source=Path(args.project).resolve()
    building,weather,active,error=load_building_project(str(source))
    if error: parser.error(str(error))
    ids=configuration.get('space_ids') or [active]
    spaces=[building.get_space(i) for i in ids]
    if any(s is None for s in spaces): parser.error('配置中的房间ID不存在')
    from core.space_geometry import space_floor_area_mm2
    actual_area=sum(space_floor_area_mm2(s) for s in spaces)/1e6
    expected_area=configuration.get('expected_net_area_m2')
    if expected_area is not None and not np.isclose(actual_area,float(expected_area),atol=1e-6,rtol=0):
        parser.error(f'实际净面积{actual_area:.6f}㎡与配置核对值{expected_area}㎡不同。没有替换几何；请选择当时的完整工程，或明确修改核对值用于另一案例。')
    engine_values=deepcopy(configuration['engine'])
    engine_values['profile']=configuration['operation_profile']
    engine_values['north_angle_deg']=building.north_angle_deg
    engine_values['grid_mm']=250
    engine=EngineSettings(**engine_values)
    if not engine.epw_path: parser.error('请先在统一实验配置.json填入实际长沙EPW路径')
    validate_settings(engine)
    # Acceptance is tied to exact bytes of the actual project, climate, config,
    # and all calculation source. It cannot authorize a different/old model.
    code_signature=digest({p.name:hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(Path('core').glob('*.py'))})
    context={'project_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
        'configuration_sha256':digest(configuration), 'calculation_source_sha256':code_signature,
        'epw_sha256':hashlib.sha256(Path(engine.epw_path).read_bytes()).hexdigest()}
    if args.full:
        if not args.acceptance: parser.error('先运行小批量，再用--acceptance指定通过的验收记录')
        accepted=json.loads(Path(args.acceptance).read_text(encoding='utf-8'))
        if not accepted.get('passed') or accepted.get('context') != context:
            parser.error('验收未通过或模型/配置/气象/源码已改变，必须重新小批量检查')
    parameters=configuration['experiments']
    shelf=parameters.get('light_shelf_params',{})
    if any(float(v)>0 for v in shelf.get('inside_depths',[])):
        parser.error('本联合光热入口仅支持外置反光板：inside_depths必须明确为[0]。保留原内伸范围，但不得按外置构件替代其热计算。')
    stamp=datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    out=Path(args.out)/stamp; out.mkdir(parents=True,exist_ok=False)
    engine.cache_directory=str((out/'计算缓存').resolve())
    configure(engine)
    original=[asdict(s) for s in spaces]
    kwargs=dict(weather=weather, grid_mm=250, operation_profile=engine.profile,
        north_angle_deg=building.north_angle_deg, latitude_deg=building.location.latitude,
        enable_combinations=False)
    targets=configuration.get('target_window_keys')
    if targets is not None: kwargs['target_window_keys']=targets
    if args.full:
        from core.staged_experiments import StagedExperimentPipeline
        from ui.mpl_font import setup_font
        from core.experiments import plot_experiments,build_pareto3d_figure
        parameters = {**parameters,'operation_profile':engine.profile,'device_types':list(KINDS),
            'enable_combinations':False,'y':configuration.get('thermal_objective','thermal_discomfort'),
            'maximize_y':False,'u0_min':configuration.get('u0_min',0)}
        pipeline=StagedExperimentPipeline(spaces,weather,engine,parameters,
            target_window_keys=targets,latitude_deg=building.location.latitude,north_angle_deg=building.north_angle_deg)
        callback=lambda i,n,label='':print(f'{label} {i}/{n}',flush=True)
        pipeline.stage1(callback); pipeline.stage2(callback); pipeline.stage3(callback)
        result=pipeline.stage4(callback)
        export_experiment_records(result,out/'三类联合实验结果.csv')
        for kind in KINDS:
            export_experiment_records(result[result.device_type.isin([kind,'baseline'])].copy(),out/(kind+'_实验结果.csv'))
        setup_font()
        plot_experiments(result,str(out/'采光与热环境.png'),x='daylight_score',y='thermal_discomfort',
            maximize_y=False,u0_min=configuration.get('u0_min',0),x_label='Cd2',y_label='热不舒适度（℃·h）')
        figure,_=build_pareto3d_figure(result,thermal_label='占用热不舒适度（℃·h）')
        figure.savefig(out/'三类联合3D帕累托.png',dpi=180,bbox_inches='tight')
        (out/'全量运行配置.json').write_text(json.dumps(configuration,ensure_ascii=False,indent=2),encoding='utf-8')
        print(str(out.resolve()),flush=True)
        return
    frames=[]; baselines=[]
    for kind in KINDS:
        values=deepcopy(parameters)
        by_kind=values.pop('materials_by_device',{})
        values['materials']=by_kind.get(kind,values.get('materials',['普通/氧化铝板']))
        if not args.full:
            for key in ('tilt_degs','depth_mms','gap_mms','materials'):
                if key in values:
                    sequence=values[key]
                    if key=='depth_mms': sequence=[v for v in sequence if float(v)>0]
                    values[key]=sequence[:1]
            for group in ('louver_params','light_shelf_params'):
                for key, sequence in values.get(group,{}).items():
                    if group=='light_shelf_params' and key=='outside_depths':
                        sequence=[v for v in sequence if float(v)>0]
                    values[group][key]=sequence[:1]
        print(f'{kind}: 开始'+('全量' if args.full else '小批量'),flush=True)
        frame=run_multi_space_experiments(spaces, device_types=[kind], **kwargs, **values,
            progress_cb=lambda tag,i,n:print(f'{tag} {i}/{n}',flush=True))
        frame['calculation_stage']='统一250mm同精度单次扫描（未进行收敛复核）'
        frame['precision_verified']=False
        frame=pd.DataFrame([stage_record(r.to_dict(),r.to_dict()) for _,r in frame.iterrows()])
        frame.attrs['original_space_configs']=original
        export_experiment_records(frame,out/(kind+'_实验结果.csv'))
        baseline=frame[frame.device_type.eq('baseline')].iloc[0]
        baselines.append(baseline)
        frames.append(frame)
    for row in baselines[1:]: same_baseline(baselines[0],row)
    if original != [asdict(s) for s in spaces]: raise AssertionError('原始配置被修改')
    # B: baseline after schemes. C: reverse order/repeat, then separate empty
    # cache. A newly created cache avoids deleting any historical results.
    check_rows=[]
    if not args.full:
        for sequence in (KINDS,tuple(reversed(KINDS))):
            for kind in sequence:
                repeat=run_multi_space_experiments(spaces,device_types=[kind],
                    materials=['普通/氧化铝板'],tilt_degs=[90],depth_mms=[300],gap_mms=[0],
                    louver_params={'angles':[0],'widths':[100],'spacings':[150]},
                    light_shelf_params={'inside_depths':[0],'outside_depths':[300],'tilts':[0],'upper_ratios':[.35]},
                    **kwargs)
                row=repeat[repeat.device_type.eq('baseline')].iloc[0]
                same_baseline(baselines[0],row)
                check_rows.append(dict(entry=kind,cache='repeat',**{k:row[k] for k in CHECK_METRICS}))
        fresh=deepcopy(engine); fresh.cache_directory=str((out/'空缓存复算').resolve()); configure(fresh)
        empty=run_multi_space_experiments(spaces,device_types=['horizontal_overhang'],
            depth_mms=[0],tilt_degs=[90],gap_mms=[0],materials=['普通/氧化铝板'],**kwargs)
        row=empty[empty.device_type.eq('baseline')].iloc[0]
        same_baseline(baselines[0],row)
        check_rows.append(dict(entry='baseline',cache='fresh',**{k:row[k] for k in CHECK_METRICS}))
        configure(engine)
    combined=pd.concat([frames[0][frames[0].device_type.eq('baseline')]]+
        [f[~f.device_type.isin(['baseline','none'])] for f in frames],ignore_index=True)
    combined=fresh_pareto(attach_baseline(combined),thermal_col=configuration.get('thermal_objective','thermal_discomfort'),
        u0_min=configuration.get('u0_min',0))
    combined.attrs['original_space_configs']=original
    export_experiment_records(combined,out/'三类联合实验结果.csv')
    from ui.mpl_font import setup_font
    from core.experiments import plot_experiments, build_pareto3d_figure
    setup_font()
    for x,y,title in [('daylight_score','thermal_discomfort','采光与热环境'),
        ('daylight_score','cost','采光与年综合费用'),('thermal_discomfort','cost','热环境与年综合费用')]:
        plot_experiments(combined,str(out/(title+'.png')),x=x,y=y,
            maximize_x=x=='daylight_score',maximize_y=False,u0_min=configuration.get('u0_min',0),
            x_label={'daylight_score':'Cd2','thermal_discomfort':'占用热不舒适度（℃·h）'}[x],
            y_label='占用热不舒适度（℃·h）' if y=='thermal_discomfort' else '年综合费用（元/年）',title=title)
    figure,_=build_pareto3d_figure(combined,thermal_label='占用热不舒适度（℃·h）')
    figure.savefig(out/'三类联合3D帕累托.png',dpi=180,bbox_inches='tight')
    pd.DataFrame(check_rows).to_csv(out/'基准入口顺序与空缓存检查.csv',index=False,encoding='utf-8-sig')
    acceptance={'passed':True,'context':context,'actual_project':str(source),
        'mode':'full' if args.full else 'small', 'floating_tolerance':{'rtol':1e-10,'atol':1e-10},
        'physical_validation':False,'scan_precision_verified':False,
        'baseline_record':{k:baselines[0][k] for k in CHECK_METRICS},
        'checks':{'A':'passed','B':'passed' if not args.full else 'small acceptance reused',
                  'C':'passed' if not args.full else 'small acceptance reused',
                  'F':'three normal cases computed and exported'}}
    (out/'小批量验收.json').write_text(json.dumps(acceptance,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    print(str(out.resolve()),flush=True)


if __name__=='__main__': main()
