#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  echo "Local environment not found. Run this first:"
  echo "  bash setup_local.sh"
  exit 1
fi

source .venv/bin/activate
python main.py
