import ctypes
import csv
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional


class _SYSTEM_POWER_STATUS(ctypes.Structure):
    _fields_ = [
        ("ACLineStatus", ctypes.c_ubyte),
        ("BatteryFlag", ctypes.c_ubyte),
        ("BatteryLifePercent", ctypes.c_ubyte),
        ("SystemStatusFlag", ctypes.c_ubyte),
        ("BatteryLifeTime", ctypes.c_ulong),
        ("BatteryFullLifeTime", ctypes.c_ulong),
    ]


def _read_battery_percent() -> Optional[int]:
    """Return battery percentage [0..100] on Windows, or None if unavailable."""
    status = _SYSTEM_POWER_STATUS()
    ok = ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status))
    if not ok:
        return None

    # 255 means unknown.
    if status.BatteryLifePercent == 255:
        return None

    return int(status.BatteryLifePercent)


@dataclass
class WindowsBatteryPowerReader:
    """
    Rough watts estimation based on battery percentage drop over time.

    Notes:
    - Laptop only (desktop usually returns no battery data).
    - Returns positive watts while discharging; None while charging/unknown.
    - Best effort estimate. Use external meter/HW telemetry for accurate data.
    """

    battery_capacity_wh: float = 50.0
    min_interval_s: float = 20.0
    _last_percent: Optional[int] = None
    _last_time: Optional[float] = None

    def __call__(self) -> Optional[float]:
        now = time.time()
        percent = _read_battery_percent()
        if percent is None:
            return None

        if self._last_percent is None or self._last_time is None:
            self._last_percent = percent
            self._last_time = now
            return None

        dt_s = now - self._last_time
        if dt_s < self.min_interval_s:
            return None

        delta_percent = self._last_percent - percent
        self._last_percent = percent
        self._last_time = now

        # If battery went up or stayed equal, device is charging/idle for this window.
        if delta_percent <= 0:
            return None

        dt_h = dt_s / 3600.0
        watts = (self.battery_capacity_wh * (delta_percent / 100.0)) / dt_h
        return max(0.0, float(watts))


