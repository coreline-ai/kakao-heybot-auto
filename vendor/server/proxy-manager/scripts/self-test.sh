#!/usr/bin/env bash
set -euo pipefail
curl --fail --silent http://127.0.0.1:4340/health >/dev/null
curl --fail --silent http://127.0.0.1:4340/ready >/dev/null
printf '%s\n' "proxy-manager ready"
