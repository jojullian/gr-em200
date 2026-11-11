"""Bloque fuente de GNU Radio 3.10: radiogoniometría (DF) del R&S EM200 a partir de la traza DFPan.

Necesita la opción R&S CS-DF y una antena DF conectada (AUX1 + RF). Sin antena el equipo envía DFPan
igualmente, pero con DFOption bit0 = 0 y azimut/calidad = 32766 (no válidos).

Salidas (float, una muestra por paquete DFPan, unas 10 por segundo):
  0 azimut  [°]    (si hold_last=True mantiene el último válido; si no, NaN cuando no es válido)
  1 calidad [%]    (0 cuando no es válido)
  2 nivel   [dBµV] en la frecuencia de recepción
  3 válido  (1.0 / 0.0)
Puerto de mensajes 'df': diccionario PMT con todos los campos (rumbo, GPS, timestamps...).
"""
import collections
import socket
import threading
from struct import error as struct_error

import numpy as np
import pmt
from gnuradio import gr

from . import eb200
from .scpi import Scpi, open_trace_socket

DF_BANDWIDTHS = [50, 100, 150, 300, 600, 1000, 1500, 2100, 2400, 2700, 3100, 4000, 4800, 6000, 8333,
                 9000, 12000, 15000, 25000, 30000, 50000, 75000, 120000, 150000, 250000, 300000,
                 500000, 800000, 1000000, 1250000, 1500000, 2000000]
SPANS = [1e3, 2e3, 5e3, 10e3, 20e3, 50e3, 100e3, 200e3, 500e3, 1e6, 2e6, 5e6, 10e6, 20e6, 40e6]


def _nearest(values, v):
    return min(values, key=lambda x: abs(x - float(v)))


