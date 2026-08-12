"""
core/plan_export.py — 房间平面图绘制与测点叠加导出  v2.8.0（新增，v2.9.0 天正风格标注+遮阳可视化）
======================================================================
不依赖 Qt 的纯 matplotlib 绘图模块，是 ui/canvas.py 平面图渲染逻辑的抽取
（供 GUI 交互画布与命令行静态导出共用，不重复实现同一套绘制代码）。

v2.8.0 能力：在房间平面图上叠加"验证测点"标注（测点编号 + 世界坐标 + 所属测线）。
v2.9.0 新增：
  · 天正建筑风格尺寸标注（细线 + 45°短斜线起止符号 + 尺寸界线，而非箭头），
    对有窗的墙自动画两道尺寸链——贴墙一道逐段标注"端墙/窗宽/窗间墙"，外侧一道
    标总尺寸，标注含窗户宽度，不再需要只看图侧文字表。
  · 窗户图例改为洞口内四线（玻璃层+窗框示意），而非纯色块填充。
  · 遮阳构件可视化：水平挑檐按窗宽/出挑深度画外凸矩形；垂直翼板按窗间墙实际
    宽度画外凸矩形（矩形宽度=真实窗间墙宽度，不是示意值），均带出挑深度标注。

已知局限：当前 RoomModel 无门（door）数据结构，仅窗户(Window)有完整位置/尺寸
记录，故本图只能标注窗，无法标注门的位置——如需门的平面标注，需先在
core/models.py 扩展门的数据模型（本次未做，超出本次任务范围）。
"""
from __future__ import annotations
import math
from typing import Optional, Sequence, Tuple, List

import matplotlib
from matplotlib import patches, patheffects
from matplotlib.figure import Figure

from core.models import RoomModel, Window, WALL_MAP_R

# ── 配色（与 ui/canvas.py 白色学术主题一致，ui/canvas.py 从本模块导入，不重复定义）──
C_BG       = "#ffffff"
C_ACCENT   = "#2563eb"
C_TEXT     = "#1a1e2e"
C_TEXTSEC  = "#5a6175"
C_WIN      = "#bfdbfe80"
C_WIN_ED   = "#2563eb"
C_CARD     = "#eef0f4"
C_FLOOR    = "#f8f9fb"
C_DIM      = "#d97706"
C_COMPASS  = "#16a34a"
C_WALL_LINE = "#5a6175"   # 墙体双线（内墙线）颜色

# 遮阳构件配色
C_OVERHANG = "#f59e0b"   # 水平挑檐（琥珀色）
C_FIN      = "#16a34a"   # 垂直翼板（绿色）

# 测点标注配色：与 validate_daylight.py 的两条测线配色保持一致，便于对照阅读
C_PROBE_WINDOW = "#2563eb"   # 沿窗中线测点
C_PROBE_PIER   = "#d97706"   # 沿窗间墙中线测点
C_PROBE_OTHER  = "#dc2626"   # 未归类测点（兜底色）

# 图例文字（按颜色自动生成图例时使用）
PROBE_COLOR_LEGEND = {
    C_PROBE_WINDOW: "沿窗中线测点",
    C_PROBE_PIER:   "沿窗间墙中线测点",
    C_PROBE_OTHER:  "测点",
}

_OUTWARD = {"south": (0, -1), "north": (0, 1), "east": (1, 0), "west": (-1, 0)}


# ═════════════════════════════════════════════════════════════════════════
# 天正风格尺寸标注（细线 + 45°短斜线起止符号 + 尺寸界线）
# ═════════════════════════════════════════════════════════════════════════
def _tz_tick(ax, x, y, horiz: bool, size, color=C_DIM, lw=1.1, zorder=8):
    """45°短斜线（国标/天正尺寸线起止符号），horiz=尺寸线是否为水平方向。"""
    a = math.radians(45)
    dx, dy = size * math.cos(a), size * math.sin(a)
    ax.plot([x - dx / 2, x + dx / 2], [y - dy / 2, y + dy / 2],
            color=color, lw=lw, solid_capstyle="butt", zorder=zorder)


