import ctypes
import csv
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple


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
    # Detect delimiter from header line only.
    # This avoids false positives when values use decimal comma (e.g. 29,48).
    header = ""
    for line in sample.splitlines():
        if line.strip():
            header = line
            break
    if not header:
        return ","

    candidates = [",", ";", "\t"]
    best = ","
    best_count = -1
    for delimiter in candidates:
        count = header.count(delimiter)
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
    _encoding_candidates: Optional[List[str]] = None
    _last_diag: Optional[Dict[str, object]] = None

    def __post_init__(self) -> None:
        if self._encoding_candidates is None:
            # HWiNFO commonly writes ANSI (cp1252) on Windows.
            # Some setups may also export UTF-16.
            self._encoding_candidates = ["utf-8-sig", "utf-16", "utf-16-le", "cp1252", "latin-1"]
        if self._last_diag is None:
            self._last_diag = {}

    def _set_diag(self, **kwargs: object) -> None:
        self._last_diag.update(kwargs)

    def get_debug_info(self) -> Dict[str, object]:
        return dict(self._last_diag)

    @staticmethod
    def _score_decoded_text(text: str) -> float:
        if not text:
            return -1e9

        non_empty_lines = [line for line in text.splitlines() if line.strip()]
        if not non_empty_lines:
            return -1e9

        header = non_empty_lines[0]
        delimiter = _detect_delimiter(header)
        columns = [c.strip().strip('"') for c in header.split(delimiter)]
        field_count = len(columns)
        has_date = any(c.lower() == "date" for c in columns)
        has_time = any(c.lower() == "time" for c in columns)
        has_power = any("power" in c.lower() for c in columns)
        null_ratio = (text.count("\x00") / max(1, len(text)))

        score = 0.0
        score += field_count * 2.0
        if field_count == 1:
            score -= 200.0
        if has_date:
            score += 100.0
        if has_time:
            score += 100.0
        if has_power:
            score += 40.0
        score -= null_ratio * 1000.0
        return score

    def _decode_bytes(self, raw: bytes) -> Tuple[Optional[str], Optional[str]]:
        best_text: Optional[str] = None
        best_encoding: Optional[str] = None
        best_score = -1e18
        tried = []

        for encoding in self._encoding_candidates or []:
            try:
                text = raw.decode(encoding)
            except UnicodeDecodeError:
                tried.append((encoding, "decode_error"))
                continue

            score = self._score_decoded_text(text)
            tried.append((encoding, score))
            if score > best_score:
                best_score = score
                best_text = text
                best_encoding = encoding

        self._set_diag(encoding_candidates_tried=tried)
        return best_text, best_encoding

    def _read_last_row(self) -> Optional[Dict[str, str]]:
        path = Path(self.csv_path)
        self._set_diag(csv_path=str(path), file_exists=path.exists(), file_is_file=path.is_file())
        if not path.exists() or not path.is_file():
            self._last_ok = False
            self._set_diag(last_error="csv_missing_or_not_file")
            return None

        try:
            raw = path.read_bytes()
        except OSError:
            self._last_ok = False
            self._set_diag(last_error="csv_read_os_error")
            return None

        self._set_diag(file_size_bytes=len(raw))
        data, encoding = self._decode_bytes(raw)
        if data is None:
            self._last_ok = False
            self._set_diag(last_error="csv_decode_failed", encoding_candidates=self._encoding_candidates)
            return None
        self._set_diag(encoding=encoding)

        if not data.strip():
            self._last_ok = False
            self._set_diag(last_error="csv_empty")
            return None

        _ensure_csv_field_limit()
        delimiter = _detect_delimiter(data[:4096])
        parsed = csv.DictReader(data.splitlines(), delimiter=delimiter)
        rows = list(parsed)
        if not rows:
            self._last_ok = False
            self._set_diag(
                last_error="csv_no_data_rows",
                delimiter=delimiter,
                header_count=0 if parsed.fieldnames is None else len(parsed.fieldnames),
            )
            return None

        fieldnames = parsed.fieldnames or []
        selected_row: Optional[Dict[str, str]] = None
        selected_offset_from_end = None
        for idx, candidate in enumerate(reversed(rows), start=1):
            has_non_empty_sensor_value = False
            for key, value in candidate.items():
                if key is None:
                    continue
                if key.strip().lower() in {"date", "time"}:
                    continue
                if value is not None and str(value).strip():
                    has_non_empty_sensor_value = True
                    break
            if has_non_empty_sensor_value:
                selected_row = candidate
                selected_offset_from_end = idx
                break
        if selected_row is None:
            self._last_ok = False
            self._set_diag(
                last_error="csv_only_empty_rows",
                delimiter=delimiter,
                row_count=len(rows),
                header_count=len(fieldnames),
            )
            return None

        self._last_ok = True
        self._set_diag(
            last_error=None,
            delimiter=delimiter,
            row_count=len(rows),
            header_count=len(fieldnames),
            selected_row_offset_from_end=selected_offset_from_end,
            sample_headers=fieldnames[:20],
        )
        return selected_row

    @staticmethod
    def _pick_value_from_row_with_key(
        row: Dict[str, str],
        keywords: List[str],
    ) -> Tuple[Optional[float], Optional[str]]:
        if not row:
            return None, None

        valid_keys = [k for k in row.keys() if isinstance(k, str)]
        lowered_keys = {k.lower(): k for k in valid_keys}
        for keyword in keywords:
            needle = keyword.lower()

            # Prefer exact match.
            if needle in lowered_keys:
                value = _parse_float(row.get(lowered_keys[needle], ""))
                if value is not None:
                    return value, lowered_keys[needle]

            # Fallback contains match.
            for key in valid_keys:
                if needle in key.lower():
                    value = _parse_float(row.get(key, ""))
                    if value is not None:
                        return value, key
        return None, None

    def read_power_w(self) -> Optional[float]:
        try:
            row = self._read_last_row()
            value: Optional[float] = None
            matched_col: Optional[str] = None
            candidate_columns: List[Tuple[str, Optional[str], Optional[float]]] = []
            positive_choice: Optional[Tuple[str, float]] = None
            any_choice: Optional[Tuple[str, float]] = None
            power_related_columns = []
            if row:
                try:
                    valid_keys = [k for k in row.keys() if isinstance(k, str)]
                    lowered = {k.lower(): k for k in valid_keys}
                    power_related_columns = [
                        (k, row.get(k))
                        for k in valid_keys
                        if "power" in k.lower()
                    ][:20]
                    for keyword in self.power_keywords:
                        needle = keyword.lower()
                        exact = lowered.get(needle)
                        if exact:
                            raw_value = row.get(exact)
                            parsed_value = _parse_float(raw_value)
                            candidate_columns.append((exact, raw_value, parsed_value))
                            if parsed_value is not None:
                                if any_choice is None:
                                    any_choice = (exact, parsed_value)
                                if parsed_value > 0 and positive_choice is None:
                                    positive_choice = (exact, parsed_value)
                        else:
                            for key in valid_keys:
                                if needle in key.lower():
                                    raw_value = row.get(key)
                                    parsed_value = _parse_float(raw_value)
                                    candidate_columns.append((key, raw_value, parsed_value))
                                    if parsed_value is not None:
                                        if any_choice is None:
                                            any_choice = (key, parsed_value)
                                        if parsed_value > 0 and positive_choice is None:
                                            positive_choice = (key, parsed_value)
                                    break
                except Exception as exc:
                    self._set_diag(last_error="power_candidates_exception", power_candidates_exception=repr(exc))

            if positive_choice is not None:
                matched_col, value = positive_choice
            elif any_choice is not None:
                matched_col, value = any_choice

            self._set_diag(
                power_value=value,
                power_matched_column=matched_col,
                power_keywords=self.power_keywords,
                power_candidate_columns=candidate_columns[:10],
                power_related_columns=power_related_columns,
            )
            if value is None:
                self._set_diag(last_error=self._last_diag.get("last_error") or "no_power_match")
            return value
        except Exception as exc:
            self._set_diag(last_error="power_read_exception", power_exception=repr(exc))
            return None

    def read_temperature_c(self) -> Optional[float]:
        row = self._read_last_row()
        value, matched_col = self._pick_value_from_row_with_key(row or {}, self.temp_keywords)
        self._set_diag(
            temperature_value=value,
            temperature_matched_column=matched_col,
            temp_keywords=self.temp_keywords,
        )
        if value is None:
            self._set_diag(last_error=self._last_diag.get("last_error") or "no_temperature_match")
        return value

    def read_snapshot(
        self,
        total_power_keywords: List[str],
        cpu_power_keywords: List[str],
        gpu_power_keywords: List[str],
        stapm_power_keywords: List[str],
        cpu_temp_keywords: List[str],
        gpu_temp_keywords: List[str],
        soc_temp_keywords: List[str],
    ) -> Dict[str, Optional[float]]:
        row = self._read_last_row() or {}
        total_power_w, _ = self._pick_value_from_row_with_key(row, total_power_keywords)
        cpu_package_power_w, _ = self._pick_value_from_row_with_key(row, cpu_power_keywords)
        gpu_asic_power_w, _ = self._pick_value_from_row_with_key(row, gpu_power_keywords)
        apu_stapm_w, _ = self._pick_value_from_row_with_key(row, stapm_power_keywords)
        cpu_temp_c, _ = self._pick_value_from_row_with_key(row, cpu_temp_keywords)
        gpu_temp_c, _ = self._pick_value_from_row_with_key(row, gpu_temp_keywords)
        soc_temp_c, _ = self._pick_value_from_row_with_key(row, soc_temp_keywords)
        return {
            "total_power_w": total_power_w,
            "cpu_package_power_w": cpu_package_power_w,
            "gpu_asic_power_w": gpu_asic_power_w,
            "apu_stapm_w": apu_stapm_w,
            "cpu_temp_c": cpu_temp_c,
            "gpu_temp_c": gpu_temp_c,
            "soc_temp_c": soc_temp_c,
        }


