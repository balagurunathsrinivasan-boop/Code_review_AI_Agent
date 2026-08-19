#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")" || exit 1

pause_before_close() {
  echo
  read -r -p "Press Enter to close this window..." _
}

trap pause_before_close EXIT

print_line() {
  printf '%*s\n' "${COLUMNS:-72}" '' | tr ' ' '='
}

env_value() {
  local key="$1"
  local line
  line="$(grep -E "^[[:space:]]*${key}=" .env 2>/dev/null | tail -n 1 || true)"
  line="${line#*=}"
  line="${line%$'\r'}"
  line="$(printf '%s' "$line" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
  line="${line%\"}"
  line="${line#\"}"
  line="${line%\'}"
  line="${line#\'}"
  printf '%s' "$line"
}

is_missing_or_placeholder() {
  local value="$1"

  if [ -z "$value" ]; then
    return 0
  fi

  case "$value" in
    replace_with_*|your_*|*_here|github_owner_or_org|repository_name)
      return 0
      ;;
  esac

  return 1
}

configuration_is_incomplete() {
  is_missing_or_placeholder "$(env_value GITHUB_TOKEN)" && return 0
  is_missing_or_placeholder "$(env_value GEMINI_API_KEY)" && return 0
  is_missing_or_placeholder "$(env_value GITHUB_OWNER)" && return 0
  is_missing_or_placeholder "$(env_value GITHUB_REPO)" && return 0
  return 1
}

print_missing_configuration() {
  local missing_count=0

  echo "Configuration is incomplete."
  echo
  echo "Please update these values in .env:"
  echo

  if is_missing_or_placeholder "$(env_value GITHUB_TOKEN)"; then
    echo "  - GITHUB_TOKEN"
    missing_count=$((missing_count + 1))
  fi

  if is_missing_or_placeholder "$(env_value GEMINI_API_KEY)"; then
    echo "  - GEMINI_API_KEY"
    missing_count=$((missing_count + 1))
  fi

  if is_missing_or_placeholder "$(env_value GITHUB_OWNER)"; then
    echo "  - GITHUB_OWNER"
    missing_count=$((missing_count + 1))
  fi

  if is_missing_or_placeholder "$(env_value GITHUB_REPO)"; then
    echo "  - GITHUB_REPO"
    missing_count=$((missing_count + 1))
  fi

  echo
  echo "The .env file was already opened for this run."
  echo "Save your changes, then double-click START_PR_REVIEW_AGENT.command again."
}

open_env_for_editing() {
  if command -v open >/dev/null 2>&1; then
    open -a TextEdit .env >/dev/null 2>&1 || open .env >/dev/null 2>&1 || true
  fi
}

ask_reviewer_to_review_env() {
  echo "Opening .env now."
  echo
  echo "Please review or update these details for this PR review run:"
  echo
  echo "  GITHUB_TOKEN"
  echo "  GEMINI_API_KEY"
  echo "  GITHUB_OWNER"
  echo "  GITHUB_REPO"
  echo "  PR_LIST_STATE"
  echo "  PULL_REQUEST_NUMBER"
  echo
  echo "Save .env after making changes."
  echo
  open_env_for_editing
  read -r -p "After reviewing and saving .env, press Enter to continue..."
}

clear
print_line
echo "             PR Review - One Click Launcher"
print_line
echo

if [ ! -f ".env" ]; then
  cp .env.example .env
fi

ask_reviewer_to_review_env

if configuration_is_incomplete; then
  echo
  print_missing_configuration
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is required but was not found on this machine."
  echo "Install Python 3.10 or newer, then double-click this launcher again."
  exit 1
fi

echo "Preparing local runtime..."

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

source .venv/bin/activate

if [ ! -f ".venv/.requirements-installed" ] || [ requirements.txt -nt ".venv/.requirements-installed" ]; then
  python -m pip install --upgrade pip
  python -m pip install -r requirements.txt
  touch .venv/.requirements-installed
fi

echo
print_line
echo "Starting PR review..."
print_line
echo

python main.py
