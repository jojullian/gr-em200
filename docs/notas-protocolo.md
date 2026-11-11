# Notas del protocolo EB200 en el EM200 real

Todo esto se comprobó con un EM200, firmware **V06.81**, capturando las trazas reales.
Donde el comportamiento difiere del *Operation Manual* (4108.3063.02, versión 15), se indica.

## Transporte

- **SCPI:** TCP **5555**.
- **Trazas por TCP:** puerto SCPI + 10 = **5565**.
  - El cliente se conecta primero.
  - Luego envía 1 byte para arrancar el keep-alive.
  - Después registra la ruta con su IP y puerto locales: `TRAC:TCP:TAG "<ip>", <puerto>, <traza>`.
- **Trazas por UDP:** el equipo hace un *ping* propietario al destino antes de enviar. Se desactiva con `SYST:COMM:LAN:PING OFF`.
- **TCP frente a UDP:** con TCP no hay pérdidas en la red. Si aparecen saltos de secuencia es porque **el equipo descartó** datos: la red o el cliente no daban abasto.

## Orden de bytes

- **Big-endian:** la cabecera EB200 (16 bytes), el GenericAttribute y la parte fija del TraceAttribute.
- **Little-endian:** el **OptionalHeader** y los **datos de traza**, porque el EM200 activa el SelectorFlag **SWAP** (`0x20000000`).
- El parser mira el flag SWAP en cada paquete.

## Formatos de atributo

- **Tag < 5000 (formato convencional):**
  - GenericAttribute: `u16 tag, u16 len`.
  - TraceAttribute: `i16 n, u8 canal, u8 optlen, u32 flags`.
- **Tag ≥ 5000 (formato avanzado):**
  - GenericAttribute: `u16 tag, u16 res, u32 len, 4×u32 res`.
  - TraceAttribute: `u32 n, u32 canal, u32 optlen, u32 flagsLow, u32 flagsHigh, 4×u32 res`.
- **En ambos:** para saltar a los datos se usa siempre `OptionalHeaderLength`. Nunca se deduce de los flags.

## IF (tag 901)

- **OptionalHeader:** 74 bytes.
- **Contenido de cada paquete:** con IF SHORT y 120 kHz, 6000 muestras IQ en 24102 bytes.
- **Tasa exacta:** `nBaseSamplerate × nInterpolation / nDecimation`.

## DFPan (tag 1401)

- **OptionalHeader:** **132 bytes**. El manual dice 102 en la tabla y 128 en el ejemplo. Incluye `MeasureTimestamp`, `Attenuator` y `Overload`.
- **Valor no válido:** **`32766`**.
- **`DFMeasureTime`:** llega en **milisegundos**, no en µs como dice el manual. Con 0,5 s llega 500.
- **Configuración:** `BAND:DF:RES` y `FREQ:SPAN` dependen entre sí. Si se configuran en el orden equivocado, el equipo da `-221,"Settings conflict;DF bandwidth too high for IFPAN span"`.

## PScan (tag 11201)

- **Formato:** **avanzado**, con un OptionalHeader de 100 bytes.
- **Flag de nivel:** **`"IFPan1"`**. Con `"VOLT:AC"` el equipo no envía nada.
- **Marcador de fin:** cada barrido termina con un valor extra **`2000`** (200,0 dBµV) que `nTotalNofTraceValues` no cuenta.
- **Barridos largos:** llegan troceados. Cada paquete trae `nOffsetOfFirstValue` y el mismo `traceID`.
- **Errores de orden en el equipo:**
  - `-221,"Settings conflict;Not during DF"`: PSCAN desde la app DF.
  - `-221,"Settings conflict;Not during PSCAN"`: cambio de app con el barrido activo.

## Unidades (décimas)

| Campo | Unidad |
|---|---|
| Nivel | 1/10 dBµV |
| Azimut y elevación | 1/10 ° |
| Calidad | 1/10 % |
| Rumbo de brújula | 1/10 ° |