def tz_dim_segment(ax, x1, y1, x2, y2, label, horiz: bool, tick_size,
                   fontsize=8, color=C_DIM, lw=0.9):
    """一段天正风格尺寸线：细直线 + 两端 45°短斜线 + 居中文字，不含尺寸界线。"""
    ax.plot([x1, x2], [y1, y2], color=color, lw=lw, zorder=6)
    _tz_tick(ax, x1, y1, horiz, tick_size, color)
    _tz_tick(ax, x2, y2, horiz, tick_size, color)
    mx, my = (x1 + x2) / 2, (y1 + y2) / 2
    rot = 0 if horiz else 90
    ax.text(mx, my, label, ha="center", va="center",
            fontsize=fontsize, color=color, fontweight="bold", rotation=rot,
            path_effects=[patheffects.withStroke(linewidth=1.3, foreground=C_BG)],
            zorder=7)


def dim_arrow(ax, x1, y1, x2, y2, label, direction="h", fontsize=10):
    """
    单段天正风格尺寸标注（细线+45°短斜线起止符号）。
    保留原函数名/签名以兼容既有调用方（房间总宽/总长、立面总宽/总高、窗宽/窗高
    等场景），v2.9.0 起内部实现已从箭头式改为天正风格，不再画箭头——程序里所有
    尺寸标注统一走这一套样式，不需要逐个调用点分别修改。
    """
    horiz = direction == "h"
    span = abs(x2 - x1) if horiz else abs(y2 - y1)
    tick_size = max(span, 1.0) * 0.05
    tz_dim_segment(ax, x1, y1, x2, y2, label, horiz, tick_size, fontsize,
                   color=C_DIM, lw=1.0)


def tz_leader_tick(ax, x, y, tail_dx, tail_dy, label, fontsize=8, color=C_DIM):
    """
    天正风格引出标注（细线+起点45°短斜线，取代箭头指引线）。用于窗台高度等
    "指向一个点、旁边写数值"的场景，而非两点间的尺寸标注。
    """
    x2, y2 = x + tail_dx, y + tail_dy
    ax.plot([x, x2], [y, y2], color=color, lw=0.9, zorder=8)
    horiz = abs(tail_dx) >= abs(tail_dy)
    _tz_tick(ax, x, y, horiz, max(abs(tail_dx), abs(tail_dy), 1.0) * 0.5, color)
    ax.text(x2, y2, label, ha="right" if tail_dx <= 0 else "left",
            va="center", fontsize=fontsize, color=color,
            path_effects=[patheffects.withStroke(linewidth=1.3, foreground=C_BG)],
            zorder=9)


def draw_compass(ax, cx, cy, r):
    """指北针。"""
    ax.annotate("", xy=(cx, cy + r), xytext=(cx, cy),
                arrowprops=dict(arrowstyle="-|>", color=C_COMPASS,
                                lw=2, mutation_scale=14))
    ax.text(cx, cy + r * 1.3, "N",
            ha="center", va="bottom",
            fontsize=11, color=C_COMPASS, fontweight="900")
    ax.add_patch(patches.Circle((cx, cy), r * 0.35,
                                fill=False, edgecolor=C_COMPASS,
                                linewidth=1.0, alpha=0.5))


# ═════════════════════════════════════════════════════════════════════════
# 墙面局部坐标 ↔ 世界坐标（与 draw_window_plan 已有约定保持一致，不引入新映射）
# ═════════════════════════════════════════════════════════════════════════
def _wall_axis_len(room: RoomModel, wall: str) -> float:
    return room.width if wall in ("south", "north") else room.length


def _axis_point(room: RoomModel, wall: str, u: float) -> Tuple[float, float]:
    """墙局部坐标 u（沿墙, mm）→ 世界坐标 (x,y)，贴墙线（out=0）。"""
    W, L = room.width, room.length
    if wall == "south":
        return (u, 0.0)
    if wall == "north":
        return (u, L)
    if wall == "east":
        return (W, u)
    return (0.0, u)   # west


