"""Bloque fuente de GNU Radio 3.10: IQ del R&S EM200 por Ethernet (EB200 sobre TCP).

Al arrancar el flowgraph:
  1. Abre el control SCPI (puerto 5555) y guarda la configuración actual del equipo.
  2. Pone el equipo en app Receiver, FFM, demodulación IQ, la frecuencia y el ancho de banda pedidos
     y la IF remota en SHORT o LONG.
  3. Abre el socket de datos TCP (puerto SCPI + 10 = 5565) y registra en él la traza IF.
Al parar: borra la ruta TCP y, si restore=True, deja el equipo como estaba.
"""
import collections
import socket
import threading
import time

import numpy as np
import pmt
from gnuradio import gr

from . import eb200
from .scpi import Scpi, open_trace_socket

# Tabla 7-48 del manual (tipo de ancho de banda NORMal): ancho de banda [Hz] -> tasa IQ [S/s].
BANDWIDTH_TO_RATE = {
    10: 195.3125, 30: 195.3125, 50: 195.3125,
    100: 1562.5, 150: 1562.5, 200: 1562.5, 300: 1562.5,
    600: 3125, 1000: 3125,
    1500: 6250, 2100: 6250, 2400: 6250, 2700: 6250,
    3100: 12500, 4000: 12500, 4800: 12500,
    6000: 25000, 8333: 25000, 9000: 25000, 12000: 25000, 15000: 25000,
    25000: 50000, 30000: 50000,
    50000: 100000, 75000: 100000,
    120000: 200000, 150000: 200000,
    250000: 400000, 300000: 400000,
    500000: 800000,
    800000: 1600000, 1000000: 1600000, 1250000: 1600000,
    1500000: 3200000, 2000000: 3200000,
    5000000: 6400000,
    8000000: 12800000, 10000000: 12800000,
    12500000: 25600000, 15000000: 25600000, 20000000: 25600000,
    40000000: 51200000,
}
BANDWIDTHS = sorted(BANDWIDTH_TO_RATE)
MAX_BW_LONG = 5000000  # en LONG (2x int32) el manual no da tasas por encima de 5 MHz


def nearest_bandwidth(bw):
    return min(BANDWIDTHS, key=lambda b: abs(b - float(bw)))


def sample_rate(bw):
    """Tasa de muestreo IQ [S/s] que entregará el EM200 para un ancho de banda dado."""
    return float(BANDWIDTH_TO_RATE[nearest_bandwidth(bw)])


