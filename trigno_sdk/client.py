"""
Implements a TCP Client to the Trigno SDK Server

To use the SDK 

Connect to the Trigno SDK Server via TCP/IP
• Configure the Trigno system hardware (see Section 5)
• Start data acquisition using one of two methods:
    o Send the command “START” over the Command port
    o Arm the system and send a start trigger to the Trigno Base Station (see the Trigno Wireless EMG System User Guide)
• Process the data streams that are being sent over the data ports (see Section 6

All data values are IEEE floats (4 bytes). For synchronization purposes, always process
bytes in segments determined by multiples of the following factor
    (No. of data channels on port) * (4 bytes/sample)

6.2 Packet Structure

Each command is terminated with <CR><LF>. The end of a command packet is terminated by
two consecutive <CR><LF> pairs, and the server will process app commands received
to this point when two <CR><LF> are received

"""

from timeit import default_timer
import pkg_resources
from typing import Dict, Tuple, List
from pathlib import Path
from queue import Queue
from dataclasses import asdict
import threading
import json
import struct
import socket
from io import StringIO
import time

from .datastructure import DSChannel, EMGSensor, EMGSensorMeta

__all__ = ("TrignoClient",)

# Load Avanti Modes file. Must use Unix line endings
def load_avanti_modes():
    raw = pkg_resources.resource_string(__name__, "avanti_modes.tsv").decode()
    buf = StringIO(raw.strip())
    keys = buf.readline().strip().split("\t")[1:]
    modes = {}
    for _line in buf.readlines():
        line = _line.strip().split("\t")
        modes[int(line[0])] = {k: v for k, v in zip(keys, line[1:])}
    return modes


AVANTI_MODES = load_avanti_modes()

COMMAND_PORT = 50040  # receives control commands, sends replies to commands
EMG_DATA_PORT = 50043  # sends EMG and primary non-EMG data
AUX_DATA_PORT = 50044  # sends auxiliary data

# IP_ADDR = "10.229.96.239"
IP_ADDR = "10.229.96.105"


def _print(*args, **kwargs):
    print("[TrignoClient]", *args, **kwargs)


def recv(sock: socket.socket, maxlen=1024) -> bytes:
    "For receiving from the COMMAND_PORT"
    return sock.recv(maxlen).strip()


def recv_sz(sock: socket.socket, sz: int) -> bytes:
    "For receiving from the EMG_DATA_PORT"
    buf = b""
    while len(buf) < sz:
        buf += sock.recv(sz - len(buf))
    return buf


