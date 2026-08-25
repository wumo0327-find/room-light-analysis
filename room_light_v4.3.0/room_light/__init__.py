"""
validate_daylight.py — 采光实测数据交叉验证命令行入口  v2.10.1
================================================================
不依赖 GUI：加载房间（占位几何 或 .rlproj 工程文件）→ 沿"窗中线"/"窗间墙中线"
两条测线生成测点 → 用现有 DF 网格双线性插值取各点采光系数 → 若提供实测 CSV，
计算逐点相对误差 / MAE / 平均相对误差，并出白底学术风格对比图。

不改动 core/daylight.py 的 Ds/Dext/Dint 核心算法，只是取值/对比的轻量封装
（core/validation.py）。

用法：
    # 占位几何冒烟测试（无实测数据，只验证管线通畅）
    python validate_daylight.py --out validate_out

    # 真实教室：从 .rlproj 加载几何 + 实测 CSV 对比
    python validate_daylight.py --project classroom_A.rlproj \
        --measured measured_A.csv --first-offset 0.5 --spacing 0.5 \
        --out validate_out_A

实测 CSV 格式（UTF-8，表头任一别名均可，见 _LINE_ALIASES）：
    测线,测点,实测采光系数
    窗中线,1,3.2
    窗中线,2,2.1
    ...
    窗间墙,1,1.8
    ...
"""
from __future__ import annotations
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd

from core.models import RoomModel, WALL_MAP_R
from core.daylight import compute as compute_daylight
from core.validation import make_probe_lines, sample_df_at_points
from core.plan_export import export_plan_png, C_PROBE_WINDOW, C_PROBE_PIER
from ui.mpl_font import setup_font

# 测线中文/英文别名 → 内部统一键名
_LINE_ALIASES = {
    "window_center_line": "window_center_line", "窗中线": "window_center_line",
    "沿窗中线": "window_center_line", "窗户中线": "window_center_line",
    "pier_center_line": "pier_center_line", "窗间墙": "pier_center_line",
    "沿窗间墙中线": "pier_center_line", "窗间墙中线": "pier_center_line",
}
_LINE_LABEL_ZH = {"window_center_line": "沿窗中线", "pier_center_line": "沿窗间墙中线"}


def _default_room() -> RoomModel:
    """占位几何：6×4×3m 教室，南墙 2 扇窗（用于冒烟测试管线通畅）。"""
    room = RoomModel()
    room.length, room.width, room.height = 6000.0, 4000.0, 3000.0
    w1 = room.add_window("south"); w1.x, w1.width, w1.height = 400.0, 1500.0, 1500.0
    w2 = room.add_window("south"); w2.x, w2.width, w2.height = 2500.0, 1500.0, 1500.0
    return room


def _load_room(project_path: str | None) -> RoomModel:
    if not project_path:
        print("未指定 --project，使用占位几何（6×4×3m 教室 + 2 扇南窗）做管线冒烟测试。")
        return _default_room()
    from io_utils.project_io import load_project
    room, _weather, err = load_project(project_path)
    if err:
        raise SystemExit(f"加载工程文件失败: {err}")
    return room


def _load_measured(path: str | None) -> dict:
    """返回 {(line_key, point_idx:int): measured_DF}；point_idx 从 1 开始。"""
    if not path:
        return {}
    df = pd.read_csv(path, encoding="utf-8-sig")
    cols = {c.strip(): c for c in df.columns}
    # 允许中英文表头
    col_line = next((cols[c] for c in cols if c in ("测线", "line")), None)
    col_pt   = next((cols[c] for c in cols if c in ("测点", "point")), None)
    col_val  = next((cols[c] for c in cols if c in ("实测采光系数", "measured_DF", "measured")), None)
    if not (col_line and col_pt and col_val):
        raise SystemExit(f"实测 CSV 表头无法识别，需要 测线/line、测点/point、实测采光系数/measured_DF 三列，实际列: {list(df.columns)}")
    out = {}
    for _, row in df.iterrows():
        raw_line = str(row[col_line]).strip()
        key = _LINE_ALIASES.get(raw_line)
        if key is None:
            print(f"警告: 无法识别测线名 {raw_line!r}，已跳过该行。")
            continue
        out[(key, int(row[col_pt]))] = float(row[col_val])
    return out


