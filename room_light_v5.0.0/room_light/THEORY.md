# 计算理论与公式推导

> 本文档用于论文方法论章节引用，涵盖程序中所有计算模块的物理公式、假设条件、精度分析与文献来源。

---

## 1. 采光分析模块

### 1.0 v4 几何计算口径

BP 绘图中的墙体采用可设置“居中/靠左/靠右”的宿主轴线，窗户沿宿主轴线保存偏移和宽度。v4 不再把该轴线直接当作室内净边界，而是同时生成实体墙朝房间一侧的内表面多边形：

- 墙、窗宿主关系、窗洞起止位置和玻璃面法向使用 BP 轴线；
- 房间净面积、工作面采样范围、测点距墙边距离使用实体墙内表面净边界；
- 热工外墙面积使用各墙段的内表面净长度乘层高，再扣除洞口；
- 窗贴墙角时允许 `offset=0`，不会为了墙厚而强制把窗向内移动；玻璃面到宿主轴线的偏移由墙轴线位置自动确定。

分析前执行 BP/RL 拓扑、房间签名和窗位置/尺寸核验。该步骤保证“显示模型”和“输入计算的几何”一致，但不等同于证明下述简化物理模型与真实建筑完全一致。

### 1.1 天空亮度模型：CIE 标准全阴天

采用 CIE 110-1994 标准全阴天（Overcast Sky）模型，天空亮度仅随仰角 $\theta$ 变化：

$$L(\theta) = L_z \cdot \frac{1 + 2\sin\theta}{3}$$

$$E_{out} = \frac{7\pi}{9} L_z \quad \Rightarrow \quad L_z = \frac{9}{7\pi} E_{out}$$

- $\theta$：仰角（rad），0 = 水平，$\pi/2$ = 天顶  
- $L_z$：天顶亮度（cd/m²）  
- $E_{out}$：室外水平面照度（lux）

该模型给出静态采光系数，用于最不利（最暗）工况评估，符合 GB/T 50033-2013 附录 A 规定。

**来源：** CIE Publication 110-1994, *Spatial Distribution of Daylight — CIE Standard Overcast Sky*, Vienna.

---

### 1.2 天空分量 $D_s$：数值立体角积分

将窗口离散为 $N \times N$（$N=40$）个小面元 $Q_{ij}$，对测点 $P$ 进行数值积分：

$$D_s = \frac{\tau}{\pi E_{out}} \sum_{i=1}^{N}\sum_{j=1}^{N} L(\theta_{ij}) \cdot \cos\beta_{ij} \cdot \frac{\cos\alpha_{ij} \cdot \Delta A}{r_{ij}^2}$$

其中：
- $\theta_{ij}$：面元 $Q_{ij}$ 相对测点 $P$ 的仰角
- $\alpha_{ij}$：窗面法向与 $\overrightarrow{PQ_{ij}}$ 的夹角（入射角）
- $\beta_{ij}$：工作面法向（竖直向上）与 $\overrightarrow{PQ_{ij}}$ 的夹角
- $\Delta A$：面元面积，$\Delta A = (w_x/N)(w_z/N)$（m²）
- $\tau$：玻璃可见光透射比

计算中过滤以下无效贡献：$\sin\theta_{ij} \le 0$（地平线以下）；$\cos\alpha_{ij} \le 0$（背面）；$\cos\beta_{ij} \le 0$（工作面以下）。

**精度控制：** 正式结果默认 $N=40$（每扇窗1600个面元），参数化实验先以 $N=20$ 扫描，再对二维/三维帕累托和推荐候选按 $N=40\rightarrow60\rightarrow80$ 自适应复核，相邻两级达到 $|\Delta Ra|\le0.005$、$|\Delta U_0|\le0.01$ 即停止。月均热模型不依赖窗面离散数，不随 $N$ 重复计算。v4 不宣称所有几何下都具有固定相对误差；工作面网格默认250 mm，其离散误差仍需通过网格敏感性或独立软件/实测验证评估。

