# RoomLight v5.4.0 开源组件与代码位置

| 项目 | 本程序位置 | 用途 | 成熟性判断 |
|---|---|---|---|
| LBNL Radiance | `engines/radiance/`<br>`room_light/core/research_radiance.py`<br>`room_light/core/research_scene.py` | CIE全阴天光线追踪、多次反射、逐点照度和DF；V5.4以固定分块并行调用官方rtrace | LBNL官方开源引擎；项目自称且有公开验证研究的 validated lighting simulation tool；跨平台自动测试与持续发布。 |
| EnergyPlus | `room_light/core/research_energy.py` | 生成IDF、调用官方引擎进行逐时热平衡与年能耗计算 | 美国国家实验室官方建筑能耗引擎；长期版本发布；官方文档列出ASHRAE 140、BESTest、分析解和热平衡测试。 |
| pymoo | `room_light/core/pareto_engine.py`<br>`room_light/core/experiments.py` | 统一2D/3D/N维非支配排序 | 同行评审的IEEE Access论文；官方实现包含NSGA-II/III、非支配排序和回归测试；Apache-2.0许可。 |
| Shapely | `room_light/core/research_scene.py` | 凹多边形、孔洞、窗洞布尔运算与三角化 | 广泛使用的GEOS Python接口，拓扑运算不再由RoomLight自行重复实现。 |

## 边界

- Radiance 点时标准全阴天结果不等于全年 sDA/UDI/ASE。
- EnergyPlus 只在真实 EPW、完整时刻表和引擎成功运行后才产生逐时精算结论。
- pymoo 保证非支配排序逻辑，不保证输入的光热物理数值本身正确。
- V5.4的粗筛、复筛和最终复核均调用Radiance/EnergyPlus；“粗”仅指采光数值网格和积分密度较低，不表示换回自研物理公式。
- 最终推荐必须通过相邻精度级别的收敛阈值；引擎成熟并不替代本项目几何映射、参数输入和实测交叉验证。
