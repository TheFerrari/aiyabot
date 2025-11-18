import time
import threading
from typing import Callable, Optional, Dict, Any


class PowerMonitor:
    """
    Monitor genérico de consumo de energía.

    - power_reader: función sin argumentos que devuelve Watts (float) o None.
    - sample_interval: segundos entre lecturas cuando se usa el modo en segundo plano.
    """

    def __init__(self, power_reader: Callable[[], Optional[float]], sample_interval: float = 1.0):
        self.power_reader = power_reader
        self.sample_interval = sample_interval

        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # Estado interno
        self._start_time: Optional[float] = None
        self._last_time: Optional[float] = None
        self._last_watts: Optional[float] = None

        self._min_w: Optional[float] = None
        self._max_w: Optional[float] = None
        self._energy_Wh: float = 0.0  # energía acumulada en Wh

    # -------------------- MODO MANUAL / INTERNO --------------------

    def _step(self, now: float) -> None:
        """Un paso de actualización: lee potencia y actualiza integrales."""
        watts = self.power_reader()
        if watts is None:
            # Si no hay lectura, simplemente no integramos nada
            return

        with self._lock:
            if self._start_time is None:
                # Primera lectura
                self._start_time = now
                self._last_time = now
                self._last_watts = watts
                self._min_w = watts
                self._max_w = watts
                return

            # Actualizar min/max
            if self._min_w is None or watts < self._min_w:
                self._min_w = watts
            if self._max_w is None or watts > self._max_w:
                self._max_w = watts

            # Integrar energía (método del trapecio entre última lectura y la actual)
            if self._last_time is not None and self._last_watts is not None:
                dt_seconds = now - self._last_time
                if dt_seconds > 0:
                    dt_hours = dt_seconds / 3600.0
                    avg_watts = (self._last_watts + watts) / 2.0
                    self._energy_Wh += avg_watts * dt_hours

            # Actualizar último estado
            self._last_time = now
            self._last_watts = watts

    def update(self) -> None:
        """
        Llamar manualmente a este método para actualizar el estado del monitor.
        Útil si ya tienes tu propio loop en el bot.
        """
        now = time.time()
        self._step(now)

    # -------------------- MODO EN SEGUNDO PLANO --------------------

    def _run_loop(self) -> None:
        """Loop interno para el modo background."""
        while self._running:
            start = time.time()
            self._step(start)
            # Dormir el tiempo restante hasta el siguiente sample
            elapsed = time.time() - start
            to_sleep = self.sample_interval - elapsed
            if to_sleep > 0:
                time.sleep(to_sleep)

    def start_background(self) -> None:
        """
        Arranca un hilo en segundo plano que va leyendo el consumo
        cada sample_interval segundos.
        """
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop_background(self) -> None:
        """Detiene el hilo en segundo plano (si está activo)."""
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    # -------------------- CONSULTA DE ESTADÍSTICAS --------------------

    def get_stats(self) -> Dict[str, Any]:
        """
        Devuelve un dict con:
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
        """Resetea todas las métricas acumuladas."""
        with self._lock:
            self._start_time = None
            self._last_time = None
            self._last_watts = None
            self._min_w = None
            self._max_w = None
            self._energy_Wh = 0.0
