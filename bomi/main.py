from pathlib import Path

import PySide6.QtGui as qg
import PySide6.QtWidgets as qw

from bomi.device_managers.yost_manager import YostDeviceManager
from bomi.device_managers.yost_widget import YostWidget

from bomi.device_managers.qtm_manager import QtmDeviceManager
from bomi.device_managers.qtm_widget import QtmWidget

from bomi.start_react_widget import StartReactWidget
from bomi.window_mixin import WindowMixin
from bomi.base_widgets import wrap_gb, ConfirmationDialog

from bomi.device_managers.trigno_widget import TrignoWidget, TrignoClient



__appname__ = "BoMI"
__all__ = ["MainWindow", "main"]


class MainWindow(qw.QMainWindow, WindowMixin):
    """Main entry point to BoMI"""

    def __init__(self):
        super().__init__()
        self.yost_dm = YostDeviceManager()
        self.trigno_client = TrignoClient()
        self.qtm_dm = QtmDeviceManager()
        self.save_dir = None

        while not self.save_dir:
            self.prompt_for_save_dir_name()

        self.init_ui()
        self.init_actions()
        
        # menu bar overlaps with the device manager on Windows
        # self.init_menus()

        self.status_msg("Welcome to Seanez Lab")
        self.setWindowTitle(__appname__)
        self.setMinimumSize(1000, 600)

    def status_msg(self, msg: str):
        self.statusBar().showMessage(msg)

    def init_ui(self):
        w = qw.QWidget()
        self.setCentralWidget(w)
        tabs = qw.QTabWidget()

        hbox = qw.QHBoxLayout(w)
        vbox1 = qw.QVBoxLayout()
        vbox2 = qw.QVBoxLayout()

        hbox.addLayout(vbox1)
        tmp = qw.QWidget()
        tmp.setLayout(vbox2)
        tmp.setSizePolicy(qw.QSizePolicy.Fixed, qw.QSizePolicy.Expanding)
        hbox.addWidget(tmp)

        ### Device manager group
        ### YOST IMU mamanger group
        _gbIMU = wrap_gb("Yost devices", YostWidget(self.save_dir, self.yost_dm))
        _gbIMU.setSizePolicy(qw.QSizePolicy.Expanding, qw.QSizePolicy.Fixed)
        tabs.addTab(_gbIMU, "Yost")
        #vbox1.addWidget(tabs)

        ### Trigno Device manager group
        _gbTrigno = wrap_gb("Trigno devices", TrignoWidget(self.save_dir, self.trigno_client))
        tabs.addTab(_gbTrigno, "Trigno")

        ### Biodex manager group
        _gbBiodex = wrap_gb("Biodex devices", QtmWidget(self.save_dir, self.qtm_dm))
        _gbBiodex.setSizePolicy(qw.QSizePolicy.Expanding, qw.QSizePolicy.Fixed)
        tabs.addTab(_gbBiodex, "Biodex")

        vbox1.addWidget(tabs) # adds tab widget to vertical box layout 1

        ### StartReact manager group
        vbox2.addWidget(
            wrap_gb("StartReact", StartReactWidget(self.save_dir, [self.yost_dm, self.qtm_dm], self.trigno_client))
        )
   
    def init_actions(self):
        """
        Initialize QActions
        """
        quitAct = qg.QAction("Exit", self)
        quitAct.setShortcut("ctrl+q")
        quitAct.triggered.connect(self.close)  # type: ignore

        self.addAction(quitAct)

    def init_menus(self):
        """
        Initialize QMenuBar
        """
        menu_bar = qw.QMenuBar(self)
        self.file_menu = menu_bar.addMenu("File")
        self.file_menu.addActions(self.actions())

    def closeEvent(self, event: qg.QCloseEvent) -> None:
        #self.cursor_control.stop()
        return super().closeEvent(event)
    
    def prompt_for_save_dir_name(self):
        confirmation_dialog = ConfirmationDialog(parent=self)
        confirmation_dialog.sig_save.connect(self.on_confirmation)
        confirmation_dialog.sig_close.connect(self.closeEvent)
        confirmation_dialog.exec()

    def on_confirmation(self, save_dir: Path):
        self.save_dir = Path(save_dir)
    
def main():
    app = qw.QApplication()
    win = MainWindow()
    win.show()
    app.exec()


if __name__ == "__main__":
    main()
