# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## 版本管理约定

本项目按版本文件夹组织代码：`room_light_v<MAJOR>.<MINOR>.<PATCH>/room_light/`（语义化版本号 [SemVer](https://semver.org)，例如当前最新的 `room_light_v2.13.0/room_light/`）。

**版本号含义：**

| 位 | 名称 | 何时递增 | 递增时低位归零 |
|----|------|----------|----------------|
| 第 1 位 | MAJOR（主版本号） | 架构性/不兼容变更 — 如整个计算引擎重构、`.rlproj` 格式不再向后兼容 | MINOR、PATCH 都归零 |
| 第 2 位 | MINOR（次版本号） | 新增功能/新模块，但向后兼容 — 如新增一个分析模块、新增 UI 面板、新增可选参数 | PATCH 归零 |
| 第 3 位 | PATCH（修订号） | 纯 bug 修复、样式/文案微调，不引入新功能、不改变已有行为的使用方式 | — |

判断一次改动该加哪一位，看改动内容而非工作量大小：修 bug → PATCH；加新功能/新面板/新字段（不破坏旧存档兼容性）→ MINOR；推翻旧设计、旧 `.rlproj` 文件读不了 → MAJOR。

**操作规则：**

- **读取代码时**：始终定位并读取版本号最大（最新）的文件夹，除非用户明确要求查看旧版本。按语义化版本号比较（先比 MAJOR，再 MINOR，再 PATCH），而非按文件夹的修改时间。
- **修改代码时**：不要直接修改旧版本文件夹。决定发布新版本时，复制最新版本文件夹为一个新的版本文件夹，按上表规则正确递增版本号，再在新文件夹中进行修改；同步更新新文件夹内代码/文档中出现的版本号自引用（`main.py`/`main_window.py` 窗口标题、各模块文件头注释、`MANUAL.md` 变更日志新增条目等）。
- 每次创建新版本前，先确认当前最新版本号，避免命名冲突或跳号。
- 各版本文件夹内部结构一致（见下方"代码架构"），新版本通常只是在旧版本基础上增删模块。
- 历史版本号曾有归类偏差（例如把新增功能误标为 PATCH），已于 2026-07-12 按上表规则统一重新核对并连锁重命名过一次（`v2.1.2→v2.2.0`、`v2.1.3→v2.2.1`、`v2.1.4→v2.3.0`、`v2.2.0→v2.4.0`），文件夹内所有版本号自引用已同步更新。此后新版本请从一开始就按规则正确定级，避免再次出现连锁重命名。

## 项目简介

建筑室内采光分析工具（PyQt6 桌面应用）：基于 CIE 标准全阴天模型计算侧窗采光系数（DF）与室内平均照度，可选进行单区集总参数热环境分析（v2.4.0 新增），并支持水平/倾斜挑檐遮阳对采光/热工的精算与倾斜角×板长参数化实验（v2.5.0 新增遮阳精算，v2.12.0 由抽象特征角β改为可直接施工测绘的倾斜角θ×板长L）。面向建筑学论文中的采光/热工计算、遮阳优化与图表输出。

## 常用命令

在目标版本文件夹的 `room_light/` 子目录下执行（`main.py` 用相对路径打开 `ui/style.qss`，必须以该目录为工作目录运行）：

```bash
cd room_light_v2.13.0/room_light   # 定位到最新版本
pip install -r requirements.txt
python main.py
```

无 GUI 的参数化实验批量运行（遮阳 θ×L×h 网格 + 玻璃对照 → 帕累托前沿 + 散点气泡图；GUI 里还可多选材料/看3D点云，命令行版材料用单一默认k_diff）：

```bash
python run_experiments.py --out experiment_out          # 默认参数（遮阳组：θ∈60~120°步长10、L∈300~1500mm步长300）
python run_experiments.py --tilt-min 60 --tilt-max 120 --tilt-step 10 --depth-min 300 --depth-max 1500 --depth-step 300
python run_experiments.py --y thermal_discomfort --u0-min 0.0   # 换连续热轴/放宽合规筛选
```

无 GUI 的采光实测数据交叉验证（沿窗中线/窗间墙中线取点比对实测采光系数，v2.6 新增）：

```bash
python validate_daylight.py --out validate_out                                    # 占位几何冒烟测试
python validate_daylight.py --project classroom_A.rlproj --measured measured_A.csv \
    --first-offset 0.5 --spacing 0.5 --out validate_out_A                          # 真实教室+实测对比
```

无自动化测试套件（无 `tests/` 目录），验证方式是运行 UI 并手动检查采光/热环境计算结果与图表导出，或跑 `run_experiments.py` / `validate_daylight.py` 核对实验表与图。

## 代码架构

每个版本文件夹内 `room_light/` 的结构：

- `main.py` — 入口，加载中文字体（`ui/mpl_font.py`）、全局 QSS 样式（`ui/style.qss`），创建 `MainWindow`。
- `core/` — 纯计算引擎，不依赖 Qt：
  - `models.py` — 数据模型：`RoomModel`（房间几何 + 窗户列表 + 材料/热工/遮阳/位置参数的聚合根）、`Window`、`MaterialParams`（光学反射率）、`ThermalParams`（热工参数）、`ShadingDevice`（水平/倾斜挑檐已在 v2.5 实现精算，含 `diffuse_residual`、`beam_shade_fraction()`；`overhang_depth_mm` 语义为板长L(沿板自身方向)，v2.12.0 新增 `overhang_tilt_deg`（默认90.0=水平，>90°上扬/<90°下垂，方向隐含在90°哪一侧不需要符号位）；v2.7 新增垂直遮阳翼板/装饰柱 `vertical_fin_enabled`/`vertical_fin_depth_mm`，仅采光、与水平挑檐独立叠加；v2.10 新增逐窗/逐位置覆盖 `overhang_overrides`（键=`str(window.id)`，v2.12.0 起可选子键`tilt_deg`）、`fin_overrides`（键=`"{window.id}:L/R"`）及取值辅助 `get_overhang_for()`（返回(depth,gap,tilt)三元组）/`get_fin_depth_for()`，缺失覆盖时沿用全局默认值；v2.10.2 新增 `fin_column_width_mm`（默认540mm，仅用于两端外墙位置柱宽，贴窗边缘，不等于整段端墙剩余宽度——窗间墙位置柱宽仍取真实窗间墙宽度）；v2.12.0 新增遮阳材料反射率→`diffuse_residual`预设，v2.13.0 扩为分类材料库 `MATERIAL_LIBRARY`（混凝土/金属/木材/涂料四大类共10种，每种含反射率参考值+k_diff估算+绘图配色，**数值非严格标定**）+ 辅助 `iter_materials()`/`get_material()`/`DEFAULT_SELECTED_MATERIALS`（`MATERIAL_PRESETS` 保留为派生别名向后兼容）；百叶/导光板仍为预留）、`LocationParams`。内部几何单位统一为 **毫米**，计算时换算为米。
  - `daylight.py` — 采光核心引擎：将窗口离散为 N×N 面元数值积分求天空分量 `D_s`，加上室外反射分量 `D_ext`（Littlefair 简化公式）与室内互反射分量 `D_int`（BRS 简化法，注意 ρ̄≥0.6 时公式本身已超出文献验证范围、易系统性偏高），得到总采光系数 DF 和平均/最低照度、均匀度 U₀、采光达标面积比 Ra（v2.5）等 KPI；`_ds_point` 含水平/倾斜挑檐（v2.5水平，v2.10 起支持逐窗覆盖深度/间隙，**v2.12.0 推广为任意倾斜角θ**：挑檐所在平面从固定高度z=z_over推广为斜面z=z_over−s_out·cotθ，θ=90°退化为与旧版水平公式逐位一致——已做bit-for-bit回归验证）+ 垂直遮阳翼板/装饰柱（v2.7；v2.10.1 重新设计为**射线-立方体(AABB)相交判定**，见 `_fin_slots_full_m()`：翼板不再是贴在窗户自己边缘的零宽度面，而是按真实占据的横向范围×出挑深度×竖向范围建模为实心长方体，任意窗户的天空面元只要视线穿过该体积就算被遮挡，不再限定"只测自身两侧"；出挑深度已按 `room.thermal.wall_thickness_mm` 扣除墙厚换算有效值——v2.10.0 的标注深度含墙厚直接代入曾系统性放大遮挡；v2.10.2 修正两端外墙槽宽度为固定 `fin_column_width_mm`（贴窗边缘，此前 v2.10.0/v2.10.1 曾错误地铺满整段端墙剩余宽度，比现场真实柱宽宽近3倍）)。**已知局限（v2.10.1 解析证明、已与用户确认接受）：窗间墙测点的遮挡幅度在当前"自由柱"几何模型下数学上不可能超过窗户自身中心测点——自由柱无法遮挡直接相邻窗边缘的近距离视线（该视线穿过窗洞面时恒已偏离柱体所在横向坐标），如需解决需引入窗户在墙体内的安装位置/洞口斜壁(reveal)这一新几何维度，未实现**；真实教室验证：v2.10.2 修正端墙柱宽后，窗1测线MRE基本未变（房间A/B: 128.8%/292.6%，此前 v2.10.1 为130.0%/292.5%）——v2.10.1 变更日志中"更宽的端墙槽导致窗1遮挡更强"的归因经复核并不准确已更正，窗1测线MRE持续偏高的真正原因仍未查明，详见 `MANUAL.md` v2.10.2 变更日志；`compute()` 支持逐行进度回调（`row_cb`）与可配置 `ra_threshold`。
  - `thermal.py` — 热环境引擎（v2.4 新增）：单区集总热平衡模型，逐月计算自然室温、舒适/超温/欠温月数、热流分解。v2.5 起 `SC_effective` 为逐月序列（含挑檐遮阳精算），外墙太阳附加得热已按 sol-air 修正。**已知简化（v2.12.0）**：倾斜挑檐的SC计算暂用水平投影深度(L·sinθ)代入原有水平公式近似处理，未对倾斜面做精确太阳方位角修正（采光Ds计算已做精确的斜面射线相交判定，热工暂未跟进到同等精度）。
  - `solar.py` — 太阳位置模块（v2.5 新增）：赤纬、正午太阳高度角/方位角、墙朝向方位角、遮阳剖面角 profile angle，代表工况取「月中日正午」。是 thermal 遮阳精算的前置依赖。
  - `experiments.py` — 参数化实验框架（v2.5 新增）：玻璃对照组 + 遮阳主实验组批量运行（pandas 结果表）、2D 帕累托前沿提取、散点气泡图导出。由根目录 `run_experiments.py` 命令行调用。默认算例房间 `_base_room()` v2.11.0 起改为夏热冬冷地区幼儿园活动室代表尺寸（层高3.6m/窗台0.6m/窗高2.4m/进深6.6m引自JGJ39-2016，开间9.0m/窗宽4.2m为按班额规模推算的估算值，非规范直接给出）。**v2.12.0**：`run_overhang_experiment()` 遮阳主实验组从"β单变量+固定H反算深度"改为"倾斜角θ×板长L网格扫描"（默认7×5=35组合），材料(diffuse_residual)/安装间隙(overhang_gap_mm)在网格里固定不扫描——按"先筛几何主效应、材料/间隙留到候选方案上做二阶段敏感性复核"的策略避免组合数爆炸；`plot_experiments()` 新增逐点参数+数值标注、气泡缩小、图例移至图外，解决可读性问题；新增 `pareto_front_nd()`（N维非支配解，供3D用）与 `build_pareto3d_figure()`（3D帕累托点云）。**v2.13.0**：`run_overhang_experiment()`/`run_all_experiments()` 参数改为 `tilt_degs/depth_mms/gap_mms/materials/include_glass`，遮阳组扩为 **θ×L×h×材料 四维网格**；**采光只按几何(θ,L,h)算一次、逐材料只补算热环境**（材料只经 k_diff 影响热工，与采光无关，避免材料维度成倍放大最耗时的采光计算）；`plot_experiments()`/`build_pareto3d_figure()` 改为**按材料着色**（`_plot_color` 列，玻璃组绿色），3D 图 y 轴改为热不舒适度（反向显示、外侧更优）、去掉点击弹二维图、返回 `(fig, ax)` 供复位视角；2D 图点数>24 时只标注帕累托点避免糊成一团。
  - `validation.py` — 采光实测数据交叉验证（v2.6 新增）：`sample_df_at_points()` 在现有 DF 网格上双线性插值取任意坐标点的 DF/Ds/Dext/Dint；`make_probe_lines()` 按窗户局部坐标（复用 `daylight._wall_axes` 换算）自动生成"沿窗中线"/"沿窗间墙中线"测线坐标。不改动 Ds/Dext/Dint 核心算法。由根目录 `validate_daylight.py` 命令行调用。
  - `plan_export.py` — 房间平面图绘制（v2.8 新增，从 `ui/canvas.py` 抽取，GUI 画布与命令行静态导出共用同一套纯 matplotlib 绘制函数）：`draw_room_plan()` 画房间轮廓/墙（v2.9.1 起双线表示墙厚）/窗（天正建筑风格：洞口四线图例+窗号，v2.9.0 起有窗的墙自动画天正风格分段尺寸链——贴墙逐段标"端墙/窗宽/窗间墙"+外侧总尺寸）；`draw_shading_plan()`（v2.9.0 新增，平面）/`draw_shading_elevation()`（v2.9.1 新增，立面）按窗宽/翼板区间真实宽度 × 出挑深度画水平挑檐/垂直翼板外凸（或立面薄板）示意+出挑深度标注，v2.10 起翼板区间由 `_fin_slots()` 统一生成（含两端外墙位置，深度支持逐窗/逐位置覆盖）；`dim_arrow()` 起 v2.9.1 内部统一为天正风格（不再画箭头），`ui/canvas.py` 的立面标注复用此函数因此同步统一样式；`export_plan_png()` 额外叠加验证测点（按测线分色+自动图例）与窗户尺寸/遮阳参数/测点坐标核对表，供人工核实房间与测点是否符合现场情况。**当前数据模型无门（door），平面图画不出门**。
  - 计算理论与公式来源见各版本内的 `THEORY.md`（v2.4 起从 README 拆分出来）。
