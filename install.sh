#!/usr/bin/env bash
# Instala los bloques R&S EM200 (gr-em200) para GNU Radio 3.10 en el usuario actual (sin sudo), con enlaces simbólicos.
#   ./install.sh            instala
#   ./install.sh --remove   desinstala
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYDIR="$(python3 -c 'import site; print(site.getusersitepackages())')"
GRCDIR="$HOME/.grc_gnuradio"

if [[ "${1:-}" == "--remove" ]]; then
    rm -f "$PYDIR/em200" "$GRCDIR"/em200_*.block.yml
    rm -f "$HOME/.cache/grc_gnuradio/cache_v2.json"
    echo "Bloques gr-em200 desinstalados."
    exit 0
fi

mkdir -p "$PYDIR" "$GRCDIR"
ln -sfn "$HERE/python/em200" "$PYDIR/em200"
for f in "$HERE"/grc/*.block.yml; do
    ln -sfn "$f" "$GRCDIR/$(basename "$f")"
done
rm -f "$HOME/.cache/grc_gnuradio/cache_v2.json"   # fuerza a GRC a recargar los bloques
python3 -c "import em200; print('Módulo em200 OK:', em200.__file__)"
echo "Bloques instalados. En GRC aparecen en la categoría [R&S EM200]."
