"""Application-wide protection against accidental wheel edits in spin boxes."""
from __future__ import annotations

from PyQt6.QtCore import QEvent, QObject, Qt
from PyQt6.QtWidgets import (
    QAbstractScrollArea,
    QAbstractSpinBox,
    QApplication,
    QWidget,
)


class _NoWheelSpinBoxFilter(QObject):
    """Consume spin-box wheel events and pass their motion to a scroll area."""

    @staticmethod
    def _spin_box_for(watched) -> QAbstractSpinBox | None:
        current = watched if isinstance(watched, QWidget) else None
        while current is not None:
            if isinstance(current, QAbstractSpinBox):
                return current
            current = current.parentWidget()
        return None

    @staticmethod
    def _scroll_area_for(widget: QWidget) -> QAbstractScrollArea | None:
        current = widget.parentWidget()
        while current is not None:
            if isinstance(current, QAbstractScrollArea):
                return current
            current = current.parentWidget()
        return None

    def eventFilter(self, watched, event) -> bool:
        if event.type() != QEvent.Type.Wheel:
            return False
        spin_box = self._spin_box_for(watched)
        if spin_box is None:
            return False

        # Never change a numeric value with the wheel. If the field belongs
        # to a scrollable panel, use that same wheel motion to scroll the page.
        scroll_area = self._scroll_area_for(spin_box)
        if scroll_area is not None:
            horizontal = bool(
                event.modifiers() & Qt.KeyboardModifier.ShiftModifier
            )
            bar = (
                scroll_area.horizontalScrollBar()
                if horizontal
                else scroll_area.verticalScrollBar()
            )
            pixel = (
                event.pixelDelta().x()
                if horizontal
                else event.pixelDelta().y()
            )
            angle = (
                event.angleDelta().x()
                if horizontal
                else event.angleDelta().y()
            )
            if pixel:
                movement = pixel
            elif angle:
                movement = (angle / 120.0) * max(bar.singleStep() * 3, 24)
            else:
                movement = 0
            if movement:
                bar.setValue(round(bar.value() - movement))
        event.accept()
        return True


def install_no_wheel_spinbox_filter(app: QApplication) -> None:
    """Install once for RoomLight and all embedded BP windows/dialogs."""
    attribute = "_roomlight_no_wheel_spinbox_filter"
    if getattr(app, attribute, None) is not None:
        return
    event_filter = _NoWheelSpinBoxFilter(app)
    setattr(app, attribute, event_filter)
    app.installEventFilter(event_filter)