_hwinfo_reader: Optional[HwinfoCsvReader] = None
_hwinfo_snapshot_fn: Optional[Callable[[], Dict[str, Optional[float]]]] = None
_hwinfo_debug_fn: Optional[Callable[[], Dict[str, object]]] = None
_csv_field_limit_configured = False


def _ensure_csv_field_limit() -> None:
    """
    Raise CSV parser field size limit for very wide HWiNFO rows.
    """
    global _csv_field_limit_configured
    if _csv_field_limit_configured:
        return

    limit = sys.maxsize
    while True:
        try:
            csv.field_size_limit(limit)
            _csv_field_limit_configured = True
            return
        except OverflowError:
            limit = limit // 10


def _build_hwinfo_snapshot_reader_from_env(
    reader: HwinfoCsvReader,
) -> Callable[[], Dict[str, Optional[float]]]:
    total_power_keywords = _split_keywords(
        os.getenv(
            "HWINFO_TOTAL_POWER_KEYWORDS",
            "Total System Power,Core+SoC+SR Power (SVI3 TFN),APU STAPM,CPU Package Power",
        )
    )
    cpu_power_keywords = _split_keywords(
        os.getenv(
            "HWINFO_CPU_POWER_KEYWORDS",
            "CPU Package Power,CPU Core Power (SVI3 TFN),Core+SoC+SR Power (SVI3 TFN)",
        )
    )
    gpu_power_keywords = _split_keywords(
        os.getenv(
            "HWINFO_GPU_POWER_KEYWORDS",
            "GPU ASIC Power",
        )
    )
    stapm_power_keywords = _split_keywords(
        os.getenv(
            "HWINFO_STAPM_POWER_KEYWORDS",
            "APU STAPM",
        )
    )
    cpu_temp_keywords = _split_keywords(
        os.getenv(
            "HWINFO_CPU_TEMP_KEYWORDS",
            "CPU (Tctl/Tdie),CPU Core,Core Temperatures (avg)",
        )
    )
    gpu_temp_keywords = _split_keywords(
        os.getenv(
            "HWINFO_GPU_TEMP_KEYWORDS",
            "GPU Temperature,APU GFX",
        )
    )
    soc_temp_keywords = _split_keywords(
        os.getenv(
            "HWINFO_SOC_TEMP_KEYWORDS",
            "CPU SOC",
        )
    )

    def _snapshot() -> Dict[str, Optional[float]]:
        return reader.read_snapshot(
            total_power_keywords=total_power_keywords,
            cpu_power_keywords=cpu_power_keywords,
            gpu_power_keywords=gpu_power_keywords,
            stapm_power_keywords=stapm_power_keywords,
            cpu_temp_keywords=cpu_temp_keywords,
            gpu_temp_keywords=gpu_temp_keywords,
            soc_temp_keywords=soc_temp_keywords,
        )

    return _snapshot


