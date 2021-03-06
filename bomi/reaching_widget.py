from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from timeit import default_timer
from typing import ClassVar, List, Optional, Tuple

import numpy as np
import PySide6.QtCore as qc
import PySide6.QtGui as qg
import PySide6.QtWidgets as qw
from PySide6.QtCore import Qt

from bomi.base_widgets import generate_edit_form
from bomi.window_mixin import WindowMixin


def _print(*args):
    print("[Reaching]", *args)


@dataclass
class Config:
    # Reaching task params
    hold_time: float = field(default=0.5, metadata=dict(range=(0, 2), step=0.1))
    time_limit: float = field(default=1.0, metadata=dict(range=(0, 2), step=0.1))
    n_targets: int = field(default=8, metadata=dict(range=(1, 10), name="No. Targets"))
    n_reps: int = field(default=3, metadata=dict(range=(1, 5), name="No. Reps"))
    target_radius: int = field(default=40)
    base_radius: int = field(default=60)


YELLOW = qg.QColor(244, 224, 135)
CYAN = qg.QColor(104, 224, 214)
RED = Qt.red
GREEN = Qt.green
BLACK = qg.QColor(34, 36, 41)
GRAY = qg.QColor(111, 112, 116)


@dataclass
class Targets:
    base: qc.QPoint
    all: List[qc.QPoint]
    uniq: List[qc.QPoint]
    idx: int = 0

    inactive_line_clr: ClassVar[qg.QColor] = GRAY
    inactive_fill_clr: ClassVar[qg.QColor] = GRAY
    active_line_clr: ClassVar[qg.QColor] = YELLOW
    active_fill_clr: ClassVar[qg.QColor] = YELLOW

    @classmethod
    def init(cls, n_targets: int, n_reps: int) -> Targets:
        center, targets = cls.generate_targets(n_targets=n_targets, n_reps=n_reps)
        return cls(base=center, all=targets, uniq=list(set(targets)))

    def reinit(self, n_targets: int, n_reps: int):
        center, targets = self.generate_targets(n_targets=n_targets, n_reps=n_reps)
        self.base = center
        self.all = targets
        self.uniq = list(set(targets))
        self.idx = 0

    @property
    def curr(self) -> qc.QPoint:
        return self.all[self.idx % len(self.all)]

    @property
    def n_left(self) -> int:
        return len(self.all) - self.idx

    def move(self) -> None:
        self.active_line_clr = GREEN
        self.active_fill_clr = GRAY
        self.idx += 1

    @staticmethod
    def generate_targets(n_targets=8, n_reps=3) -> Tuple[qc.QPoint, List[qc.QPoint]]:
        # Init targets
        geo = qw.QApplication.primaryScreen().geometry()
        dist = geo.height() / 2 * 0.8
        center = qc.QPoint(geo.width() // 2, geo.height() // 2)
        targets: List[qc.QPoint] = []
        for i in range(n_targets):
            alpha = 2 * np.pi * i / n_targets
            c = center + qc.QPoint(dist * np.cos(alpha), dist * np.sin(alpha))
            targets.append(c)

        target_n = []
        for _ in range(n_reps):
            random.shuffle(targets)
            target_n += targets
        return center, target_n


class ReachingWidget(qw.QWidget, WindowMixin):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Reaching Task")
        self.setWindowFlags(Qt.FramelessWindowHint)

        self.config = config = Config()
        self._targ_phantom_r = 0

        self.init_ui()  # Inititialize user interface
        self.running = False
        self.task_begin_time = 0

        ### Cursor states
        # This is updated only by `mouseMoveEvent`,
        # read-only for `_update_reaching_state`
        self.cursor_pos = qc.QPoint(0, 0)
        self.last_cursor_inside = False
        self.target_acquired_time = math.inf
        self.target_moved_time = math.inf

        ### Task history
        # Every new cursor event is recorded in cursor_history as
        #    (timestamp, (x, y))
        # When a target is reached, the task_history stores
        #    ((target_x, target_y), cursor_history)
        PointT = Tuple[int, int]
        CursorPointT = Tuple[float, PointT]
        CursorHistoryT = List[CursorPointT]
        self.cursor_history: CursorHistoryT = []
        self.task_history: List[Tuple[PointT, CursorHistoryT]] = []

        ### Timer to update states
        self.timer = qc.QTimer()
        self.timer.setInterval(1000 / 50)  # 50 Hz update rate
        self.timer.timeout.connect(self.update_task_state)

        self.targets = Targets.init(config.n_targets, config.n_reps)

        self.popup: Optional[qw.QWidget] = None

        ### target animations
        self.targ_clr_animation = qc.QPropertyAnimation(self, b"target_color")
        self.targ_clr_animation.setDuration(100)

        self._targ_phantom_r = 0
        self.targ_phantom_animation = qc.QPropertyAnimation(self, b"target_phantom_r")
        self.targ_phantom_animation.setDuration(config.hold_time * 1000)
        self.targ_phantom_animation.setEasingCurve(qc.QEasingCurve.OutSine)

    def showEvent(self, event: qg.QShowEvent) -> None:
        self.begin_task()
        return super().showEvent(event)

    def init_ui(self):
        layout = qw.QGridLayout(self)
        self.setLayout(layout)

        # change background color
        p = self.palette()
        p.setColor(self.backgroundRole(), Qt.black)
        self.setPalette(p)

        # init top label
        self.top_label = l1 = qw.QLabel("Reaching", self)
        l1.setStyleSheet("QLabel { background-color: black; color: white; }")
        l1.setFont(qg.QFont("Arial", 18))
        layout.addWidget(l1, 0, 0, alignment=Qt.AlignTop | Qt.AlignLeft)

        self.bottom_label = l1 = qw.QLabel("Press Esc to exit", self)
        l1.setStyleSheet("QLabel { background-color: black; color: white; }")
        l1.setFont(qg.QFont("Arial", 18))
        layout.addWidget(l1, 0, 0, alignment=Qt.AlignBottom | Qt.AlignLeft)

    @qc.Property(qg.QColor)
    def target_color(self) -> qg.QColor:
        return self.targets.active_fill_clr

    @target_color.setter
    def target_color(self, clr: qg.QColor):
        self.targets.active_fill_clr = clr
        self.update()

    @qc.Property(float)
    def target_phantom_r(self) -> float:
        return self._targ_phantom_r

    @target_phantom_r.setter
    def target_phantom_r(self, r: float):
        self._targ_phantom_r = r
        self.update()

    def start_clr_transition(self, val: qg.QColor):
        animation = self.targ_clr_animation
        animation.stop()
        animation.setEndValue(val)
        animation.start()

    def start_phantom_transition(self):
        animation = self.targ_phantom_animation
        if animation.Running == animation.state():
            return

        animation.setStartValue(0)
        animation.setEndValue(self.config.target_radius)

        animation.start()

    def stop_phantom_transition(self):
        animation = self.targ_phantom_animation
        animation.stop()
        self.target_phantom_r = 0

    def begin_task(self, msg: str = "Get Ready"):
        """Start the task"""
        # Show popup window with instructions and countdown
        self.popup = popup = qw.QWidget(self, Qt.SplashScreen)
        layout = qw.QVBoxLayout()
        popup.setLayout(layout)

        instructions = qw.QLabel(
            "Move the cursor inside the active target as quickly as possible."
        )
        instructions.setFont(qg.QFont("Arial", 14))
        instructions.setWordWrap(True)
        layout.addWidget(instructions)

        l1 = qw.QLabel(msg)
        layout.addWidget(l1)
        l1.setFont(qg.QFont("Arial", 18))
        l1.setFrameStyle(qw.QLabel.Panel)
        l1.setAlignment(Qt.AlignCenter)

        btn_layout = qw.QGridLayout()
        layout.addLayout(btn_layout)

        config_btn = qw.QPushButton("Config")
        config_btn.setFont(qg.QFont("Arial", 18))
        btn_layout.addWidget(config_btn, 1, 1, 1, 2)

        def show_config_widget():
            self.config_widget = generate_edit_form(self.config, dialog_box=True)
            self.config_widget.show()

        config_btn.clicked.connect(show_config_widget)

        exit_btn = qw.QPushButton("Exit")
        exit_btn.setFont(qg.QFont("Arial", 18))
        btn_layout.addWidget(exit_btn, 2, 1)

        start_btn = qw.QPushButton("Start")
        start_btn.setFont(qg.QFont("Arial", 18))
        start_btn.setStyleSheet("QPushButton {background-color: rgb(0,255,0);}")
        btn_layout.addWidget(start_btn, 2, 2)

        def _begin_task():
            popup.close()
            self._begin_task()

        def _begin_countdown():
            start_btn.setEnabled(False)
            l1.setText("Starting in 3 s")
            qc.QTimer.singleShot(1000, lambda: l1.setText("Starting in 2 s"))
            qc.QTimer.singleShot(2000, lambda: l1.setText("Starting in 1 s"))
            qc.QTimer.singleShot(3000, _begin_task)

        start_btn.clicked.connect(_begin_task)
        exit_btn.clicked.connect(self.close)

        popup.setFixedSize(480, 250)
        popup.show()

    def _begin_task(self):
        # get new config params
        self.targets.reinit(self.config.n_targets, self.config.n_reps)
        self.targ_phantom_animation.setDuration(self.config.hold_time * 1000)

        _print("Begin task")
        self.running = True
        self.task_begin_time = default_timer()
        self.setMouseTracking(True)
        self.timer.start()
        self.move_target()
        self.update()

    def move_target(self):
        self.targets.move()
        self.target_moved_time = default_timer()
        self.start_clr_transition(YELLOW)

    def finish_task(self):
        """Save task history, reinitialize states."""
        _print("Finish task")
        self.running = False
        self.setMouseTracking(False)
        self.timer.stop()

        # TODO: save task history
        h = self.task_history

        self.begin_task("Good job! Restart?")

    def paintEvent(self, event: qg.QPaintEvent):
        tgts = self.targets
        painter = qg.QPainter(self)
        painter.setRenderHint(qg.QPainter.Antialiasing)

        painter.setPen(qg.QPen(GRAY, 3))
        painter.setBrush(GRAY)

        r = self.config.target_radius
        base_r = self.config.base_radius
        painter.drawEllipse(tgts.base, base_r, base_r)
        for t in tgts.uniq:
            painter.drawEllipse(t, r, r)

        if self.running:
            # Paint target
            painter.setPen(qg.QPen(tgts.active_line_clr, 3))
            painter.setBrush(tgts.active_fill_clr)
            painter.drawEllipse(tgts.curr, r, r)

            # Paint target phantom
            painter.setPen(qg.QPen(CYAN, 3))
            painter.setBrush(CYAN)
            painter.drawEllipse(tgts.curr, self.target_phantom_r, self.target_phantom_r)

        return super().paintEvent(event)

    def update_task_state(self):
        """Check cursor states and decide whether to update the target"""
        now = default_timer()
        if now - self.target_acquired_time > 0:  # inside target
            self.start_phantom_transition()
        else:
            self.stop_phantom_transition()
        tgts = self.targets

        if now - self.target_acquired_time > self.config.hold_time:
            # Cursor has been held inside target for enough time.
            # _print(f"Held for long enough ({self.HOLD_TIME}s). Moving target")

            # Check if task is over
            if tgts.idx >= len(tgts.all):
                self.finish_task()
                self.update()
                return

            # Save task history
            curr = tgts.curr
            self.task_history.append(
                (
                    (curr.x(), curr.y()),
                    self.cursor_history,
                )
            )
            self.cursor_history = []

            # Create a new target
            self.move_target()
            self.update()

        elif now - self.target_moved_time > self.config.time_limit:
            # exceeded time limit
            if tgts.active_line_clr != RED and not self.last_cursor_inside:
                # _print(f"Failed to reach target within time limit ({self.TIME_LIMIT}s)")
                tgts.active_line_clr = RED
                # self.start_transition(self.RED)
                self.update()

    def update(self):
        now = default_timer()
        tgts = self.targets

        if self.running:
            # save cursor history
            self.cursor_history.append(
                (now - self.task_begin_time, self.cursor_pos.toTuple())
            )

            # update cursor states
            d = tgts.curr - self.cursor_pos
            cursor_inside = math.hypot(d.x(), d.y()) < self.config.target_radius
            if cursor_inside:
                if not self.last_cursor_inside:
                    self.target_acquired_time = now
            else:
                self.target_acquired_time = math.inf

            self.last_cursor_inside = cursor_inside
        self.top_label.setText(f"Reaching. Targets to reach: {tgts.n_left}")
        return super().update()

    def mouseMoveEvent(self, event: qg.QMouseEvent) -> None:
        "Callback for any cursor movement inside the widget"
        self.cursor_pos = event.pos()
        self.update()
        return super().mouseMoveEvent(event)

    def closeEvent(self, event: qg.QCloseEvent) -> None:
        self.popup and self.popup.close()
        event.accept()
        return super().closeEvent(event)

    def keyPressEvent(self, event: qg.QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Q):
            self.close()
        return super().keyPressEvent(event)


if __name__ == "__main__":
    app = qw.QApplication()
    win = ReachingWidget()
    win.showMaximized()
    app.exec()
