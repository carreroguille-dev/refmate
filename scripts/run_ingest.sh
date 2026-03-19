#!/usr/bin/env bash
# Lanza la pipeline de ingesta dockerizada
set -euo pipefail

docker compose --profile ingest run --rm ingest "$@"
