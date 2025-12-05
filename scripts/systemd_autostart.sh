#!/usr/bin/env bash
# Ensures all required Docker Compose profiles are brought up for autostart scenarios.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEFAULT_PROFILES="real"

# Allow callers (e.g., systemd) to override which profiles should be activated.
PROFILES_RAW="${AUTOSTART_COMPOSE_PROFILES:-$DEFAULT_PROFILES}"

# Split by whitespace or comma, ignore empty entries.
IFS=', ' read -r -a PROFILE_TOKENS <<< "$PROFILES_RAW"
COMPOSE_ARGS=()
for profile in "${PROFILE_TOKENS[@]}"; do
  profile="$(echo "$profile" | xargs)"  # trim whitespace
  [[ -z "$profile" ]] && continue
  COMPOSE_ARGS+=(--profile "$profile")
done

if [[ ${#COMPOSE_ARGS[@]} -eq 0 ]]; then
  echo "[systemd-autostart] No compose profiles resolved from '$PROFILES_RAW'." >&2
  exit 1
fi

echo "[systemd-autostart] Activating profiles: ${PROFILES_RAW}"
cd "$PROJECT_ROOT"
/usr/bin/docker compose "${COMPOSE_ARGS[@]}" up -d