def _build_table(probe: dict, result, first_offset_m: float, spacing_m: float,
                 measured: dict) -> pd.DataFrame:
    rows = []
    for line_key in ("window_center_line", "pier_center_line"):
        pts = probe.get(line_key)
        if pts is None:
            continue
        samples = sample_df_at_points(result, pts)
        for i, (pt, s) in enumerate(zip(pts, samples), start=1):
            depth = first_offset_m + (i - 1) * spacing_m
            meas = measured.get((line_key, i))
            rec = {
                "测线":       _LINE_LABEL_ZH[line_key],
                "测点":       i,
                "x_m":        round(pt[0], 4),
                "y_m":        round(pt[1], 4),
                "离窗距离_m": round(depth, 4),
                "DF_程序(%)": round(s["DF"], 4),
                "DF_实测(%)": meas,
                "取值方式":   s["method"],
            }
            if meas is not None:
                err = s["DF"] - meas
                rec["绝对误差"] = round(err, 4)
                rec["相对误差(%)"] = round(err / meas * 100.0, 2) if abs(meas) > 1e-9 else None
            else:
                rec["绝对误差"] = None
                rec["相对误差(%)"] = None
            rows.append(rec)
    return pd.DataFrame(rows)


def _plot_comparison(table: pd.DataFrame, out_path: str, has_measured: bool):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    C_BG, C_TEXT, C_SEC = "#ffffff", "#1a1e2e", "#5a6175"
    C_ACCENT, C_BORDER, C_GRID = "#2563eb", "#d0d5e0", "#f0f2f6"
    C_MEAS = "#dc2626"

    lines = [l for l in table["测线"].unique()]
    n = max(1, len(lines))
    fig, axes = plt.subplots(1, n, figsize=(6.5 * n, 5), facecolor=C_BG, squeeze=False)
    axes = axes[0]

    for ax, line in zip(axes, lines):
        sub = table[table["测线"] == line].sort_values("离窗距离_m")
        ax.set_facecolor(C_BG)
        ax.plot(sub["离窗距离_m"], sub["DF_程序(%)"], "-o", color=C_ACCENT,
                lw=2.0, ms=5, label="程序计算 DF", zorder=3)
        if has_measured and sub["DF_实测(%)"].notna().any():
            ax.scatter(sub["离窗距离_m"], sub["DF_实测(%)"], color=C_MEAS,
                       s=60, marker="s", label="实测 DF", zorder=4)
        ax.set_xlabel("离窗距离 (m)", color=C_TEXT, fontsize=10)
        ax.set_ylabel("采光系数 DF (%)", color=C_TEXT, fontsize=10)
        ax.set_title(line, color=C_TEXT, fontsize=12, fontweight="bold")
        ax.grid(True, color=C_GRID, lw=0.6)
        for sp in ax.spines.values():
            sp.set_color(C_BORDER); sp.set_linewidth(0.7)
        ax.tick_params(colors=C_SEC, labelsize=9)
        ax.legend(fontsize=9, loc="best", labelcolor=C_TEXT,
                  facecolor="#ffffff", edgecolor=C_BORDER, framealpha=0.95)

    fig.suptitle("采光系数实测对比 — 沿窗中线 / 沿窗间墙中线", color=C_TEXT,
                fontsize=13, fontweight="bold")
    fig.tight_layout()
    fig.savefig(out_path, dpi=300, facecolor=C_BG, bbox_inches="tight")
    plt.close(fig)


# 两条测线各自的配色（单图叠加模式复用；同一测线的散点/曲线同色，仅标记不同）
_LINE_COLORS = {"沿窗中线": "#2563eb", "沿窗间墙中线": "#d97706"}
_LINE_MARKERS = {"沿窗中线": "o", "沿窗间墙中线": "s"}


