# Room Light Analysis

面向建筑学研究的室内采光、热环境与遮阳优化桌面工具。

项目以 Python 和 PyQt6 构建，将建筑平面建模、采光计算、简化热工分析、参数化实验、
帕累托筛选与论文图表导出整合到同一个桌面工作流中。当前最新版本为 **v4.4.3**。

> 本工具主要用于论文前期方案筛选、参数敏感性分析与结果可视化，不替代
> Radiance、EnergyPlus 等逐时专业模拟，也不替代规范审查或实测验证。

## 项目亮点

- **建筑建模**：内置平面编辑器，支持任意多边形空间、墙体、窗体、栏杆及尺寸标注。
- **采光分析**：基于 CIE 标准全阴天模型计算采光系数、照度、均匀度与达标面积比。
- **热环境分析**：采用单区集总热平衡模型，输出逐月自然室温与热不舒适指标。
- **遮阳构件模拟**：支持挑檐、垂直翼板、水平百叶和内外反光板等构件。
- **参数化优化**：批量扫描几何、材料和控制参数，提取二维及三维帕累托前沿。
- **决策辅助**：在成本约束下比较采光与热环境表现，输出候选方案和失效瓶颈。
- **研究输出**：支持热力图、二维/三维点云、PNG 和多工作表 Excel 报告导出。
- **工程兼容**：`.rlproj` 工程文件保持向后兼容，并支持旧版工程自动转换。

## 技术栈

- Python 3.10+
- PyQt6
- NumPy / pandas
- Matplotlib
- openpyxl

## 快速开始

克隆仓库：

```bash
git clone https://github.com/wumo0327-find/room-light-analysis.git
cd room-light-analysis/room_light_v4.4.3/room_light
```

安装依赖并启动：

```bash
pip install -r requirements.txt
python main.py
```

`main.py` 会通过相对路径读取界面资源，因此请在最新版的 `room_light` 目录中运行。

## 测试

最新版测试代码位于 `room_light_v4.4.3/room_light/tests/`。在安装项目依赖后运行：

```bash
cd room_light_v4.4.3/room_light
python -m unittest discover -s tests -v
```

## 代码结构

```text
room_light_v4.4.3/room_light/
├── main.py                 # 桌面应用入口
├── bp_editor/              # 建筑平面建模器
├── core/                   # 采光、热工、遮阳与优化计算
├── io_utils/               # 工程、气象数据与结果导出
├── ui/                     # PyQt6 界面
├── tests/                  # 回归测试
├── tools/                  # 数据生成辅助工具
├── MANUAL.md               # 使用手册与版本日志
├── THEORY.md               # 计算理论与公式说明
└── VALIDATION_PROTOCOL.md  # 论文级验证流程与适用边界
```

仓库按语义化版本文件夹保存开发快照。若只想阅读或运行程序，请优先查看
[`room_light_v4.4.3/room_light`](room_light_v4.4.3/room_light)。

## 文档

- [最新版详细说明](room_light_v4.4.3/room_light/README.md)
- [用户手册与版本日志](room_light_v4.4.3/room_light/MANUAL.md)
- [计算理论](room_light_v4.4.3/room_light/THEORY.md)
- [验证流程与适用边界](room_light_v4.4.3/room_light/VALIDATION_PROTOCOL.md)

## 数据说明

出于隐私、体积与研究数据管理考虑，仓库不包含实际建筑 `.rlproj` 模型、CAD 原始图纸、
实测数据及批量实验导出结果。示例目录保留可复现建模流程所需的脚本和说明文档。

## 当前研究边界

- 采光核心采用 CIE 标准全阴天与简化反射模型，复杂项目应结合实测或专业软件复核。
- 热工模块采用月代表工况的准稳态筛选模型，不等同于逐时动态能耗模拟。
- 材料光热参数和工程价格用于方案比较，正式设计应替换为项目所在地的实测或询价数据。

## 版本

当前版本：**v4.4.3**
完整变更记录见最新版 [MANUAL.md](room_light_v4.4.3/room_light/MANUAL.md)。