def _wall_rect_outward(room: RoomModel, wall: str, u0: float, u1: float,
                       depth: float) -> Tuple[float, float, float, float]:
    """沿墙局部坐标区间 [u0,u1] 向外（室外方向）出挑 depth 的矩形 (bx,by,bw,bh)。"""
    W, L = room.width, room.length
    if wall == "south":
        return (u0, -depth, u1 - u0, depth)
    if wall == "north":
        return (u0, L, u1 - u0, depth)
    if wall == "east":
        return (W, u0, depth, u1 - u0)
    return (-depth, u0, depth, u1 - u0)   # west


def _wall_open_segments(room: RoomModel, wall: str):
    """
    该墙从 0 到墙长的分段列表：[(u0,u1,kind,win_or_None), ...]
    kind ∈ {"wall","window"}；wall 段再细分为"端墙"（在两端）或"窗间墙"（夹在窗之间），
    由调用方按位置判断，这里只给出原始几何分段。
    """
    wins = sorted(room.windows_on(wall), key=lambda w: w.x)
    axis_len = _wall_axis_len(room, wall)
    segs = []
    cur = 0.0
    for w in wins:
        if w.x > cur + 1e-6:
            segs.append((cur, w.x, "wall", None))
        segs.append((w.x, w.x + w.width, "window", w))
        cur = w.x + w.width
    if cur < axis_len - 1e-6:
        segs.append((cur, axis_len, "wall", None))
    return segs, axis_len


def _pier_gaps(room: RoomModel, wall: str) -> List[Tuple[float, float]]:
    """相邻两窗之间的窗间墙区间 [(u0,u1), ...]（真实宽度，非示意值）。"""
    wins = sorted(room.windows_on(wall), key=lambda w: w.x)
    gaps = []
    for i in range(len(wins) - 1):
        u0, u1 = wins[i].right, wins[i + 1].x
        if u1 > u0 + 1e-6:
            gaps.append((u0, u1))
    return gaps


def draw_dim_chain(ax, room: RoomModel, wall: str,
                   offset_seg_mm: float, offset_total_mm: float,
                   fontsize=8):
    """
    天正风格分段尺寸链：贴墙第一道逐段标注（端墙/窗宽/窗间墙），外侧第二道标总长。
    只对该墙实际存在窗户时调用（否则退化为普通总尺寸，见 draw_room_plan）。
    """
    segs, axis_len = _wall_open_segments(room, wall)
    if not any(kind == "window" for _, _, kind, _ in segs):
        return  # 该墙无窗，不画分段链（由调用方画普通总尺寸线）

    horiz = wall in ("south", "north")
    ox, oy = _OUTWARD[wall]
    tick_size = max(room.width, room.length) * 0.011
    n_segs = len(segs)

    def pt(u, off):
        bx, by = _axis_point(room, wall, u)
        return bx + ox * off, by + oy * off

    # 尺寸界线：从墙面（贴墙留一点点空隙）引到最外道尺寸线
    gap0 = tick_size * 0.35
    bounds = sorted({s[0] for s in segs} | {s[1] for s in segs})
    for u in bounds:
        x0, y0 = pt(u, gap0)
        x2, y2 = pt(u, offset_total_mm + tick_size * 0.6)
        ax.plot([x0, x2], [y0, y2], color=C_DIM, lw=0.5, alpha=0.6, zorder=2)

    # 第一道：端墙/窗宽/窗间墙 逐段
    for i, (u0, u1, kind, win) in enumerate(segs):
        x0, y0 = pt(u0, offset_seg_mm)
        x1, y1 = pt(u1, offset_seg_mm)
        length = u1 - u0
        if kind == "window":
            label = f"{length:.0f}"
        else:
            tag = "端墙" if (i == 0 or i == n_segs - 1) else "墙"
            label = f"{tag}{length:.0f}"
        tz_dim_segment(ax, x0, y0, x1, y1, label, horiz, tick_size, fontsize)

    # 第二道：总尺寸
    x0, y0 = pt(0, offset_total_mm)
    x1, y1 = pt(axis_len, offset_total_mm)
    tz_dim_segment(ax, x0, y0, x1, y1, f"{axis_len/1000:.2f} m",
                   horiz, tick_size, fontsize + 1, lw=1.1)


