from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from timeit import default_timer
from typing import ClassVar, NamedTuple, TextIO, Tuple

import numpy as np


class Packet(NamedTuple):
    """Packet represents one streaming batch from one Yost sensor"""

    pitch: float
    yaw: float
    roll: float
    battery: int
    t: float  # time
    name: str  # device nickname


DATA_ROOT = Path.home() / "Documents" / "BoMI Data"
DATA_ROOT.mkdir(exist_ok=True)


def get_savedir(task_name: str, mkdir=True) -> Path:
    datestr = datetime.now().strftime("%Y-%m-%d %H-%M-%S-%f")
    savedir = DATA_ROOT / "_".join((datestr, task_name))
    if mkdir:
        savedir.mkdir()
    return savedir


@dataclass
class SubjectMetadata:
    subject_id: str = "unknown"
    joint: str = "unknown"
    max_rom: int = -1
    stim: bool = False

    def dict(self):
        return asdict(self)

    def to_disk(self, savedir: Path):
        "Write metadata to `savedir`"
        with (savedir / "meta.json").open("w") as fp:
            json.dump(asdict(self), fp, indent=2)


class YostBuffer:
    """Manage all data (packets) consumed from the queue

    YostBuffer holds data from 1 Yost body sensor
    """

    LABELS: ClassVar = ("Roll", "Pitch", "Yaw", "abs(roll) + abs(pitch)")
    NAME_TEMPLATE: ClassVar = "yost_sensor_{name}.csv"

    def __init__(self, bufsize: int, savedir: Path, name: str):
        self.bufsize = bufsize
        # 1D array of timestamps
        self.timestamp: np.ndarray = np.zeros(bufsize)
        # 2D array of `labels`
        self.data: np.ndarray = np.zeros((bufsize, len(self.LABELS)))

        fp = open(savedir / self.NAME_TEMPLATE.format(name=name), "w")

        # filepointer to write CSV data to
        self.sensor_fp: TextIO = fp
        # name of this device
        self.name: str = name

        self._angle_in_use: int = -1  # idx of the labelled angle in use
        self.last_angle: float = 0.0  # angle used for task purposes

        self.savedir: Path = savedir
        header = ",".join(("t", *self.LABELS)) + "\n"
        self.sensor_fp.write(header)

    def __len__(self):
        return len(self.data)

    def __del__(self):
        "Close open file pointers"
        self.sensor_fp.close()

    def set_angle_type(self, label: str):
        i = self.LABELS.index(label)
        self._angle_in_use = i

    def add_packet(self, packet: Packet):
        "Add `Packet` of sensor data"
        _packet = (
            packet.roll,
            packet.pitch,
            packet.yaw,
            abs(packet.roll) + abs(packet.pitch),
        )

        # Write to file pointer
        self.sensor_fp.write(",".join((str(v) for v in (packet.t, *_packet))) + "\n")

        ### Shift buffer when full, never changing buffer size
        self.data[:-1] = self.data[1:]
        self.data[-1] = _packet
        self.timestamp[:-1] = self.timestamp[1:]
        self.timestamp[-1] = packet.t

        self.last_angle = _packet[self._angle_in_use]


class DelsysBuffer:
    """Manage data for all Delsys EMG sensors"""

    def __init__(self, bufsize: int, savedir: Path):
        self.bufsize = bufsize

        # 1D array of timestamps
        self.timestamp: np.ndarray = np.zeros(bufsize)

        # 2D array of `labels`
        self.data: np.ndarray = np.zeros((bufsize, 16))

    def add_packet(self, packet: Tuple[float, ...]):
        # assert len(packet) == 16

        ### Shift buffer when full, never changing buffer size
        self.data[:-1] = self.data[1:]
        self.data[-1] = packet
        self.timestamp[:-1] = self.timestamp[1:]
        self.timestamp[-1] = default_timer()

    def add_packets(self, packets: np.ndarray):
        n = len(packets)

        ### Shift buffer when full, never changing buffer size
        self.data[:-n] = self.data[n:]
        self.data[-n:] = packets
        self.timestamp[:-n] = self.timestamp[n:]
        self.timestamp[-n:] = [default_timer()] * n


if __name__ == "__main__":
    from dis import dis

    dis(DelsysBuffer.add_packets)