def _plot_comparison_combined(table: pd.DataFrame, out_path: str, title: str,
                              has_measured: bool, dim_lines=None):
    """单图叠加两条测线：实测散点 + 程序曲线，同一测线同色（仅标记区分），
    白底学术风格。用于"每个房间一张图"的场景（对比 _plot_comparison 的分子图版）。
    dim_lines: 测线中文名集合，命中的测线弱化为灰色虚线+半透明（仍显示，不参与
    视觉主对比，用于"本图聚焦沿窗测线，窗间墙测线仅弱化参考"的场景）。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    C_BG, C_TEXT, C_SEC = "#ffffff", "#1a1e2e", "#5a6175"
    C_BORDER, C_GRID = "#d0d5e0", "#f0f2f6"
    C_DIM = "#b0b6c4"
    dim_lines = set(dim_lines or [])

    fig, ax = plt.subplots(1, 1, figsize=(7.5, 5.5), facecolor=C_BG)
    ax.set_facecolor(C_BG)

    for line in table["测线"].unique():
        sub = table[table["测线"] == line].sort_values("离窗距离_m")
        dimmed = line in dim_lines
        color = C_DIM if dimmed else _LINE_COLORS.get(line, "#2563eb")
        marker = _LINE_MARKERS.get(line, "o")
        ls = "--" if dimmed else "-"
        alpha = 0.55 if dimmed else 1.0
        suffix = "（弱化参考）" if dimmed else ""
        ax.plot(sub["离窗距离_m"], sub["DF_程序(%)"], ls, color=color, alpha=alpha,
                lw=1.4 if dimmed else 2.0, marker=marker, ms=4 if dimmed else 6,
                label=f"{line}{suffix} — 程序计算", zorder=2 if dimmed else 3)
        if has_measured and sub["DF_实测(%)"].notna().any():
            ax.scatter(sub["离窗距离_m"], sub["DF_实测(%)"], color=color, alpha=alpha,
                       s=45 if dimmed else 70, marker=marker,
                       edgecolors="white", linewidths=1.0,
                       label=f"{line}{suffix} — 实测", zorder=2 if dimmed else 4)

    ax.set_xlabel("离窗距离 (m)", color=C_TEXT, fontsize=11)
    ax.set_ylabel("采光系数 DF (%)", color=C_TEXT, fontsize=11)
    ax.set_title(title, color=C_TEXT, fontsize=13, fontweight="bold", pad=10)
    ax.grid(True, color=C_GRID, lw=0.6)
    for sp in ax.spines.values():
        sp.set_color(C_BORDER); sp.set_linewidth(0.7)
    ax.tick_params(colors=C_SEC, labelsize=9)
    ax.legend(fontsize=9, loc="best", labelcolor=C_TEXT,
              facecolor="#ffffff", edgecolor=C_BORDER, framealpha=0.95)
    fig.tight_layout()
    fig.savefig(out_path, dpi=300, facecolor=C_BG, bbox_inches="tight")
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description="采光实测数据交叉验证")
    ap.add_argument("--project", default=None, help=".rlproj 工程文件路径（不填用占位几何）")
    ap.add_argument("--measured", default=None, help="实测数据 CSV 路径（不填只出程序计算值）")
    ap.add_argument("--out", default="validate_out", help="输出目录")
    ap.add_argument("--n-points", type=int, default=5, help="每条测线测点数")
    ap.add_argument("--first-offset", type=float, default=0.5, help="第一测点离窗距离(m)")
    ap.add_argument("--spacing", type=float, default=0.5, help="测点间距(m)")
    ap.add_argument("--window-id", type=int, default=None, help="指定测线基准窗户 id（默认取第一扇）")
    ap.add_argument("--E-out", type=float, default=13500.0, help="室外水平照度(lux)，仅影响 E_lux 列")
    ap.add_argument("--plot-mode", choices=["subplots", "combined"], default="subplots",
                    help="subplots=两测线分子图(默认)；combined=单图叠加两测线(同测线同色)")
    ap.add_argument("--title", default=None, help="对比图标题（默认自动生成）")
    ap.add_argument("--dim-lines", default=None,
                    help="combined 模式下弱化显示的测线名（逗号分隔中文名，如 '沿窗间墙中线'），"
                         "弱化为灰色虚线+半透明，仍显示但不参与视觉主对比")
    ap.add_argument("--no-plan", action="store_true",
                    help="不导出平面图+测点标注图（默认会导出，用于人工核对房间/窗户尺寸和测点位置）")
    ap.add_argument("--all-windows", action="store_true",
                    help="额外导出该墙全部窗户各自沿窗中线的模拟DF（复用同一次网格计算，"
                         "不额外计算）。仅 --window-id 指定的那扇窗有对应实测数据可比对误差，"
                         "其余窗户只有模拟值、无实测对照，输出到独立CSV，用于横向核对各窗是否"
                         "有明显异常（如端墙窗因翼板覆盖范围不同而偏离其余窗户）。")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    setup_font()

    room = _load_room(args.project)
    if not room.windows:
        raise SystemExit("房间没有任何窗户，无法验证。")

    print("计算 DF 网格 ...")
    result = compute_daylight(room, E_out=args.E_out)

    probe = make_probe_lines(room, n_points=args.n_points,
                             first_offset_m=args.first_offset,
                             spacing_m=args.spacing,
                             window_id=args.window_id)
    for w in probe["warnings"]:
        print(f"提示: {w}")
    print(f"测线基准: {probe['wall']} 墙 / 窗户 id={probe['window_id']}")

    if not args.no_plan:
        plan_pts = []
        for i, (x, y) in enumerate(probe["window_center_line"], 1):
            plan_pts.append((x * 1000, y * 1000, f"测-{i}", C_PROBE_WINDOW))
        if probe["pier_center_line"]:
            for i, (x, y) in enumerate(probe["pier_center_line"], 1):
                plan_pts.append((x * 1000, y * 1000, f"墙-{i}", C_PROBE_PIER))
        plan_path = os.path.join(args.out, "平面图.png")
        export_plan_png(room, plan_path, probe_points=plan_pts,
                        title_suffix=f"（{WALL_MAP_R.get(probe['wall'], probe['wall'])}"
                                     f"墙测线基准 W{probe['window_id']}）")
        print(f"平面图+测点已保存: {plan_path}  ← 用于核对房间/窗户尺寸和测点位置")

    measured = _load_measured(args.measured)
    table = _build_table(probe, result, args.first_offset, args.spacing, measured)

    csv_path = os.path.join(args.out, "验证结果表.csv")
    table.to_csv(csv_path, index=False, encoding="utf-8-sig")
    print(f"\n结果表已保存: {csv_path}")
    print(table.to_string(index=False))

    has_measured = bool(measured)
    if has_measured:
        valid = table.dropna(subset=["绝对误差"])
        if not valid.empty:
            mae = float(valid["绝对误差"].abs().mean())
            mre = float(valid["相对误差(%)"].abs().mean())
            print(f"\n平均绝对误差 MAE = {mae:.4f} (DF%)")
            print(f"平均相对误差 MRE = {mre:.2f} %")
        else:
            print("\n实测 CSV 未能与任何测点匹配，跳过误差统计。")

    png_path = os.path.join(args.out, "对比图.png")
    if args.plot_mode == "combined":
        title = args.title or "采光系数实测对比 — 沿窗中线 / 沿窗间墙中线"
        dim = [s.strip() for s in args.dim_lines.split(",")] if args.dim_lines else None
        _plot_comparison_combined(table, png_path, title, has_measured, dim_lines=dim)
    else:
        _plot_comparison(table, png_path, has_measured)
    print(f"对比图已保存: {png_path}")

    if args.all_windows:
        rows = []
        for win in sorted(room.windows_on(probe["wall"]), key=lambda w: w.x):
            p2 = make_probe_lines(room, n_points=args.n_points,
                                  first_offset_m=args.first_offset,
                                  spacing_m=args.spacing, window_id=win.id)
            samp = sample_df_at_points(result, p2["window_center_line"])
            for i, (pt, s) in enumerate(zip(p2["window_center_line"], samp), 1):
                rows.append({
                    "窗号": win.id, "测点": i,
                    "x_m": round(pt[0], 3), "y_m": round(pt[1], 3),
                    "离窗距离_m": args.first_offset + (i - 1) * args.spacing,
                    "DF_程序(%)": round(s["DF"], 4),
                    "有实测对照": "是" if win.id == probe["window_id"] else "否",
                })
        all_win_path = os.path.join(args.out, "全部窗口沿窗中线模拟值.csv")
        pd.DataFrame(rows).to_csv(all_win_path, index=False, encoding="utf-8-sig")
        print(f"全部窗口沿窗中线模拟值已保存: {all_win_path}  "
              f"（仅窗{probe['window_id']}有实测对照，其余窗户只有模拟值供横向核对）")


if __name__ == "__main__":
    main()