- `ui/` — PyQt6 界面层，均依赖 `core/` 的数据模型和计算结果，不包含计算逻辑：
  - `main_window.py` — 主窗口，用 `QStackedWidget` 管理四个视图（建筑视图 `canvas.py` / 采光分析 `analysis_panel.py` / 热环境 `thermal_panel.py` / 参数化实验 `experiment_panel.py`，v2.5.1 新增），协调工具栏动作（全部分析、参数化实验、打开/保存 `.rlproj`、导出、查看验证结果 v2.8 新增）。
  - `sidebar.py` — 可折叠参数侧边栏（房间几何、窗户 CRUD、遮阳构件全局默认（v2.5.1 新增水平挑檐几何+k_diff，v2.10 新增垂直翼板启用开关+默认深度的GUI入口，v2.10.2 新增"端墙柱宽"输入框）、遮阳构件逐窗设置（v2.10 新增，列表形式，可对单扇窗户覆盖挑檐深度/间隙、左右翼板深度，支持"应用到全部窗户"批量广播）、材料反射率、热工参数），通过信号驱动画布/结果实时刷新（防抖 150ms）；遮阳相关修改仅触发画布重绘，沿用"手动点『▶ 全部分析』才重算"的交互习惯。
  - `canvas.py` — 建筑视图画布（平面图/四向立面图），平面图绘制逻辑委托给 `core/plan_export.py`（v2.8 重构，行为不变）；立面图 v2.9.1 起同步显示遮阳构件示意。
  - `experiment_sidebar.py` — 参数化实验专用侧边栏（**v2.13.0 新增**）：点"参数化实验"视图时主窗口左侧从"房间参数侧边栏"整体切换为本栏（`main_window` 把左侧改成两页 `QStackedWidget`）。含遮阳几何(θ/L/h 三个范围+步长)、遮阳材料多选清单(带色块，勾选谁算谁)、玻璃对照组(可选)、图表设置(热轴/U0)、运行/导出按钮；θ/L/h 右键弹 `_render_tilt_diagram()` 剖面示意图，其余参数右键弹文字说明。收集参数后发 `run_requested(dict)`/`export_*_requested`，纯视图不含计算。
  - `experiment_panel.py` — 参数化实验结果显示面板（v2.5.1 新增，**v2.13.0 改为纯显示**，所有输入控件已移到 `experiment_sidebar.py`）：顶部"2D图/3D点云"切换 + "↺ 恢复默认视图"（复位3D视角）；2D 模式下方三个按钮"采光×热轴/采光×成本/热轴×成本"直接切三张两两指标的二维投影图（复用 `plot_experiments()`）；3D 用 `FigureCanvasQTAgg` 真实嵌入、可鼠标旋转、按材料着色（v2.13.0 去掉了 v2.12.0 的点击弹二维图交互）。`show_result(df, params)` 接收结果，`export_png()/export_csv()` 由侧边栏导出按钮经 `main_window` 调用。
  - `validation_viewer_dialog.py` — 验证结果查看器（v2.8 新增）：通用文件夹浏览对话框，递归扫描 PNG/CSV 并预览（图片缩放 / CSV 表格化），不写死具体某次验证路径；由工具栏「🔍 验证结果」按钮打开，默认定位到 `examples/` 目录。
  - `progress_dialog.py` — 后台线程 `AnalysisWorker`（`QThread`）执行采光+热环境分析，支持暂停/取消，避免阻塞 UI。
  - `export_dialog.py` — 勾选式批量导出（各模块 PNG + 光热综合拼图 + Excel）。
  - `weather_dialog.py` — 气象数据输入（手动填月度照度/温度，或导入 Excel/CSV，自动识别 lux / W/m² 并换算）。
  - `mpl_font.py` — matplotlib 中文字体配置；`style.qss` — 全局白色学术主题样式表。
