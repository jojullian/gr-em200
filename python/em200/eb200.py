"""Parser del protocolo EB200 de Rohde & Schwarz (EM200, manual cap. 7.5).

Solo implementa lo necesario para la traza IF (tag 901): IQ en formato SHORT (2x int16) o LONG (2x int32).

Orden de bytes, verificado con un EM200 real (FW 06.81):
  * La cabecera EB200, el GenericAttribute y la parte fija del TraceAttribute van en big-endian.
  * Si el SelectorFlag SWAP (0x20000000) está activo, el OptionalHeader y las muestras van en
    little-endian. El EM200 lo activa por defecto.
"""
import struct
from dataclasses import dataclass

import numpy as np

MAGIC = 0x000EB200
MAGIC_BYTES = struct.pack(">I", MAGIC)
HEADER = struct.Struct(">IHHHHI")  # magic, ver_minor, ver_major, seq_low, seq_high, data_size

TAG_IF = 901
FLAG_SWAP = 0x20000000
FLAG_OPTIONAL_HEADER = 0x80000000

IFMODE_SHORT = 1
IFMODE_LONG = 2

# IFTraceHeader (74 bytes), Tabla 7-19 del manual.
_IF_HDR_FIELDS = "hhIIIHhHh8sQIh2xQhQII"
_IF_HDR_NAMES = (
    "if_mode", "frame_length", "sample_rate", "freq_low", "bandwidth", "demodulation",
    "rx_attenuation", "flags", "k_factor", "demod_string", "sample_count", "freq_high",
    "rx_gain", "start_timestamp", "signal_source", "base_samplerate", "decimation",
    "interpolation",
)
IF_HDR_LE = struct.Struct("<" + _IF_HDR_FIELDS)
IF_HDR_BE = struct.Struct(">" + _IF_HDR_FIELDS)
assert IF_HDR_LE.size == 74


@dataclass
class IFPacket:
    sequence: int
    samples: np.ndarray        # complex64, normalizado a [-1, 1)
    sample_rate: float         # tasa exacta (fraccional si el equipo la informa)
    frequency: int             # Hz
    bandwidth: int             # Hz
    if_mode: int
    sample_count: int          # índice de la primera muestra según el equipo
    start_timestamp_ns: int
    rx_attenuation: int
    signal_valid: bool


def _parse_if_attribute(payload, sequence):
    """payload = TraceAttribute convencional completo (sin Tag/Length)."""
    n_items, _channel, opt_len, flags = struct.unpack_from(">hBBI", payload, 0)
    off = 8
    little = bool(flags & FLAG_SWAP)
    hdr = None
    if opt_len >= IF_HDR_LE.size:
        st = IF_HDR_LE if little else IF_HDR_BE
        hdr = dict(zip(_IF_HDR_NAMES, st.unpack_from(payload, off)))
    off += opt_len  # siempre saltar con OptionalHeaderLength (manual 7.5.3)

    if hdr is None:
        return None
    if_mode = hdr["if_mode"]
    if if_mode == IFMODE_SHORT:
        dtype, scale = ("<i2" if little else ">i2"), 1.0 / 32768.0
    elif if_mode == IFMODE_LONG:
        dtype, scale = ("<i4" if little else ">i4"), 1.0 / 2147483648.0
    else:
        return None

    raw = np.frombuffer(payload, dtype=dtype, count=2 * n_items, offset=off).astype(np.float32)
    raw *= scale
    samples = raw.view(np.complex64)

    sample_rate = float(hdr["sample_rate"])
    if hdr["decimation"]:
        sample_rate = hdr["base_samplerate"] * hdr["interpolation"] / hdr["decimation"]

    return IFPacket(
        sequence=sequence,
        samples=samples,
        sample_rate=sample_rate,
        frequency=hdr["freq_low"] | (hdr["freq_high"] << 32),
        bandwidth=hdr["bandwidth"],
        if_mode=if_mode,
        sample_count=hdr["sample_count"],
        start_timestamp_ns=hdr["start_timestamp"],
        rx_attenuation=hdr["rx_attenuation"],
        signal_valid=bool(hdr["flags"] & 0x1),
    )


# ---------------------------------------------------------------------------------------------- DFPan
TAG_DFPAN = 1401
DF_INVALID = 32766  # el EM200 marca así los valores DF no válidos (visto en el equipo real)

# DFPanTraceHeader (Tabla 7-21). En FW 06.81 mide 132 bytes (incluye Attenuator y Overload).
_DF_HDR_FIELDS = ("IIIiiIIIiHhiiIIQ"     # 0..63: frecuencias, umbral, BW DF, paso, tiempo, opción, rumbo...
                  "hhhhfhhff"            # 64..87: GPSHeader (24 bytes)
                  "IIQ"                  # 88..103: paso fraccional, BW DF alta resolución [mHz]
                  "hhhhh6x"              # 104..119: Level, Azimuth, Quality, Elevation, Omniphase
                  "Q")                   # 120..127: MeasureTimestamp
