# 建筑室内采光分析工具  v3.0.0

> 基于 CIE 标准全阴天模型的侧窗采光系数与平均照度计算程序
> Python 3.10+  |  PyQt6  |  matplotlib  |  numpy  |  openpyxl
>
> ⚠️ 详细使用说明和完整版本日志见 [MANUAL.md](MANUAL.md)，计算理论见
> [THEORY.md](THEORY.md)。v3.0.0 正在把矩形房间引擎升级为任意多边形、多空间架构。

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
│   ├── models.py                # 数据模型（RoomModel / Window / MaterialParams）
│   └── daylight.py              # 采光计算核心引擎
├── ui/
│   ├── main_window.py           # 主窗口（工具栏 + 布局协调）
│   ├── canvas.py                # 建筑平面 / 立面可视化
│   ├── analysis_panel.py        # 采光热力图 + 统计面板
│   ├── sidebar.py               # 可折叠参数侧边栏
│   ├── weather_dialog.py        # 气象数据输入对话框（手动 / Excel）
│   ├── mpl_font.py              # 中文字体配置
│   └── style.qss                # 全局暗色主题样式
└── io_utils/
    ├── weather_data.py          # 气象数据模型 + Excel/CSV 解析
    └── exporter.py              # Excel 报告 + PNG 导出
```

---

## 使用说明

### 1. 输入房间参数
在左侧侧边栏设置长 / 宽 / 高（mm），添加窗户并设置位置、尺寸和透射比。  
可视化窗口实时更新平面图和立面图，侧边栏底部**快速估算**栏即时显示 Lynes 解析结果。

### 2. 设置气象数据
点击侧边栏**「⚙ 设置气象数据…」**，弹出对话框，支持：
- **手动输入**：逐月填写室外水平照度（lux），提供北京 TMY 参考值一键填充；
- **导入文件**：支持 `.xlsx / .xls / .csv`，自动识别 lux 或 W/m²（自动 ×110 换算）；
- **下载模板**：内置 Excel 模板下载，格式 = `月份 | 室外照度(lux) | GHI(W/m²) 参考`。

### 3. 运行分析
工具栏点击**「▶ 开始分析」**，后台线程计算完成后自动跳转至**「采光分析」**视图，显示：
- 照度热力图（颜色映射 + 等值线 + 300 lux 阈值线）
- 顶部 KPI 栏（平均照度、最低照度、均匀度 U₀、DF_avg、窗地比），绿色=合格，红色=不合格；
- 中心截面照度分布折线图；

### 4. 导出结果
工具栏右侧：
- **↓ PNG**：弹出路径选择，保存热力图（200 dpi）；
- **↓ Excel**：弹出路径选择，保存多 Sheet 报告（汇总 / 照度矩阵 / DF 矩阵 / 气象数据）。

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

### v3.0.0（当前开发版本）

- 新增建筑—楼层—空间—边界环—墙段—洞口的复杂空间数据模型。
- 新增确定性的多边形、空洞、墙段和门窗几何校验。
- 新增旧矩形 `RoomModel` 到复杂空间模型的兼容转换。
- 原“建筑视图”已直接切换为复杂功能模型，不再设置单独的“复杂空间模型”工具栏
  入口；侧栏可编辑空间/窗户、调整气象数据，并切换真实平面图和逐墙立面图。
- 新增任意多边形采光及单区热环境引擎；所有新旧 `.rlproj` 统一进入 v3 建筑数据流，
  旧矩形工程自动转换，保存、拖拽打开和光热分析结果导出继续可用。
- 参数化实验入口和参数页仍保留，但复杂空间迁移完成前禁用运行，避免误调用旧矩形
  引擎；完整多空间编辑仍在后续开发中。

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