# ═════════════════════════════════════════════════════════════════════════
# 窗户图例（天正风格：洞口 + 四线示意玻璃/窗框，而非纯色块）
# ═════════════════════════════════════════════════════════════════════════
def draw_window_plan(ax, win: Window, wt: float, room: RoomModel):
    """在平面图上画一扇窗（洞口四线图例 + 窗号标注），wt=墙厚(mm-units)。"""
    gap = wt * 1.05
    w = win.wall
    if w == "south":
        bx, by = win.x, 0
        bw, bh = win.width, gap
    elif w == "north":
        bx, by = win.x, room.length - gap
        bw, bh = win.width, gap
    elif w == "east":
        bx, by = room.width - gap, win.x
        bw, bh = gap, win.width
    else:  # west
        bx, by = 0, win.x
        bw, bh = gap, win.width

    # 白色开洞（擦除墙体填充），洞口边框
    ax.add_patch(patches.Rectangle((bx, by), bw, bh,
                                   linewidth=0, facecolor=C_BG, zorder=3))
    ax.add_patch(patches.Rectangle((bx, by), bw, bh,
                                   linewidth=1.2, edgecolor=C_WIN_ED,
                                   facecolor="none", zorder=4))

    # 天正风格窗图例：洞口内四条平行细线（示意双层玻璃 + 两侧窗框）
    horiz = w in ("south", "north")
    n_lines = 4
    if horiz:
        for i in range(n_lines):
            yy = by + bh * (i + 0.5) / n_lines
            ax.plot([bx, bx + bw], [yy, yy], color=C_WIN_ED, lw=0.8, zorder=4)
    else:
        for i in range(n_lines):
            xx = bx + bw * (i + 0.5) / n_lines
            ax.plot([xx, xx], [by, by + bh], color=C_WIN_ED, lw=0.8, zorder=4)

    # 窗号标注放在室内一侧（避免与洞口四线、外侧尺寸链重叠）
    inward = {"south": (0, 1), "north": (0, -1),
             "east": (-1, 0), "west": (1, 0)}[w]
    label_off = gap * 2.4
    cx, cy = bx + bw / 2, by + bh / 2
    lx, ly = cx + inward[0] * label_off, cy + inward[1] * label_off
    ax.text(lx, ly, f"W{win.id}",
            ha="center", va="center",
            fontsize=8, color=C_WIN_ED, fontweight="bold", zorder=5,
            path_effects=[patheffects.withStroke(linewidth=1.5, foreground=C_BG)])


