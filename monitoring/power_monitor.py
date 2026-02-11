import time
import threading
from typing import Callable, Optional, Dict, Any


class PowerMonitor:
    """
    Generic power consumption monitor.

    - power_reader: zero-argument callable returning Watts (float) or None.
    - sample_interval: seconds between reads in background mode.
    """

    def __init__(self, power_reader: Callable[[], Optional[float]], sample_interval: float = 1.0):
        self.power_reader = power_reader
        self.sample_interval = sample_interval
        self.sample_callback: Optional[Callable[[float, float], None]] = None

        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # Internal state
        self._start_time: Optional[float] = None
        self._last_time: Optional[float] = None
        self._last_watts: Optional[float] = None

        self._min_w: Optional[float] = None
        self._max_w: Optional[float] = None
        self._energy_Wh: float = 0.0  # accumulated energy in Wh

    # -------------------- MANUAL / INTERNAL MODE --------------------

    def _step(self, now: float) -> None:
        """Single update step: read power and update aggregates."""
        try:
            watts = self.power_reader()
        except Exception:
            # Keep monitoring alive on transient reader failures.
            return
        if watts is None:
            # No reading available, skip integration for this step.
            return

        with self._lock:
            if self._start_time is None:
                # First reading
                self._start_time = now
                self._last_time = now
                self._last_watts = watts
                self._min_w = watts
                self._max_w = watts
            else:
                # Update min/max
                if self._min_w is None or watts < self._min_w:
                    self._min_w = watts
                if self._max_w is None or watts > self._max_w:
                    self._max_w = watts
                # Integrate energy using trapezoidal rule.
                if self._last_time is not None and self._last_watts is not None:
                    dt_seconds = now - self._last_time
                    if dt_seconds > 0:
                        dt_hours = dt_seconds / 3600.0
                        avg_watts = (self._last_watts + watts) / 2.0
                        self._energy_Wh += avg_watts * dt_hours

                # Update last state
                self._last_time = now
                self._last_watts = watts

        # Optional hook for external history/telemetry collection.
        if self.sample_callback is not None:
            try:
                self.sample_callback(now, watts)
            except Exception:
                pass

    def update(self) -> None:
        """
        Manually update monitor state.
        Useful when the caller already owns the main loop.
        """
        now = time.time()
        self._step(now)

    # -------------------- BACKGROUND MODE --------------------

    def _run_loop(self) -> None:
        """Internal loop for background mode."""
        while self._running:
            start = time.time()
            self._step(start)
            # Sleep until next sample time.
            elapsed = time.time() - start
            to_sleep = self.sample_interval - elapsed
            if to_sleep > 0:
                time.sleep(to_sleep)

    def start_background(self) -> None:
        """
        Start a background thread that samples every sample_interval seconds.
        """
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop_background(self) -> None:
        """Stop the background thread (if active)."""
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    # -------------------- STATS --------------------

    def get_stats(self) -> Dict[str, Any]:
        """
        Returns:
        - current_w
        - min_w
        - max_w
        - elapsed_s
        - elapsed_h
        - energy_Wh
        - energy_kWh
        """
        with self._lock:
            now = time.time()
            if self._start_time is None:
                elapsed_s = 0.0
            else:
                elapsed_s = now - self._start_time

            current_w = self._last_watts
            min_w = self._min_w
            max_w = self._max_w
            energy_Wh = self._energy_Wh
            energy_kWh = energy_Wh / 1000.0

        return {
            "current_w": current_w,
            "min_w": min_w,
            "max_w": max_w,
            "elapsed_s": elapsed_s,
            "elapsed_h": elapsed_s / 3600.0,
            "energy_Wh": energy_Wh,
            "energy_kWh": energy_kWh,
        }

    def reset(self) -> None:
        """Reset all accumulated metrics."""
        with self._lock:
            self._start_time = None
            self._last_time = None
            self._last_watts = None
            self._min_w = None
            self._max_w = None
            self._energy_Wh = 0.0