def _build_hwinfo_reader_from_env() -> HwinfoCsvReader:
    csv_path = os.getenv("HWINFO_CSV_PATH", "").strip()
    if not csv_path:
        raise ValueError("HWINFO_CSV_PATH is required for POWER_READER=hwinfo_csv")

    power_keywords = _split_keywords(
        os.getenv(
            "HWINFO_POWER_COLUMN_KEYWORDS",
            "Total System Power,Core+SoC+SR Power (SVI3 TFN),APU STAPM,CPU Package Power,GPU ASIC Power",
        )
    )
    temp_keywords = _split_keywords(
        os.getenv(
            "HWINFO_TEMP_COLUMN_KEYWORDS",
            "CPU (Tctl/Tdie),CPU Core,Core Temperatures (avg),GPU Temperature,APU GFX,CPU SOC",
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
      - HWINFO_CSV_PATH=path to HWiNFO logging CSV
      - HWINFO_POWER_COLUMN_KEYWORDS=comma-separated list
      - HWINFO_TEMP_COLUMN_KEYWORDS=comma-separated list (for env_temperature_reader)
    """
    global _hwinfo_reader, _hwinfo_snapshot_fn, _hwinfo_debug_fn
    mode = os.getenv("POWER_READER", "hwinfo_csv").strip().lower()

    if mode == "mock":
        mock_watts = float(os.getenv("MOCK_POWER_WATTS", "120"))

        def _mock_reader() -> float:
            return mock_watts

        return _mock_reader
    if mode == "hwinfo_csv":
        _hwinfo_reader = _build_hwinfo_reader_from_env()
        _hwinfo_snapshot_fn = _build_hwinfo_snapshot_reader_from_env(_hwinfo_reader)
        _hwinfo_debug_fn = _hwinfo_reader.get_debug_info
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
    global _hwinfo_reader, _hwinfo_snapshot_fn, _hwinfo_debug_fn
    mode = os.getenv("POWER_READER", "hwinfo_csv").strip().lower()
    if mode != "hwinfo_csv":
        return None
    if _hwinfo_reader is None:
        _hwinfo_reader = _build_hwinfo_reader_from_env()
        _hwinfo_debug_fn = _hwinfo_reader.get_debug_info
    if _hwinfo_snapshot_fn is None:
        _hwinfo_snapshot_fn = _build_hwinfo_snapshot_reader_from_env(_hwinfo_reader)
    return _hwinfo_reader.read_temperature_c


def env_hwinfo_snapshot_reader() -> Optional[Callable[[], Dict[str, Optional[float]]]]:
    """
    Returns a detailed HWiNFO snapshot reader when POWER_READER=hwinfo_csv.
    """
    global _hwinfo_reader, _hwinfo_snapshot_fn, _hwinfo_debug_fn
    mode = os.getenv("POWER_READER", "hwinfo_csv").strip().lower()
    if mode != "hwinfo_csv":
        return None
    if _hwinfo_reader is None:
        _hwinfo_reader = _build_hwinfo_reader_from_env()
        _hwinfo_debug_fn = _hwinfo_reader.get_debug_info
    if _hwinfo_snapshot_fn is None:
        _hwinfo_snapshot_fn = _build_hwinfo_snapshot_reader_from_env(_hwinfo_reader)
    return _hwinfo_snapshot_fn


def env_hwinfo_debug_reader() -> Optional[Callable[[], Dict[str, object]]]:
    """
    Returns a diagnostic reader for HWiNFO CSV mode.
    """
    global _hwinfo_reader, _hwinfo_debug_fn
    mode = os.getenv("POWER_READER", "hwinfo_csv").strip().lower()
    if mode != "hwinfo_csv":
        return None
    if _hwinfo_reader is None:
        _hwinfo_reader = _build_hwinfo_reader_from_env()
    if _hwinfo_debug_fn is None:
        _hwinfo_debug_fn = _hwinfo_reader.get_debug_info
    return _hwinfo_debug_fn
