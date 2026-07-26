#!/usr/bin/env bash
# 12개 프롬프트 영상의 단일 UHD 렌더 큐.
# 각 build/audit/package 중 하나라도 실패하면 set -e로 즉시 멈춘다.
# 원본 PNG와 기존 납품물을 삭제하지 않는다. 1번 재납품 전 기존 폴더는 보존 백업한다.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="$ROOT/pipeline/.venv/bin/python"
DELIVERY_ROOT="output/delivery"
# 복구 큐: START_AT=3 FROM_3=routes 처럼 실패한 프로젝트부터 안전하게 재개한다.
# 기본값은 1번부터 전체 큐를 실행하는 기존 동작을 보존한다.
START_AT="${START_AT:-1}"
FROM_3="${FROM_3:-cues}"

run_project() {
  local project_id="$1"
  local from_stage="$2"
  local yaml="$ROOT/examples/generated/$project_id/project.yaml"

  echo "==> render: $project_id (from $from_stage)"
  PYTHONPATH="$ROOT/pipeline" "$PYTHON" "$ROOT/bin/build.py" "$yaml" \
    --final --verify-sources --from "$from_stage" --audit
  echo "==> package: $project_id"
  PYTHONPATH="$ROOT/pipeline" "$PYTHON" "$ROOT/bin/package-delivery.py" "$yaml" \
    --delivery-root "$DELIVERY_ROOT"
}

backup_existing_delivery() {
  local project_id="$1"
  local delivery="$DELIVERY_ROOT/$project_id"
  if [[ -e "$delivery" ]]; then
    local stamp
    stamp="$(date +%Y%m%d-%H%M%S)"
    local backup="$DELIVERY_ROOT/${project_id}.pre-final-profile-${stamp}"
    echo "==> preserve existing delivery: $delivery -> $backup"
    mv "$delivery" "$backup"
  fi
}

# 1번은 이 스크립트 시작 전에 UHD render/audit이 완료되어 있어야 한다.
if (( START_AT <= 1 )); then
  backup_existing_delivery "future-seoul-ink-cyberpunk-60"
  echo "==> package: future-seoul-ink-cyberpunk-60"
  PYTHONPATH="$ROOT/pipeline" "$PYTHON" "$ROOT/bin/package-delivery.py" \
    "$ROOT/examples/generated/future-seoul-ink-cyberpunk-60/project.yaml" \
    --delivery-root "$DELIVERY_ROOT"
fi

# no cached prep
if (( START_AT <= 2 )); then run_project "korean-folk-watercolor" "props"; fi
if (( START_AT <= 3 )); then run_project "korean-music-dance-ink-60" "$FROM_3"; fi
if (( START_AT <= 4 )); then run_project "kpop-season-2" "cues"; fi
if (( START_AT <= 5 )); then run_project "world_beautiful_places" "cues"; fi

# preexisting routes/props, but regenerate props for no-overlay/camera contract
if (( START_AT <= 6 )); then run_project "invisible-music-landscape-60" "props"; fi
if (( START_AT <= 7 )); then run_project "kpop-ink-color" "cues"; fi
if (( START_AT <= 8 )); then run_project "seoul-night-60" "cues"; fi

# K-food has valid background copies only; regenerate clean/routes/props/render
if (( START_AT <= 9 )); then run_project "k-food-pen-color-60" "clean"; fi

# full-bleed/progressive props already satisfy no-overlay profile contracts
if (( START_AT <= 10 )); then run_project "four-seasons-bicycle-60" "render"; fi
if (( START_AT <= 11 )); then run_project "world-map-drawing-60" "render"; fi
if (( START_AT <= 12 )); then run_project "ordinary-cat-space-adventure-60" "render"; fi

echo "==> all prompt-video deliveries completed"
