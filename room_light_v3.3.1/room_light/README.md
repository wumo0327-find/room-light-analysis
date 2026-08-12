# 建筑室内采光分析工具  v3.3.1

> 基于 CIE 标准全阴天模型的侧窗采光系数与平均照度计算程序
> Python 3.10+  |  PyQt6  |  matplotlib  |  numpy  |  openpyxl
>
> ⚠️ 详细使用说明和完整版本日志见 [MANUAL.md](MANUAL.md)，计算理论见
> [THEORY.md](THEORY.md)。v3.3.1 已支持在建筑平面中鼠标选择多个房间，并对
> 这些任意多边形空间批量完成采光、热环境与统一遮阳参数化实验。

---

## 快速启动

```bash
pip install -r requirements.txt
python main.py
```

---

## 项目结构

```
room_light/
├── main.py                      # 程序入口
├── requirements.txt
├── core/
│   ├── complex_models.py        # 建筑/楼层/空间/墙段/洞口模型
│   ├── space_geometry.py        # 多边形拓扑与几何校验
│   ├── complex_daylight.py      # 任意多边形采光引擎
│   ├── complex_thermal.py       # 任意墙向单区热环境引擎
│   ├── complex_experiments.py   # 复杂空间参数化遮阳实验
│   └── experiments.py           # 帕累托、均衡推荐与2D/3D绘图
├── ui/
│   ├── main_window.py           # 主窗口（工具栏 + 布局协调）
│   ├── complex_space_editor.py  # 复杂空间编辑、平面/逐墙立面
│   ├── analysis_panel.py        # 采光热力图 + 统计面板
│   ├── thermal_panel.py         # 热环境结果面板
│   ├── experiment_sidebar.py    # 参数实验输入与造价
│   ├── experiment_panel.py      # 帕累托图、推荐与导出
│   ├── weather_dialog.py        # 气象数据输入对话框（手动 / Excel）
│   ├── mpl_font.py              # 中文字体配置
│   └── style.qss                # 全局白色学术主题
└── io_utils/
    ├── project_io.py            # v3工程读写与旧rlproj自动转换
    ├── weather_data.py          # 气象数据模型 + Excel/CSV 解析
    └── exporter.py              # Excel 报告 + PNG 导出
```

---

## 使用说明

### 1. 打开或建立模型
打开 `.rlproj` 后，建筑视图显示当前楼层全部房间。左键点击房间可加入或移出
批量计算范围，右键点击可只选择一个房间；蓝色表示已选，红色边框表示当前房间。
侧边栏仍可编辑空间边界、墙段、窗户、玻璃和气象参数。

### 2. 设置气象数据
点击侧边栏**「⚙ 设置气象数据…」**，弹出对话框，支持：
- **手动输入**：逐月填写室外水平照度（lux），提供北京 TMY 参考值一键填充；
- **导入文件**：支持 `.xlsx / .xls / .csv`，自动识别 lux 或 W/m²（自动 ×110 换算）；
- **下载模板**：内置 Excel 模板下载，格式 = `月份 | 室外照度(lux) | GHI(W/m²) 参考`。

### 3. 运行分析
工具栏点击**「▶ 分析选中房间」**，程序在后台逐房间计算采光与热环境，完成后
自动跳转至采光分析视图。采光/热环境视图显示红框当前房间的结果：
- 照度热力图（颜色映射 + 等值线 + 300 lux 阈值线）
- 顶部 KPI 栏（平均照度、最低照度、均匀度 U₀、DF_avg、窗地比），绿色=合格，红色=不合格；
- 中心截面照度分布折线图；

### 4. 参数化与导出
参数化实验把同一组遮阳几何与材料应用到全部已选房间，以面积加权汇总采光和
热舒适指标、合计真实工程造价，再求整组房间的全局帕累托与均衡推荐。

工具栏「导出结果」会逐房间保存采光/热环境图片；多房间时额外生成
`多房间分析汇总.csv`。参数化实验继续导出默认3D图、三张2D图、完整CSV和各个
已选房间采用推荐遮阳后的采光/热环境图片。

---

## 计算理论验证

### 概述

本程序采用三分量采光系数（Daylight Factor, DF）法，依据国际主流文献与中国建筑采光设计标准实现，以下逐一列出计算公式、假设条件和引用来源，证明程序在理论上的准确性与适用范围。