**来源：**
- CIE 110-1994, Section 4.3
- Hopkinson R.G., Petherbridge P. & Longmore J. (1966). *Daylighting*. Heinemann, London. pp. 33–42.

---

### 1.3 室外反射分量 $D_{ext}$：Littlefair 简化公式

$$D_{ext} = \frac{\rho_g \cdot \tau \cdot (1 - \cos\beta_{sill})}{2}$$

- $\rho_g$：室外地面反射率（默认 0.20）
- $\beta_{sill}$：测点对窗台底边中点的仰角
- $\tau$：玻璃透射比

**来源：** Littlefair P.J. (1991). *Site Layout Planning for Daylight and Sunlight*. BRE Report 209. Watford: BRE. Eq.(2.2), p.11.

---

### 1.4 室内反射分量 $D_{int}$：BRS 互反射简化法

$$D_{int} = \frac{\bar{\rho} \cdot (D_s + D_{ext})}{1 - \bar{\rho}}$$

加权平均反射率：

$$\bar{\rho} = \frac{\rho_c A_c + \rho_w A_w + \rho_f A_f}{A_c + A_w + A_f}$$

（$A_w$ 为墙面净面积，已扣除窗口面积）

**适用范围：** 该式是低阶互反射近似；当 $\bar{\rho}$ 较高、空间强烈凹凸或存在复杂高反射遮挡面时误差可能明显增大。程序不把任何固定误差上限写作已验证结论，论文使用前应与 Radiance 或实测结果交叉验证。

**来源：** Hopkinson R.G. et al. (1966). p.384（BRS Interreflection Method）; Mardaljevic J. (2000). *Lighting Research & Technology*, 32(3), 111–118.

---

### 1.5 总采光系数与照度

$$DF = D_s + D_{ext} + D_{int} \quad [\%]$$

$$E(x,y) = \frac{DF(x,y)}{100} \times E_{out} \quad [\text{lux}]$$

**合规判定标准（GB/T 50033-2013 表4.0.4，III类空间）：**

| 指标 | 阈值 |
|------|------|
| 平均照度 $E_{avg}$ | ≥ 300 lux |
| 均匀度 $U_0 = E_{min}/E_{avg}$ | ≥ 0.70 |
| 平均采光系数 $DF_{avg}$ | ≥ 2.0% |

---

### 1.6 快速解析估算：Lynes 通量法

$$\bar{E} = \frac{E_{out} \cdot \tau_{eff} \cdot A_w}{A_f \cdot (1 - \bar{\rho})}$$

（仅用于侧边栏实时预估，不用于报告）

**来源：** Lynes J.A. (1968). *Principles of Natural Lighting*. Elsevier, London. p.95.

---

## 2. 热环境分析模块

### 2.1 单区集总热平衡模型

在无机械空调干预的自然工况下，月均稳态室内温度由以下方程求解：

$$T_{in,m} = T_{out,m} + \frac{Q_{solar,m} + Q_{wall\_solar,m} + Q_{int}}{H_{envelope} + H_{vent,m}}$$

通风换热系数按换气次数直接计算：

$$H_{vent,m} = \frac{n_{ach} \cdot V \cdot \rho_{air} \cdot c_p}{3600} \quad [\text{W/K}]$$

**来源：**
- GB 50176-2016 §6 稳态热平衡方法
- ISO 13786:2017 建筑构件热性能计算

---

### 2.2 围护结构总热导 $H_{envelope}$

$$H_{envelope} = U_{wall} A_{wall,net} + U_{win} A_{win} + \chi_{roof}U_{roof} A_{floor} + \chi_{floor}U_{floor} A_{floor} + \Psi_{edge} L_{bridge}$$

其中 $\chi_{roof}$ 与 $\chi_{floor}$ 为上、下边界暴露开关：顶层房间的屋面、首层接地地面按实际情况取 1；与恒温相邻房间或非室外环境相接时取 0。v4.0.0 可按所选房间批量设置，避免把所有房间都误算成“顶层且首层”。

