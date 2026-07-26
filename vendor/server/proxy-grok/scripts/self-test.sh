#!/usr/bin/env bash
set -euo pipefail
curl --fail --silent http://127.0.0.1:4358/health >/dev/null
curl --fail --silent http://127.0.0.1:4358/ready >/dev/null
printf '%s\n' 'proxy-grok ready'
