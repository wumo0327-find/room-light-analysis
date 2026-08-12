"""
core/models.py — Legacy rectangular data models  v3.1.0
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
    # 关于斜面射线相交判定的推导；core/thermal.py 的SC计算目前仍按水平投影深度
    # (L·sinθ) 近似处理，未对倾斜做精确太阳几何修正，是已知简化。
    overhang_tilt_deg:  float = 90.0

    # v2.10: 逐窗水平挑檐覆盖，键=str(window.id)，值={"depth_mm":.., "gap_mm":..,
    # "tilt_deg":..}（v2.12.0新增tilt_deg，可选）；缺失该键/子键的窗户使用上面的
    # 全局 overhang_depth_mm/overhang_height_mm/overhang_tilt_deg。
    overhang_overrides: Dict[str, Dict[str, float]] = field(default_factory=dict)

    # v2.5: 完全遮挡时的残余透过比 k_diff（散射天空+地面反射仍可入射），可配置
    diffuse_residual:   float = 0.30

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
        tilt_deg：v2.12.0 新增，θ=90°水平（默认）。倾斜时用水平投影深度
              D_eff = depth_mm·sinθ 代入原水平公式做近似处理——这是简化，未对
              倾斜面做精确太阳几何修正（倾斜挑檐严格算法应同时考虑太阳方位角，
              比水平挑檐复杂得多），采光 Ds 计算（core/daylight.py）已做精确的
              斜面射线相交判定，此处热工SC计算暂只用这个近似，是已知局限。
        """
        import math
        D_nominal = self.overhang_depth_mm  if depth_mm is None else depth_mm
        gap = self.overhang_height_mm if gap_mm is None else gap_mm
        theta_deg = self.overhang_tilt_deg if tilt_deg is None else tilt_deg
        D = D_nominal * math.sin(math.radians(theta_deg))   # 水平投影深度（近似）
        if self.type != "horizontal_overhang" or D <= 0.0 or profile_angle_deg is None:
            return 0.0
        p = math.radians(profile_angle_deg)
        if p <= 0.0:
            return 0.0
        drop   = D * math.tan(p)      # mm 阴影下垂量
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
#          installed_cost_per_m2=含板材加工/表面处理的初始综合估算单价(元/㎡)。
# 初始单价只用于建立工程量造价框架，不是真实项目报价；GUI可按当地当期信息价或
# 厂家含税报价修改，最终总价还会叠加支撑构件和逐窗安装费。
MATERIAL_LIBRARY = {
    "混凝土类": {
        "深色/多孔混凝土":   {"reflect": 0.16, "k_diff": 0.20, "color": "#78716c", "installed_cost_per_m2": 650.0},
        "密实混凝土":        {"reflect": 0.32, "k_diff": 0.30, "color": "#a8a29e", "installed_cost_per_m2": 750.0},
        "白色反射涂料混凝土": {"reflect": 0.65, "k_diff": 0.46, "color": "#d6d3d1", "installed_cost_per_m2": 850.0},
    },
    "金属类": {
        "深色阳极氧化铝板":   {"reflect": 0.30, "k_diff": 0.28, "color": "#1d4ed8", "installed_cost_per_m2": 950.0},
        "普通/氧化铝板":     {"reflect": 0.50, "k_diff": 0.38, "color": "#3b82f6", "installed_cost_per_m2": 850.0},
        "镜面高反射铝板":    {"reflect": 0.78, "k_diff": 0.55, "color": "#93c5fd", "installed_cost_per_m2": 1150.0},
    },
    "木材类": {
        "深色木材(胡桃木)":  {"reflect": 0.20, "k_diff": 0.23, "color": "#92400e", "installed_cost_per_m2": 1100.0},
        "浅色木材(松木)":    {"reflect": 0.40, "k_diff": 0.34, "color": "#d97706", "installed_cost_per_m2": 850.0},
    },
    "涂料/其他类": {
        "深色涂料":         {"reflect": 0.15, "k_diff": 0.18, "color": "#166534", "installed_cost_per_m2": 550.0},
        "反射隔热白涂料":    {"reflect": 0.85, "k_diff": 0.58, "color": "#22c55e", "installed_cost_per_m2": 650.0},
    },
}


def iter_materials():
    """展平材料库为 [(category, name, spec_dict), ...]，spec含reflect/k_diff/color。"""
    out = []
    for cat, mats in MATERIAL_LIBRARY.items():
        for name, spec in mats.items():
            out.append((cat, name, spec))
    return out


def get_material(name: str):
    """按材料名取 spec（含reflect/k_diff/color）；找不到返回 None。"""
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
