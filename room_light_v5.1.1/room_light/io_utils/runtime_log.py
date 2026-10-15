"""Small, dependency-free crash logger shared by UI and worker entry points."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import traceback


def write_exception_log(exc_type, exc_value, exc_traceback, *, context="程序") -> Path:
    log_dir = Path.cwd() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    path = log_dir / f"异常_{stamp}.log"
    details = "".join(traceback.format_exception(
        exc_type, exc_value, exc_traceback))
    path.write_text(
        f"时间：{datetime.now().isoformat(timespec='seconds')}\n"
        f"位置：{context}\n\n{details}",
        encoding="utf-8-sig",
    )
    return path


def log_current_exception(*, context="程序") -> Path:
    import sys
    return write_exception_log(*sys.exc_info(), context=context)
