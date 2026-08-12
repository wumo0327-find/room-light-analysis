"""将长沙幼儿园 DXF 中与墙、柱、窗有关的基础图元渲染为检查图。

这不是通用 DXF 解释器；它只读取本示例转换所需的 LINE 和
LWPOLYLINE，目的是让人工核对 CAD 原始线条范围、图层与平面位置。
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


HERE = Path(__file__).resolve().parent
DXF_PATH = HERE / "长沙幼儿园原始图纸.dxf"
OUTPUT_PATH = HERE / "长沙幼儿园_CAD墙窗图层总览.png"
FIRST_FLOOR_PATH = HERE / "长沙幼儿园_CAD首层墙窗核对图.png"
FIRST_FLOOR_BOUNDS = (-995_000.0, -940_000.0, 688_000.0, 718_000.0)

LAYER_COLORS = {
    "WALL": "#111827",
    "COLUMN": "#374151",
    "WINDOW": "#0284c7",
    "窗-外框": "#0ea5e9",
    "窗-内框分割": "#38bdf8",
}


def read_pairs(path: Path):
    lines = path.read_text(encoding="gbk", errors="replace").splitlines()
    return [
        (lines[index].strip(), lines[index + 1].strip())
        for index in range(0, len(lines) - 1, 2)
    ]


def entity_records(pairs):
    in_entities = False
    current = None
    section_pending = False
    for code, value in pairs:
        if code == "0" and value == "SECTION":
            section_pending = True
            continue
        if section_pending and code == "2":
            in_entities = value == "ENTITIES"
            section_pending = False
            continue
        if code == "0" and value == "ENDSEC":
            if current is not None:
                yield current
                current = None
            in_entities = False
            continue
        if not in_entities:
            continue
        if code == "0":
            if current is not None:
                yield current
            current = {"type": value, "pairs": []}
        elif current is not None:
            current["pairs"].append((code, value))
    if current is not None:
        yield current


def first_value(pairs, wanted, default=""):
    return next((value for code, value in pairs if code == wanted), default)


def entity_path(entity):
    pairs = entity["pairs"]
    if entity["type"] == "LINE":
        values = {code: value for code, value in pairs}
        try:
            return [
                (float(values["10"]), float(values["20"])),
                (float(values["11"]), float(values["21"])),
            ]
        except (KeyError, ValueError):
            return []
    if entity["type"] != "LWPOLYLINE":
        return []
    vertices = []
    pending_x = None
    for code, value in pairs:
        if code == "10":
            pending_x = float(value)
        elif code == "20" and pending_x is not None:
            vertices.append((pending_x, float(value)))
            pending_x = None
    closed = int(float(first_value(pairs, "70", "0"))) & 1
    if closed and vertices:
        vertices.append(vertices[0])
    return vertices


def main():
    entities = list(entity_records(read_pairs(DXF_PATH)))
    paths = []
    texts = []
    counts = Counter()
    for entity in entities:
        layer = first_value(entity["pairs"], "8", "")
        if entity["type"] in {"TEXT", "MTEXT"}:
            values = {}
            fragments = []
            for code, value in entity["pairs"]:
                if code in {"10", "20"} and code not in values:
                    values[code] = value
                if code in {"1", "3"}:
                    fragments.append(value)
            try:
                x, y = float(values["10"]), float(values["20"])
            except (KeyError, ValueError):
                continue
            text = "".join(fragments).replace("\\P", " ").strip()
            if text:
                texts.append((x, y, text))
        if layer not in LAYER_COLORS:
            continue
        path = entity_path(entity)
        if len(path) < 2:
            continue
        paths.append((layer, path))
        counts[(layer, entity["type"])] += 1

    figure, axis = plt.subplots(figsize=(15, 11), dpi=180)
    used_labels = set()
    for layer, path in paths:
        xs, ys = zip(*path)
        label = layer if layer not in used_labels else None
        used_labels.add(layer)
        axis.plot(
            xs,
            ys,
            color=LAYER_COLORS[layer],
            linewidth=0.42 if layer != "WALL" else 0.62,
            alpha=0.90,
            label=label,
        )
    axis.set_aspect("equal", adjustable="datalim")
    axis.set_facecolor("#ffffff")
    axis.grid(True, color="#e5e7eb", linewidth=0.3)
    axis.set_title("长沙幼儿园 CAD 原始墙体与窗图层总览（未做房间识别）")
    axis.set_xlabel("CAD X")
    axis.set_ylabel("CAD Y")
    axis.legend(loc="upper right")
    figure.tight_layout()
    figure.savefig(OUTPUT_PATH, bbox_inches="tight")

    x_min, x_max, y_min, y_max = FIRST_FLOOR_BOUNDS
    plt.rcParams["font.sans-serif"] = [
        "Microsoft YaHei",
        "SimHei",
        "Arial Unicode MS",
    ]
    crop_figure, crop_axis = plt.subplots(figsize=(16, 9), dpi=180)
    for layer, path in paths:
        if not any(
            x_min <= x <= x_max and y_min <= y <= y_max
            for x, y in path
        ):
            continue
        xs, ys = zip(*path)
        crop_axis.plot(
            xs,
            ys,
            color=LAYER_COLORS[layer],
            linewidth=0.75 if layer != "WALL" else 1.1,
            alpha=0.95,
        )
    shown_texts = []
    for x, y, text in texts:
        if x_min <= x <= x_max and y_min <= y <= y_max:
            shown_texts.append(text)
            crop_axis.text(
                x,
                y,
                text,
                fontsize=5.5,
                color="#7c2d12",
                ha="center",
                va="center",
            )
    crop_axis.set_xlim(x_min, x_max)
    crop_axis.set_ylim(y_min, y_max)
    crop_axis.set_aspect("equal", adjustable="box")
    crop_axis.set_facecolor("#ffffff")
    crop_axis.grid(True, color="#e5e7eb", linewidth=0.35)
    crop_axis.set_title("长沙幼儿园首层 CAD 墙体、窗和文字核对图")
    crop_axis.set_xlabel("CAD X")
    crop_axis.set_ylabel("CAD Y")
    crop_figure.tight_layout()
    crop_figure.savefig(FIRST_FLOOR_PATH, bbox_inches="tight")
    print(f"输出：{OUTPUT_PATH}")
    print(f"输出：{FIRST_FLOOR_PATH}")
    # 原始天正文字可能混有无法映射到当前终端编码的字符；这里只报告数量，
    # 文字本身已绘入核对图，避免命令行在最后一步因 UnicodeEncodeError 失败。
    print(f"首层范围内文字数量：{len(shown_texts)}")
    for key, count in sorted(counts.items()):
        print(f"{key[0]} / {key[1]}: {count}")


if __name__ == "__main__":
    main()