---

### 1. 天空亮度模型：CIE 标准全阴天

**公式：**

$$L(\theta) = L_z \cdot \frac{1 + 2\sin\theta}{3}$$

$$E_{\text{out}} = \frac{7\pi}{9} L_z \quad \Rightarrow \quad L_z = \frac{9}{7\pi} E_{\text{out}}$$

- $\theta$：仰角（rad），$0$ = 水平，$\pi/2$ = 天顶  
- $L_z$：天顶亮度（cd/m²）  
- $E_{\text{out}}$：室外水平面照度（lux）

**引用来源：**

> CIE Publication 110-1994, *Spatial Distribution of Daylight — CIE Standard Overcast Sky*,  
> Commission Internationale de l'Éclairage, Vienna, 1994.

**适用说明：** CIE 全阴天模型是采光系数计算的标准天空条件，假设全云覆盖、无直射阳光，天空亮度仅随仰角变化。该模型给出静态采光系数，适用于最不利（最暗）工况评估，符合 GB/T 50033-2013 附录 A 规定。

---

### 2. 天空分量 $D_s$：数值立体角积分

**推导：**

工作面上测点 $P$ 受到窗口透过的天空光照度为：

$$E_s = \int_{\Omega_{\text{win}}} L(\theta) \cdot \cos\beta \, d\omega_P$$

其中 $d\omega_P$ 为测点处的立体角微元，$\beta$ 为入射光与工作面法向的夹角。  
将窗口离散为 $N \times N$ 个小面元 $Q_{ij}$，每个面元面积 $\Delta A$：

$$d\omega_{P \leftarrow Q_{ij}} = \frac{\cos\alpha_{ij} \cdot \Delta A}{r_{ij}^2}$$

$\alpha_{ij}$ 为面元法向与 $\overrightarrow{PQ_{ij}}$ 方向的夹角，$r_{ij} = |\overrightarrow{PQ_{ij}}|$。  
代入 CIE 亮度分布，并除以室外照度归一化：

$$D_s = \frac{\tau}{\pi E_{\text{out}}} \sum_{i,j} L(\theta_{ij}) \cdot \cos\beta_{ij} \cdot \frac{\cos\alpha_{ij} \cdot \Delta A}{r_{ij}^2}$$

（结果乘 100 转换为百分比）

**实现参数：** 窗口默认 $N = 20$，即 400 个面元，精度与速度平衡；计算时自动过滤仰角 $\theta \le 0$（地平线以下）及负余弦项。

**引用来源：**

> Hopkinson, R.G., Petherbridge, P., & Longmore, J. (1966).  
> *Daylighting*. Heinemann, London. pp. 33–42.  
>
> CIE 110-1994, Section 4.3 — Sky Component Calculation.

---

### 3. 室外反射分量 $D_{\text{ext}}$：Littlefair 简化公式

**公式：**

$$D_{\text{ext}} = \frac{\rho_g \cdot \tau \cdot (1 - \cos\beta_{\text{sill}})}{2}$$

- $\rho_g$：室外地面反射率（默认 0.20）  
- $\beta_{\text{sill}}$：测点 $P$ 对窗台底边中点的仰角  
- $\tau$：玻璃可见光透射比

**引用来源：**

> Littlefair, P.J. (1991).  
> *Site Layout Planning for Daylight and Sunlight: A Guide to Good Practice*.  
> BRE Report BR 209, Building Research Establishment, Watford. Eq. (2.2), p. 11.

**适用说明：** 该公式假设室外地面为均匀漫反射体，适用于无外部遮挡的侧窗场景。

---

### 4. 室内反射分量 $D_{\text{int}}$：BRS 互反射简化法

**公式：**

$$D_{\text{int}} = \frac{\bar{\rho} \cdot (D_s + D_{\text{ext}})}{1 - \bar{\rho}}$$

加权平均反射率：

$$\bar{\rho} = \frac{\rho_c A_c + \rho_w A_w + \rho_f A_f}{A_c + A_w + A_f}$$

- $\rho_c, \rho_w, \rho_f$：顶棚、墙面、地面反射率  
- $A_c, A_w, A_f$：对应面积（窗口面积从墙面扣除）

**引用来源：**