class TrignoClient:
    """
    DelsysClient interfaces with the Delsys SDK server via its TCP sockets.
    Handles device management and data streaming
    """

    __slots__ = (
        "connected",
        "host_ip",
        "command_sock",
        "emg_data_sock",
        "aux_data_sock",
        "sensors",
        "sensor_idx",
        "n_sensors",
        "sensor_meta",
        "start_time",
        "_done_streaming",
        "_worker_thread",
        "backwards_compatibility",
        "upsampling",
        "frame_interval",
        "max_samples_emg",
        "emg_sample_rate",
        "max_samples_aux",
        "aux_sample_rate",
        "endianness",
        "base_firmware",
        "base_serial",
    )

    AVANTI_MODES = AVANTI_MODES
    
    def __init__(self, host_ip: str = IP_ADDR):
        self.connected = False
        self.host_ip = host_ip
        self._init_state()

        # Initialize all attributes that might be accessed later
        self.backwards_compatibility = None
        self.upsampling = None
        self.frame_interval = None
        self.max_samples_emg = None
        self.emg_sample_rate = None
        self.max_samples_aux = None
        self.aux_sample_rate = None
        self.endianness = None
        self.base_firmware = None
        self.base_serial = None

    def _init_state(self):
        self.command_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.emg_data_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.aux_data_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

        self.sensors: List[EMGSensor | None] = [None] * 17  # use 1 indexing
        self.sensor_idx: List[int] = []
        self.n_sensors = 0

        self.sensor_meta: Dict[str, EMGSensorMeta] = {}  # Mapping[serial, meta]

        self.start_time = 0.0
        self._done_streaming = threading.Event()
        self._worker_thread: threading.Thread | None = None

    def __call__(self, cmd: str):
        return self.send_cmd(cmd)

    def __repr__(self):
        return "<{_class} @{_id:x} {_attrs}>".format(
            _class=self.__class__.__name__,
            _id=id(self) & 0xFFFFFF,
            _attrs=" ".join(
                "{}={!r}".format(k, getattr(self, k, 'N/A')) for k in sorted(self.__slots__)
            ),
        )

    def __getitem__(self, idx: int):
        return self.sensors[idx]

    def __len__(self) -> int:
        return len(self.sensors)

    def disconnect(self):
        self.stop_stream()
        self.command_sock.close()
        self.emg_data_sock.close()
        if hasattr(self, 'aux_data_sock'):
            self.aux_data_sock.close()
        self._init_state()
        self.connected = False
        _print("Disconnected")

    def connect(self) -> str:
        if not self.connected:
            try:
                self.command_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.emg_data_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.aux_data_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

                self.command_sock.settimeout(1)
                self.command_sock.connect((self.host_ip, COMMAND_PORT))
                self.command_sock.settimeout(5)
                buf = recv(self.command_sock)
                _print(buf.decode())

                self.emg_data_sock.connect((self.host_ip, EMG_DATA_PORT))
                self.aux_data_sock.connect((self.host_ip, AUX_DATA_PORT))

                self.connected = True
            except TimeoutError as e:
                err_str = "Failed to connect to Base Station: " + str(e)
                _print(err_str)
                return err_str

        self.connected = True
        cmd = lambda _cmd: self.send_cmd(_cmd).decode()

        # Ensure backwards compatibility off
        response = cmd("BACKWARDS COMPATIBILITY OFF")
        if response != "OK":
            _print(f"Warning: BACKWARDS COMPATIBILITY OFF command returned '{response}'")

        # Retrieve the correct frame interval and calculate rates based on mode 67
        self.frame_interval = float(cmd("FRAME INTERVAL?"))

        self.emg_sample_rate = 1482  # Fixed sampling rate for EMG
        self.aux_sample_rate = 74  # Fixed sampling rate for AUX data (orientation)

        self.endianness = cmd("ENDIANNESS?")
        self.base_firmware = cmd("BASE FIRMWARE?")
        self.base_serial = cmd("BASE SERIAL?")

        self.query_devices()
        return ""

    def query_device(self, i: int):
        """
        Checks for devices connected to the base and updates `self.sensors`.
        Updates mode to 67 (1 EMG + 4 AUX channels).
        """
        assert self.connected

        cmd = lambda _cmd: self.send_cmd(_cmd).decode()

        if cmd(f"SENSOR {i} PAIRED?") == "NO" or cmd(f"SENSOR {i} ACTIVE?") == "NO":
            return

        _type = cmd(f"SENSOR {i} TYPE?")
        res = cmd(f"SENSOR {i} SETMODE 67")  # Change mode to 67
        _mode = int(cmd(f"SENSOR {i} MODE?"))

        _serial = cmd(f"SENSOR {i} SERIAL?")
        firmware = cmd(f"SENSOR {i} FIRMWARE?")
        emg_channels = int(cmd(f"SENSOR {i} EMGCHANNELCOUNT?"))
        aux_channels = int(cmd(f"SENSOR {i} AUXCHANNELCOUNT?"))
        start_idx = int(cmd(f"SENSOR {i} STARTINDEX?"))

        channel_count = emg_channels + aux_channels
        channels = [
            DSChannel(
                gain=float(cmd(f"SENSOR {i} CHANNEL {j} GAIN?")),
                samples=int(cmd(f"SENSOR {i} CHANNEL {j} SAMPLES?")),
                rate=float(cmd(f"SENSOR {i} CHANNEL {j} RATE?")),
                units=cmd(f"SENSOR {i} CHANNEL {j} UNITS?"),
            )
            for j in range(1, channel_count + 1)
        ]

        return EMGSensor(
            serial=_serial,
            type=_type,
            mode=_mode,
            firmware=firmware,
            emg_channels=emg_channels,
            aux_channels=aux_channels,
            start_idx=start_idx,
            channel_count=channel_count,
            channels=channels,
        )

    def query_devices(self):
        """Query the Base Station for all 16 devices"""
        assert self.connected

        for i in range(1, 17):
            self.sensors[i] = self.query_device(i)

        self.sensor_idx = [i for i, s in enumerate(self.sensors) if s]
        self.n_sensors = sum([1 for s in self.sensors if s])

    def send_cmd(self, cmd: str) -> bytes:
        self.command_sock.send(cmd.encode() + b"\r\n\r\n")
        return recv(self.command_sock)

    def send_cmds(self, cmds: List[str]) -> List[bytes]:
        for cmd in cmds:
            self.command_sock.send(cmd.encode() + b"\r\n")
        self.command_sock.send(b"\r\n")
        return [recv(self.command_sock) for _ in cmds]

    def start_stream(self):
        assert self.connected
        self.send_cmd("START")
        self.start_time = default_timer()
        self._done_streaming.clear()

    def stop_stream(self):
        self._done_streaming.set()
        self._worker_thread and self._worker_thread.join()
        if self.connected:
            self.send_cmd("STOP")

    def recv_emg(self) -> Tuple[float, ...]:
        """
        Receive one EMG frame
        """
        buf = recv_sz(self.emg_data_sock, 4 * 16)  # 16 devices, 4 byte float
        return struct.unpack("<ffffffffffffffff", buf)

    def recv_aux(self) -> Tuple[float, ...]:
        """Receive one AUX frame from the sensor."""
        if not hasattr(self, 'aux_data_sock') or not self.aux_data_sock:
            raise ConnectionError("AUX data socket is not connected.")

        # Each sensor provides 4 channels of AUX data at 32-bit (4 bytes each)
        packet_size = 4 * (self.n_sensors * 4)  # 4 bytes per value, 4 values per sensor

        buf = recv_sz(self.aux_data_sock, packet_size)
        return struct.unpack(f"<{4 * self.n_sensors}f", buf)

    def handle_stream(self, queue: Queue[Tuple[float]], savedir: Path):
        """
        If `queue` is passed, append data into the queue.
        If `savedir` is passed, write to `savedir/sensor_EMG.csv`.
            Also persist metadata in `savedir` before and after stream
        """
        assert self.connected
        self.start_stream()
        self.save_meta(savedir / "trigno_meta.json")
        self._worker_thread = threading.Thread(
            target=self.stream_worker, args=(queue, savedir)
        )
        self._worker_thread.start()

    def stream_worker(self, queue: Queue[Tuple[float]], savedir: Path = None):
        """Stream worker handling EMG and AUX data separately."""

        with open(savedir / "trigno_emg.csv", "w") as emg_fp, open(savedir / "trigno_aux.csv", "w") as aux_fp:
            while not self._done_streaming.is_set():
                try:
                    emg_data = self.recv_emg()
                    aux_data = self.recv_aux()
                    timestamp = default_timer()
                except struct.error as e:
                    _print("Failed to parse packet", e)
                    continue

                queue.put((timestamp, emg_data))  # Only EMG data goes to queue

                # Save EMG and AUX data with correct floating point precision
                emg_fp.write(f"{timestamp}," + ",".join(f"{v:.6f}" for v in emg_data) + "\n")
                aux_fp.write(f"{timestamp}," + ",".join(f"{v:.6f}" for v in aux_data) + "\n")

                


    def close(self):
        self.stop_stream()
        if self.connected:
            self.send_cmd("QUIT")
            self.connected = False
        self.command_sock.close()
        self.emg_data_sock.close()
        self.sensor_idx = []
        self.sensors = []

    def save_meta(self, fpath: Path | str, slim=False):
        """Save metadata as JSON to fpath"""
        tmp = {k: asdict(v) for k, v in self.sensor_meta.items()}

        if not slim:
            tmp["idx2sensor"] = {
                str(idx): asdict(self.sensors[idx]) for idx in self.sensor_idx
            }
            tmp["start_time"] = self.start_time  # type: ignore

        with open(fpath, "w") as fp:
            json.dump(tmp, fp, indent=2)

    def load_meta(self, fpath: Path | str):
        """Load JSON metadata from fpath"""
        with open(fpath, "r") as fp:
            tmp: Dict = json.load(fp)

        if "idx2sensor" in tmp:
            del tmp["idx2sensor"]

        if "start_time" in tmp:
            del tmp["start_time"]

        for k, v in tmp.items():
            self.sensor_meta[k] = EMGSensorMeta(**v)

    def __del__(self):
        self.close()


def load_full_emg_meta(fpath: Path):
    with open(fpath, "r") as fp:
        tmp: Dict = json.load(fp)


if __name__ == "__main__":
    dm = TrignoClient()
    print(dm)
    breakpoint()

    dm.start_stream()
    while True:
        buf = dm.recv_emg()
        if any(buf):
            print(buf)
