#!/usr/bin/env bash
# Jalankan dari folder ini: ./run.sh [--dry-run|--stream-id ID]
cd "$(dirname "$0")" || exit 1
exec .venv/bin/python schedule_streams.py "$@"
