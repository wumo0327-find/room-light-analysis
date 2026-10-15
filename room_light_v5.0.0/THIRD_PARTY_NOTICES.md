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
