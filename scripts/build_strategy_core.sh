#!/usr/bin/env bash
# Build & install the Rust-based strategy_core module into the current Python environment.
# Usage: ./scripts/build_strategy_core.sh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CRATE_DIR="${ROOT_DIR}/shared/strategy_core"

if [[ ! -d "${CRATE_DIR}" ]]; then
  echo "strategy_core crate not found at ${CRATE_DIR}" >&2
  exit 1
fi

USE_VENV=0
if [[ -d "${ROOT_DIR}/.venv" ]]; then
  # shellcheck disable=SC1091
  source "${ROOT_DIR}/.venv/bin/activate"
  USE_VENV=1
  echo "[build] Activated .venv environment"
fi

if ! command -v cargo >/dev/null 2>&1; then
  if [[ -f "${HOME}/.cargo/env" ]]; then
    # shellcheck disable=SC1091
    source "${HOME}/.cargo/env"
    echo "[build] Sourced ~/.cargo/env"
  else
    echo "cargo is not installed. Install Rust via rustup before running this script." >&2
    exit 1
  fi
fi

if ! python -c "import maturin" >/dev/null 2>&1; then
  echo "[build] Installing maturin into current Python environment"
  pip install --upgrade maturin
fi

pushd "${CRATE_DIR}" >/dev/null
echo "[build] Compiling strategy_core via maturin (release mode)"
if [[ "${USE_VENV}" -eq 1 ]]; then
  maturin develop --release
else
  maturin build --release --interpreter python3
  latest_wheel="$(ls -1 target/wheels/strategy_core-*.whl | tail -n 1 || true)"
  if [[ -z "${latest_wheel}" ]]; then
    echo "Failed to locate built strategy_core wheel in target/wheels" >&2
    exit 1
  fi
  echo "[build] Installing wheel ${latest_wheel}"
  pip install --no-cache-dir "${latest_wheel}"
fi
popd >/dev/null

echo "[build] strategy_core module installed successfully"

