"""
core/plan_export.py — 房间平面图绘制与测点叠加导出  v2.8.0（新增）
======================================================================
不依赖 Qt 的纯 matplotlib 绘图模块，是 ui/canvas.py 平面图渲染逻辑的抽取
（供 GUI 交互画布与命令行静态导出共用，不重复实现同一套绘制代码）。

新增能力：在房间平面图上叠加"验证测点"标注（测点编号 + 世界坐标 + 所属测线），
用于人工核对——房间尺寸、窗户位置/尺寸是否与实际教室一致，测点位置是否符合
现场量测的预期，主要配合 core/validation.py 的 make_probe_lines() 使用。

已知局限：当前 RoomModel 无门（door）数据结构，仅窗户(Window)有完整位置/尺寸
记录，故本图只能标注窗，无法标注门的位置——如需门的平面标注，需先在
core/models.py 扩展门的数据模型（本次未做，超出本次任务范围）。
"""
from __future__ import annotations
from typing import Optional, Sequence, Tuple, List

import matplotlib
from matplotlib import patches, patheffects
from matplotlib.figure import Figure

from core.models import RoomModel, Window

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


def dim_arrow(ax, x1, y1, x2, y2, label, direction="h", fontsize=10):
    """双箭头尺寸标注线，中点标数值。"""
    ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                arrowprops=dict(arrowstyle="<->", color=C_DIM, lw=1.2))
    mx, my = (x1 + x2) / 2, (y1 + y2) / 2
    rot = 0 if direction == "h" else 90
    ax.text(mx, my, label,
            ha="center", va="center",
            fontsize=fontsize, color=C_DIM,
            rotation=rot, fontweight="bold",
            path_effects=[patheffects.withStroke(linewidth=1.5, foreground=C_BG)])


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


def draw_window_plan(ax, win: Window, wt: float, room: RoomModel):
    """在平面图上画一扇窗（含窗号标注），wt=墙厚(mm-units)。"""
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

    ax.add_patch(patches.Rectangle((bx, by), bw, bh,
                                   linewidth=0, facecolor=C_BG, zorder=3))
    ax.add_patch(patches.Rectangle((bx, by), bw, bh,
                                   linewidth=1.5, edgecolor=C_WIN_ED,
                                   facecolor=C_WIN, zorder=4))
    cx, cy = bx + bw / 2, by + bh / 2
    ax.text(cx, cy, f"W{win.id}",
            ha="center", va="center",
            fontsize=8, color=C_WIN_ED, fontweight="bold", zorder=5,
            path_effects=[patheffects.withStroke(linewidth=1.5, foreground=C_BG)])


def draw_room_plan(ax, room: RoomModel,
                   probe_points: Optional[Sequence[Tuple[float, float, str, str]]] = None,
                   window_dim_table: bool = True):
    """
    在给定 Axes 上画完整房间平面图：房间轮廓、墙、窗（含窗号）、总尺寸标注、
    指北针；可选叠加验证测点。

    probe_points: [(x_mm, y_mm, label, color), ...]，世界坐标(毫米)。
        label 显示在标记旁（如 "P1"），color 建议用本模块 C_PROBE_* 常量按
        测线区分。传 None 则不画测点（等价于原 ui/canvas.py 的普通平面图）。
    window_dim_table: 是否在图侧打印每扇窗的位置/尺寸文字表（用于人工核对）。
    """
    W, L, H = room.width, room.length, room.height  # mm

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

    for win in room.windows:
        draw_window_plan(ax, win, wt, room)

    dim_arrow(ax, 0, -L * 0.08, W, -L * 0.08, f"宽 {W/1000:.2f} m", "h")
    dim_arrow(ax, -W * 0.1, 0, -W * 0.1, L, f"长 {L/1000:.2f} m", "v")
    draw_compass(ax, W * 0.92, L * 0.92, L * 0.05)

    margin = max(W, L) * 0.18
    extra_right = 0.0

    # ── 测点叠加 ──────────────────────────────────────────────────────────
    # 注意：测点 label 不要用 "W<n>" 前缀，会与窗户编号 W1/W2/... 混淆；
    # 调用方请用不冲突的前缀（如 "沿窗-n" / "墙-n" 或 core.validation 里
    # 建议的记号），本函数按 color 分组自动生成图例，不假设 label 命名规则。
    if probe_points:
        import matplotlib.lines as mlines
        seen_colors: dict = {}
        for x_mm, y_mm, label, color in probe_points:
            ax.scatter([x_mm], [y_mm], s=90, marker="o", color=color,
                      edgecolors="white", linewidths=1.2, zorder=6)
            ax.annotate(label, (x_mm, y_mm),
                       xytext=(0, 10), textcoords="offset points",
                       ha="center", fontsize=8, color=color, fontweight="bold",
                       path_effects=[patheffects.withStroke(linewidth=1.5, foreground=C_BG)],
                       zorder=7)
            seen_colors.setdefault(color, 0)
        handles = [mlines.Line2D([], [], marker="o", linestyle="none",
                                 color=c, markeredgecolor="white",
                                 markersize=8, label=PROBE_COLOR_LEGEND.get(c, c))
                  for c in seen_colors]
        ax.legend(handles=handles, loc="upper left", fontsize=8.5,
                  frameon=True, facecolor="#ffffff", edgecolor="#d0d5e0",
                  framealpha=0.95)

    ax.set_title(f"平面图    层高 {H/1000:.2f} m",
                color=C_TEXT, fontsize=13, fontweight="bold", pad=8)
    ax.set_xlim(-margin, W + margin * 0.5 + extra_right)
    ax.set_ylim(-margin, L + margin * 0.5)
    ax.set_aspect("equal")


def window_dimension_lines(room: RoomModel) -> List[str]:
    """每扇窗的位置/尺寸文字说明（mm），用于图侧核对表 / 命令行打印。"""
    from core.models import WALL_MAP_R
    lines = []
    for win in sorted(room.windows, key=lambda w: (w.wall, w.x)):
        lines.append(
            f"W{win.id}（{WALL_MAP_R.get(win.wall, win.wall)}墙）: "
            f"x={win.x:.0f}~{win.right:.0f}mm  宽{win.width:.0f}×高{win.height:.0f}mm  "
            f"窗台{win.y:.0f}mm  τ={win.tau:.2f}")
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
    图右侧留白打印窗户尺寸表 + 测点坐标表，便于直接对照核实，不用再翻表格。
    """
    matplotlib.use("Agg")
    fig = Figure(figsize=(11, 7.5), facecolor=C_BG)
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
