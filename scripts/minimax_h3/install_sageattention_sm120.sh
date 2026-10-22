#!/usr/bin/env bash
set -euo pipefail

sage_script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "${sage_script_dir}/install_sageattention_sm120.py" "$@"