_DF_HDR_NAMES = (
    "freq_low", "freq_high", "span", "threshold_mode", "threshold", "df_bandwidth", "step",
    "meas_time_ms", "df_option", "compass_heading", "compass_heading_type", "antenna_factor",
    "demod_channel", "demod_freq_low", "demod_freq_high", "output_timestamp",
    "gps_valid", "gps_sats", "lat_ref", "lat_deg", "lat_min", "lon_ref", "lon_deg", "lon_min", "hdop",
    "step_num", "step_den", "df_bandwidth_mhz",
    "level", "azimuth", "quality", "elevation", "omniphase",
    "meas_timestamp",
)
DF_HDR_LE = struct.Struct("<" + _DF_HDR_FIELDS)
DF_HDR_BE = struct.Struct(">" + _DF_HDR_FIELDS)
assert DF_HDR_LE.size == 128


def _tenths(v):
    return None if abs(v) >= DF_INVALID else v / 10.0


@dataclass
class DFPacket:
    sequence: int
    frequency: int           # frecuencia de recepción [Hz]
    df_possible: bool        # DFOption bit 0: hay antena DF para esta frecuencia
    df_mode: int             # 1 wideband, 2 canal único
    azimuth: object          # grados, o None si no es válido
    quality: object          # %, o None
    level: object            # dBµV, o None
    elevation: object        # grados, o None
    heading: object          # rumbo de la brújula [grados], o None
    heading_type: int        # -1 inválido, 0 desconocido, 1 sin corregir, 2 magnético, 3 verdadero, 4..6 GPS
    df_bandwidth: int        # Hz
    meas_time: float         # s
    gps_valid: bool
    lat: object
    lon: object
    gps_sats: int
    hdop: float
    timestamp_ns: int        # instante de la medida
    spectrum_level: object   # np.ndarray float32 [dBµV] por bin del span, o None
    span: int
    step: int

    @property
    def valid(self):
        return self.df_possible and self.azimuth is not None


def _parse_dfpan_attribute(payload, sequence):
    n_items, _channel, opt_len, flags = struct.unpack_from(">hBBI", payload, 0)
    off = 8
    if opt_len < DF_HDR_LE.size:
        return None
    little = bool(flags & FLAG_SWAP)
    h = dict(zip(_DF_HDR_NAMES, (DF_HDR_LE if little else DF_HDR_BE).unpack_from(payload, off)))
    off += opt_len

    spectrum = None
    if flags & 0x800 and n_items > 0:  # DF_LEVEL: el primer bloque de datos es el nivel por bin
        spectrum = np.frombuffer(payload, dtype=("<i2" if little else ">i2"), count=n_items, offset=off)
        spectrum = np.where(np.abs(spectrum) >= DF_INVALID, np.nan, spectrum / 10.0).astype(np.float32)

    gps_ok = h["gps_valid"] == 1
    lat = lon = None
    if gps_ok:
        lat = (h["lat_deg"] + h["lat_min"] / 60.0) * (-1 if h["lat_ref"] == ord("S") else 1)
        lon = (h["lon_deg"] + h["lon_min"] / 60.0) * (-1 if h["lon_ref"] == ord("W") else 1)
    htype = h["compass_heading_type"]

    return DFPacket(
        sequence=sequence,
        frequency=h["freq_low"] | (h["freq_high"] << 32),
        df_possible=bool(h["df_option"] & 0x1),
        df_mode=(h["df_option"] >> 8) & 0xFF,
        azimuth=_tenths(h["azimuth"]),
        quality=_tenths(h["quality"]),
        level=_tenths(h["level"]),
        elevation=_tenths(h["elevation"]),
        heading=None if htype < 0 else h["compass_heading"] / 10.0,
        heading_type=htype,
        df_bandwidth=h["df_bandwidth"],
        meas_time=h["meas_time_ms"] / 1e3,  # el manual dice µs, pero el equipo envía ms (500 = 0,5 s)
        gps_valid=gps_ok,
        lat=lat,
        lon=lon,
        gps_sats=h["gps_sats"],
        hdop=h["hdop"],
        timestamp_ns=h["meas_timestamp"],
        spectrum_level=spectrum,
        span=h["span"],
        step=h["step"],
    )


# ---------------------------------------------------------------------------------------------- PScan
TAG_PSCAN = 11201      # tag >= 5000: GenericAttribute y TraceAttribute en formato "avanzado"
PSCAN_END_MARKER = 2000  # último valor de cada barrido (200,0 dBµV), visto en el equipo real

