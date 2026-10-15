"""
Room Daylighting Analysis Tool
Entry point
"""
import sys
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QMessageBox
from ui.mpl_font import setup_font
from ui.main_window import MainWindow
from ui.no_wheel_spinboxes import install_no_wheel_spinbox_filter
from io_utils.runtime_log import write_exception_log


def _install_exception_guard(app):
    """Keep uncaught Qt-slot exceptions from silently terminating the app."""
    handling = {"active": False}

    def _hook(exc_type, exc_value, exc_traceback):
        try:
            path = write_exception_log(
                exc_type, exc_value, exc_traceback, context="未捕获界面异常")
        except Exception:
            path = None
        if handling["active"]:
            return
        handling["active"] = True

        def _show():
            try:
                suffix = f"\n\n错误日志：{path}" if path else ""
                QMessageBox.critical(
                    None, "操作失败",
                    "本次操作发生异常，程序已阻止异常继续扩散。"
                    "工程文件不会因此被覆盖。" + suffix)
            finally:
                handling["active"] = False
        if app.closingDown():
            handling["active"] = False
        else:
            QTimer.singleShot(0, _show)

    sys.excepthook = _hook


def main():
    setup_font()
    app = QApplication(sys.argv)
    app.setApplicationName("采光分析工具")
    app.setStyle("Fusion")
    _install_exception_guard(app)
    install_no_wheel_spinbox_filter(app)

    # Apply global stylesheet
    with open("ui/style.qss", "r", encoding="utf-8") as f:
        app.setStyleSheet(f.read())

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
