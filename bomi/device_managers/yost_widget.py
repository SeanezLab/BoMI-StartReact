from __future__ import annotations

import traceback
from typing import Final, Tuple

import pyqtgraph as pg
import PySide6.QtCore as qc
import PySide6.QtWidgets as qw
from PySide6.QtCore import Qt
from pathlib import Path

from bomi.datastructure import get_savedir
from bomi.device_managers.table_model import (
    TableModel,
    ColumnProps,
    prop_getter,
    make_getter,
    make_setter,
)
from bomi.device_managers.yost_manager import DeviceT, YostDeviceManager
from bomi.scope_widget import ScopeWidget, ScopeConfig
from bomi.window_mixin import WindowMixin


__all__ = ("YostWidget",)


def _print(*args):
    print("[YostWidget]", *args)


DEVICE_TYPE: Final = {
    "???": "Unknown",
    "BTL": "Bootloader (No Firmware)",
    "USB": "USB",
    "DNG": "Dongle",
    "WL": "Wireless",
    "EM": "Embedded",
    "DL": "Data-logging",
    "BT": "Bluetooth",
}


def get_device_type(dev: DeviceT) -> str:
    return DEVICE_TYPE[dev.device_type]


def get_wl_table(dongle: DeviceT):
    return dongle.wireless_table


def set_wl_table(dongle: DeviceT, idx: int, serial_hex: str):
    try:
        assert 0 <= 15 < idx, "Idx must be between 0 and 14"
        hw_id = int(serial_hex, 16)
        dongle.setSensorToDongle(idx, hw_id)
    except:
        ...


# a list of column props for rendering the device manager table
YOST_COL_PROPS: Tuple[ColumnProps, ...] = (
    ColumnProps("Nickname", str),
    ColumnProps("Serial Number", int).use_getter(prop_getter("serial_number_hex")),
    ColumnProps("Device Type", str).use_getter(get_device_type),
    ColumnProps("Battery", int).use_getter(make_getter("getBatteryPercentRemaining")),
    ColumnProps("Serial Port", str).use_getter(prop_getter("port_name")),
    ColumnProps("Channel", int)
    .use_getter(make_getter("getWirelessChannel"))
    .use_setter(make_setter("setWirelessChannel")),
    ColumnProps("Pan ID", int)
    .use_getter(make_getter("getWirelessPanID"))
    .use_setter(make_setter("setWirelessPanID")),
)


class YostWidget(qw.QWidget, WindowMixin):
    """A GUI for YostDeviceManager.
    Can be used standalone or embedded as a widget
    """

    def __init__(self, save_dir: Path, yost_device_manager: YostDeviceManager):
        super().__init__()
        self.yost_dm = yost_device_manager
        self.save_dir = save_dir
        self.subject_id = self.save_dir.parts[-2]
        self.setWindowTitle("Yost devices")
        self.setMinimumSize(350, 200)
        self.setSizePolicy(qw.QSizePolicy.Expanding, qw.QSizePolicy.Fixed)


        main_layout = qw.QHBoxLayout(self)

        ### Device controls
        layout = qw.QVBoxLayout()
        main_layout.addLayout(layout)

        discover_btn = qw.QPushButton("&Discover devices")
        discover_btn.setStyleSheet("QPushButton { background-color: rgb(0,255,0); }")
        discover_btn.clicked.connect(self.s_discover_devices)

        tare_btn = qw.QPushButton(text="&Tare all devices")
        tare_btn.clicked.connect(self.s_tare_all)

        chart_btn = qw.QPushButton(text="Data &charts")
        chart_btn.clicked.connect(self.s_data_charts)

        commit_btn = qw.QPushButton(text="Commit all settings")
        commit_btn.clicked.connect(self.s_commit_all)

        disconnect_btn = qw.QPushButton(text="Disconnect all")
        disconnect_btn.clicked.connect(self.s_disconnect_all)

        layout.addWidget(discover_btn)
        layout.addWidget(tare_btn)
        layout.addWidget(chart_btn)
        layout.addWidget(commit_btn)
        layout.addWidget(disconnect_btn)

        ### Setup nickname callbacks
        def nn_getter(dev: DeviceT):
            return yost_device_manager.get_device_name(dev.serial_number_hex)

        def nn_setter(dev: DeviceT, name):
            return yost_device_manager.set_device_name(dev.serial_number_hex, name)

        nickname_col = YOST_COL_PROPS[0]
        nickname_col.use_getter(nn_getter).use_setter(nn_setter)

        # Show device status
        self.yost_model = TableModel(YOST_COL_PROPS)
        self.yost_proxy_model = qc.QSortFilterProxyModel()
        self.yost_proxy_model.setSourceModel(self.yost_model)
        self.yost_proxy_model.setDynamicSortFilter(True)

        yost_tv = qw.QTableView()
        yost_tv.setModel(self.yost_proxy_model)
        yost_tv.setSortingEnabled(True)
        yost_tv.setSelectionBehavior(qw.QAbstractItemView.SelectRows)
        yost_tv.setSelectionMode(qw.QAbstractItemView.SingleSelection)
        yost_tv.horizontalHeader().setSectionResizeMode(qw.QHeaderView.Stretch)
        yost_tv.resizeColumnsToContents()
        yost_tv.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        yost_tv.setSizePolicy(qw.QSizePolicy.Expanding, qw.QSizePolicy.Expanding)

        main_layout.addWidget(yost_tv)

    @qc.Slot()
    def s_discover_devices(self):
        self.s_disconnect_all()
        with pg.BusyCursor():
            self.yost_dm.discover_devices()
        if not self.yost_dm.all_sensors:
            self.error_dialog(
                "No devices found. Make sure wired dongle/sensors are plugged in, "
                "and make sure wireless sensors are turned on, and use the same "
                "Channel and Pan ID as the dongle."
            )
        self.yost_model.set_devices(self.yost_dm.dongles + self.yost_dm.all_sensors)
        self.yost_proxy_model.invalidate()

    @qc.Slot()
    def s_tare_all(self):
        if not self.yost_dm.has_sensors():
            return self.no_sensors_error(self.yost_dm)

        self.yost_dm.tare_all_devices()

    @qc.Slot()
    def s_commit_all(self):
        for dev in self.yost_dm.all_sensors + self.yost_dm.dongles:
            dev.commitSettings()

    @qc.Slot()
    def s_data_charts(self):
        if not self.yost_dm.has_sensors():
            return self.no_sensors_error(self.yost_dm)

        ## Start scope here.
        try:
            self._sw = ScopeWidget(
                self.yost_dm,
                get_savedir(self.save_dir, "Scope"),
                self.subject_id,
                ScopeConfig({channel: True for channel in self.yost_dm.CHANNEL_LABELS}),
            )
            self._sw.showMaximized()
        except Exception as e:
            _print(traceback.format_exc())
            self.yost_dm.stop_stream()

    @qc.Slot()
    def s_disconnect_all(self):
        self.yost_dm.close_all_devices()
        self.yost_model.set_devices([])
        self.yost_proxy_model.invalidate()


if __name__ == "__main__":
    app = qw.QApplication()
    dm = YostDeviceManager()
    win = YostWidget(dm)
    win.show()
    app.exec()