热桥周长：

$$L_{bridge} = n_{corner}H + 2P_{ext}$$

其中 $n_{corner}$ 只统计外墙方向真正发生变化的转角，BP 中同一直墙的共线拆段不重复计入；$P_{ext}$ 为外墙净几何周长。

**参考：** ISO 14683:2017, $\Psi_{edge}$ 默认值 0.10 W/(m·K)（无热桥构造详图时的保守估算）。

---

### 2.3 太阳辐射得热 $Q_{solar,m}$

$$Q_{solar,m} = \sum_{j \in windows} A_{win,j} \cdot SC_{eff,j} \cdot I_{T,j,m} \cdot \eta_{frame}$$

各朝向竖直面辐射强度由水平面 GHI 经朝向修正系数转换：

$$I_{T,m}^{wall} = GHI_m \times f_{orient,m}$$

朝向修正系数 $f$ 按 28.59°N（益阳纬度）精算（Duffie & Beckman，2013，Table 2.13.1）：

| 朝向 | 年均 $f$ | 说明 |
|------|---------|------|
| 南 | 0.84–1.48（逐月）| 冬季最大，夏季最小 |
| 东/西 | 0.65（均值）| 年内变化不大 |
| 北 | 0.11–0.28（逐月）| 以散射为主 |

有效遮阳系数：$SC_{eff} = SC_{glass} \times f_{shading}$（v2.3 实现遮阳构件修正）

**来源：** Duffie J.A. & Beckman W.A. (2013). *Solar Engineering of Thermal Processes*, 4th ed. Wiley.

---

### 2.4 外墙太阳辐射附加得热 $Q_{wall\_solar,m}$

$$Q_{wall\_solar,m} = \frac{\alpha_{wall} \cdot GHI_m \cdot \bar{f}_{vert} \cdot A_{wall,net}}{R_{wall,ext}}$$

- $\alpha_{wall}$：外墙太阳辐射吸收系数（深色 0.80，浅色 0.40，默认 0.65）
- $\bar{f}_{vert} = 0.60$：各朝向竖直面平均相对水平面的辐射比（简化均值）
- $R_{wall,ext}$：外表面热阻（含对流），由墙体参数估算

此项通常被简化方法忽略，本程序将其显式纳入方案间相对比较。其对室温的绝对影响取决于墙体构造、辐射数据和使用工况，必须通过独立动态模拟或实测校核，不在此预设固定温差。

---

### 2.5 内热扰 $Q_{int}$

$$Q_{int} = (q_{people} + q_{equipment} + q_{lighting}) \times A_f$$

| 参数 | 默认值 | 依据 |
|------|--------|------|
| $q_{people}$ | 6.0 W/m² | 幼儿园约 30 人/133m²，65 W/人（GB 50736-2012）|
| $q_{equipment}$| 5.0 W/m² | 投影、电脑等 |
| $q_{lighting}$ | 4.0 W/m² | LED 照明（约 4 W/m²）|

---

### 2.6 蓄热修正：指数加权双向平滑

墙体热容引起的热惰性用时间常数 $\tau$ 量化：

$$\tau = \frac{m_{wall} \cdot c_p}{H_{envelope}} \quad [\text{hours}]$$

$$m_{wall} = \frac{d_{wall}}{1000} \cdot A_{wall,net} \cdot \rho_{wall}$$

对逐月温度序列应用双向指数加权移动平均（平滑权重 $\alpha = 1 - e^{-720/\tau}$，720 h ≈ 1个月），模拟月尺度上墙体热惰性对室内温度波动的阻尼效应。

**物理意义：** 蓄热系数越大（厚重实体墙），月际温度波动越平缓；轻质墙体则月际响应更迅速。

---

### 2.7 热舒适判定标准

| 指标 | 阈值 | 依据 |
|------|------|------|
| 舒适温度下限 | 18℃ | GB/T 50785-2012 §4 自然通风工况 |
| 舒适温度上限 | 26℃ | GB/T 50785-2012 §4 自然通风工况 |
| 舒适月数目标 | ≥ 7 月 | 夏热冬冷地区参考值 |