class iq_source(gr.sync_block):
    """Fuente IQ complex64 desde un R&S EM200."""

    _STATE_QUERIES = ("MEAS:APPL", "FREQ:MODE", "DEM", "BAND", "FREQ", "FREQ:DEM", "SYST:IF:REM:MODE")

    def __init__(self, ip="192.168.29.200", port=5555, freq=100e6, bandwidth=1e6,
                 if_mode="SHORT", restore=True, buffer_seconds=2.0):
        gr.sync_block.__init__(self, name="R&S EM200 IQ Source", in_sig=None, out_sig=[np.complex64])
        self.ip = ip
        self.port = int(port)
        self.freq = float(freq)
        self.if_mode = str(if_mode).upper()
        self.restore = bool(restore)
        self.buffer_seconds = float(buffer_seconds)
        self.bandwidth = self._check_bandwidth(bandwidth)

        self._scpi = None
        self._data = None
        self._data_addr = None
        self._saved = {}
        self._thread = None
        self._running = threading.Event()
        self._cond = threading.Condition()
        self._queue = collections.deque()   # elementos: (samples, freq, rate)
        self._queued = 0
        self._head_offset = 0
        self._last_seq = None
        self._last_tag = (None, None)
        self._overflows = 0
        self._lost = 0

    # ------------------------------------------------------------------ utilidades
    def _check_bandwidth(self, bw):
        nb = nearest_bandwidth(bw)
        if self.if_mode == "LONG" and nb > MAX_BW_LONG:
            print(f"[EM200] {nb/1e3:g} kHz no está disponible en LONG; uso {MAX_BW_LONG/1e3:g} kHz")
            nb = MAX_BW_LONG
        if abs(nb - float(bw)) > 0.5:
            print(f"[EM200] ancho de banda {float(bw):g} Hz no es válido; uso el más cercano: {nb} Hz")
        return nb

    def _log_errors(self, context):
        for err in self._scpi.errors():
            print(f"[EM200] {context}: {err}")

    # ------------------------------------------------------------------ ciclo de vida
    def start(self):
        self._scpi = Scpi(self.ip, self.port)
        idn = self._scpi.query("*IDN?")
        print(f"[EM200] conectado: {idn}")
        if self.restore:
            self._saved = {q: self._scpi.query(q + "?") for q in self._STATE_QUERIES}

        # Socket de datos primero: la ruta TCP se identifica por la IP/puerto locales de este socket.
        self._data = open_trace_socket(self.ip, self.port)
        self._data_addr = self._data.getsockname()

        for cmd in ("MEAS:APPL RX", "FREQ:MODE FFM", "DEM IQ",
                    f"FREQ {self.freq:.0f}", f"FREQ:DEM {self.freq:.0f}",
                    f"BAND {self.bandwidth}", f"SYST:IF:REM:MODE {self.if_mode}"):
            self._scpi.write(cmd)
        lip, lport = self._data_addr
        self._scpi.write(f'TRAC:TCP:TAG "{lip}", {lport}, IF')
        self._scpi.write(f'TRAC:TCP:FLAG "{lip}", {lport}, "OPT"')
        self._log_errors("configuración")
        print(f"[EM200] {self.freq/1e6:.6f} MHz, BW {self.bandwidth/1e3:g} kHz, "
              f"{sample_rate(self.bandwidth)/1e3:g} kS/s, IQ {self.if_mode}")

        self._running.set()
        self._thread = threading.Thread(target=self._reader, name="em200-reader", daemon=True)
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
                    for q, v in self._saved.items():
                        self._scpi.write(f"{q} {v}")
                    self._log_errors("restauración")
                    print("[EM200] configuración del equipo restaurada")
            except OSError as e:
                print(f"[EM200] error al parar: {e}")
            self._scpi.close()
        if self._data:
            self._data.close()
        if self._overflows or self._lost:
            print(f"[EM200] desbordes del buffer: {self._overflows}, paquetes perdidos: {self._lost}")
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
                print("[EM200] el equipo cerró el socket de datos")
                break
            for dg in reasm.feed(chunk):
                try:
                    pkts = eb200.parse_datagram(dg)
                except (ValueError, IndexError) as e:
                    print(f"[EM200] datagrama inválido: {e}")
                    continue
                for p in pkts:
                    if isinstance(p, eb200.IFPacket):
                        self._enqueue(p)
        with self._cond:
            self._cond.notify_all()

    def _enqueue(self, p):
        if self._last_seq is not None and p.sequence != (self._last_seq + 1) & 0xFFFFFFFF:
            self._lost += 1
        self._last_seq = p.sequence
        limit = int(max(p.sample_rate, 1.0) * self.buffer_seconds)
        with self._cond:
            self._queue.append((p.samples, p.frequency, p.sample_rate))
            self._queued += len(p.samples)
            while self._queued - self._head_offset > limit and len(self._queue) > 1:
                old = self._queue.popleft()
                self._queued -= len(old[0])
                self._head_offset = 0
                self._overflows += 1
                print("O", end="", flush=True)  # como UHD: el flowgraph no consume a tiempo
            self._cond.notify()

    # ------------------------------------------------------------------ GNU Radio
    def work(self, input_items, output_items):
        out = output_items[0]
        n = 0
        with self._cond:
            if not self._queue:
                self._cond.wait(timeout=0.1)
            while n < len(out) and self._queue:
                samples, f, rate = self._queue[0]
                if (f, rate) != self._last_tag:
                    off = self.nitems_written(0) + n
                    self.add_item_tag(0, off, pmt.intern("rx_freq"), pmt.from_double(float(f)))
                    self.add_item_tag(0, off, pmt.intern("rx_rate"), pmt.from_double(float(rate)))
                    self._last_tag = (f, rate)
                avail = len(samples) - self._head_offset
                take = min(avail, len(out) - n)
                out[n:n + take] = samples[self._head_offset:self._head_offset + take]
                n += take
                self._head_offset += take
                if self._head_offset >= len(samples):
                    self._queue.popleft()
                    self._queued -= len(samples)
                    self._head_offset = 0
        return n

    # ------------------------------------------------------------------ callbacks de GRC
    def set_freq(self, freq):
        self.freq = float(freq)
        if self._scpi and self._running.is_set():
            self._scpi.write(f"FREQ {self.freq:.0f}")
            self._scpi.write(f"FREQ:DEM {self.freq:.0f}")

    def set_bandwidth(self, bandwidth):
        self.bandwidth = self._check_bandwidth(bandwidth)
        if self._scpi and self._running.is_set():
            self._scpi.write(f"BAND {self.bandwidth}")
            print(f"[EM200] BW {self.bandwidth/1e3:g} kHz -> {sample_rate(self.bandwidth)/1e3:g} kS/s")