def _split_keywords(value: str) -> List[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def _parse_float(value: str) -> Optional[float]:
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    # Keep digits/sign/decimal separators only.
    text = re.sub(r"[^0-9,.\-+]", "", text)
    if not text:
        return None

    # Support either 12.34 or 12,34
    if text.count(",") == 1 and text.count(".") == 0:
        text = text.replace(",", ".")
    elif text.count(",") > 1 and text.count(".") == 0:
        text = text.replace(",", "")
    elif text.count(",") >= 1 and text.count(".") >= 1:
        text = text.replace(",", "")

    try:
        return float(text)
    except ValueError:
        return None


def _detect_delimiter(sample: str) -> str:
    candidates = [",", ";", "\t"]
    best = ","
    best_count = -1
    for delimiter in candidates:
        count = sample.count(delimiter)
        if count > best_count:
            best = delimiter
            best_count = count
    return best


@dataclass
class HwinfoCsvReader:
    """
    Reads latest sensor values from a HWiNFO CSV logging file.

    The CSV must be continuously written by HWiNFO Sensor Logging.
    """

    csv_path: str
    power_keywords: List[str]
    temp_keywords: List[str]
    _last_ok: bool = False
    _encoding_candidates: List[str] = None

    def __post_init__(self) -> None:
        if self._encoding_candidates is None:
            # HWiNFO commonly writes ANSI (cp1252) on Windows.
            self._encoding_candidates = ["utf-8-sig", "cp1252", "latin-1"]

    def _decode_bytes(self, raw: bytes) -> Optional[str]:
        for encoding in self._encoding_candidates:
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
        return None

    def _read_last_row(self) -> Optional[Dict[str, str]]:
        path = Path(self.csv_path)
        if not path.exists() or not path.is_file():
            self._last_ok = False
            return None

        try:
            raw = path.read_bytes()
        except OSError:
            self._last_ok = False
            return None

        data = self._decode_bytes(raw)
        if data is None:
            self._last_ok = False
            return None

        if not data.strip():
            self._last_ok = False
            return None

        delimiter = _detect_delimiter(data[:4096])
        rows = list(csv.DictReader(data.splitlines(), delimiter=delimiter))
        if not rows:
            self._last_ok = False
            return None

        self._last_ok = True
        return rows[-1]

    @staticmethod
    def _pick_value_from_row(row: Dict[str, str], keywords: List[str]) -> Optional[float]:
        if not row:
            return None

        lowered_keys = {k.lower(): k for k in row.keys()}
        for keyword in keywords:
            needle = keyword.lower()

            # Prefer exact match.
            if needle in lowered_keys:
                value = _parse_float(row.get(lowered_keys[needle], ""))
                if value is not None:
                    return value

            # Fallback contains match.
            for key in row.keys():
                if needle in key.lower():
                    value = _parse_float(row.get(key, ""))
                    if value is not None:
                        return value
        return None

    def read_power_w(self) -> Optional[float]:
        row = self._read_last_row()
        return self._pick_value_from_row(row or {}, self.power_keywords)

    def read_temperature_c(self) -> Optional[float]:
        row = self._read_last_row()
        return self._pick_value_from_row(row or {}, self.temp_keywords)


_hwinfo_reader: Optional[HwinfoCsvReader] = None


def _build_hwinfo_reader_from_env() -> HwinfoCsvReader:
    csv_path = os.getenv("HWINFO_CSV_PATH", "").strip()
    if not csv_path:
        raise ValueError("HWINFO_CSV_PATH es requerido para POWER_READER=hwinfo_csv")

    power_keywords = _split_keywords(
        os.getenv(
            "HWINFO_POWER_COLUMN_KEYWORDS",
            "CPU Package Power,Package Power,APU STAPM,SoC Power,GPU ASIC Power,Total System Power",
        )
    )
    temp_keywords = _split_keywords(
        os.getenv(
            "HWINFO_TEMP_COLUMN_KEYWORDS",
            "CPU (Tctl/Tdie),CPU Die (average),CPU CCD1 (Tdie),GPU Temperature,GPU Hot Spot Temperature",
        )
    )
    return HwinfoCsvReader(
        csv_path=csv_path,
        power_keywords=power_keywords,
        temp_keywords=temp_keywords,
    )


def env_power_reader() -> Callable[[], Optional[float]]:
    """
    Build a reader using environment flags:

    - POWER_READER=mock
      - MOCK_POWER_WATTS=number (default 120)
    - POWER_READER=windows_battery
      - BATTERY_CAPACITY_WH=number (default 50)
      - BATTERY_MIN_INTERVAL_S=number (default 20)
    - POWER_READER=hwinfo_csv (default)
      - HWINFO_CSV_PATH=path al CSV de logging de HWiNFO
      - HWINFO_POWER_COLUMN_KEYWORDS=lista separada por comas
      - HWINFO_TEMP_COLUMN_KEYWORDS=lista separada por comas (para env_temperature_reader)
    """
    global _hwinfo_reader
    mode = os.getenv("POWER_READER", "hwinfo_csv").strip().lower()

    if mode == "mock":
        mock_watts = float(os.getenv("MOCK_POWER_WATTS", "120"))

        def _mock_reader() -> float:
            return mock_watts

        return _mock_reader
    if mode == "hwinfo_csv":
        _hwinfo_reader = _build_hwinfo_reader_from_env()
        return _hwinfo_reader.read_power_w

    capacity_wh = float(os.getenv("BATTERY_CAPACITY_WH", "50"))
    min_interval_s = float(os.getenv("BATTERY_MIN_INTERVAL_S", "20"))
    return WindowsBatteryPowerReader(
        battery_capacity_wh=capacity_wh,
        min_interval_s=min_interval_s,
    )


def env_temperature_reader() -> Optional[Callable[[], Optional[float]]]:
    """
    Returns a temperature reader when current POWER_READER supports it.
    Currently only available for POWER_READER=hwinfo_csv.
    """
    global _hwinfo_reader
    mode = os.getenv("POWER_READER", "hwinfo_csv").strip().lower()
    if mode != "hwinfo_csv":
        return None
    if _hwinfo_reader is None:
        _hwinfo_reader = _build_hwinfo_reader_from_env()
    return _hwinfo_reader.read_temperature_c
