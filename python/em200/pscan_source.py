"""Bloque fuente de GNU Radio 3.10: panorama scan (PScan) del R&S EM200 (opción CS-PS).

Salida: vectores float de longitud fija (puntos = (stop-start)/step + 1) con el nivel en dBµV por canal,
pensados para un "QT GUI Vector Sink".

La pantalla se MANTIENE: el bloque reenvía la imagen actual cada `refresh` segundos aunque el equipo
tarde varios segundos en completar un barrido ancho. Con `progressive=True` cada trozo que llega se pinta
encima del barrido anterior (como un analizador de espectro); con False solo se actualiza al completar.

Fallo del QT GUI Vector Sink de GNU Radio 3.10.9 (comprobado): dibuja el vector SIGUIENTE al que procesa,
así que un vector aislado no se ve (queda la línea en 0). Por eso cada imagen se envía dos veces seguidas
(`duplicate=True`). Si conectas la salida a otra cosa que no sea ese visor, pon duplicate=False.
"""
import time
import socket
import threading
from struct import error as struct_error

import numpy as np
import pmt
from gnuradio import gr

from . import eb200
from .scpi import Scpi, open_trace_socket

PSCAN_STEPS = [100, 125, 200, 250, 500, 625, 1000, 1250, 2000, 2500, 3125, 5000, 6250, 8333, 10000,
               12500, 20000, 25000, 50000, 100000, 200000, 500000, 1000000, 2000000]


def pscan_points(start, stop, step):
    """Puntos de un barrido completo, igual que los calcula el EM200 (801 para 88-108 MHz / 25 kHz)."""
    return int(round((float(stop) - float(start)) / float(step))) + 1


