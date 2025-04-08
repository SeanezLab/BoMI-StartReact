from __future__ import annotations

from dataclasses import dataclass
from queue import Queue
from enum import Enum
from pathlib import Path
from timeit import default_timer
from typing import Dict, List, Tuple, Protocol, Iterable

import pyqtgraph as pg
import pyqtgraph.parametertree as ptree
import PySide6.QtCore as qc
import PySide6.QtGui as qg
import PySide6.QtWidgets as qw
from pyqtgraph.parametertree.parameterTypes import ActionParameter
from pyqtgraph.parametertree.parameterTypes.basetypes import Parameter

from bomi.base_widgets import TaskEvent, TaskDisplay, generate_edit_form
from bomi.datastructure import MultichannelBuffer, SubjectMetadata, Packet, TaskType
from bomi.device_managers.protocols import (
    SupportsStreaming,
    SupportsGetSensorMetadata,
    HasChannelLabels,
    HasInputKind,
    SupportsGetChannelMetadata
)
import bomi.colors as bcolors
from trigno_sdk.client import TrignoClient


def _print(*args):
    print("[ScopeWidget]", *args)


PENS = [pg.mkPen(clr, width=2) for clr in bcolors.COLORS]

TARGET_BRUSH_BG = pg.mkBrush(qg.QColor(128, 128, 128, 50))
GREEN = pg.mkBrush(qg.QColor(0, 255, 0, 60))
# TARGET_BRUSH_FG = pg.mkBrush(qg.QColor(254, 136, 33, 50))


@dataclass
class ScopeConfig:
    "Configuration parameters for ScopeWidget"

    input_channels_visibility: dict[str, bool]
    """
    A dictionary containing all the available input channels as keys.
    A corresponding value is set to True to make that channel visible in the ScopeWidget,
    and False to make that channel hidden in the ScopeWidget.
    """

    window_title: str = "Scope"
    show_scope_params: bool = True

    target_show: bool = False
    target_range: Tuple[float, float] = (-50, 50)

    prepared_show: bool = False
    prepared_range: Tuple[float, float] = (-1, 1)

    base_show: bool = False
    base_range: Tuple[float, float] = (-7, -5)

    xrange: Tuple[float, float] = (-6, 0)
    yrange: Tuple[float, float] = (-180, 180)

    autoscale_y: bool = False


