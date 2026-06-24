#!/usr/bin/env bash
# Copia el paquete tender_contracts (repo hermano) a ./vendor para el build de Docker.
# Necesario porque tender-shared-contracts es privado y no se puede instalar desde git en el
# build remoto. Ejecutar antes de `compute deploy`. El contenido de vendor/ está gitignored.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$HERE/../tender-shared-contracts/python/tender_contracts"
DEST="$HERE/vendor/tender_contracts"

if [ ! -d "$SRC" ]; then
  echo "No encuentro $SRC (¿está tender-shared-contracts como repo hermano?)" >&2
  exit 1
fi

rm -rf "$HERE/vendor"
mkdir -p "$HERE/vendor"
cp -r "$SRC" "$DEST"
echo "Vendorizado tender_contracts en $DEST"
