"""The animated session-state dot (spec 14.4, 14.3).

The design system pulses this with a CSS keyframe animation. Qt stylesheets have no
keyframes, so the pulse is a QVariantAnimation driving a custom paint.

Spec 14.3's constraint on what it may claim still holds, but the ground under it moved.
Under docking a launcher's window caption was the only source, so LIVE and DEAD were the
honest limit. Owning the pty means the program tells us its title and rings its own bell,
and every state below is something observed rather than inferred -- see
``term/activity.py`` for what each one is read from.

A pulse still only appears for a state that is genuinely live. Animating a dot for a
finished turn would be inventing activity, which is the same mistake in a smaller place.
"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QSize, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget

from ..theme import Theme

#: Session states the dot understands. LIVE and DEAD come from the process; RUNNING and
#: WAITING are read off the pty title by ``term/activity.py``; ATTENTION comes from the
#: program ringing the bell. None of them needs a launcher to declare anything now that we
#: own the byte stream -- the title_patterns mechanism spec 14.3 rejected is gone.
LIVE = "live"
DEAD = "dead"
RUNNING = "running"
WAITING = "waiting"
ATTENTION = "attention"

DIAMETER = 7
#: DS keyframe: opacity .3 -> 1 -> .3.
MIN_OPACITY = 0.3


class StatusDot(QWidget):
    """A small round state indicator, pulsing only when the state is genuinely known."""

    def __init__(self, state: str = DEAD, theme: Theme | None = None,
                 diameter: int = DIAMETER, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme or Theme()
        self._state = state
        self._diameter = diameter
        self._opacity = 1.0

        self.setFixedSize(QSize(diameter, diameter))
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self._animation = QVariantAnimation(self)
        self._animation.setStartValue(1.0)
        self._animation.setEndValue(MIN_OPACITY)
        self._animation.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._animation.setLoopCount(-1)
        self._animation.valueChanged.connect(self._on_opacity)

        self._sync()

    # --- state ---------------------------------------------------------------------

    @property
    def state(self) -> str:
        return self._state

    def set_state(self, state: str) -> None:
        if state == self._state:
            return
        self._state = state
        self._sync()

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        self._sync()

    def _sync(self) -> None:
        if self._theme.dot_pulses(self._state):
            # Halved, then reversed by the loop, so a full cycle matches the DS period.
            self._animation.setDuration(self._theme.pulse_period_ms(self._state) // 2)
            self._animation.setDirection(QVariantAnimation.Direction.Forward)
            if self._animation.state() != QVariantAnimation.State.Running:
                self._animation.start()
        else:
            self._animation.stop()
            self._opacity = 1.0
        self.update()

    def _on_opacity(self, value: float) -> None:
        self._opacity = float(value)
        # Reverse at each end rather than snapping back, which would read as a blink.
        anim = self._animation
        if anim.currentTime() >= anim.duration():
            anim.setDirection(
                QVariantAnimation.Direction.Backward
                if anim.direction() == QVariantAnimation.Direction.Forward
                else QVariantAnimation.Direction.Forward
            )
        self.update()

    # --- paint ---------------------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        colour = QColor(self._theme.dot_color(self._state))
        colour.setAlphaF(self._opacity)
        painter.setBrush(colour)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(self.rect())

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(self._diameter, self._diameter)