@dataclass
class PlotHandle:
    """Holds a PlotItem and its curves"""
    plot: pg.PlotItem | pg.ViewBox
    curves: dict[str, pg.PlotCurveItem | pg.PlotDataItem]
    target: pg.LinearRegionItem | None
    prepared: pg.LinearRegionItem | None
    base: pg.LinearRegionItem | None

    TARGET_NAME = "Target"
    PREPARED_NAME = "Prepare"
    BASE_NAME = "Rest"

    @classmethod
    def init(
        cls,
        plot: pg.PlotItem,
        channel_labels: Iterable[str],
        target_range: Tuple[float, float] = None,
        prepared_range: Tuple[float, float] = None,
        base_range: Tuple[float, float] = None,
    ) -> PlotHandle:
        """Create curves on the given plot object"""
        curves = {
            label: plot.plot(pen=pen, name=label)
            for pen, label in zip(PENS, channel_labels)
        }

        target = (
            cls.init_line_region(plot, target_range, label=cls.TARGET_NAME)
            if target_range
            else None
        )

        prepared = (
            cls.init_line_region(plot, prepared_range, label=cls.PREPARED_NAME)
            if prepared_range
            else None
        )

        base = (
            cls.init_line_region(plot, base_range, label=cls.BASE_NAME)
            if base_range
            else None
        )

        return PlotHandle(plot=plot, curves=curves, target=target, prepared= prepared, base=base)

    @staticmethod
    def init_line_region(
        plot: pg.PlotItem,
        target_range: Tuple[float, float],
        label="Target",
        movable=False,
    ) -> pg.LinearRegionItem:
        """Create a Region on the plot (target or base)"""
        region = pg.LinearRegionItem(
            values=target_range,
            orientation="horizontal",
            movable=movable,
            brush=TARGET_BRUSH_BG,
        )
        pg.InfLineLabel(region.lines[0], label, position=0.05, anchor=(1, 1), color="k")
        plot.addItem(region)
        return region

    ### [[[ Target methods
    def update_target(self, target_range: Tuple[float, float]):
        """Update the 'target' region's position"""
        if self.target is None:
            self.target = self.init_line_region(
                self.plot, target_range, label=self.TARGET_NAME
            )
        else:
            self.target.lines[0].setValue(target_range[0])
            self.target.lines[1].setValue(target_range[1])

    def update_target_color(self, is_green, *args, **argv):
        if self.target:
            if is_green:
                self.target.setBrush(GREEN)
            else:
                self.target.setBrush(TARGET_BRUSH_BG)

    def clear_target(self):
        """Remove the 'target' line region"""
        self.plot.removeItem(self.target)
        self.target = None

    ### Target methods]]]
        
    ### [[[ Prepared methods
    def update_prepared(self, prepared_range: Tuple[float, float]):
        """Update the 'prepared' region's position"""
        if self.prepared is None:
            self.prepared = self.init_line_region(
                self.plot, prepared_range, label=self.PREPARED_NAME
            )
        else:
            self.prepared.lines[0].setValue(prepared_range[0])
            self.prepared.lines[1].setValue(prepared_range[1])

    def update_prepared_color(self, is_green, *args, **argv):
        if self.prepared:
            if is_green:
                self.prepared.setBrush(GREEN)
            else:
                self.prepared.setBrush(TARGET_BRUSH_BG)
    
    def clear_prepared(self):
        """Remove the 'target' line region"""
        self.plot.removeItem(self.prepared)
        self.prepared = None

    ### Target methods]]]

    ### [[[ Base methods
    def update_base(self, base_range: Tuple[float, float]):
        """Update the 'base' region's position"""
        if self.base is None:
            self.base = self.init_line_region(
                self.plot, base_range, label=self.BASE_NAME
            )
        else:
            self.base.lines[0].setValue(base_range[0])
            self.base.lines[1].setValue(base_range[1])

    def update_base_color(self, is_green, *args, **argv):
        if self.base:
            if is_green:
                self.base.setBrush(GREEN)
            else:
                self.base.setBrush(TARGET_BRUSH_BG)

    def clear_base(self):
        """Remove the 'base' line region"""
        self.plot.removeItem(self.base)
        self.base = None

    ### Base methods]]]


class TaskState(Enum):
    OUTSIDE = 0
    IN_TARGET = 1
    IN_BASE = 2
    IN_PREP = 3