# ═════════════════════════════════════════════════════════════════════════
# 遮阳构件可视化（水平挑檐 + 垂直翼板，平面图外凸表示）
# ═════════════════════════════════════════════════════════════════════════
def draw_shading_plan(ax, room: RoomModel, fontsize=8, label_beyond_mm=0.0):
    """
    水平挑檐：按每扇窗的窗宽 × 出挑深度画外凸矩形（虚线边框+半透明填充）。
    垂直翼板：按窗间墙的真实宽度 × 出挑深度画外凸矩形（宽度=真实窗间墙宽度，
             不是示意值，因为翼板被视为填满整个窗间墙区间后向外出挑）。
    两者可同时存在，独立绘制、独立标注出挑深度，返回是否画了图例项。

    label_beyond_mm: 出挑深度标注文字放在"离墙 label_beyond_mm + 一点间距"处的
        绝对距离（而非按 depth 的倍数），确保不管出挑深度多大，标注都画在尺寸
        标注链的外侧，不会互相压字；两条标注（挑檐/翼板）再彼此错开一档。
    """
    sh = room.shading
    has_overhang = (getattr(sh, "type", "none") == "horizontal_overhang"
                    and sh.overhang_depth_mm > 0)
    has_fin = (getattr(sh, "vertical_fin_enabled", False)
              and sh.vertical_fin_depth_mm > 0)
    step = max(room.width, room.length) * 0.045

    if has_overhang:
        D = sh.overhang_depth_mm
        wins = room.windows
        mid_win = wins[len(wins) // 2] if wins else None
        for win in wins:
            bx, by, bw, bh = _wall_rect_outward(
                room, win.wall, win.x, win.right, D)
            ax.add_patch(patches.Rectangle(
                (bx, by), bw, bh, linewidth=1.0, edgecolor=C_OVERHANG,
                facecolor=C_OVERHANG, alpha=0.22, linestyle="--", zorder=2))
            ax.add_patch(patches.Rectangle(
                (bx, by), bw, bh, linewidth=1.0, edgecolor=C_OVERHANG,
                facecolor="none", linestyle="--", zorder=3))
            if win is mid_win:
                _label_shading_depth(ax, room, win.wall, win.x, win.right,
                                     max(D, label_beyond_mm) + step,
                                     f"挑檐出挑{D:.0f}", C_OVERHANG, fontsize)

    if has_fin:
        Df = sh.vertical_fin_depth_mm
        for wall in ("south", "north", "east", "west"):
            gaps = _pier_gaps(room, wall)
            mid_gap = gaps[len(gaps) // 2] if gaps else None
            for (u0, u1) in gaps:
                bx, by, bw, bh = _wall_rect_outward(room, wall, u0, u1, Df)
                ax.add_patch(patches.Rectangle(
                    (bx, by), bw, bh, linewidth=1.0, edgecolor=C_FIN,
                    facecolor=C_FIN, alpha=0.28, zorder=2))
                ax.add_patch(patches.Rectangle(
                    (bx, by), bw, bh, linewidth=1.0, edgecolor=C_FIN,
                    facecolor="none", zorder=3))
                if (u0, u1) == mid_gap:
                    _label_shading_depth(ax, room, wall, u0, u1,
                                         max(Df, label_beyond_mm) + step * 2.2,
                                         f"翼板出挑{Df:.0f}", C_FIN, fontsize)

    return has_overhang, has_fin


def _label_shading_depth(ax, room, wall, u0, u1, place_at_mm, text, color, fontsize):
    """在指定的绝对外扩距离 place_at_mm 处标一次出挑深度文字（只标一次代表值）。"""
    ox, oy = _OUTWARD[wall]
    bx, by = _axis_point(room, wall, (u0 + u1) / 2)
    lx = bx + ox * place_at_mm
    ly = by + oy * place_at_mm
    ax.text(lx, ly, text, ha="center", va="center",
            fontsize=fontsize, color=color, fontweight="bold",
            path_effects=[patheffects.withStroke(linewidth=1.3, foreground=C_BG)],
            zorder=9)


def draw_shading_elevation(ax, room: RoomModel, wall: str, fontsize=8):
    """
    在立面图上画该墙的遮阳构件示意（v2.9.0 新增，对应平面图的外凸矩形，
    改画正视投影）：
      · 水平挑檐：窗顶上方的横向薄板，宽度=窗宽，与平面一致；
      · 垂直翼板：窗间墙位置的竖向薄板，宽度=真实窗间墙宽度、高度=窗台~窗顶
        （与相邻两窗的窗台/窗顶取值一致，通常相邻窗高度相同）。
    注意：挑檐/翼板在立面上显示的"厚度"（挑檐板厚/翼板可见宽度之外的进深方向
    厚度）未在数据模型中定义具体值，图中厚度仅为示意，不代表真实构造尺寸；
    真正参与计算的出挑深度（850mm 等）见平面图标注。
    """
    sh = room.shading
    has_overhang = (getattr(sh, "type", "none") == "horizontal_overhang"
                    and sh.overhang_depth_mm > 0)
    has_fin = (getattr(sh, "vertical_fin_enabled", False)
              and sh.vertical_fin_depth_mm > 0)
    wins = sorted(room.windows_on(wall), key=lambda w: w.x)
    if not wins:
        return False, False   # 该墙没有窗户，自然也画不出任何遮阳构件

    H = room.height
    nominal_h = max(H * 0.035, 80.0)   # 示意厚度（未在模型中定义具体值）

    if has_overhang:
        for win in wins:
            ax.add_patch(patches.Rectangle(
                (win.x, win.head), win.width, nominal_h,
                linewidth=1.0, edgecolor=C_OVERHANG, facecolor=C_OVERHANG,
                alpha=0.55, zorder=6))
            ax.add_patch(patches.Rectangle(
                (win.x, win.head), win.width, nominal_h,
                linewidth=1.0, edgecolor=C_OVERHANG, facecolor="none", zorder=7))

    fin_drawn = False
    if has_fin:
        for i in range(len(wins) - 1):
            u0, u1 = wins[i].right, wins[i + 1].x
            if u1 <= u0:
                continue
            sill = min(wins[i].sill, wins[i + 1].sill)
            head = max(wins[i].head, wins[i + 1].head)
            ax.add_patch(patches.Rectangle(
                (u0, sill), u1 - u0, head - sill,
                linewidth=1.0, edgecolor=C_FIN, facecolor=C_FIN,
                alpha=0.32, zorder=6))
            ax.add_patch(patches.Rectangle(
                (u0, sill), u1 - u0, head - sill,
                linewidth=1.0, edgecolor=C_FIN, facecolor="none", zorder=7))
            fin_drawn = True

    return has_overhang, fin_drawn


# ═════════════════════════════════════════════════════════════════════════
# 主绘制入口
# ═════════════════════════════════════════════════════════════════════════
def draw_room_plan(ax, room: RoomModel,
                   probe_points: Optional[Sequence[Tuple[float, float, str, str]]] = None):
    """
    在给定 Axes 上画完整房间平面图：房间轮廓、墙、窗（天正风格洞口图例+窗号）、
    遮阳构件外凸示意、天正风格尺寸标注（有窗的墙画分段尺寸链，无窗的墙画普通总
    尺寸）、指北针；可选叠加验证测点。

    probe_points: [(x_mm, y_mm, label, color), ...]，世界坐标(毫米)。
        label 显示在标记旁；请勿用 "W<n>" 前缀（与窗户编号冲突），建议用
        core.validation 生成测线时搭配的 "测-n"/"墙-n" 之类记号。
        color 建议用本模块 C_PROBE_* 常量按测线区分。传 None 则不画测点。
    """
    W, L, H = room.width, room.length, room.height  # mm

    # 墙体双线表示（天正风格）：外墙线（房间外轮廓）+ 内墙线（内凹 wt 后的轮廓），
    # 中间填充 C_CARD 代表墙体厚度，比单线填色块更清楚地表达墙宽。
    ax.add_patch(patches.FancyBboxPatch(
        (0, 0), W, L, boxstyle="square,pad=0",
        linewidth=2, edgecolor=C_ACCENT, facecolor=C_FLOOR))

    wt = max(W, L) * 0.018
    for (bx, by, bw, bh) in [
        (0, 0, W, wt), (0, L - wt, W, wt),
        (0, 0, wt, L), (W - wt, 0, wt, L),
    ]:
        ax.add_patch(patches.Rectangle((bx, by), bw, bh,
                                       linewidth=0, facecolor=C_CARD))
    ax.add_patch(patches.Rectangle(
        (wt, wt), W - 2 * wt, L - 2 * wt,
        linewidth=1.1, edgecolor=C_WALL_LINE, facecolor="none", zorder=2))

    # 尺寸标注偏移先算好（遮阳出挑标注要放在总尺寸链外侧，避免压字）
    seg_off = max(W, L) * 0.045
    total_off = max(W, L) * 0.10

    # 遮阳构件先画（在墙/窗之下、尺寸标注之上的视觉层次由 zorder 控制，这里画的
    # 顺序不影响最终叠放，因为矩形本身 zorder 已固定为 2-3）
    has_overhang, has_fin = draw_shading_plan(ax, room, label_beyond_mm=total_off)

    for win in room.windows:
        draw_window_plan(ax, win, wt, room)

    # 尺寸标注：有窗的墙画天正风格分段链，其余墙画普通总尺寸线
    walls_with_windows = {w.wall for w in room.windows}

    if "south" in walls_with_windows:
        draw_dim_chain(ax, room, "south", seg_off, total_off)
    else:
        dim_arrow(ax, 0, -L * 0.08, W, -L * 0.08, f"宽 {W/1000:.2f} m", "h")

    if "north" in walls_with_windows:
        draw_dim_chain(ax, room, "north", seg_off, total_off)

    if "west" in walls_with_windows:
        draw_dim_chain(ax, room, "west", seg_off, total_off)
    else:
        dim_arrow(ax, -W * 0.1, 0, -W * 0.1, L, f"长 {L/1000:.2f} m", "v")

    if "east" in walls_with_windows:
        draw_dim_chain(ax, room, "east", seg_off, total_off)

    draw_compass(ax, W * 0.92, L * 0.92, L * 0.05)

    margin = max(W, L) * 0.24

    # ── 测点叠加 ──────────────────────────────────────────────────────────
    legend_handles = []
    import matplotlib.lines as mlines
    if probe_points:
        seen_colors: dict = {}
        for x_mm, y_mm, label, color in probe_points:
            ax.scatter([x_mm], [y_mm], s=90, marker="o", color=color,
                      edgecolors="white", linewidths=1.2, zorder=10)
            ax.annotate(label, (x_mm, y_mm),
                       xytext=(0, 10), textcoords="offset points",
                       ha="center", fontsize=8, color=color, fontweight="bold",
                       path_effects=[patheffects.withStroke(linewidth=1.5, foreground=C_BG)],
                       zorder=11)
            seen_colors.setdefault(color, 0)
        legend_handles += [mlines.Line2D([], [], marker="o", linestyle="none",
                                         color=c, markeredgecolor="white",
                                         markersize=8, label=PROBE_COLOR_LEGEND.get(c, c))
                          for c in seen_colors]

    if has_overhang:
        legend_handles.append(mlines.Line2D(
            [], [], marker="s", linestyle="none", color=C_OVERHANG,
            markersize=9, alpha=0.7, label="水平挑檐（外凸示意）"))
    if has_fin:
        legend_handles.append(mlines.Line2D(
            [], [], marker="s", linestyle="none", color=C_FIN,
            markersize=9, alpha=0.7, label="垂直翼板（外凸示意）"))

    if legend_handles:
        ax.legend(handles=legend_handles, loc="upper left", fontsize=8.5,
                 frameon=True, facecolor="#ffffff", edgecolor="#d0d5e0",
                 framealpha=0.95)

    ax.set_title(f"平面图    层高 {H/1000:.2f} m",
                color=C_TEXT, fontsize=13, fontweight="bold", pad=8)
    ax.set_xlim(-margin, W + margin * 0.5)
    ax.set_ylim(-margin, L + margin * 0.5)
    ax.set_aspect("equal")


def window_dimension_lines(room: RoomModel) -> List[str]:
    """每扇窗的位置/尺寸文字说明（mm），用于图侧核对表 / 命令行打印。"""
    lines = []
    for win in sorted(room.windows, key=lambda w: (w.wall, w.x)):
        lines.append(
            f"W{win.id}（{WALL_MAP_R.get(win.wall, win.wall)}墙）: "
            f"x={win.x:.0f}~{win.right:.0f}mm  宽{win.width:.0f}×高{win.height:.0f}mm  "
            f"窗台{win.y:.0f}mm  τ={win.tau:.2f}")
    return lines


def shading_dimension_lines(room: RoomModel) -> List[str]:
    """遮阳构件参数文字说明，用于图侧核对表。"""
    sh = room.shading
    lines = []
    if getattr(sh, "type", "none") == "horizontal_overhang" and sh.overhang_depth_mm > 0:
        lines.append(f"水平挑檐: 出挑{sh.overhang_depth_mm:.0f}mm  "
                     f"安装间隙{sh.overhang_height_mm:.0f}mm（每扇窗上方均设置）")
    if getattr(sh, "vertical_fin_enabled", False) and sh.vertical_fin_depth_mm > 0:
        lines.append(f"垂直翼板: 出挑{sh.vertical_fin_depth_mm:.0f}mm  "
                     f"（仅窗间墙位置，两端外墙不设，宽度=真实窗间墙宽度）")
    if not lines:
        lines.append("无遮阳构件")
    return lines


def export_plan_png(
    room: RoomModel,
    out_path: str,
    probe_points: Optional[Sequence[Tuple[float, float, str, str]]] = None,
    title_suffix: str = "",
    dpi: int = 200,
) -> str:
    """
    独立导出平面图 PNG（不依赖 Qt，可在命令行/无 GUI 环境调用）。
    图右侧留白打印窗户尺寸表 + 遮阳构件参数 + 测点坐标表，便于直接对照核实。
    """
    matplotlib.use("Agg")
    fig = Figure(figsize=(12, 8), facecolor=C_BG)
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    FigureCanvasAgg(fig)
    ax = fig.add_axes([0.06, 0.08, 0.56, 0.84])
    ax.set_facecolor(C_BG)
    for sp in ax.spines.values():
        sp.set_visible(False)
    ax.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False)

    draw_room_plan(ax, room, probe_points=probe_points)
    if title_suffix:
        ax.set_title(ax.get_title() + "  " + title_suffix,
                    color=C_TEXT, fontsize=13, fontweight="bold", pad=8)

    # ── 右侧文字核对表 ────────────────────────────────────────────────────
    tax = fig.add_axes([0.66, 0.05, 0.32, 0.9])
    tax.axis("off")
    y = 1.0
    tax.text(0, y, "窗户尺寸/位置核对表", fontsize=12, fontweight="bold",
             color=C_TEXT, transform=tax.transAxes, va="top")
    y -= 0.05
    for line in window_dimension_lines(room):
        tax.text(0, y, line, fontsize=9, color=C_TEXTSEC,
                 transform=tax.transAxes, va="top", wrap=True)
        y -= 0.045

    y -= 0.02
    tax.text(0, y, "遮阳构件参数", fontsize=12, fontweight="bold",
             color=C_TEXT, transform=tax.transAxes, va="top")
    y -= 0.05
    for line in shading_dimension_lines(room):
        tax.text(0, y, line, fontsize=9, color=C_TEXTSEC,
                 transform=tax.transAxes, va="top", wrap=True)
        y -= 0.045

    if probe_points:
        y -= 0.03
        tax.text(0, y, "验证测点坐标（世界坐标, mm）", fontsize=12,
                 fontweight="bold", color=C_TEXT, transform=tax.transAxes, va="top")
        y -= 0.05
        for x_mm, y_mm, label, color in probe_points:
            tax.text(0, y, f"● {label}:  x={x_mm:.0f}mm  y={y_mm:.0f}mm",
                    fontsize=9, color=color, fontweight="bold",
                    transform=tax.transAxes, va="top")
            y -= 0.04

    room_note = (f"房间: 宽{room.width:.0f}×长{room.length:.0f}×高{room.height:.0f}mm  "
                f"（本图不含门：当前数据模型未记录门的位置/尺寸）")
    fig.text(0.02, 0.02, room_note, fontsize=8.5, color="#9aa0b0")

    fig.savefig(out_path, dpi=dpi, facecolor=C_BG, bbox_inches="tight")
    return out_path
