#!/bin/bash
# Ask for a Shodan API key with hidden input and store it in the local .env file.
# This script does not print the key and does not contact Shodan.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${SHODAN_ENV_FILE:-$ROOT/.env}"

if [[ -e "$ENV_FILE" ]]; then
  echo "A .env file already exists. Refusing to overwrite it." >&2
  exit 2
fi

KEY=""
cleanup() {
  unset KEY
}
trap cleanup EXIT

echo "Enter the Shodan API key. Input stays hidden. Press Enter when done."
IFS= read -r -s KEY </dev/tty
echo
cd "$ROOT"
printf '%s\n' "$KEY" | python3 -m envfile create "$ENV_FILE"
