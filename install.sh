#!/usr/bin/env sh
set -eu

cd "$(dirname "$0")"
"${PYTHON:-python3}" install-dependencies.py
