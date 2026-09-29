#!/usr/bin/env bash
# Jalankan UI Streamlit: ./ui.sh
cd "$(dirname "$0")" || exit 1
exec .venv/bin/streamlit run app.py