> Hopkinson, R.G., Petherbridge, P., & Longmore, J. (1966).  
> *Daylighting*. Heinemann, London. p. 384 (BRS Interreflection Method).  
>
> Mardaljevic, J. (2000).  
> "Simulation of Annual Daylighting Profiles for Internal Illuminance."  
> *Lighting Research & Technology*, 32(3), 111–118.

**精度说明：** BRS 简化公式假设室内各表面为均匀漫反射 Lambertian 体，光线经多次漫反射趋近均匀分布（积分球近似）。文献表明，在 $\bar{\rho} < 0.6$ 时与完整辐射度法（Radiosity）相比误差通常 < 5%。

---

### 5. 总采光系数

$$DF = D_s + D_{\text{ext}} + D_{\text{int}}$$

$$E_{\text{avg}} = \frac{1}{N} \sum_{P} DF(P) \times E_{\text{out}}$$

---

### 6. 快速估算：Lynes 通量法（Flux Method）

**公式：**

$$\bar{E} = \frac{E_{\text{out}} \cdot \tau_{\text{eff}} \cdot A_w}{A_f \cdot (1 - \bar{\rho})}$$

- $A_w$：总窗面积（m²）  
- $A_f$：地板面积（m²）  
- $\tau_{\text{eff}}$：面积加权有效透射比  
- WFR（窗地比）= $A_w / A_f$

**引用来源：**

> Lynes, J.A. (1968).  
> *Principles of Natural Lighting*. Elsevier, London. p. 95.

**精度说明：** 通量法为解析估算，假设室内光线均匀分布，适用于方案阶段快速预判。与数值积分法误差通常在 ±15% 以内（开窗面积较大时偏差增大）。程序中将其作为即时反馈，不替代数值积分结果。

---

### 7. 合规判定标准

| 指标 | 合格线 | 依据标准 |
|------|--------|----------|
| 平均照度 $E_{\text{avg}}$ | ≥ 300 lux | GB/T 50033-2013 表 4.0.4，III 类（办公室/教室） |
| 采光均匀度 $U_0 = E_{\min}/E_{\text{avg}}$ | ≥ 0.70 | GB/T 50033-2013 §5.0.2 |
| 平均采光系数 $DF_{\text{avg}}$ | ≥ 2.0% | GB/T 50033-2013 表 4.0.4，III 类 |

---

### 8. 计算假设与局限性

| 项目 | 当前版本 | 后续可扩展方向 |
|------|---------|----------------|
| 天空模型 | CIE 全阴天（静态 DF） | Perez 动态天空 / CBDM（气候数据驱动） |
| 室内反射 | BRS 简化公式（均匀漫反射假设） | 完整 Radiosity 解 |
| 外部遮挡 | 不考虑 | 周边建筑遮挡角 |
| 窗型 | 矩形侧窗 | 天窗、斜屋顶窗 |
| 窗框遮蔽 | 已含于 $\tau$ | 单独窗框系数 |
| 工作面 | 固定 0.75 m 水平面 | 任意高度 |

---

## 单位规定

程序内部所有几何尺寸统一使用 **毫米（mm）**，物理计算时转换为 **米（m）**，照度结果输出为 **勒克斯（lux）**。

---

## 参考文献

1. CIE (1994). *CIE 110-1994: Spatial Distribution of Daylight — CIE Standard Overcast Sky*. Vienna: CIE.
2. GB/T 50033-2013. *建筑采光设计标准*. 北京: 中国建筑工业出版社.
3. Hopkinson, R.G., Petherbridge, P., & Longmore, J. (1966). *Daylighting*. London: Heinemann.
4. Littlefair, P.J. (1991). *Site Layout Planning for Daylight and Sunlight*. BRE Report 209. Watford: BRE.
5. Lynes, J.A. (1968). *Principles of Natural Lighting*. London: Elsevier.
6. Mardaljevic, J. (2000). "Simulation of Annual Daylighting Profiles for Internal Illuminance." *Lighting Research & Technology*, 32(3), 111–118.
7. Muneer, T. (2004). *Solar Radiation and Daylight Models*. 2nd ed. Oxford: Elsevier Butterworth-Heinemann.

---

## 版本更新记录

### v3.3.1（当前版本）

