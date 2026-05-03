#!/usr/bin/env bash
# Asset Factory — git hook 설치 스크립트.
#
# 새 클론 / 워크트리에서 1회 실행. 본 repo 의 scripts/git-hooks/ 아래 hook 들을
# .git/hooks/ 로 symlink 한다. 트래킹 파일을 수정하면 모든 클론에 자동 반영
# (.git/hooks/ 자체는 .gitignore 영역이라 트래킹 안 됨).
#
# 본 repo 는 worktree 도 종종 쓰는데, worktree 는 .git/ 가 .git 파일 (gitdir
# pointer) 로 되어 있어 git rev-parse --git-path hooks 가 실제 hooks 디렉토리를
# 정확히 알려준다.
#
# 사용:
#   ./scripts/install-hooks.sh
#   ./scripts/install-hooks.sh --uninstall  # 되돌리기

set -euo pipefail

ROOT_DIR="$(git rev-parse --show-toplevel)"
HOOK_SRC_DIR="$ROOT_DIR/scripts/git-hooks"
HOOK_DST_DIR="$(git rev-parse --git-path hooks)"

if [ ! -d "$HOOK_SRC_DIR" ]; then
  echo "[install-hooks] $HOOK_SRC_DIR 가 없습니다." >&2
  exit 1
fi

mkdir -p "$HOOK_DST_DIR"

UNINSTALL=0
if [ "${1:-}" = "--uninstall" ]; then
  UNINSTALL=1
fi

for src in "$HOOK_SRC_DIR"/*; do
  [ -f "$src" ] || continue
  hook_name="$(basename "$src")"
  dst="$HOOK_DST_DIR/$hook_name"

  if [ "$UNINSTALL" -eq 1 ]; then
    if [ -L "$dst" ]; then
      rm "$dst"
      echo "[install-hooks] removed $dst"
    fi
    continue
  fi

  # 기존 hook 이 일반 파일이면 덮어쓰지 않고 경고 — 사용자 커스텀 보존.
  if [ -e "$dst" ] && [ ! -L "$dst" ]; then
    echo "[install-hooks] $dst 가 일반 파일로 존재 — symlink 안 함 (백업 후 다시 실행하세요)." >&2
    continue
  fi

  ln -sf "$src" "$dst"
  chmod +x "$src"
  echo "[install-hooks] linked $hook_name → $src"
done

if [ "$UNINSTALL" -eq 1 ]; then
  echo "[install-hooks] uninstall 완료."
else
  echo "[install-hooks] 설치 완료. 임시 우회: git commit --no-verify."
fi