# PScanTraceHeader (Tabla 7-12), primeros campos útiles (el header completo mide 100 bytes en FW 06.81).
_PSC_HDR_FIELDS = "IIIIIIQIIQhhhhhhhII"
_PSC_HDR_NAMES = ("start_low", "stop_low", "step", "start_high", "stop_high", "trace_id",
                  "output_timestamp", "step_num", "step_den", "first_freq",
                  "avg_type", "set_index", "avg2", "avg3", "avg4", "att_max", "overload",
                  "total", "offset")
PSC_HDR_LE = struct.Struct("<" + _PSC_HDR_FIELDS)
PSC_HDR_BE = struct.Struct(">" + _PSC_HDR_FIELDS)


@dataclass
class PScanPacket:
    sequence: int
    trace_id: int
    start: int            # Hz
    stop: int             # Hz
    step: float           # Hz (fraccional exacto)
    first_freq: int       # frecuencia del primer valor de ESTE paquete [Hz]
    total: int            # puntos de un barrido completo (sin el marcador final)
    offset: int           # índice del primer valor de este paquete dentro del barrido
    levels: np.ndarray    # float32 dBµV, sin el marcador de fin
    end_of_sweep: bool
    overload: bool
    timestamp_ns: int


def _parse_pscan_attribute(payload, sequence, advanced):
    if advanced:
        n_items, _ch, opt_len, flags, _fh = struct.unpack_from(">IIIII", payload, 0)
        off = 36
    else:
        n_items, _ch, opt_len, flags = struct.unpack_from(">hBBI", payload, 0)
        off = 8
    if opt_len < PSC_HDR_LE.size:
        return None
    little = bool(flags & FLAG_SWAP)
    h = dict(zip(_PSC_HDR_NAMES, (PSC_HDR_LE if little else PSC_HDR_BE).unpack_from(payload, off)))
    off += opt_len
    raw = np.frombuffer(payload, dtype=("<i2" if little else ">i2"), count=n_items, offset=off)
    end = n_items > 0 and raw[-1] == PSCAN_END_MARKER and h["offset"] + n_items - 1 >= h["total"]
    vals = raw[:-1] if end else raw
    step = h["step_num"] / h["step_den"] if h["step_den"] else float(h["step"])
    return PScanPacket(
        sequence=sequence,
        trace_id=h["trace_id"],
        start=h["start_low"] | (h["start_high"] << 32),
        stop=h["stop_low"] | (h["stop_high"] << 32),
        step=step,
        first_freq=h["first_freq"],
        total=h["total"],
        offset=h["offset"],
        levels=(vals.astype(np.float32) / 10.0),
        end_of_sweep=bool(end),
        overload=bool(h["overload"]),
        timestamp_ns=h["output_timestamp"],
    )


def parse_datagram(data):
    """Devuelve la lista de IFPacket/DFPacket/PScanPacket que contiene un datagrama EB200 completo."""
    magic, _vmin, _vmaj, seq_lo, seq_hi, size = HEADER.unpack_from(data, 0)
    if magic != MAGIC:
        raise ValueError("magic EB200 incorrecto")
    sequence = seq_lo | (seq_hi << 16)
    out = []
    off = HEADER.size
    end = min(size, len(data))
    while off + 4 <= end:
        tag, length = struct.unpack_from(">HH", data, off)
        if tag >= 5000:  # GenericAttribute avanzado: tag, reservado, length u32, 4x u32 reservados
            (length,) = struct.unpack_from(">I", data, off + 4)
            body = off + 24
        else:
            body = off + 4
        payload = memoryview(data)[body:body + length]
        pkt = None
        if tag == TAG_PSCAN:
            pkt = _parse_pscan_attribute(payload, sequence, advanced=True)
        elif tag < 5000:
            parser = {TAG_IF: _parse_if_attribute, TAG_DFPAN: _parse_dfpan_attribute}.get(tag)
            if parser is not None:
                pkt = parser(payload, sequence)
        if pkt is not None:
            out.append(pkt)
        off = body + length
    return out


class StreamReassembler:
    """Reconstruye datagramas EB200 a partir de un flujo TCP (que no respeta límites de paquete)."""

    def __init__(self):
        self._buf = bytearray()

    def feed(self, chunk):
        self._buf += chunk
        datagrams = []
        while True:
            if len(self._buf) < HEADER.size:
                break
            if self._buf[:4] != MAGIC_BYTES:
                idx = self._buf.find(MAGIC_BYTES, 1)
                if idx < 0:
                    del self._buf[:-3]
                    break
                del self._buf[:idx]
                continue
            size = struct.unpack_from(">I", self._buf, 12)[0]
            if size < HEADER.size:
                del self._buf[:4]
                continue
            if len(self._buf) < size:
                break
            datagrams.append(bytes(self._buf[:size]))
            del self._buf[:size]
        return datagrams
