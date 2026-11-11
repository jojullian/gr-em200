# R&S EM200 IQ Source

Fuente de **IQ complejo en vivo** desde el EM200. Usa la traza IF del protocolo EB200 (tag 901) sobre TCP.
La salida es `complex64` normalizado a ±1, lista para conectar a cualquier bloque de GNU Radio: FFT, demoduladores, grabación a archivo…

## Parámetros

| Parámetro | Descripción | Por defecto |
|---|---|---|
| IP del EM200 | Dirección del receptor | `192.168.29.200` |
| Puerto SCPI | Control SCPI. Los datos van al puerto + 10 | `5555` |
| Frecuencia (Hz) | Frecuencia central (8 kHz – 8 GHz). **Se puede cambiar en caliente** | `100e6` |
| Ancho de banda (Hz) | Ancho de banda de demodulación del EM200. Fija la tasa IQ. **Se puede cambiar en caliente** | `1e6` |
| Formato IQ | `SHORT` (2×16 bit) o `LONG` (2×32 bit, solo hasta 5 MHz) | `SHORT` |
| Restaurar equipo al parar | Deja el EM200 como estaba al detener el flowgraph | Sí |
| Buffer (s) | Segundos de IQ que se guardan si GNU Radio no consume a tiempo | `2.0` |

Si el ancho de banda pedido no es uno de los del equipo, se usa el más cercano y se avisa en la consola.

## Tasa de muestreo

La tasa la decide el EM200 según el ancho de banda (manual, tabla 7-48). Para usarla en el flowgraph:

```python
import em200
samp_rate = em200.sample_rate(bw)   # p. ej. em200.sample_rate(1e6) -> 1600000.0
```

| BW | Tasa IQ | Mb/s (SHORT) |
|---|---|---|
| 120 kHz | 200 kS/s | 6,4 |
| 300 kHz | 400 kS/s | 12,8 |
| 500 kHz | 800 kS/s | 25,6 |
| 1 – 1,25 MHz | 1,6 MS/s | 51,2 |
| 2 MHz | 3,2 MS/s | 102,4 |
| 5 MHz | 6,4 MS/s | 204,8 |
| 10 MHz | 12,8 MS/s | 409,6 |
| 20 MHz | 25,6 MS/s | 819,2 |
| 40 MHz | 51,2 MS/s | 1638 (solo por 10GE) |

`em200.BANDWIDTHS` contiene la lista completa de anchos de banda, desde 10 Hz.

## Ancho de banda y red

**La red es el límite práctico.** Con un enlace de **100 Mb/s** el máximo útil es **1,25 MHz** de ancho de banda. Por encima, el equipo descarta paquetes y el bloque lo informa al parar (`paquetes perdidos: N`).

Para 2–20 MHz necesitas **1 Gb/s** extremo a extremo: tarjeta, cable Cat5e o superior, y switch gigabit. Para 40 MHz hace falta la interfaz opcional de 10 GbE.

Para comprobar la velocidad negociada: `cat /sys/class/net/<interfaz>/speed`.

## Tags de salida

El bloque añade tags al flujo al inicio y cada vez que cambian la frecuencia o la tasa:

- `rx_freq`: frecuencia central [Hz]
- `rx_rate`: tasa de muestreo [S/s]

## Mensajes en consola

| Mensaje | Significado |
|---|---|
| `[EM200] conectado: ROHDE&SCHWARZ,EM200,...` | Conexión SCPI correcta |
| `O` | Desborde: el flowgraph no consume el IQ a tiempo y se descartan datos (como en UHD) |
| `paquetes perdidos: N` | Saltos de secuencia EB200. Suele indicar que la red no da abasto. Al cambiar el BW en caliente aparecen unos pocos y es normal |
| `configuración del equipo restaurada` | Al parar, el EM200 volvió a su estado anterior |

## Qué hace con el equipo

- **Al arrancar:**
  - `MEAS:APPL RX`
  - `FREQ:MODE FFM`
  - `DEM IQ`
  - `FREQ` y `FREQ:DEM`
  - `BAND`
  - `SYST:IF:REM:MODE SHORT|LONG`
  - `TRAC:TCP:TAG <ip>,<puerto>,IF` y `TRAC:TCP:FLAG ...,"OPT"`
- **Al parar:** `TRAC:TCP:DEL` y la restauración de los parámetros anteriores.

## Ejemplos

- **`examples/em200_espectro.grc`:** espectro y waterfall, con selector de frecuencia (20 MHz – 8 GHz) y de ancho de banda.
- **`examples/em200-fm.grc`:** receptor de FM comercial.
  - Usa un selector de ancho de banda de demodulación (120–500 kHz), que cambia el filtro IF real del EM200.
  - Un *Polyphase Arbitrary Resampler* lleva la tasa variable del equipo a 400 kS/s fijos, para poder cambiar el ancho de banda en caliente sin reiniciar el demodulador.

## Notas

- **Cortes de audio:** el reloj del EM200 y el de la tarjeta de sonido son distintos. En flowgraphs con audio puede aparecer de vez en cuando un `aU`/`O` y un corte corto. Es normal en cualquier SDR con salida de audio.
- **LSB/USB:** en esas demodulaciones la IF tiene un desplazamiento de portadora. Por eso el bloque fuerza `DEM IQ`.
