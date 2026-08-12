"""
run_experiments.py — 参数化实验命令行入口  v2.5.1
==================================================
不启动 GUI，批量运行「玻璃对照 + 遮阳β扫描」两组实验，
导出结果表(CSV)与散点气泡图(PNG)。

用法（在 room_light/ 目录下）：
    python run_experiments.py                     # 默认参数
    python run_experiments.py --out out_dir       # 指定输出目录
    python run_experiments.py --beta-max 60 --beta-step 5 --H 1500
    python run_experiments.py --ndiv 40           # 更高采光精度(更慢)
    python run_experiments.py --u0-min 0.0        # 放宽合规筛选(侧窗U0偏低)
    python run_experiments.py --y thermal_discomfort  # 热轴改用连续量(区分度更好)
"""
from __future__ import annotations
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import experiments as ex
from io_utils.weather_data import default_dataset
from ui.mpl_font import setup_font   # 复用项目中文字体配置（供图表标签）


# y 轴指标 → (是否越大越好, 中文标签)
_Y_META = {
    "comfort_ratio":       (True,  "舒适月数占比"),
    "comfort_months":      (True,  "舒适月数"),
    "thermal_discomfort":  (False, "热不舒适度 Σ(超温+欠温) ℃·月"),
    "overheat_degree_months": (False, "超温强度 Σ(T−26) ℃·月"),
}


def main():
    ap = argparse.ArgumentParser(description="room_light 参数化实验批量运行")
    ap.add_argument("--out", default="experiment_out", help="输出目录")
    ap.add_argument("--ndiv", type=int, default=20, help="采光窗口离散数(越大越精、越慢)")
    ap.add_argument("--ra-threshold", type=float, default=2.0, help="Ra 达标 DF 阈值(%)")
    ap.add_argument("--beta-max", type=float, default=60.0, help="遮阳特征角上限(°)")
    ap.add_argument("--beta-step", type=float, default=5.0, help="遮阳特征角步长(°)")
    ap.add_argument("--H", type=float, default=1500.0, help="遮阳基准高度 H(mm)")
    ap.add_argument("--u0-min", type=float, default=0.70, help="合规筛选 U0 下限")
    ap.add_argument("--y", default="comfort_ratio", choices=list(_Y_META),
                    help="帕累托/绘图的热轴指标")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    setup_font()                      # 配置 matplotlib 中文字体
    weather = default_dataset()
    n = int(round(args.beta_max / args.beta_step)) + 1
    beta_degs = [round(i * args.beta_step, 3) for i in range(n)]

    def prog(tag, i, total):
        print(f"  [{tag}] {i+1}/{total}", end="\r", flush=True)

    print("运行实验中 ...")
    df = ex.run_all_experiments(
        weather=weather, ndiv=args.ndiv, ra_threshold=args.ra_threshold,
        beta_degs=beta_degs, H_mm=args.H, progress_cb=prog)
    print()

    csv_path = os.path.join(args.out, "experiment_results.csv")
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    print(f"结果表已保存: {csv_path}  ({len(df)} 行)")

    maximize_y, y_label = _Y_META[args.y]
    png_path = os.path.join(args.out, "pareto_scatter.png")
    ex.plot_experiments(
        df, out_path=png_path, x="Ra", y=args.y, maximize_y=maximize_y,
        size_col="cost", u0_min=args.u0_min, y_label=y_label, dpi=200)
    print(f"散点气泡图已保存: {png_path}")

    pf = ex.pareto_front(df, x="Ra", y=args.y, maximize_y=maximize_y,
                         u0_min=args.u0_min)
    print(f"\n帕累托前沿(U0≥{args.u0_min:g}, x=Ra, y={args.y})：{len(pf)} 个非支配解")
    if not pf.empty:
        show = ["group", "param_label", "Ra", "U0", args.y, "cost"]
        print(pf[[c for c in show if c in pf.columns]].to_string(index=False))
    else:
        print("  （无满足合规筛选的点；侧窗 U0 偏低时可用 --u0-min 放宽）")


if __name__ == "__main__":
    main()
