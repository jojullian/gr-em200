# gr-em200 — Bloques de GNU Radio para el receptor R&S® EM200

Estos bloques de **GNU Radio Companion 3.10** permiten usar un receptor digital compacto **Rohde & Schwarz EM200** como fuente de señal en tus flowgraphs.
Funcionan por Ethernet, con el protocolo nativo del equipo (**EB200** sobre TCP).
Están escritos en Python puro: no hay nada que compilar.

| Bloque | Qué entrega | Opción del EM200 | Documentación |
|---|---|---|---|
| **R&S EM200 IQ Source** | IQ complejo en vivo (`complex64`), hasta 40 MHz de ancho de banda | — | [docs/iq_source.md](docs/iq_source.md) |
| **R&S EM200 DF Source** | Radiogoniometría: azimut, calidad, nivel, rumbo y GPS | R&S CS-DF | [docs/df_source.md](docs/df_source.md) |
| **R&S EM200 PScan Source** | Panorama scan: barridos de varios GHz como vectores de nivel | R&S CS-PS | [docs/pscan_source.md](docs/pscan_source.md) |

Flowgraphs de ejemplo en [`examples/`](examples/):

| Ejemplo | Qué hace |
|---|---|
| `em200_espectro.grc` | Analizador de espectro y waterfall con el IQ del EM200 |
| `em200-fm.grc` | Receptor de FM comercial con selector de ancho de banda de demodulación |
| `em200-df.grc` | Radiogoniómetro con compás, valores numéricos e histórico |
| `em200ps.grc` | Panorama scan con barrido progresivo y promedio |

> Probado con un EM200 real, firmware **V06.81**, en Linux Mint 22.3 / Ubuntu 24.04 con GNU Radio **3.10.9**.

---

## Requisitos

- **GNU Radio 3.10** con GRC y QT GUI. En Ubuntu/Debian/Mint: `sudo apt install gnuradio`.
- **Python 3** con `numpy`, que ya viene con GNU Radio.
- **Un R&S EM200** accesible por red:
  - SCPI en el puerto TCP **5555**;
  - trazas de datos en **5565** (puerto SCPI + 10).
  - La IP de fábrica es `192.168.29.200/16`.
- **Opciones del equipo:** las del bloque que vayas a usar (CS-DF para DF, CS-PS para PScan). Lo compruebas con `*OPT?`.
- **Red de 1 Gb/s** si quieres IQ por encima de 1,25 MHz de ancho de banda. Más detalles en [docs/iq_source.md](docs/iq_source.md#ancho-de-banda-y-red).

## Instalación

No necesita `sudo`. Se instala en tu usuario con enlaces simbólicos, así que puedes editar el código sin reinstalar.

```bash
git clone https://github.com/<tu-usuario>/gr-em200.git
cd gr-em200
./install.sh
```

El script:
1. enlaza el paquete Python `em200` en `~/.local/lib/python3.X/site-packages/`;
2. enlaza los bloques `grc/*.block.yml` en `~/.grc_gnuradio/`;
3. borra la caché de bloques de GRC para que los cargue al abrirse.

Si GRC estaba abierto, ciérralo y vuelve a abrirlo. Los bloques aparecen en la categoría **[R&S EM200]**.

Para desinstalar:

```bash
./install.sh --remove
```

### Instalación manual (alternativa)
```bash
cp -r python/em200 ~/.local/lib/python3.12/site-packages/
mkdir -p ~/.grc_gnuradio && cp grc/*.block.yml ~/.grc_gnuradio/
rm -f ~/.cache/grc_gnuradio/cache_v2.json
```

### Comprobar la instalación y la conexión con el equipo
```bash
python3 -c "import em200; print(em200.__file__)"
python3 - <<'EOF'
import socket
s = socket.create_connection(("192.168.29.200", 5555), timeout=5)
s.sendall(b"*IDN?\n*OPT?\n"); print(s.recv(4096).decode())
EOF
```

## Uso rápido

1. Instala los bloques y abre un ejemplo:
   ```bash
   gnuradio-companion examples/em200_espectro.grc
   ```
2. Si tu EM200 no está en `192.168.29.200`, cambia la IP en el bloque fuente.
3. Pulsa ▶.

Para usarlos en tus propios flowgraphs, arrastra el bloque desde **[R&S EM200]**. Para la tasa de muestreo del IQ, añade un bloque *Import* con `import em200` y define la variable `samp_rate = em200.sample_rate(bw)`.

## Cómo se comportan los bloques con el equipo

- **Al arrancar** el flowgraph, el bloque:
  1. **guarda la configuración actual** del EM200;
  2. lo configura (app, modo, frecuencia, anchos de banda…);
  3. abre el socket de datos y registra en él su traza.
- **Al parar**, borra su ruta de datos y **deja el equipo exactamente como estaba**. Se puede desactivar con el parámetro *Restaurar equipo al parar*.
- **Un EM200 solo tiene un receptor.** No ejecutes a la vez dos flowgraphs que usen bloques distintos sobre el mismo equipo: IQ usa la app Receiver, DF usa la app DF y PScan usa el modo PSCan. El segundo reconfiguraría al primero.

## Estructura del repositorio

```
gr-em200/
├── install.sh              instalación / desinstalación
├── grc/                    definiciones de bloques para GRC (YAML)
├── python/em200/
│   ├── eb200.py            parser del protocolo EB200 (IF, DFPan, PScan)
│   ├── scpi.py             conexión SCPI y socket de trazas
│   ├── iq_source.py        bloque IQ Source
│   ├── df_source.py        bloque DF Source
│   └── pscan_source.py     bloque PScan Source
├── examples/               flowgraphs de ejemplo
└── docs/                   documentación de cada bloque y notas del protocolo
```

Las peculiaridades del protocolo que encontramos en el equipo real, y en qué difiere del manual, están en [docs/notas-protocolo.md](docs/notas-protocolo.md).

## Aviso

Proyecto independiente y no oficial, sin relación con Rohde & Schwarz ni respaldo de su parte.
R&S® y EM200 son marcas de Rohde & Schwarz GmbH & Co. KG.
Úsalo bajo tu responsabilidad: los bloques cambian la configuración del receptor mientras se ejecutan.

## Licencia

[GPL-3.0-or-later](LICENSE), la misma familia de licencias que GNU Radio.