- `io_utils/` — I/O 与序列化，不含计算逻辑：
  - `weather_data.py` — `WeatherDataset`（月度照度 + 温度），默认数据集为湖南益阳 TMY；v2.11.0 新增四川宜宾数据集（夏热冬冷地区，月均温度取自公开气候资料，月均照度因无逐月实测TMY按年日照时数比例估算，非实测值，见代码注释）。
  - `weather_fetcher.py` — 外部气象数据抓取。
  - `project_io.py` — `.rlproj` 工程文件（JSON）的 `save_project` / `load_project`，**向后兼容所有历史版本**，缺失字段自动填默认值——修改 `models.py` 中的数据类字段时需同步检查这里的兼容逻辑。
  - `exporter.py` — Excel 多 Sheet 报告 + PNG 导出。

### 数据流

`Sidebar` 编辑 `RoomModel`/`WeatherDataset` → 信号触发 `RoomCanvas` 实时重绘（几何/立面预览，不做采光计算）→ 用户点击"全部分析" → `AnalysisWorker`（后台线程）调用 `core.daylight.compute()` / `core.thermal.compute_thermal()` → 结果（`DaylightResult` / `ThermalResult`）回传主线程 → `AnalysisPanel` / `ThermalPanel` 渲染热力图/折线图 + KPI → 可通过 `export_dialog.py` 批量导出 PNG/Excel，或通过 `project_io.py` 整体保存为 `.rlproj`。

### 单位规范

内部几何尺寸统一用**毫米（mm）**存储，物理计算时转换为**米（m）**；照度结果单位为**勒克斯（lux）**；热工计算单位为国际单位制（W、K、m）。