---

### 2.8 模型假设与局限性

| 假设 | 影响 | 处理方式 |
|------|------|----------|
| 月均稳态（忽略日波动） | 无法表达峰值、逐时波动和使用时段 | 只用于月级方案趋势比较，绝对误差需另行验证 |
| 单区均匀混合 | 忽略室内温度分布和房间内部气流 | 每个所选房间独立作为一个热区计算 |
| 固定内热扰 | 忽略使用时段变化 | 保守估算（偏高估热） |
| 无渗透风修正 | 夏季偏高估室温 | 对超温分析偏安全 |
| SC 全局值 | 不区分朝向差异 | v2.3 引入遮阳构件后逐窗精算 |

**论文表述建议：** 本模型采用月均单区集总热平衡，用于同一建筑、同一气象和同一使用假设下不同遮阳方案的相对比较。它不是逐时动态能耗模型，未经 EnergyPlus 或实测校核时，不应将自然室温绝对值表述为论文级已验证结果。

---

## 3. 参数默认值汇总

| 参数 | 默认值 | 单位 | 来源 |
|------|--------|------|------|
| 外墙传热系数 $U_{wall}$ | 1.50 | W/(m²K) | GB 50189-2015（既有砖混） |
| 屋顶传热系数 $U_{roof}$ | 1.00 | W/(m²K) | GB 50189-2015 |
| 外窗传热系数 $U_{win}$ | 2.70 | W/(m²K) | 普通中空 6+12A+6 |
| 玻璃遮阳系数 $SC$ | 0.85 | — | 普通中空白玻（无遮阳） |
| 外墙吸收系数 $\alpha$ | 0.65 | — | GB 50176-2016 表 B.4 |
| 换气次数 $n_{ach}$ | 0.5 | 次/h | GB 50736-2012 §6.3（非使用时段） |
| 热桥线传热系数 $\Psi$ | 0.10 | W/(mK) | ISO 14683 默认值 |
| 工作面高度 | 750 | mm | GB/T 50033-2013 |
| 窗口离散数 $N$ | 40 | — | 程序精度设定 |
| 计算网格步长 | 250 | mm | 程序精度设定 |

---

## 4. 完整参考文献

1. CIE (1994). *CIE 110-1994: Spatial Distribution of Daylight — CIE Standard Overcast Sky*. Vienna: CIE.
2. Hopkinson R.G., Petherbridge P. & Longmore J. (1966). *Daylighting*. London: Heinemann.
3. Littlefair P.J. (1991). *Site Layout Planning for Daylight and Sunlight*. BRE Report 209. Watford: BRE.
4. Lynes J.A. (1968). *Principles of Natural Lighting*. London: Elsevier.
5. Mardaljevic J. (2000). "Simulation of Annual Daylighting Profiles for Internal Illuminance." *Lighting Research & Technology*, 32(3), 111–118.
6. Duffie J.A. & Beckman W.A. (2013). *Solar Engineering of Thermal Processes*, 4th ed. Wiley.
7. GB/T 50033-2013. 建筑采光设计标准. 北京: 中国建筑工业出版社.
8. GB 50176-2016. 民用建筑热工设计规范. 北京: 中国建筑工业出版社.
9. GB 50189-2015. 公共建筑节能设计标准. 北京: 中国建筑工业出版社.
10. GB 50736-2012. 民用建筑供暖通风与空气调节设计规范.
11. GB/T 50785-2012. 民用建筑室内热湿环境评价标准.
12. ISO 13786:2017. *Thermal performance of building components — Dynamic thermal characteristics*.
13. ISO 14683:2017. *Thermal bridges in building construction — Linear thermal transmittance*.
14. 柳孝图 (2010). 《建筑物理》第三版. 北京: 中国建筑工业出版社.
15. 崔艳秋等 (2010). "寒冷地区窗口百叶外遮阳节能改造设计策略探讨." *建筑科学*.
