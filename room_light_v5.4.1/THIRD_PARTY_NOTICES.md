# 第三方引擎说明

## Radiance 6.0.2

V5 的 `engines/radiance/` 包含 Lawrence Berkeley National Laboratory
发布的 Windows 二进制程序和运行库，用于论文精算中的标准全阴天光线追踪。

- 项目主页：https://www.radiance-online.org/
- 源代码与许可证：https://github.com/LBNL-ETA/Radiance
- 本地许可证：`engines/radiance/LICENSE.txt`

RoomLight 仅负责把用户模型转换为 Radiance 场景、调用引擎并整理结果；
Radiance 的版权和许可归其原权利人所有。

## EnergyPlus 25.2

RoomLight V5 能生成 EnergyPlus 25.2 原生 IDF，并可调用用户本机安装的官方
EnergyPlus。V5 当前不捆绑 EnergyPlus 可执行文件，用户应从官方渠道安装并遵守
其许可证。

- 项目主页：https://energyplus.net/
- 源代码与许可证：https://github.com/NatLabRockies/EnergyPlus

## pymoo 0.6.x

RoomLight v5.1.1 使用 pymoo 的 `NonDominatedSorting` 计算2D和N维帕累托
非支配解集。RoomLight 仍负责硬约束、指标方向、工程容差、综合推荐和绘图。

- 源代码：https://github.com/anyoptimization/pymoo
- 论文：J. Blank and K. Deb, *pymoo: Multi-Objective Optimization in
  Python*, IEEE Access 8 (2020), 89497–89509.
- 许可证：Apache License 2.0

## Shapely 2.x

Radiance/EnergyPlus 场景转换使用 Shapely 做多边形、孔洞与三角化的拓扑处理。

- 源代码：https://github.com/shapely/shapely
- 许可证：BSD 3-Clause
