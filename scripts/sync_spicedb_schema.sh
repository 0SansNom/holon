#!/usr/bin/env bash
# Keep Helm's SpiceDB schema copy identical to docker/spicedb/schema.zed.
# Helm cannot reference files outside the chart directory, so we maintain
# a checked-in copy under deploy/helm/holon/files/.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/docker/spicedb/schema.zed"
DST="$ROOT/deploy/helm/holon/files/spicedb-schema.zed"

usage() {
  echo "usage: $0 [--check | --sync]" >&2
  exit 2
}

mode="${1:---check}"
case "$mode" in
  --check)
    if ! cmp -s "$SRC" "$DST"; then
      echo "SpiceDB schema drift: $DST != $SRC" >&2
      echo "Run: make sync-spicedb-schema" >&2
      exit 1
    fi
    echo "OK: Helm SpiceDB schema matches docker/spicedb/schema.zed"
    ;;
  --sync)
    cp "$SRC" "$DST"
    echo "synced $DST from $SRC"
    ;;
  *)
    usage
    ;;
esac
