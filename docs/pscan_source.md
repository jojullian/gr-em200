# R&S EM200 PScan Source

**Panorama scan (PScan)**: el EM200 barre un rango de frecuencias (puede ser de varios GHz) con un paso fijo y entrega el nivel de cada canal.
El bloque convierte cada barrido en **un vector `float`** de nivel en dBµV, listo para un *QT GUI Vector Sink*.

**Requisito:** la opción **R&S CS-PS**.

## Parámetros

| Parámetro | Descripción | Por defecto |
|---|---|---|
| IP del EM200 / Puerto SCPI | Conexión | `192.168.29.200` / `5555` |
| Inicio (Hz) | Frecuencia inicial | `88e6` |
| Fin (Hz) | Frecuencia final | `108e6` |
| Paso / RBW (Hz) | Separación entre canales y RBW. Valores válidos: 100, 125, 200, 250, 500, 625, 1k, 1,25k, 2k, 2,5k, 3,125k, 5k, 6,25k, 8,333k, 10k, 12,5k, 20k, 25k, 50k, 100k, 200k, 500k, 1M y 2M | `25e3` |
| Refresco pantalla (s) | Cada cuánto se reenvía la imagen actual. **En caliente** | `0.2` |
| Barrido progresivo | Sí: cada trozo se pinta al llegar sobre el barrido anterior. No: solo se actualiza al completar el barrido | Sí |
| Corrección QT Vector Sink | Envía cada imagen dos veces (ver abajo) | Sí |
| Restaurar equipo al parar | | Sí |

**Longitud del vector** = `(Fin − Inicio) / Paso + 1`. Por ejemplo, 88–108 MHz con paso de 25 kHz da 801 puntos, y 30–1000 MHz con paso de 100 kHz da 9701.
Como la longitud del vector es fija, **Inicio, Fin y Paso no se pueden cambiar en caliente**: cámbialos y vuelve a ejecutar.

## Salidas

| Puerto | Contenido |
|---|---|
| `barrido` | Vector `float` de nivel [dBµV] por canal |
| `sweep` (mensajes) | Por cada barrido completo: `start`, `stop`, `step`, `trace_id`, `timestamp_ns`, `overload`, `peak_freq` y `peak_level` |

## Cómo conectarlo al visor

Usa un *QT GUI Vector Sink* con:
- **Vector Size** = número de puntos (en el ejemplo, la variable `npoints`)
- **X start** = `inicio / 1e6`
- **X step** = `paso / 1e6`
- **X units** = `MHz`
- **Y units** = `dBuV`

Para promediar, pon un *Single Pole IIR Filter* con `vlen = npoints` entre la fuente y una segunda entrada del visor. El promedio se calcula **por refresco** (cada 0,2 s), no por barrido.

> En GRC **no puedes llamar `start` ni `stop` a una variable**: son métodos reservados del flowgraph. El ejemplo usa `f_start` y `f_stop`.

## Velocidad de barrido

Medida con el equipo real:

| Rango / paso | Puntos | Paquetes por barrido | Barridos |
|---|---|---|---|
| 88–108 MHz / 25 kHz | 801 | 1 | ~10 por segundo |
| 30–1000 MHz / 100 kHz | 9701 | ~26 (uno cada 100 ms) | 1 cada ~2,6 s |

En rangos anchos el barrido tarda segundos. Gracias al **refresco mantenido** y al **barrido progresivo**, la pantalla nunca se queda vacía y ves avanzar el barrido como en un analizador de espectro.

## Fallo del QT GUI Vector Sink (GNU Radio 3.10.9)

El *QT GUI Vector Sink* de GNU Radio 3.10.9 **dibuja el vector siguiente al que está procesando**.
- **Síntoma:** si le llega un vector aislado, no lo muestra y la línea queda en 0. Con barridos lentos parecía que el barrido aparecía y se borraba.
- **Comprobación:** un vector aislado a 50 dBµV no se dibuja, pero dos copias seguidas sí.
- **Solución en el bloque:** con *Corrección QT Vector Sink* = Sí, cada imagen se envía **dos veces seguidas**.
- **Si la salida no va a ese visor** (archivo, procesado propio…), pon este parámetro en **No**.

## Ejemplo: `examples/em200ps.grc`

- Visor de vectores con dos curvas:
  - el barrido en vivo (azul);
  - el promedio exponencial (rojo), con el control *Promedio (alfa)*.
- El visor trae botones de *Max Hold* y *Min Hold*.
- Un *Message Debug*, deshabilitado. Habilítalo para ver el pico de cada barrido en la consola.
- Para cambiar el rango, edita las variables `f_start`, `f_stop` y `step`.

## Qué hace con el equipo

- **Al arrancar:**
  - `ABOR`
  - `MEAS:APPL RX`: PScan no se admite en la app DF ("Not during DF")
  - `FREQ:PSC:STAR` y `FREQ:PSC:STOP`
  - `PSC:STEP`
  - `PSC:COUN INF`
  - `FREQ:MODE PSC`
  - `TRAC:TCP:TAG ...,PSC` con los flags `IFPan1` y `OPT`
  - `INIT`
- **Al parar:** `ABOR`, luego `FREQ:MODE` anterior, luego el resto de parámetros, y **al final** `MEAS:APPL`. No se puede cambiar de app con el PSCAN activo ("Not during PSCAN").