class pscan_source(gr.sync_block):
    """Barridos PScan del R&S EM200 como vectores de nivel [dBµV]."""

    _STATE_QUERIES = ("MEAS:APPL", "FREQ:MODE", "FREQ:PSC:STAR", "FREQ:PSC:STOP", "PSC:STEP")

    def __init__(self, ip="192.168.29.200", port=5555, start=88e6, stop=108e6, step=25e3, restore=True,
                 refresh=0.2, progressive=True, duplicate=True):
        self.ip = ip
        self.port = int(port)
        self.start_freq = float(start)
        self.stop_freq = float(stop)
        self.step = float(step)
        if int(self.step) not in PSCAN_STEPS and self.step != 8333:
            print(f"[EM200 PScan] AVISO: paso {self.step:g} Hz no es uno de los del equipo: {PSCAN_STEPS}")
        self.npoints = pscan_points(self.start_freq, self.stop_freq, self.step)
        gr.sync_block.__init__(self, name="R&S EM200 PScan Source", in_sig=None,
                               out_sig=[(np.float32, self.npoints)])
        self.message_port_register_out(pmt.intern("sweep"))
        self.restore = bool(restore)
        self.refresh = max(float(refresh), 0.02)
        self.progressive = bool(progressive)
        self.duplicate = bool(duplicate)

        self._display = np.full(self.npoints, np.nan, dtype=np.float32)  # lo que se muestra
        self._have_data = False
        self._next_emit = 0.0
        self._scpi = None
        self._data = None
        self._data_addr = None
        self._saved = {}
        self._thread = None
        self._running = threading.Event()
        self._cond = threading.Condition()
        self._cur = None
        self._cur_id = None
        self._sweeps = 0
        self._incomplete = 0
        self._warned_size = False

    # ------------------------------------------------------------------ ciclo de vida
    def start(self):
        self._scpi = Scpi(self.ip, self.port)
        print(f"[EM200 PScan] conectado: {self._scpi.query('*IDN?')}")
        if "PS" not in self._scpi.query("*OPT?").split(","):
            print("[EM200 PScan] AVISO: el equipo no tiene la opción CS-PS")
        if self.restore:
            self._saved = {q: self._scpi.query(q + "?") for q in self._STATE_QUERIES}

        self._data = open_trace_socket(self.ip, self.port, rcvbuf=8 * 1024 * 1024)
        self._data_addr = self._data.getsockname()
        s = self._scpi
        # PScan no se admite en la app DF ("Not during DF"): primero pasar a Receiver.
        s.write("ABOR")
        s.write("MEAS:APPL RX")
        s.query("*OPC?")
        for cmd in (f"FREQ:PSC:STAR {self.start_freq:.0f}", f"FREQ:PSC:STOP {self.stop_freq:.0f}",
                    f"PSC:STEP {self.step:g}", "PSC:COUN INF", "FREQ:MODE PSC"):
            s.write(cmd)
        lip, lport = self._data_addr
        s.write(f'TRAC:TCP:TAG "{lip}", {lport}, PSC')
        s.write(f'TRAC:TCP:FLAG "{lip}", {lport}, "IFPan1", "OPT"')  # "VOLT:AC" no produce datos en PScan
        s.write("INIT")
        for e in s.errors():
            print(f"[EM200 PScan] configuración: {e}")
        print(f"[EM200 PScan] {self.start_freq/1e6:g}-{self.stop_freq/1e6:g} MHz, paso {self.step/1e3:g} kHz, "
              f"{self.npoints} puntos")

        self._running.set()
        self._thread = threading.Thread(target=self._reader, name="em200-pscan-reader", daemon=True)
        self._thread.start()
        return True

    def stop(self):
        self._running.clear()
        with self._cond:
            self._cond.notify_all()
        if self._thread:
            self._thread.join(timeout=2.0)
        if self._scpi:
            s = self._scpi
            try:
                if self._data_addr:
                    lip, lport = self._data_addr
                    s.write(f'TRAC:TCP:DEL "{lip}", {lport}')
                s.write("ABOR")
                s.query("*OPC?")
                if self.restore and self._saved:
                    sv = dict(self._saved)
                    # Orden importante: salir de PSC antes de cambiar de app ("Not during PSCAN").
                    s.write(f"FREQ:MODE {sv.pop('FREQ:MODE')}")
                    app = sv.pop("MEAS:APPL")
                    for q, v in sv.items():
                        s.write(f"{q} {v}")
                    s.query("*OPC?")
                    s.write(f"MEAS:APPL {app}")
                    for e in s.errors():
                        print(f"[EM200 PScan] restauración: {e}")
                    print("[EM200 PScan] configuración del equipo restaurada")
            except OSError as e:
                print(f"[EM200 PScan] error al parar: {e}")
            s.close()
        if self._data:
            self._data.close()
        print(f"[EM200 PScan] barridos: {self._sweeps}, incompletos: {self._incomplete}")
        return True

    # ------------------------------------------------------------------ hilo lector
    def _reader(self):
        reasm = eb200.StreamReassembler()
        while self._running.is_set():
            try:
                chunk = self._data.recv(1 << 20)
            except socket.timeout:
                continue
            except OSError:
                break
            if not chunk:
                print("[EM200 PScan] el equipo cerró el socket de datos")
                break
            for dg in reasm.feed(chunk):
                try:
                    pkts = eb200.parse_datagram(dg)
                except (ValueError, IndexError, struct_error) as e:
                    print(f"[EM200 PScan] datagrama inválido: {e}")
                    continue
                for p in pkts:
                    if isinstance(p, eb200.PScanPacket):
                        self._handle(p)
        with self._cond:
            self._cond.notify_all()

    def _handle(self, p):
        if p.total != self.npoints and not self._warned_size:
            print(f"[EM200 PScan] AVISO: el equipo envía {p.total} puntos y el bloque espera {self.npoints}; "
                  "se recorta/rellena con NaN")
            self._warned_size = True
        if p.trace_id != self._cur_id:
            if self._cur is not None:
                self._incomplete += 1  # empezó otro barrido sin terminar el anterior
            self._cur = np.full(self.npoints, np.nan, dtype=np.float32)
            self._cur_id = p.trace_id
        a = max(0, p.offset)
        b = min(self.npoints, p.offset + len(p.levels))
        if b > a:
            self._cur[a:b] = p.levels[:b - a]
            if self.progressive:
                with self._cond:
                    self._display[a:b] = p.levels[:b - a]
                    self._have_data = True
        if p.end_of_sweep:
            sweep = self._cur
            self._cur, self._cur_id = None, None
            self._sweeps += 1
            self.message_port_pub(pmt.intern("sweep"), pmt.to_pmt({
                "start": float(p.start), "stop": float(p.stop), "step": float(p.step),
                "trace_id": int(p.trace_id), "timestamp_ns": int(p.timestamp_ns),
                "overload": p.overload,
                "peak_freq": float(p.start + np.nanargmax(sweep) * p.step) if np.isfinite(sweep).any() else None,
                "peak_level": float(np.nanmax(sweep)) if np.isfinite(sweep).any() else None,
            }))
            with self._cond:
                valid = np.isfinite(sweep)
                self._display[valid] = sweep[valid]  # lo que no llegó conserva el barrido anterior
                self._have_data = True
                self._cond.notify()

    # ------------------------------------------------------------------ GNU Radio
    def work(self, input_items, output_items):
        out = output_items[0]
        with self._cond:
            wait = self._next_emit - time.monotonic()
            if wait > 0 or not self._have_data:
                self._cond.wait(timeout=min(max(wait, 0.0), 0.1) if self._have_data else 0.1)
            if not self._have_data or time.monotonic() < self._next_emit:
                return 0
            frame = self._display.copy()
        self._next_emit = time.monotonic() + self.refresh
        missing = ~np.isfinite(frame)
        if missing.any():  # canales aún sin dato: se dibujan en el suelo de ruido (evita NaN en el visor/promedio)
            frame[missing] = np.nanmin(frame) if (~missing).any() else 0.0
        n = 2 if (self.duplicate and len(out) >= 2) else 1
        out[:n] = frame
        return n

    def set_refresh(self, refresh):
        self.refresh = max(float(refresh), 0.02)
