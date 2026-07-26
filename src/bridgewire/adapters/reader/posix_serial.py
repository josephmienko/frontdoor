from __future__ import annotations

import os
import subprocess
from importlib import import_module
from pathlib import Path
from typing import Any

from bridgewire.reader import ReaderDisconnectedError, ReaderSession, SerialDevice

UDEVADM_TIMEOUT_SECONDS = 0.5


class PosixSerialSession(ReaderSession):
    def __init__(self, path: Path, baud_rate: int) -> None:
        termios: Any = import_module("termios")
        tty: Any = import_module("tty")
        if baud_rate != 9600:
            raise ValueError("hardware reader requires 9600 baud")
        self._fd = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOCTTY", 0) | getattr(os, "O_NONBLOCK", 0),
        )
        try:
            tty.setraw(self._fd)
            attributes = termios.tcgetattr(self._fd)
            attributes[4] = termios.B9600
            attributes[5] = termios.B9600
            attributes[2] = (
                (attributes[2] & ~(termios.CSIZE | termios.PARENB | termios.CSTOPB))
                | termios.CS8
                | termios.CLOCAL
                | termios.CREAD
            )
            termios.tcsetattr(self._fd, termios.TCSANOW, attributes)
            termios.tcflush(self._fd, termios.TCIFLUSH)
        except Exception:
            os.close(self._fd)
            raise

    def read(self) -> bytes:
        select: Any = import_module("select")
        if self._fd < 0:
            raise ReaderDisconnectedError("reader is closed")
        try:
            if not select.select([self._fd], [], [], 0.05)[0]:
                return b""
            return os.read(self._fd, 64)
        except OSError as exc:
            raise ReaderDisconnectedError("reader disconnected") from exc

    def close(self) -> None:
        if self._fd >= 0:
            os.close(self._fd)
            self._fd = -1


def enumerate_serial_devices() -> list[SerialDevice]:
    root = Path("/dev/serial/by-id")
    if not root.is_dir():
        return []
    devices: list[SerialDevice] = []
    for path in sorted(root.iterdir()):
        if not path.is_symlink():
            continue
        device = _serial_device_from_by_id(path)
        if device is not None:
            devices.append(device)
    return devices


def _serial_device_from_by_id(path: Path) -> SerialDevice | None:
    properties: dict[str, str] = {}
    try:
        output = subprocess.check_output(
            ["udevadm", "info", "--query=property", f"--name={path}"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=UDEVADM_TIMEOUT_SECONDS,
        )
        properties = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    return SerialDevice(
        path=path.resolve(),
        by_id_path=path,
        vid=(int(properties["ID_VENDOR_ID"], 16) if "ID_VENDOR_ID" in properties else None),
        pid=(int(properties["ID_MODEL_ID"], 16) if "ID_MODEL_ID" in properties else None),
        serial_number=properties.get("ID_SERIAL_SHORT"),
        manufacturer=properties.get("ID_VENDOR"),
        product=properties.get("ID_MODEL"),
    )
