#!/usr/bin/env bash
# Levanta el servidor vLLM con LightOnOCR en la máquina host
set -euo pipefail

vllm serve lightonai/LightOnOCR-1B-1025 \
    --limit-mm-per-prompt '{"image": 1}' \
    --mm-processor-cache-gb 0 \
    --no-enable-prefix-caching \
    --gpu-memory-utilization 0.85 \
    --max-model-len 4096