class ScopeWidget(qw.QWidget):
    class ScopeWidgetDeviceManager(
        SupportsStreaming,
        SupportsGetSensorMetadata,
        HasChannelLabels,
        HasInputKind,
        SupportsGetChannelMetadata,
        Protocol
    ):
        """
        The device manager of a scope widget must
        support streaming,
        support getting sensor metadata,
        have channel labels,
        have an input kind field,
        and support getting channel units.
        """
        pass

    # Used to inform start_react_widget that user has updated range values.
    # start_react_widget then populate fields with those values next time.
    # str = name of region, tuple = region min, max
    sig_range_updated = qc.Signal(str, tuple)

    def __init__(
        self,
        dm: ScopeWidgetDeviceManager,
        savedir: Path,
        subject_id: str,
        config: ScopeConfig,
        selected_sensor_name: str | Ellipsis = ...,
        task_widget: TaskDisplay = None,
        trigno_client: TrignoClient = None,
    ):
        super().__init__()
        self.setWindowTitle(config.window_title)
        self.dm = dm
        self.savedir = savedir
        self.subject_id = subject_id
        self.selected_sensor_name = selected_sensor_name
        self.task_widget = task_widget
        self.config = config

        if trigno_client and trigno_client.n_sensors:
            self.trigno_client = trigno_client
        else:
            self.trigno_client = None


        self.queue: Queue[Packet] = Queue()

        self.dev_names: List[str] = []  # device name/nicknames
        self.dev_sn: List[str] = []  # device serial numbers (hex str)
        self.init_bufsize = 5000  # buffer size
        self.buffers: Dict[str, MultichannelBuffer] = {}
        self.meta = SubjectMetadata(subject_id=self.subject_id)

        self.last_state = (
            TaskState.OUTSIDE
        )

        
        if self.task_widget:
            self.task_widget.sigColorRegion.connect(self.color_region)
            self.task_widget.sigFlash.connect(self.flash)

        def write_meta():
            print("write_meta", self.meta.dict())
            self.meta.to_disk(self.savedir)

        write_meta()
        self.meta_gb = generate_edit_form(
            self.meta, name="Edit Metadata", callback=write_meta
        )

        ### Parameter tree
        ptparams = [
            dict(name="streaming", title="Streaming", type="bool", value=True),
            ActionParameter(name="tare", title="Tare"),
            dict(
                name="target",
                title="Target",
                type="group",
                children=[
                    dict(
                        name="tshow",
                        title="Show",
                        type="bool",
                        value=config.target_show,
                    ),
                    dict(
                        name="tmax",
                        title="Max",
                        type="float",
                        value=config.target_range[1],
                    ),
                    dict(
                        name="tmin",
                        title="Min",
                        type="float",
                        value=config.target_range[0],
                    ),
                ],
            ),
            dict(
                name="prepared",
                title="Prepared",
                type="group",
                children=[
                    dict(
                        name="pshow",
                        title="Show",
                        type="bool",
                        value=config.prepared_show,
                    ),
                    dict(
                        name="pmax",
                        title="Max",
                        type="float",
                        value=config.prepared_range[1],
                    ),
                    dict(
                        name="pmin",
                        title="Min",
                        type="float",
                        value=config.prepared_range[0],
                    ),
                ]
            ),
            dict(
                name="base",
                title="Base",
                type="group",
                children=[
                    dict(
                        name="bshow",
                        title="Show",
                        type="bool",
                        value=config.base_show,
                    ),
                    dict(
                        name="bmax",
                        title="Max",
                        type="float",
                        value=config.base_range[1],
                    ),
                    dict(
                        name="bmin",
                        title="Min",
                        type="float",
                        value=config.base_range[0],
                    ),
                ],
            ),
            dict(
                name="show",
                title="Show",
                type="group",
                children=[
                    {
                        "name": label,
                        "title": label,
                        "type": "bool",
                        "value": config.input_channels_visibility[label]
                    }
                    for label in self.dm.CHANNEL_LABELS
                ],
            ),
        ]

        self.params: ptree.Parameter = ptree.Parameter.create(
            name="Scope Controls",
            type="group",
            children=ptparams,
        )

        def onShowHideChange(_, changes):
            for param, _, is_visible in changes:
                channel = param.name()
                self.show_hide_curve(channel, is_visible)

        self.params.child("show").sigTreeStateChanged.connect(onShowHideChange)

        def toggle_stream(_, on: bool):
            self.start_stream() if on else self.stop_stream()

        self.params.child("streaming").sigValueChanged.connect(toggle_stream)

        def updateTarget(_, val):
            # for "tshow", val is bool, for ("tmax", "tmin"), probably a value, but doesn't matter
            if val:
                self.update_targets()
            else:
                self.clear_targets()

        self.params.child("target", "tshow").sigValueChanged.connect(updateTarget)
        self.params.child("target", "tmax").sigValueChanged.connect(updateTarget)
        self.params.child("target", "tmin").sigValueChanged.connect(updateTarget)

        def updatePrepared(_, val):
            # for "tshow", val is bool, for ("tmax", "tmin"), probably a value, but doesn't matter
            if val:
                self.update_prepared()
            else:
                self.clear_prepared()    

        self.params.child("prepared", "pshow").sigValueChanged.connect(updatePrepared)
        self.params.child("prepared", "pmax").sigValueChanged.connect(updatePrepared)
        self.params.child("prepared", "pmin").sigValueChanged.connect(updatePrepared)

        def updateBase(_, val):
            if val:
                self.update_base()
            else:
                self.clear_base()

        self.params.child("base", "bshow").sigValueChanged.connect(updateBase)
        self.params.child("base", "bmax").sigValueChanged.connect(updateBase)
        self.params.child("base", "bmin").sigValueChanged.connect(updateBase)

        # keep references of these params for more efficient query
        self.param_tshow: Parameter = self.params.child("target", "tshow")
        self.param_tmax: Parameter = self.params.child("target", "tmax")
        self.param_tmin: Parameter = self.params.child("target", "tmin")
        self.target_range: Tuple[float, float] = (
            self.param_tmin.value(),
            self.param_tmax.value(),
        )

        self.param_pshow: Parameter = self.params.child("prepared", "pshow")
        self.param_pmax: Parameter = self.params.child("prepared", "pmax")
        self.param_pmin: Parameter = self.params.child("prepared", "pmin")
        self.prepared_range: Tuple[float, float] = (
            self.param_pmin.value(),
            self.param_pmax.value(),
        )

        self.param_bshow: Parameter = self.params.child("base", "bshow")
        self.param_bmax: Parameter = self.params.child("base", "bmax")
        self.param_bmin: Parameter = self.params.child("base", "bmin")
        self.base_range: Tuple[float, float] = (
            self.param_bmin.value(),
            self.param_bmax.value(),
        )

        def tare():
            with pg.BusyCursor():
                self.stop_stream()
                self.start_stream()

        self.params.child("tare").sigActivated.connect(tare)

        # init timer
        self.timer = qc.QTimer()
        self.timer.setInterval(0)
        self.timer.timeout.connect(self.update)  # type: ignore
        self.fps_counter = 0
        self.fps_last_time = default_timer()

    def color_region(self, region, is_green):
        if region.lower() == "base":
            self.update_base_color(is_green)
        elif region.lower() == "prep":
            self.update_prepared_color(is_green)
        elif region.lower() == "target":
            self.update_target_color(is_green)


    ### [[[ Targets methods
    def clear_targets(self):
        for name in self.dev_names:
            plot_handle = self.plot_handles[name]
            plot_handle.clear_target()

    def update_targets(self):
        """Handle updating target position (range)"""
        if self.param_tshow.value():
            target_range = (
                self.param_tmin.value(),
                self.param_tmax.value(),
            )
            self.target_range = target_range

            # Remember these values throughout the session
            self.sig_range_updated.emit("target", target_range)
            self.meta.target_range_min = self.target_range[0]
            self.meta.target_range_max = self.target_range[1]

            if self.task_widget:
                self.task_widget.sigTargetMoved.emit(target_range)

            for name in self.dev_names:
                self.plot_handles[name].update_target(target_range)

    def update_target_color(self, is_green, *args, **kwargs):
        """Handle updating target color on plot"""
        for name in self.dev_names:
            self.plot_handles[name].update_target_color(is_green, *args, **kwargs)

    ### Targets methods ]]]
            
    ### [[[ Prepared methods
    def clear_prepared(self):
        for name in self.dev_names:
            plot_handle = self.plot_handles[name]
            plot_handle.clear_prepared()

    def update_prepared(self):
        """Handle updating prepared position (range)"""
        if self.param_pshow.value():
            prepared_range = (
                self.param_pmin.value(),
                self.param_pmax.value(),
            )
            self.prepared_range = prepared_range

            # Remember these values throughout the session
            self.sig_range_updated.emit("prepared", self.prepared_range)
            self.meta.prepared_range_min = self.prepared_range[0]
            self.meta.prepared_range_max = self.prepared_range[1]

            if self.task_widget:
                self.task_widget.sigPreparedMoved.emit(prepared_range)

            for name in self.dev_names:
                self.plot_handles[name].update_prepared(prepared_range)

    def update_prepared_color(self, is_green, *args, **kwargs):
        """Handle updating prepared color on plot"""
        for name in self.dev_names:
            self.plot_handles[name].update_prepared_color(is_green, *args, **kwargs)

    ### Prepared methods ]]]

    ### [[[ Base methods
    def clear_base(self):
        for name in self.dev_names:
            plot_handle = self.plot_handles[name]
            plot_handle.clear_base()

    def update_base(self):
        """Handle updating base position (range)"""
        if self.param_bshow.value():
            self.base_range = base_range = (
                self.param_bmin.value(),
                self.param_bmax.value(),
            )

            self.base_range = base_range

            # Remember these values throughout the session
            self.sig_range_updated.emit("base", self.base_range)

            if self.task_widget:
                self.task_widget.sigBaseMoved.emit(base_range)

            for name in self.dev_names:
                self.plot_handles[name].update_base(base_range)

    def update_base_color(self, is_green, *args, **kwargs):
        """Handle updating target color on plot"""
        for name in self.dev_names:
            self.plot_handles[name].update_base_color(is_green, *args, **kwargs)

    ### Base methods ]]]

    def show_hide_curve(self, name: str, show: bool):  #TODO:
        if show:
            for dev in self.dev_names:
                handle = self.plot_handles[dev]
                handle.plot.addCurve(handle.curves[name])
        else:
            for dev in self.dev_names:
                handle = self.plot_handles[dev]
                handle.plot.removeItem(handle.curves[name])

    def showEvent(self, event: qg.QShowEvent) -> None:
        """Override showEvent to initialise data params and UI after the window is shown.
        This is because we need to know the number of sensors available to create the
        same number of plots
        """
        self.init_data()
        self.init_ui()
        return super().showEvent(event)

    def init_data(self): #TODO
        ### data
        while self.queue.qsize():
            self.queue.get()
        self.dev_names = self.dm.get_all_sensor_names()
        self.dev_sn = self.dm.get_all_sensor_serial()
        # instead of checking everywhere if we have selected_sensor_name set,
        # check once here and overwrite dev_names and dev_sn
        if self.selected_sensor_name is not ...:
            selected_index = self.dev_names.index(self.selected_sensor_name)
            self.dev_sn = [self.dev_sn[selected_index]]
            self.dev_names = [self.selected_sensor_name]

        for device_name in self.dev_names:
            if device_name in self.buffers:  # buffer already initialized
                continue
            self.buffers[device_name] = MultichannelBuffer(
                bufsize=self.init_bufsize,
                savedir=self.savedir,
                name=device_name,
                input_kind=self.dm.INPUT_KIND,
                channel_labels=self.dm.CHANNEL_LABELS
            )

    def init_ui(self):
        ### Init UI
        main_layout = qw.QHBoxLayout()
        self.setLayout(main_layout)
        splitter = qw.QSplitter()
        main_layout.addWidget(splitter)

        self.glw = glw = pg.GraphicsLayoutWidget()
        self.glw.setBackground("white")
        splitter.addWidget(glw)

        row = 1

        ### Init Plots
        self.plot_handles: Dict[str, PlotHandle] = {}
        plot_style = {"color": "k"}  # label style
        for name, sn in zip(self.dev_names, self.dev_sn):
            plot: pg.PlotItem = glw.addPlot(row=row, col=0)
            row += 1
            plot.showAxis('right', show=True)
            plot.showGrid(y=True,alpha=0.15)
            plot.addLegend(offset=(1, 1), **plot_style)
            plot.setXRange(*self.config.xrange)
            plot.setYRange(*self.config.yrange)

            if self.config.autoscale_y:
                plot.enableAutoRange(axis="y")
            plot.setLabel("bottom", "Time", units="s", **plot_style)

            if self.task_widget:
                channel = self.task_widget.selected_channel
                plot.setLabel("left", channel, units=self.dm.get_channel_unit(channel), **plot_style)
            else:
                plot.setLabel("left", "All channels", **plot_style)

            plot.setDownsampling(mode="peak")
            title = f"{sn}" if name == sn else f"{sn} ({name})"
            plot.setTitle(title, **plot_style)
            self.plot_handles[name] = PlotHandle.init(plot, self.dm.CHANNEL_LABELS)

        ### Init RHS of window
        RHS = qw.QWidget()
        layout = qw.QVBoxLayout()
        RHS.setLayout(layout)
        splitter.addWidget(RHS)

        # Create param tree
        pt = ptree.ParameterTree(showHeader=False)
        pt.setParameters(self.params)
        if self.config.show_scope_params:
            layout.addWidget(pt, 1)

        layout.addWidget(self.meta_gb)

        # Create task widget
        if self.task_widget is not None:
            layout.addWidget(self.task_widget, 1)

            def _trial_begin():
                if self.task_widget.task_type == TaskType.ACTIVE:
                    self.task_widget.sigColorRegion.emit("target", True)
                    self.task_widget.sigColorRegion.emit("prep", False)
                    self.flash(bcolors.GREEN)

                elif self.task_widget.task_type == TaskType.REST:
                    self.task_widget.sigColorRegion.emit("target", True)
                    self.task_widget.sigColorRegion.emit("base", False)
                    self.flash(bcolors.GREEN)

                else: 
                    self.task_widget.sigColorRegion.emit("target", True)
                    self.task_widget.sigColorRegion.emit("base", False)
                    self.flash("white")


            def _trial_end():
                pass
                # self.flash(bcolors.GREEN)

            self.task_widget.sigTrialBegin.connect(_trial_begin)
            self.task_widget.sigTrialEnd.connect(_trial_end)
            self.task_widget.config.to_disk(self.savedir)  # type: ignore

        ### apply other config
        config = self.config
        config.target_show and self.update_targets()
        config.prepared_show and self.update_prepared()
        config.base_show and self.update_base()
        for channel, is_visible in self.config.input_channels_visibility.items():
            self.show_hide_curve(channel, is_visible)
        self.start_stream()

    def flash(self, color="green", duration_ms=500):
        self.glw.setBackground(color)
        qc.QTimer.singleShot(duration_ms, lambda: self.glw.setBackground("white"))

    def start_stream(self): #TODO
        """Start the stream and show in the scope
        Clear the queue and buffers
        """
        self.init_data()

        dummy_queue = _DummyQueue()
        if self.trigno_client:
            self.trigno_client.handle_stream(dummy_queue, self.savedir)
        self.dm.start_stream(self.queue)
        #start QTM stream
        self.timer.start()

    def stop_stream(self):
        """Stop the data stream and update timer"""
        self.dm.stop_stream()
        hasattr(self, "timer") and self.timer.stop()
        self.trigno_client and self.trigno_client.stop_stream()
        #stop QTM stream

    def update(self):
        """Update function connected to the timer
        1. Consume all data currently in the queue
        2. If successful, update all plots
        3. If applicable, update task states
        """
        self.fps_counter += 1
        if self.fps_counter > 2000:
            now = default_timer()
            interval = now - self.fps_last_time
            fps = self.fps_counter / interval
            self.fps_counter = 0
            self.fps_last_time = now
            #_print("FPS: ", fps)

        q = self.queue
        qsize = q.qsize()
        # if not qsize:
        # return

        for _ in range(qsize):  # process current items in queue
            packet = q.get()

            try:
                buffer = self.buffers[packet.device_name]
            except KeyError:
                # When we select a single sensor,
                # the device manager will still populate the queue
                # with packets from the other sensors (not ideal).
                # Ignore these.
                continue
            buffer.add_packet(packet)

        # On successful read from queue, update curves 
        now = default_timer()
        for name in self.dev_names:
            buf = self.buffers[name]
            curves = self.plot_handles[name].curves

            x = -(now - buf.timestamp)
            for label in self.dm.CHANNEL_LABELS:
                curves[label].setData(x=x, y=buf.data[label])

        ### Update task states if needed
        # 1. Check if last measurement is within target range
        # 2. Check if last measurement is within base range
        if self.task_widget:
            tmin, tmax = self.target_range
            pmin, pmax = self.prepared_range
            bmin, bmax = self.base_range
            for name in self.dev_names:
                buffer = self.buffers[name]
                most_recent_measurement = buffer.data[self.task_widget.selected_channel][-1]


                #### Moving from a zone of interest to anywhere outside ####
                # If recent measurement was in target but is no longer, set state to outside
                if self.last_state == TaskState.IN_TARGET:
                    if not tmin <= most_recent_measurement <= tmax:
                        self.task_widget.sigTaskEventIn.emit(TaskEvent.EXIT_TARGET)
                        self.last_state = TaskState.OUTSIDE
                # If recent measurement was in base but is no longer, set state to outside
                elif self.last_state == TaskState.IN_BASE:
                    if not bmin <= most_recent_measurement <= bmax:
                        self.task_widget.sigTaskEventIn.emit(TaskEvent.EXIT_BASE)
                        self.last_state = TaskState.OUTSIDE
                # If recent measurement was in prepared but is no longer, set state to outside
                elif self.last_state == TaskState.IN_PREP:
                    if not pmin <= most_recent_measurement <= pmax:
                        self.task_widget.sigTaskEventIn.emit(TaskEvent.EXIT_PREP)
                        self.last_state = TaskState.OUTSIDE

                #### Moving from outside a zone of interest into one ####
                else:
                    # Recent mesurement was outside range but is now in target
                    if tmin <= most_recent_measurement <= tmax:
                        self.task_widget.sigTaskEventIn.emit(TaskEvent.ENTER_TARGET)
                        self.last_state = TaskState.IN_TARGET
                    # Recent mesurement was outside range but is now in base
                    elif bmin <= most_recent_measurement <= bmax:
                        self.task_widget.sigTaskEventIn.emit(TaskEvent.ENTER_BASE)
                        self.last_state = TaskState.IN_BASE
                    # Recent mesurement was outside range but is now in prepared
                    elif pmin <= most_recent_measurement <= pmax:
                        self.task_widget.sigTaskEventIn.emit(TaskEvent.ENTER_PREP)
                        self.last_state = TaskState.IN_PREP

    def closeEvent(self, event: qg.QCloseEvent) -> None:
        with pg.BusyCursor():
            self.stop_stream()
            self.print_max_recorded_magnitudes()

        self.task_widget and self.task_widget.close()
        # Remove references to MultichannelBuffer objects
        # The filepointers will be closed when GC runs
        self.buffers.clear()
        return super().closeEvent(event)

    def print_max_recorded_magnitudes(self):
        from numpy import genfromtxt

        print("Max magnitudes:")
        for path in self.savedir.iterdir():
            if path.suffix != ".csv":
                continue

            with open(path) as f:
                print(f"\t{path.name}:")
                array = genfromtxt(path, delimiter=",", names=True)
                try:
                    for channel in self.dm.CHANNEL_LABELS:
                        max_magnitude = max(array[channel], key=abs)
                        print(f"\t\t{channel}: {max_magnitude}")
                        print()
                except ValueError as e:
                    print(f"Value Error: {e}")


class _DummyQueue(Queue):
    def __init__(self):
        ...

    def put(self, _):
        ...

    def get(self) -> None:
        ...
