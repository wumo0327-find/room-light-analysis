"""
Room Daylighting Analysis Tool
Entry pointy
"""
import sys
from PyQt6.QtWidgets import QApplication
from ui.mpl_font import setup_font
from ui.main_window import MainWindow


def main():
    setup_font()
    app = QApplication(sys.argv)
    app.setApplicationName("采光分析工具")
    app.setStyle("Fusion")

    # Apply global stylesheet
    with open("ui/style.qss", "r", encoding="utf-8") as f:
        app.setStyleSheet(f.read())

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
