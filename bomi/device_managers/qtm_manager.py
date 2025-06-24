import bomi.device_managers.analog_streaming_client as AS
from bomi.datastructure import Packet
from queue import Queue
from typing import Iterable
from threading import Event, Thread
from PySide6.QtCore import Signal, QObject
from bomi.device_managers.analog_streaming_client import Channel


def _print(*args):

    print("[QTM]", *args)


class QtmDeviceManager(QObject):
    """
    The wrapper that calls the QTM Client. Responsible for discovering connected channels,
    implementing queue, and starting/stopping the date stream.
    
    """
    discover_devices_signal = Signal()

    CHANNEL_LABELS = [

        Channel.TORQUE,
        Channel.VELOCITY,
        Channel.POSITION,
        Channel.TRIGGER,
        
    ]

    INPUT_KIND = "QTM"

    @staticmethod
    def get_channel_unit(channel: str) -> str:
        match channel:
            case Channel.TORQUE:
                return "N⋅m"
            case Channel.VELOCITY:
                return "deg/s"
            case Channel.POSITION:
                return "deg"
            case Channel.TRIGGER:
                return "V"
            case _:
                raise ValueError("Not a valid QTM channel")

    @staticmethod
    def get_channel_default_range(channel: str) -> tuple[int, int]:
        match channel:
            case Channel.TORQUE:
                return -10, 50
            case Channel.VELOCITY:
                return -40, 40
            case Channel.POSITION:
                return -60, 60
            case Channel.TRIGGER:
                return -60, 60
            case _:
                raise ValueError("Not a valid QTM channel")

    def __init__(self):
        super().__init__()
        self.qtm_streaming = False
        self.all_channels = []
        self.qtm_ip = '10.229.96.167'  # connect to QLineEdit input of Biodex Widget
        self.port = 22223
        self.version = '1.22'
        self._done_streaming = Event()
        self._thread: Thread | None = None

    def status(self) -> str:
        """
        Check status of discovered QTM channels
        """
        return (
            f"Discovered {len(self.all_channels)} channels"
        )

    def discover_devices(self):
        """
        Sets the QTM application to remote control. Then iterates through each channel and gets
        their ID's.
        """
        try:
            channels = AS.get_channel_number(self.qtm_ip, self.port, self.version)
        except AS.QTMConnectionError:
            return None
        analog_idx = [[x] for x in channels]
        self.all_channels = analog_idx  # channels from QTM connection
        _print(self.status())
        self.discover_devices_signal.emit()

    def get_all_sensor_names(self) -> Iterable[str]:
        """
        Returns the names of the sensors added to this device manager
        For QTM, returns a list w/ string "QTM" meaning QTM is the sensor
        """
        return ["QTM"]

    def get_all_sensor_serial(self) -> Iterable[str]:
        """
        Returns the hex serials of the sensors added to this device manager
        "QTM does not have multiple sensors, so also return a list w/ string "QTM"
        """
        return ["QTM"]

    def start_stream(self, queue: Queue[Packet]) -> None:
        """
        Start streaming data to the passed in queue
        """
        if not self.has_sensors():
            _print("No sensors found. Aborting stream")
            return

        if not self.qtm_streaming:
            _print("Start streaming")
            self._done_streaming.clear()
            self._thread = Thread(
                target=AS.real_time_stream,
                args=(
                    queue,
                    self._done_streaming,
                    self.qtm_ip, self.port, self.version,
                ),
            )
            self._thread.start()


    def stop_stream(self) -> None:
        """
        Stop streaming data
        """
        if self._thread and not self._done_streaming.is_set():
            #_print("Stopping stream")
            self._done_streaming.set()
            self._thread.join()
            self._thread = None
            _print("Stream stopped")

    def has_sensors(self) -> bool:
        """
        Returns True if the device manager has sensors added, QTM is considerd a sensor.
        If channels exist, you are connect to QTM
        """
        return len(self.all_channels) > 0

    def disconnect(self):
        self.stop_stream()
        self.all_channels = []


if __name__ == '__main__':
    """
    Debugging code to test functionality of qtm manager, send internal queue
    """
    elements_to_get = 10

    qtm = QtmDeviceManager()
    qtm.discover_devices()
    print("What devices?", qtm.status())

    testing_queue = Queue()
    qtm.start_stream(testing_queue)
    for i in range(elements_to_get):
        if i < 5:
            print('i:', i)
            print(testing_queue.get())
        if i == 5:
            print("Go ahead and stop")
            qtm.stop_stream()
            print("Start again after this")
        if i > 5:
            qtm.start_stream(testing_queue)
            print('i:', i)
            print(testing_queue.get())
        if i == 9:
            print("stop again")
            qtm.stop_stream()