- **修复建筑平面忽略墙厚的问题**：整层平面和空间编辑预览不再用屏幕像素宽度
  的单线代替墙体，而是按每段墙的 `thickness_mm` 在毫米坐标中生成实体墙带。
- 墙体转角采用方形延伸相互覆盖，避免真实宽度显示后在L形、T形连接处出现缺口；
  选中房间和当前房间仍分别使用蓝色、红色边线，但墙体填充范围保持真实宽度。
- 窗体会在实体墙带中形成可见洞口，并按 `plane_offset_mm` 显示真实玻璃面位置；
  退进窗继续保留玻璃面与宿主墙之间的连接虚线。
- 建筑视图侧栏新增当前房间墙厚汇总，逐墙立面下拉项和立面标题显示该墙实际厚度。
  旧 `.rlproj` 无需转换即可读取，缺失墙厚时继续使用模型默认值。

### v3.3.0

- 新增退进/外移玻璃面的真实几何：`plane_offset_mm` 允许窗面位于宿主墙外侧，
  采光射线、地面反射分量和建筑平面显示均使用实际窗面位置；旧工程默认为0，
  计算结果保持不变。
- 新增显式采光屏障：实体返回墙、栏杆、女儿墙和屏风可保存到 `.rlproj`，并可
  区分位于玻璃外、测点与玻璃之间或整条射线路径；实体墙完全遮挡，开敞栏杆按
  有效透光比衰减。
- 新增长沙幼儿园二层活动室精细样例：按用户标注PDF重建三个6000×6600 mm活动室、
  1500 mm退进窗面、900+2300+900 mm窗组、2800 mm栏杆段、相邻寝室、盥洗卫生/
  衣帽带与公共走廊；平面尺寸与待立面复核的竖向假设分开记录。
- 建筑视图新增整层平面与鼠标选房：左键加入/移出批量范围，右键单选；蓝色为已选，
  红框为当前结果房间，选择状态随 `.rlproj` 保存。
- 采光和热环境按选中房间逐一计算、缓存与导出，不计算同层未选空间；多房间导出
  自动附带采光、热舒适度和基本几何指标汇总CSV。
- 参数化遮阳实验不再调用旧矩形 `RoomModel`，直接克隆所有选中的 `SpaceModel`，
  使用真实多边形、外墙朝向、外窗位置/尺寸、逐窗玻璃和当前气候。
- 同一组水平遮阳 `θ×L×h×材料` 统一应用到全部选中房间外窗；采光和热指标按
  地面面积加权，造价按全部房间工程量合计。原工程状态作为改造前基准，`L=0`
  只计算一次，不随角度、间隙或材料重复。
- 同一组 `θ/L/h` 只运行一次复杂空间采光，再按材料分别计算热环境；材料、支撑和
  安装造价按真实外窗宽度、外窗数量和板长计算。
- 所有材料和复杂空间几何组合继续进入同一个三目标候选池，二维投影各自判断二维
  帕累托，3D空中点云判断 `Ra↑ / 热指标更优 / 成本↓` 的全局帕累托。
- 均衡推荐方案会为每个选中房间重新运行完整复杂空间采光和热环境，可在结果页
  查看，并随四图、CSV及逐房间推荐方案图片一起导出。
- 新增长沙幼儿园CAD清理样例：34个闭合房间、43扇外窗，附原始DWG/DXF、图层
  核对图、可重复生成脚本与数据限制说明。
- 当前扫描范围仍是“全部外窗统一一种水平遮阳”；逐墙/分朝向独立优化和复杂空间
  垂直翼板遮挡计算留待后续版本。

### v3.0.0

- 新增建筑—楼层—空间—边界环—墙段—洞口的复杂空间数据模型。
- 新增确定性的多边形、空洞、墙段和门窗几何校验。
- 新增旧矩形 `RoomModel` 到复杂空间模型的兼容转换。
- 原“建筑视图”已直接切换为复杂功能模型，不再设置单独的“复杂空间模型”工具栏
  入口；侧栏可编辑空间/窗户、调整气象数据，并切换真实平面图和逐墙立面图。
- 新增任意多边形采光及单区热环境引擎；所有新旧 `.rlproj` 统一进入 v3 建筑数据流，
  旧矩形工程自动转换，保存、拖拽打开和光热分析结果导出继续可用。
