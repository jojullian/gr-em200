# R&S EM200 DF Source

**Radiogoniometría (DF)** con el EM200. Lee la traza **DFPan** (tag 1401) en **modo canal único** y entrega la marcación hacia el emisor que transmite en la frecuencia sintonizada.

**Requisitos:** la opción **R&S CS-DF** y una **antena DF** conectada (RF + AUX1), por ejemplo ADD107, ADD207, ADD307 o ADD507. Sin antena el equipo sigue enviando datos, pero todas las marcaciones salen como **no válidas**.

## Parámetros

| Parámetro | Descripción | Por defecto |
|---|---|---|
| IP del EM200 / Puerto SCPI | Conexión | `192.168.29.200` / `5555` |
| Frecuencia (Hz) | Frecuencia del emisor. **En caliente** | `100e6` |
| Ancho de banda DF (Hz) | De 50 Hz a 2 MHz, en valores discretos (se usa el más cercano). Ajústalo al ancho de la emisión. **En caliente** | `15e3` |
| Span panorama (Hz) | Panorama IF, de 1 kHz a 40 MHz. El BW DF máximo depende de él. **En caliente** | `1e6` |
| Tiempo de medida (s) | Promediado DF, de 0,1 a 10 s. El manual recomienda **0,5 s en movimiento**. **En caliente** | `0.5` |
| Modo DF | `"CONT"` (continuo), `"NORM"` (solo por encima del umbral) o `"GATE"` (emisiones cortas). **En caliente** | `"CONT"` |
| Umbral (dBµV) | Umbral para los modos NORM y GATE. **En caliente** | `10` |
| Mantener último azimut | Sí: cuando la medida no es válida repite el último azimut válido (necesario para el *QT GUI Compass*). No: saca NaN | Sí |
| Restaurar equipo al parar | | Sí |

## Salidas

Todas son `float`, con una muestra por medida (unas 10 por segundo):

| Puerto | Contenido |
|---|---|
| `azimut` | Marcación [°] |
| `calidad` | Calidad de la marcación [%]. Vale 0 cuando no es válida |
| `nivel` | Nivel en la frecuencia de recepción [dBµV] |
| `valido` | 1,0 si la marcación es válida y 0,0 si no |
| `df` (mensajes) | Diccionario PMT con la medida completa (ver abajo) |

### Mensaje `df`

Cada medida publica un diccionario como este:

```python
{
  'freq': 145000000.0, 'valid': True, 'df_possible': True,
  'azimuth': 237.4, 'quality': 82.0, 'level': 41.3, 'elevation': 12.0,
  'heading': 91.5, 'heading_type': 3,          # rumbo de la brújula y tipo (ver tabla)
  'gps_valid': True, 'lat': -33.4489, 'lon': -70.6693, 'gps_sats': 9,
  'df_bandwidth': 15000.0, 'meas_time': 0.5, 'timestamp_ns': 1794385571974667250,
}
```

Este es el puerto a usar para llevar las marcaciones a otro sistema, por ejemplo un mapa táctico (CoT/TAK), un registro CSV o una triangulación.

`heading_type` toma estos valores:
- `-1` inválido
- `0` desconocido
- `1` brújula sin corregir
- `2` magnético
- `3` norte verdadero
- `4` GPS no usable
- `5` rumbo GPS (COG)
- `6` COG demasiado lento

## Cuándo una marcación es válida

El bloque marca la medida como válida solo si:
1. el equipo indica que **hay antena DF para esa frecuencia** (`DFOption` bit 0), y
2. el azimut no es el marcador de dato inválido del equipo (`32766`).

Sin antena DF, el EM200 a veces envía un azimut con valor (por ejemplo 358°), pero lo marca como no posible. El bloque lo descarta.

## Ejemplo: `examples/em200-df.grc`

- Un **QT GUI Compass** con la aguja del azimut.
- Un **QT GUI Number Sink** con azimut, calidad, nivel y válido.
- Un **QT GUI Time Sink** con el histórico de unos 60 s de azimut y calidad.
- Controles de frecuencia, BW DF, tiempo de medida y modo DF.
- Un *Message Debug*, deshabilitado. Habilítalo para ver en la consola cada medida completa.

## Qué hace con el equipo

- **Al arrancar:**
  - `MEAS:APPL DF`
  - `FREQ:MODE FFM`
  - `FREQ` y `FREQ:DEM`
  - `BAND:DF:RES:AUTO OFF`, que da el modo canal único (`ON` sería wideband DF)
  - `FREQ:SPAN` y `BAND:DF:RES`
  - `MEAS:DF:MODE`, `MEAS:DF:TIME` y `MEAS:DF:THR`
  - `TRAC:TCP:TAG ...,DFPan` con los flags `DFLevel`, `AZImuth`, `DFQuality` y `OPT`
- **Al parar:** borra la ruta y restaura el estado anterior.

## Limitaciones conocidas / pendiente

- **Sin validar con antena real:** la escala del azimut (se asume 1/10 °, como el resto de campos DF) y si viene **corregido con la brújula** o es relativo a la antena. Se han validado los campos de nivel, tiempo de medida y BW DF.
- **Calidad de la marcación en vehículo:** depende de la configuración del EM200 que no tocan estos bloques: brújula, *heading offset*, declinación y *User Scenario* ("Vehicle"). Hazla desde el Virtual Front Panel.
