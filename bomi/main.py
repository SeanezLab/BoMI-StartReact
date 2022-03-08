from functools import partial
import PySide6.QtGui as qg
import PySide6.QtWidgets as qw
import PySide6.QtCore as qc
from PySide6.QtCore import Qt

from bomi.device_managers.yost_manager import YostDeviceManager
from bomi.device_managers.yost_widget import YostWidget

from bomi.reaching_widget import ReachingWidget
from bomi.start_react_widget import StartReactWidget
from bomi.window_mixin import WindowMixin
from bomi.base_widgets import wrap_gb
from bomi.cursor import CursorControlWidget
from bomi.version import __version__

from bomi.device_managers.trigno_widget import TrignoWidget, TrignoClient

__appname__ = "BoMI"
__all__ = ["MainWindow", "main"]


class MainWindow(qw.QMainWindow, WindowMixin):
    """Main entry point to BoMI"""

    def __init__(self):
        super().__init__()
        self.yost_dm = YostDeviceManager()
        self.trigno_client = TrignoClient()

        self.init_ui()
        self.init_actions()
        self.init_menus()

        self.status_msg("Welcome to Seanez Lab")
        self.setWindowTitle(__appname__)
        self.setMinimumSize(650, 1000)

    def status_msg(self, msg: str):
        self.statusBar().showMessage(msg)

    def init_ui(self):
        vsplit = qw.QSplitter(Qt.Vertical)
        self.setCentralWidget(vsplit)

        l = qw.QLabel(self, text="BoMI 🚶", alignment=qc.Qt.AlignCenter)  # type: ignore
        l.setFont(qg.QFont("Arial", 16))
        vsplit.addWidget(l)

        ### Device manager group
        vsplit.addWidget(wrap_gb("Yost Device Manager", YostWidget(self.yost_dm)))

        ### Trigno Device manager group
        vsplit.addWidget(
            wrap_gb("Trigno Device Manager", TrignoWidget(self.trigno_client))
        )

        hsplit = qw.QSplitter(Qt.Horizontal)
        vsplit.addWidget(hsplit)

        ### StartReact Group
        hsplit.addWidget(
            wrap_gb("StartReact", StartReactWidget(self.yost_dm, self.trigno_client))
        )

        ### Cursor Task group
        btn_reach = qw.QPushButton(text="Reaching")
        btn_reach.clicked.connect(partial(self.start_widget, ReachingWidget()))  # type: ignore

        hsplit.addWidget(wrap_gb("Cursor Tasks", btn_reach))

        ### Cursor Control group
        self.cursor_control = CursorControlWidget(
            dm=self.yost_dm, show_device_manager=False
        )
        hsplit.addWidget(self.cursor_control)
        self.installEventFilter(self.cursor_control)

    def init_actions(self):
        quitAct = qg.QAction("Exit", self)
        quitAct.setShortcut("ctrl+q")
        quitAct.triggered.connect(self.close)  # type: ignore

        self.addAction(quitAct)

    def init_menus(self):
        menu_bar = qw.QMenuBar(self)
        self.file_menu = menu_bar.addMenu("File")
        self.file_menu.addActions(self.actions())

    def closeEvent(self, event: qg.QCloseEvent) -> None:
        self.cursor_control.stop()
        return super().closeEvent(event)


def main():
    app = qw.QApplication()
    win = MainWindow()
    win.show()
    app.exec()
