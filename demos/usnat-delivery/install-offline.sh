#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 -m pip install \
  --no-index \
  --find-links "$root/wheelhouse" \
  --require-hashes \
  --requirement "$root/requirements.lock.txt"
