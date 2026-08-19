#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

echo "Setting up PR Review Agent V3 local reviewer copy..."

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is required. Install Python 3.10 or newer, then run this again."
  exit 1
fi

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

if [ ! -f ".env" ]; then
  cp .env.example .env
fi

echo
echo "Setup complete."
echo "Next step: open .env, add your GitHub/Gemini values, then run:"
echo "  bash run_review.sh"
