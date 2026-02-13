import sqlite3
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple


_SAMPLE_COLUMNS = (
    "ts",
    "total_w",
    "cpu_package_power_w",
    "gpu_asic_power_w",
    "apu_stapm_w",
    "cpu_temp_c",
    "gpu_temp_c",
    "soc_temp_c",
)


def _float_or_none(value: object) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class SQLitePowerHistoryStore:
    """
    Persistent storage for power samples.
    """

    def __init__(self, db_path: str, retention_days: Optional[float] = None) -> None:
        self.db_path = Path(db_path)
        self.retention_days = retention_days if retention_days and retention_days > 0 else None
        self._lock = threading.Lock()

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._create_schema()

    def _create_schema(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS power_samples (
                    ts REAL NOT NULL,
                    total_w REAL NOT NULL,
                    cpu_package_power_w REAL,
                    gpu_asic_power_w REAL,
                    apu_stapm_w REAL,
                    cpu_temp_c REAL,
                    gpu_temp_c REAL,
                    soc_temp_c REAL
                )
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_power_samples_ts
                ON power_samples (ts)
                """
            )
            self._conn.commit()

    @staticmethod
    def _row_to_sample(row: Tuple[float, ...]) -> Dict[str, float]:
        sample: Dict[str, float] = {
            "ts": float(row[0]),
            "total_w": float(row[1]),
        }
        optional_columns = (
            ("cpu_package_power_w", row[2]),
            ("gpu_asic_power_w", row[3]),
            ("apu_stapm_w", row[4]),
            ("cpu_temp_c", row[5]),
            ("gpu_temp_c", row[6]),
            ("soc_temp_c", row[7]),
        )
        for key, value in optional_columns:
            if value is not None:
                sample[key] = float(value)
        return sample

    def insert_sample(self, sample: Dict[str, float]) -> None:
        ts = float(sample["ts"])
        values = (
            ts,
            float(sample["total_w"]),
            _float_or_none(sample.get("cpu_package_power_w")),
            _float_or_none(sample.get("gpu_asic_power_w")),
            _float_or_none(sample.get("apu_stapm_w")),
            _float_or_none(sample.get("cpu_temp_c")),
            _float_or_none(sample.get("gpu_temp_c")),
            _float_or_none(sample.get("soc_temp_c")),
        )

        with self._lock:
            self._conn.execute(
                """
                INSERT INTO power_samples (
                    ts,
                    total_w,
                    cpu_package_power_w,
                    gpu_asic_power_w,
                    apu_stapm_w,
                    cpu_temp_c,
                    gpu_temp_c,
                    soc_temp_c
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )

            if self.retention_days is not None:
                cutoff_ts = ts - (self.retention_days * 86400.0)
                self._conn.execute("DELETE FROM power_samples WHERE ts < ?", (cutoff_ts,))

            self._conn.commit()

    def load_latest(self, limit: int) -> List[Dict[str, float]]:
        if limit <= 0:
            return []

        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT {", ".join(_SAMPLE_COLUMNS)}
                FROM power_samples
                ORDER BY ts DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()

        rows.reverse()
        return [self._row_to_sample(row) for row in rows]

    def load_since(self, cutoff_ts: float, limit: Optional[int] = None) -> List[Dict[str, float]]:
        query = f"""
            SELECT {", ".join(_SAMPLE_COLUMNS)}
            FROM power_samples
            WHERE ts >= ?
            ORDER BY ts ASC
        """
        params: List[object] = [float(cutoff_ts)]
        if limit is not None and limit > 0:
            query += "\nLIMIT ?"
            params.append(int(limit))

        with self._lock:
            rows = self._conn.execute(query, tuple(params)).fetchall()

        return [self._row_to_sample(row) for row in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