class df_source(gr.sync_block):
    """Azimut, calidad y nivel DF del R&S EM200 (traza DFPan, modo canal único)."""

    _STATE_QUERIES = ("MEAS:APPL", "FREQ:MODE", "FREQ", "FREQ:DEM", "FREQ:SPAN", "BAND:DF:RES",
                      "BAND:DF:RES:AUTO", "MEAS:DF:MODE", "MEAS:DF:TIME", "MEAS:DF:THR")

    def __init__(self, ip="192.168.29.200", port=5555, freq=100e6, df_bandwidth=15e3, span=1e6,
                 meas_time=0.5, df_mode="CONT", threshold=10, hold_last=True, restore=True):
        gr.sync_block.__init__(self, name="R&S EM200 DF Source", in_sig=None,
                               out_sig=[np.float32, np.float32, np.float32, np.float32])
        self.message_port_register_out(pmt.intern("df"))
        self.ip = ip
        self.port = int(port)
        self.freq = float(freq)
        self.df_bandwidth = _nearest(DF_BANDWIDTHS, df_bandwidth)
        self.span = _nearest(SPANS, span)
        self.meas_time = min(max(float(meas_time), 0.1), 10.0)
        self.df_mode = str(df_mode).upper()
        self.threshold = int(threshold)
        self.hold_last = bool(hold_last)
        self.restore = bool(restore)

        self._scpi = None
        self._data = None
        self._data_addr = None
        self._saved = {}
        self._thread = None
        self._running = threading.Event()
        self._cond = threading.Condition()
        self._queue = collections.deque(maxlen=1000)
        self._last_az = np.nan
        self._last_seq = None
        self._lost = 0
        self._n_valid = 0
        self._n_total = 0

    # ------------------------------------------------------------------ configuración del equipo
    def _apply_bw_span(self):
        """El BW DF admisible depende del span (si no: -221 'DF bandwidth too high for IFPAN span').
        Se prueba en un orden y, si el equipo protesta, en el otro."""
        s = self._scpi
        s.write("BAND:DF:RES:AUTO OFF")  # OFF = canal único (ON = wideband)
        orders = ((f"FREQ:SPAN {self.span:.0f}", f"BAND:DF:RES {self.df_bandwidth}"),
                  (f"BAND:DF:RES {self.df_bandwidth}", f"FREQ:SPAN {self.span:.0f}"))
        errs = []
        for order in orders:
            for cmd in order:
                s.write(cmd)
            errs = s.errors()
            if not errs:
                return
        for e in errs:
            print(f"[EM200 DF] BW DF {self.df_bandwidth} Hz / span {self.span:.0f} Hz: {e}")

    def _apply_all(self):
        s = self._scpi
        for cmd in ("MEAS:APPL DF", "FREQ:MODE FFM", f"FREQ {self.freq:.0f}", f"FREQ:DEM {self.freq:.0f}"):
            s.write(cmd)
        self._apply_bw_span()
        for cmd in (f"MEAS:DF:MODE {self.df_mode}", f"MEAS:DF:TIME {self.meas_time}",
                    f"MEAS:DF:THR {self.threshold}"):
            s.write(cmd)
        for e in s.errors():
            print(f"[EM200 DF] configuración: {e}")

    def _restore(self):
        s = self._scpi
        sv = dict(self._saved)
        s.write(f"FREQ:SPAN {sv.pop('FREQ:SPAN')}")
        auto = sv.pop("BAND:DF:RES:AUTO")
        res = sv.pop("BAND:DF:RES")
        if auto in ("1", "ON"):
            s.write("BAND:DF:RES:AUTO ON")
        else:
            s.write(f"BAND:DF:RES {res}")
        for q, v in sv.items():
            s.write(f"{q} {v}")
        for e in s.errors():
            print(f"[EM200 DF] restauración: {e}")
        print("[EM200 DF] configuración del equipo restaurada")

    # ------------------------------------------------------------------ ciclo de vida
    def start(self):
        self._scpi = Scpi(self.ip, self.port)
        print(f"[EM200 DF] conectado: {self._scpi.query('*IDN?')}")
        opts = self._scpi.query("*OPT?").split(",")
        if "DF" not in opts:
            print("[EM200 DF] AVISO: el equipo no tiene la opción CS-DF")
        if self.restore:
            self._saved = {q: self._scpi.query(q + "?") for q in self._STATE_QUERIES}

        self._data = open_trace_socket(self.ip, self.port, rcvbuf=4 * 1024 * 1024)
        self._data_addr = self._data.getsockname()
        self._apply_all()
        lip, lport = self._data_addr
        self._scpi.write(f'TRAC:TCP:TAG "{lip}", {lport}, DFPan')
        self._scpi.write(f'TRAC:TCP:FLAG "{lip}", {lport}, "DFLevel", "AZImuth", "DFQuality", "OPT"')
        for e in self._scpi.errors():
            print(f"[EM200 DF] traza: {e}")
        print(f"[EM200 DF] {self.freq/1e6:.6f} MHz, BW DF {self.df_bandwidth/1e3:g} kHz, "
              f"span {self.span/1e3:g} kHz, {self.meas_time:g} s, modo {self.df_mode}")

        self._running.set()
        self._thread = threading.Thread(target=self._reader, name="em200-df-reader", daemon=True)
        self._thread.start()
        return True

    def stop(self):
        self._running.clear()
        with self._cond:
            self._cond.notify_all()
        if self._thread:
            self._thread.join(timeout=2.0)
        if self._scpi:
            try:
                if self._data_addr:
                    lip, lport = self._data_addr
                    self._scpi.write(f'TRAC:TCP:DEL "{lip}", {lport}')
                if self.restore and self._saved:
                    self._restore()
            except OSError as e:
                print(f"[EM200 DF] error al parar: {e}")
            self._scpi.close()
        if self._data:
            self._data.close()
        print(f"[EM200 DF] medidas: {self._n_total}, válidas: {self._n_valid}, paquetes perdidos: {self._lost}")
        return True

    def _reader(self):
        reasm = eb200.StreamReassembler()
        while self._running.is_set():
            try:
                chunk = self._data.recv(1 << 18)
            except socket.timeout:
                continue
            except OSError:
                break
            if not chunk:
                print("[EM200 DF] el equipo cerró el socket de datos")
                break
            for dg in reasm.feed(chunk):
                try:
                    pkts = eb200.parse_datagram(dg)
                except (ValueError, IndexError, struct_error) as e:
                    print(f"[EM200 DF] datagrama inválido: {e}")
                    continue
                for p in pkts:
                    if isinstance(p, eb200.DFPacket):
                        self._handle(p)
        with self._cond:
            self._cond.notify_all()

    def _handle(self, p):
        if self._last_seq is not None and p.sequence != (self._last_seq + 1) & 0xFFFFFFFF:
            self._lost += 1
        self._last_seq = p.sequence
        self._n_total += 1
        if p.valid:
            self._n_valid += 1
        self.message_port_pub(pmt.intern("df"), pmt.to_pmt({
            "freq": float(p.frequency), "valid": p.valid, "df_possible": p.df_possible,
            "azimuth": p.azimuth, "quality": p.quality, "level": p.level, "elevation": p.elevation,
            "heading": p.heading, "heading_type": p.heading_type,
            "gps_valid": p.gps_valid, "lat": p.lat, "lon": p.lon, "gps_sats": p.gps_sats,
            "df_bandwidth": float(p.df_bandwidth), "meas_time": p.meas_time,
            "timestamp_ns": int(p.timestamp_ns),
        }))
        with self._cond:
            self._queue.append(p)
            self._cond.notify()

    # ------------------------------------------------------------------ GNU Radio
    def work(self, input_items, output_items):
        az, qual, lev, val = output_items
        n = 0
        with self._cond:
            if not self._queue:
                self._cond.wait(timeout=0.1)
            while n < len(az) and self._queue:
                p = self._queue.popleft()
                if p.valid:
                    self._last_az = p.azimuth
                    az[n] = p.azimuth
                else:
                    az[n] = self._last_az if self.hold_last else np.nan
                qual[n] = p.quality if (p.valid and p.quality is not None) else 0.0
                lev[n] = p.level if p.level is not None else np.nan
                val[n] = 1.0 if p.valid else 0.0
                n += 1
        if self.hold_last and n and np.isnan(az[:n]).any():
            az[:n] = np.nan_to_num(az[:n], nan=0.0)  # el compás de GRC no admite NaN
        return n

    # ------------------------------------------------------------------ callbacks de GRC
    def _live(self):
        return self._scpi is not None and self._running.is_set()

    def set_freq(self, freq):
        self.freq = float(freq)
        if self._live():
            self._scpi.write(f"FREQ {self.freq:.0f}")
            self._scpi.write(f"FREQ:DEM {self.freq:.0f}")

    def set_df_bandwidth(self, bw):
        self.df_bandwidth = _nearest(DF_BANDWIDTHS, bw)
        if self._live():
            self._apply_bw_span()

    def set_span(self, span):
        self.span = _nearest(SPANS, span)
        if self._live():
            self._apply_bw_span()

    def set_meas_time(self, t):
        self.meas_time = min(max(float(t), 0.1), 10.0)
        if self._live():
            self._scpi.write(f"MEAS:DF:TIME {self.meas_time}")

    def set_df_mode(self, mode):
        self.df_mode = str(mode).upper()
        if self._live():
            self._scpi.write(f"MEAS:DF:MODE {self.df_mode}")

    def set_threshold(self, thr):
        self.threshold = int(thr)
        if self._live():
            self._scpi.write(f"MEAS:DF:THR {self.threshold}")