- 参数化实验入口和参数页保留，等待后续迁移到复杂空间；完整多空间编辑仍在后续
  开发中。

### v2.14.0

- 参数化实验改为跨全部材料、倾角、板长和间隙的全局三目标帕累托筛选。
- 三张二维图共享同一批全局帕累托红圈，二维虚线仅表达对应投影边界。
- 3D图保留Ra、热指标和成本原始值，热不舒适度/成本轴反向显示，使更优方案靠外；
  三面与对应2D图共用坐标范围和方向。
- 新增等权均衡推荐星标、帕累托显示开关及四张PNG批量导出。
- CSV新增全局帕累托与均衡推荐标记；玻璃组仅作参照，不参与遮阳优化。

### v2.3.0
**新增功能：**
- **[新增] 项目保存** — 工具栏「💾 保存」/「另存为」按钮，将房间几何、窗户参数、材料反射率、地理位置、气象数据全部序列化为  JSON 文件，首次保存弹出路径选择，后续保存直接覆盖
- **[新增] 项目打开** — 工具栏「📂 打开」按钮，选择  文件加载；加载后侧边栏所有控件自动更新，画布实时刷新
- **[新增] 拖拽导入** — 将  文件直接拖入程序窗口即可加载，无需通过按钮
- **[新增] 标题栏显示文件名** — 打开/保存文件后标题栏显示当前文件名，便于识别工作状态
- **[修复] view_btn 选中态文字不可见** — active 状态从蓝底白字改为浅蓝底深蓝粗体字，白色背景下清晰可见

**项目文件格式（.rlproj）：**

### v2.2.1
**修复与改进：**
- **[修复] 导出热力图布局错乱** — `save_heatmap` 改用 `GridSpec(width_ratios=[1, 0.045])` 精确控制颜色条宽度，热力图不再被挤压到角落；通过 `FigureCanvasAgg` 附加离屏画布保证 `savefig` 正常工作
- **[修复] 滚轮调值问题彻底解决** — `_NoScrollSpinBox.wheelEvent` 改为无条件 `event.ignore()`，完全屏蔽滚轮调值行为，无论控件是否聚焦；数值只能通过点击箭头或键盘输入修改
- **[修复] 白底白字不可见** — `_lbl()` 默认颜色从 `#e8edf5`（白）改为 `#1a1e2e`（近黑）；气象数据对话框 `WeatherDialog` 内嵌样式表全面切换为白色学术主题，消除所有白字覆盖白底的问题
- **[更新] README 增加版本更新记录** — 每次版本更新在此记录变更内容

### v2.2.0
- 白色学术主题（QSS 全面重写，matplotlib 图表同步白底）
- 图片导出分为两张：`*_热力图.png` + `*_截面分布.png`，白底适合论文插入
- SpinBox 滚轮屏蔽（初版，基于 `hasFocus()` 判断）
- Excel 报告配色切换为白色学术风格

### v2.1.1
- **[修复]** 添加窗户闪退（SpinBox `setValue` 在构建期触发 `valueChanged` 导致崩溃）
- `_dspin()` 构建期使用 `blockSignals(True/False)` 包围 `setValue`
- `_upd` 回调改为通过 `win_id` 查找窗户，避免悬空引用
- `_del_win` 调整销毁顺序：先发信号再 `deleteLater`
- `CollapsibleSection` 标题用 `self._title` 存储，消除 `text()[2:]` 切片越界

### v2.1
- 湖南益阳 TMY 气象数据作为程序默认值（CSWD/中国气象局，年均 13 906 lux）
- 网格数值标注开关（「显示网格数值」复选框）
- 计算精度提升：`WIN_DIV=40`（1600面元/窗），`GRID_MM=250`
- KPI 栏数值显示 4 位小数，便于对照实验分辨差异

### v2.0
- 气象数据输入：手动输入表格 + Excel/CSV 文件导入
- 采光分析核心引擎（CIE 110-1994 + BRS + Littlefair BRE 209）
- 热力图分析面板（照度分布 + 截面图 + KPI 统计栏）
- Excel 多 Sheet 报告导出 + PNG 导出

### v0.2 / v0.1
- 初始 UI 框架（PyQt6 + matplotlib）
- 建筑平面图 / 四向立面图可视化
- 侧边栏参数输入与实时联动
