"""Application font loading shared by the GUI and off-screen screenshots."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtGui import QFont, QFontDatabase
from PyQt6.QtWidgets import QApplication


WINDOWS_CJK_FONTS = (
    Path(r"C:\Windows\Fonts\msyh.ttc"),
    Path(r"C:\Windows\Fonts\msyhbd.ttc"),
    Path(r"C:\Windows\Fonts\simhei.ttf"),
)


def install_chinese_font(app: QApplication, point_size: int = 10) -> str:
    """Load a CJK font explicitly so Qt off-screen rendering also has glyphs."""
    for path in WINDOWS_CJK_FONTS:
        if not path.exists():
            continue
        font_id = QFontDatabase.addApplicationFont(str(path))
        if font_id < 0:
            continue
        families = QFontDatabase.applicationFontFamilies(font_id)
        if families:
            app.setFont(QFont(families[0], point_size))
            return families[0]
    app.setFont(QFont("Microsoft YaHei", point_size))
    return "Microsoft YaHei"
