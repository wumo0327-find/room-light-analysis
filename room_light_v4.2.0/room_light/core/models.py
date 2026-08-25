"""
core/models.py — Legacy rectangular data models  v4.2.0
All internal geometry units: millimetres (mm)
Thermal units: SI (W, K, m)
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# ── 墙向映射 ─────────────────────────────────────────────────────────────────
WALL_NAMES = ["南", "北", "东", "西"]
WALL_KEYS  = ["south", "north", "east", "west"]
WALL_MAP   = dict(zip(WALL_NAMES, WALL_KEYS))
WALL_MAP_R = dict(zip(WALL_KEYS, WALL_NAMES))


# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class Window:
    """矩形外窗。内部单位 mm。"""
    wall:   str   = "south"
    x:      float = 300.0    # mm from wall left edge
    y:      float = 900.0    # mm from floor (sill height)
    width:  float = 1500.0   # mm
    height: float = 1500.0   # mm
    tau:    float = 0.71     # visible-light transmittance
    id:     int   = 0

    @property
    def sill(self)  -> float: return self.y
    @property
    def head(self)  -> float: return self.y + self.height
    @property
    def right(self) -> float: return self.x + self.width
    @property
    def area_m2(self) -> float: return (self.width / 1e3) * (self.height / 1e3)

    def wall_length(self, room: "RoomModel") -> float:
        return room.width if self.wall in ("south","north") else room.length

    def label(self) -> str:
        return f"{WALL_MAP_R.get(self.wall, self.wall)}窗 #{self.id}"


# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class MaterialParams:
    """光学反射率参数（用于采光计算）"""
    rho_wall:    float = 0.50   # 墙面反射率
    rho_ceiling: float = 0.70   # 顶棚反射率
    rho_floor:   float = 0.20   # 地面反射率
    rho_ground:  float = 0.20   # 室外地面反射率


# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class ThermalParams:
    """
    围护结构热工参数（用于热环境计算）
    参考标准: GB 50189-2015, GB 50176-2016, ISO 13786
    """
    # ── 传热系数 U  [W/(m²·K)] ───────────────────────────────────────────────
    U_wall:  float = 1.50   # 外墙  （既有砖混240mm约1.5，节能改造后≈0.6）
    U_roof:  float = 1.00   # 屋顶  （不上人屋面平均值）
    U_floor: float = 1.50   # 地面/楼板（贴地层）
    U_win:   float = 2.70   # 外窗  （普通中空6+12A+6，GB50189-2015限值3.0）

    # ── 墙体物理属性（影响蓄热修正精度）───────────────────────────────────────
    wall_thickness_mm: float = 240.0   # mm  外墙厚度
    wall_density:      float = 1800.0  # kg/m³  砖砌体密度
    wall_specific_c:   float = 1050.0  # J/(kg·K)  比热容
    wall_solar_abs:    float = 0.65    # 外表面太阳辐射吸收系数 α
    # （浅色涂料≈0.40，中灰≈0.65，深色≈0.80）

    # ── 开窗热工参数 ─────────────────────────────────────────────────────────
    SC_glass:    float = 0.85   # 玻璃遮阳系数（无外遮阳时）
    eta_frame:   float = 0.70   # 窗框有效透光面积系数（含框遮挡）

    # ── 内热扰密度  [W/m²] ───────────────────────────────────────────────────
    q_people:    float = 6.0    # 人员散热（幼儿园≈30人/133m²，65W/人）
    q_equipment: float = 5.0    # 设备散热（投影仪、电脑等）
    q_lighting:  float = 4.0    # 人工照明散热（LED）

    # ── 通风换气 ─────────────────────────────────────────────────────────────
    n_ach:       float = 0.5    # 次/h  自然渗透换气次数（GB 50736-2012 §6.3）

    # ── 热桥线传热系数  [W/(m·K)] ────────────────────────────────────────────
    psi_edge:    float = 0.10   # 墙角/楼板边等线热桥（ISO 14683 默认值）


# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class ShadingDevice:
    """
    外遮阳构件。
    v2.2 预留接口；v2.5 实现「水平挑檐 horizontal_overhang」的采光+热工精算；
    v2.7 新增「垂直遮阳翼板/装饰柱 vertical fin」的采光几何遮挡（仅采光，与
    水平挑檐互相独立、可同时生效）（百叶 louver / 导光板 light_shelf 仍为预留）；
    v2.10 新增逐窗/逐位置覆盖：水平挑檐可对单扇窗户单独设置出挑深度/安装间隙，
    垂直翼板可对每扇窗户的左/右两侧分别单独设置出挑深度（含两端外墙位置，
    见 overhang_overrides / fin_overrides 与 get_overhang_for / get_fin_depth_for）。

    采光：见 core/daylight.py 对天空面元的几何遮挡判定（水平挑檐 + 垂直翼板）。
    热工：见 core/thermal.py，逐月有效 SC = SC_glass·(1 − f·(1−k_diff))，
          f 为挑檐阴影对窗口的遮挡高度比例（beam_shade_fraction，v2.10 起支持
          传入逐窗覆盖的出挑深度/间隙），
          k_diff = diffuse_residual 为完全遮挡时的残余透过比（散射+地反射）。
          垂直翼板目前仅影响采光 Ds，未接入热工 SC 计算。
    """
    type: str = "none"
    # "none" | "horizontal_overhang" | "louver" | "light_shelf"

    # 水平固定遮阳板（全局默认值，逐窗覆盖见 overhang_overrides）
    overhang_depth_mm:  float = 0.0    # mm 挑檐板长度L（沿板自身方向，非水平投影；
                                        #    θ=90°水平时数值上等于水平出挑深度）
    overhang_height_mm: float = 0.0    # mm 挑檐板根（贴墙处）高出窗顶的距离（贴窗顶=0）

    # v2.12.0 新增：挑檐倾斜角θ（°），约定 θ=90°=水平，θ>90°=上扬（板尖高于板根），
    # θ<90°=下垂（板尖低于板根）——方向已隐含在θ落在90°哪一侧，不需要额外符号位。
    # θ=0°/180° 时板贴墙折平、水平出挑为0，等效于不设遮阳。见 core/daylight.py
    # 关于斜面射线相交判定的推导；v4.2.0起热工SC使用倾斜剖面阴影式，仍以
    # 月中日正午代表工况计算，不等同于逐时太阳轨迹积分。
    overhang_tilt_deg:  float = 90.0

    # v2.10: 逐窗水平挑檐覆盖，键=str(window.id)，值={"depth_mm":.., "gap_mm":..,
    # "tilt_deg":..}（v2.12.0新增tilt_deg，可选）；缺失该键/子键的窗户使用上面的
    # 全局 overhang_depth_mm/overhang_height_mm/overhang_tilt_deg。
    overhang_overrides: Dict[str, Dict[str, float]] = field(default_factory=dict)

    # v2.5: 完全遮挡时的残余透过比 k_diff（散射天空+地面反射仍可入射），可配置
    diffuse_residual:   float = 0.30

    # v4.2.0: 遮阳板表面光热属性。旧工程缺少这些字段时使用保守默认值，
    # 因而仍可直接打开。visible_reflectance 用于估算挑檐下表面一次反射补光；
    # solar_reflectance / thermal_emissivity / specular_fraction 用于热工筛选模型。
    visible_reflectance: float = 0.32
    solar_reflectance: float = 0.32
    thermal_emissivity: float = 0.90
    specular_fraction: float = 0.03

    # v2.7: 垂直遮阳翼板/装饰柱（独立于 type/水平挑檐，可同时启用）。
    # v2.10 起：每扇窗左/右两侧各自生成一处翼板近侧面——中间窗户的左右两侧对应
    # 相邻窗间墙位置，最左窗的左侧、最右窗的右侧则对应两端外墙位置，两端外墙
    # 不再默认不设（此前版本的简化假设，已按实测反馈取消）。
    vertical_fin_enabled:   bool  = False
    vertical_fin_depth_mm:  float = 0.0    # mm 出挑深度（水平向外，全局默认值）

    # v2.10.2 修正: 柱体真实宽度是固定值，不等于"整段端墙剩余宽度"——窗间墙
    # 位置的柱子恰好填满整个窗间墙（现场窗间墙宽度=柱宽，巧合但真实），两端
    # 外墙位置的柱子只贴着窗户边缘占这么宽，端墙上柱子之外的部分是不参与
    # 遮挡的普通墙体（v2.10.0/v2.10.1 曾错误地把端墙柱子画成整段端墙宽度，
    # 已按用户提供的现场CAD图纸核实修正，见 core/daylight.py _fin_slots_full_m）。
    fin_column_width_mm:    float = 540.0  # mm 柱体真实宽度（仅用于两端外墙位置）

    # v2.10: 逐位置垂直翼板覆盖，键="{window.id}:L" / "{window.id}:R"，值=出挑深度mm；
    # 缺失该键的位置使用上面的全局 vertical_fin_depth_mm。
    fin_overrides: Dict[str, float] = field(default_factory=dict)

    # 百叶
    louver_width_mm:  float = 100.0   # mm 叶片宽度
    louver_angle_deg: float = 45.0    # °  叶片倾角
    louver_spacing_mm:float = 100.0   # mm 叶片间距
    louver_reflect:   float = 0.60    # -  叶片反射率（抛光铝≈0.85，白涂料≈0.65）

    # 导光板
    light_shelf_depth_mm: float = 0.0   # mm 导光板进深
    light_shelf_reflect:  float = 0.80  # -  反射率

    def SC_reduction_factor(self) -> float:
        """
        与太阳位置无关的 SC 修正系数（向后兼容用）。
        水平挑檐的实际遮阳随太阳剖面角逐月变化，请用 beam_shade_fraction()
        配合 core/solar.py 在 thermal.py 中逐月计算；此处恒返回 1.0。
        百叶/导光板尚未实现，同样返回 1.0。
        """
        return 1.0

    def effective_shaded_solar_residual(self) -> float:
        """Return the shaded-window residual used by the screening model.

        ``diffuse_residual`` remains the calibrated/legacy term.  v4.2 adds a
        conservative first-bounce correction for panel reflection and absorbed
        heat re-radiation.  The small coupling coefficients represent the view
        factor from the exterior panel back to the glazing; this is an
        engineering screening approximation, not a substitute for EnergyPlus.
        """
        rho = max(0.0, min(1.0, float(self.solar_reflectance)))
        eps = max(0.0, min(1.0, float(self.thermal_emissivity)))
        spec = max(0.0, min(1.0, float(self.specular_fraction)))
        legacy = max(0.0, min(1.0, float(self.diffuse_residual)))
        reflected = 0.10 * rho * (1.0 - spec) + 0.02 * rho * spec
        reradiated = 0.04 * (1.0 - rho) * eps
        return max(0.0, min(1.0, 0.70 * legacy + reflected + reradiated))

    def get_overhang_for(self, win_id) -> tuple:
        """
        v2.10: 返回给定窗户实际生效的 (板长mm, 安装间隙mm, 倾斜角θ°)。
        v2.12.0 新增第三项 tilt_deg。若该窗在 overhang_overrides 中有覆盖项，
        优先使用覆盖值（可只覆盖其中一项，其余仍取全局默认）；否则使用全局
        overhang_depth_mm/overhang_height_mm/overhang_tilt_deg。
        """
        ov = self.overhang_overrides.get(str(win_id))
        if ov:
            return (float(ov.get("depth_mm", self.overhang_depth_mm)),
                    float(ov.get("gap_mm",   self.overhang_height_mm)),
                    float(ov.get("tilt_deg", self.overhang_tilt_deg)))
        return (self.overhang_depth_mm, self.overhang_height_mm, self.overhang_tilt_deg)

    def get_fin_depth_for(self, win_id, side: str) -> float:
        """
        v2.10: 返回给定窗户左("L")/右("R")侧垂直翼板实际生效的出挑深度mm。
        side 侧含义见 core/daylight.py 的窗户左右侧翼板近侧面几何说明——最左窗
        的左侧、最右窗的右侧对应两端外墙位置，其余对应窗间墙位置。
        """
        key = f"{win_id}:{side}"
        if key in self.fin_overrides:
            return float(self.fin_overrides[key])
        return self.vertical_fin_depth_mm

    def beam_shade_fraction(self, profile_angle_deg, window_height_mm: float,
                            depth_mm: Optional[float] = None,
                            gap_mm: Optional[float] = None,
                            tilt_deg: Optional[float] = None) -> float:
        """
        v2.5 水平挑檐：给定太阳剖面角 profile angle（自水平起算，°）与窗高(mm)，
        返回窗口被挑檐阴影遮挡的高度比例 f ∈ [0,1]（连续，非二值开关）。

        几何：阴影自挑檐下沿向下垂落 Δ = D·tan(p)（D=水平投影出挑深度），扣除
              挑檐下沿到窗顶的间隙 gap 后，除以窗高得遮挡比例。
              → 出挑越深 / 太阳越高，f 越大，SC 越低（满足 β↑→SC↓ 单调性）。
        profile_angle_deg 为 None（太阳在墙背面/无直射）或 ≤0 时返回 0。
        depth_mm/gap_mm：v2.10 新增，传入时覆盖全局 overhang_depth_mm/height_mm
              （用于逐窗覆盖场景，调用方通过 get_overhang_for() 取值后传入）。
        tilt_deg：θ=90°水平（默认）。v4.2.0起按太阳剖面内的板尖水平和竖向位移
              计算阴影下缘；太阳方位已包含在profile angle中。月内仍采用代表日
              正午工况，属于月均准稳态筛选，不是逐时动态遮阳模拟。
        """
        import math
        D_nominal = self.overhang_depth_mm  if depth_mm is None else depth_mm
        gap = self.overhang_height_mm if gap_mm is None else gap_mm
        theta_deg = self.overhang_tilt_deg if tilt_deg is None else tilt_deg
        theta = math.radians(theta_deg)
        horizontal_projection = D_nominal * math.sin(theta)
        if (self.type != "horizontal_overhang"
                or horizontal_projection <= 0.0
                or profile_angle_deg is None):
            return 0.0
        p = math.radians(profile_angle_deg)
        if p <= 0.0:
            return 0.0
        # v4.2.0倾斜剖面精确式：板尖相对板根的竖向下降量为
        # L*cos(theta)，再叠加太阳射线跨越水平投影L*sin(theta)产生的下降量。
        # theta=90°严格退化为旧水平挑檐公式；上扬(theta>90°)减少遮挡，
        # 下垂(theta<90°)增加遮挡。
        drop = D_nominal * (
            math.cos(theta) + math.sin(theta) * math.tan(p)
        )
        shaded = drop - gap           # 扣挑檐下沿到窗顶间隙
        return max(0.0, min(1.0, shaded / max(window_height_mm, 1e-6)))


# ─────────────────────────────────────────────────────────────────────────────
# v2.12.0 新增、v2.13.0 扩展为分类材料库：固定遮阳材料 → diffuse_residual(k_diff)
# 粗略估算值 + 分类 + 绘图配色。
#
# 依据（公开的太阳反射比/反照率资料）：
#   混凝土：多孔灰色≈0.16、密实≈0.32、白色反射涂料饰面≈0.6~0.8；
#   金属：深色阳极氧化铝≈0.30、普通/氧化铝板≈0.5、镜面高反射铝板≈0.7~0.85；
#   木材：深色木材（胡桃木等）≈0.20、浅色木材（松木等）≈0.40；
#   涂料：反射隔热白涂料≈0.80~0.90、深色涂料≈0.15。
# k_diff（残余透过比：完全挡住直射后仍能反射/散射进室内的比例）按"材料反射率
# 越高、残余比例越高"的定性关系粗略映射得到（约 k_diff≈0.13+0.5·反射率），
# **不是第一性原理严格推导，也不是实测标定值**，只是给参数化实验一组便于挑选、
# 有据可查的预设起点；论文若需要更严谨的 k_diff，应结合实测遮阳板材质单独标定。
#
# 每个材料：reflect=太阳反射比参考值，k_diff=残余透过比估算值，
#          color=散点/点云上表示该材料的颜色（同一类材料用相近色系），
#          installed_cost_per_m2=论文采用的综合单价推荐值(元/㎡)，
#          cost_range_per_m2=市场询价区间(元/㎡)，price_basis=取价口径简述。
# v3.6.0价格口径：材料本体、加工切割/折弯、表面处理、5%~8%常规损耗、运输
# 及9%增值税；不含支撑构件和现场安装费。依据2025年下半年至2026年8月全国及
# 华中市场资料并结合湖南省2025版消耗量标准整理，论文和实际工程仍应注明取价
# 时点，并以实施时湖南信息价或厂家含税报价复核。
MATERIAL_LIBRARY = {
    "混凝土类": {
        "深色/多孔混凝土":   {"reflect": 0.16, "k_diff": 0.20, "color": "#78716c", "installed_cost_per_m2": 250.0, "cost_range_per_m2": (220.0, 280.0), "price_basis": "50~60mm预制板，含模具摊销、配筋、深色着色、养护及综合损耗"},
        "密实混凝土":        {"reflect": 0.32, "k_diff": 0.30, "color": "#a8a29e", "installed_cost_per_m2": 230.0, "cost_range_per_m2": (200.0, 260.0), "price_basis": "C30/C40、约50mm厚预制板，含配筋、模板、加工、运输及综合损耗"},
        "白色反射涂料混凝土": {"reflect": 0.65, "k_diff": 0.46, "color": "#d6d3d1", "installed_cost_per_m2": 275.0, "cost_range_per_m2": (240.0, 310.0), "price_basis": "密实混凝土基层加底漆及白色反射面漆系统"},
    },
    "金属类": {
        "深色阳极氧化铝板":   {"reflect": 0.30, "k_diff": 0.28, "color": "#1d4ed8", "installed_cost_per_m2": 430.0, "cost_range_per_m2": (380.0, 480.0), "price_basis": "2.5~3.0mm铝单板，含深色阳极氧化、非标切割折弯及综合损耗"},
        "普通/氧化铝板":     {"reflect": 0.50, "k_diff": 0.38, "color": "#3b82f6", "installed_cost_per_m2": 370.0, "cost_range_per_m2": (320.0, 420.0), "price_basis": "2.5~3.0mm铝单板，含普通粉末喷涂或自然氧化、加工及综合损耗"},
        "镜面高反射铝板":    {"reflect": 0.78, "k_diff": 0.55, "color": "#93c5fd", "installed_cost_per_m2": 550.0, "cost_range_per_m2": (480.0, 620.0), "price_basis": "高纯铝板，含机械抛光、阳极氧化或保护覆膜及较高运输损耗"},
    },
    "木材类": {
        "深色木材(胡桃木)":  {"reflect": 0.20, "k_diff": 0.23, "color": "#92400e", "installed_cost_per_m2": 780.0, "cost_range_per_m2": (650.0, 900.0), "price_basis": "户外适用胡桃木，含防腐、切割成型、木蜡油及综合损耗"},
        "浅色木材(松木)":    {"reflect": 0.40, "k_diff": 0.34, "color": "#d97706", "installed_cost_per_m2": 320.0, "cost_range_per_m2": (260.0, 380.0), "price_basis": "樟子松/南方松，含烘干定型、防腐防虫、切割组装及户外木蜡油"},
    },
    "涂料/其他类": {
        "基层遮阳板+深色涂层":       {"reflect": 0.15, "k_diff": 0.18, "color": "#166534", "installed_cost_per_m2": 430.0, "cost_range_per_m2": (380.0, 480.0), "price_basis": "以2.5mm铝板为统一基层，含深色氟碳/外墙涂层系统、加工及综合损耗"},
        "基层遮阳板+反射隔热白涂层": {"reflect": 0.85, "k_diff": 0.58, "color": "#22c55e", "installed_cost_per_m2": 400.0, "cost_range_per_m2": (350.0, 450.0), "price_basis": "以2.5mm铝板为统一基层，含底漆、隔热中涂、白色面漆、加工及综合损耗"},
    },
}

# v4.2.0 光热属性补全。保留 ``reflect`` 旧字段作为论文既有数据口径，
# 同时把可见光反射率、太阳反射率、发射率和镜面分量明确提供给计算引擎。
# 金属的镜面分量随表面反射率提高；非金属按以漫反射为主处理。
for _category_index, (_category, _materials) in enumerate(MATERIAL_LIBRARY.items()):
    for _material_spec in _materials.values():
        _rho = float(_material_spec.get("reflect", 0.32))
        _is_metal = _category_index == 1
        _material_spec.setdefault("visible_reflectance", _rho)
        _material_spec.setdefault("solar_reflectance", _rho)
        _material_spec.setdefault(
            "specular_fraction",
            min(0.85, 0.10 + 0.85 * _rho) if _is_metal else 0.04,
        )
        _material_spec.setdefault(
            "thermal_emissivity",
            max(0.15, 0.88 - 0.80 * _rho) if _is_metal else 0.90,
        )

# 公共费用推荐值及询价区间。安装固定费300元/窗与旧默认相同；支撑构件由原来的
# 180元/m调整为本轮询价建议的65元/m。
DEFAULT_SUPPORT_COST_PER_M = 65.0
SUPPORT_COST_RANGE_PER_M = (55.0, 80.0)
DEFAULT_INSTALL_COST_PER_WINDOW = 300.0
INSTALL_COST_RANGE_PER_WINDOW = (200.0, 400.0)

# v3.6.0将“涂料”明确为“基层遮阳板+涂层”。旧名称仍可读取和用于命令行参数，
# 但新界面及新导出统一使用完整名称。
MATERIAL_NAME_ALIASES = {
    "深色涂料": "基层遮阳板+深色涂层",
    "反射隔热白涂料": "基层遮阳板+反射隔热白涂层",
}


def canonical_material_name(name: str) -> str:
    """把历史材料名称转换成当前材料库使用的正式名称。"""
    return MATERIAL_NAME_ALIASES.get(name, name)


def iter_materials():
    """展平材料库为 [(category, name, spec_dict), ...]，spec含reflect/k_diff/color。"""
    out = []
    for cat, mats in MATERIAL_LIBRARY.items():
        for name, spec in mats.items():
            out.append((cat, name, spec))
    return out


def get_material(name: str):
    """按材料名取 spec（含reflect/k_diff/color）；找不到返回 None。"""
    name = canonical_material_name(name)
    for _cat, nm, spec in iter_materials():
        if nm == name:
            return spec
    return None


# 向后兼容：旧的 MATERIAL_PRESETS（{名称: {diffuse_residual, note}}）仍可用，
# 由材料库自动派生，避免旧代码/文档引用失效。
MATERIAL_PRESETS = {
    name: {"diffuse_residual": spec["k_diff"], "note": f"反射率≈{spec['reflect']:.2f}"}
    for _cat, name, spec in iter_materials()
}
for _old_name, _new_name in MATERIAL_NAME_ALIASES.items():
    MATERIAL_PRESETS[_old_name] = MATERIAL_PRESETS[_new_name]

# 默认勾选的材料（参数化实验材料多选列表的初始选中项）——每大类挑一个代表，
# 覆盖低/中/高反射率，便于一眼看出材料反射率对热环境的影响幅度。
DEFAULT_SELECTED_MATERIALS = ["密实混凝土", "普通/氧化铝板", "浅色木材(松木)"]


# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class LocationParams:
    latitude:  float = 28.59    # °N  益阳默认
    longitude: float = 112.33   # °E
    timezone:  int   = 8        # UTC+8
    orientation_deg: float = 0.0  # °  建筑正南方向偏角（0=正南，90=正西）


# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class RoomModel:
    """
    主数据模型，拥有房间所有状态。
    所有几何尺寸单位: mm
    """
    length:   float = 6000.0   # Y方向（南北进深）
    width:    float = 4000.0   # X方向（东西面宽）
    height:   float = 3000.0   # Z方向（层高）

    windows:  List[Window]   = field(default_factory=list)
    material: MaterialParams = field(default_factory=MaterialParams)
    thermal:  ThermalParams  = field(default_factory=ThermalParams)
    shading:  ShadingDevice  = field(default_factory=ShadingDevice)
    location: LocationParams = field(default_factory=LocationParams)

    _next_win_id: int = field(default=1, init=False, repr=False)

    # ── 窗户 CRUD ─────────────────────────────────────────────────────────────
    def add_window(self, wall: str = "south") -> Window:
        w = Window(wall=wall, id=self._next_win_id)
        self._next_win_id += 1
        self.windows.append(w)
        return w

    def remove_window(self, win_id: int) -> None:
        self.windows = [w for w in self.windows if w.id != win_id]

    def get_window(self, win_id: int) -> Optional[Window]:
        for w in self.windows:
            if w.id == win_id:
                return w
        return None

    # ── 几何辅助 ─────────────────────────────────────────────────────────────
    def wall_length(self, wall: str) -> float:
        return self.width if wall in ("south","north") else self.length

    def windows_on(self, wall: str) -> List[Window]:
        return [w for w in self.windows if w.wall == wall]

    @property
    def floor_area_m2(self) -> float:
        return (self.length / 1e3) * (self.width / 1e3)

    @property
    def volume_m3(self) -> float:
        return self.floor_area_m2 * (self.height / 1e3)

    @property
    def total_window_area_m2(self) -> float:
        return sum(w.area_m2 for w in self.windows)

