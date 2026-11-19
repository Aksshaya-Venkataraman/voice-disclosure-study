#!/usr/bin/env bash
# Fails when a hash-locked requirements file no longer matches its inputs
# (.in file and, for Rasa, overrides.txt). Each lock is re-resolved with the
# committed pins as preferences, so only a real change in inputs - or an
# input that can no longer be resolved - produces a difference.
#   scripts/check_locks.sh      (run from the repository root; needs uv)
set -euo pipefail

status=0
check() {  # <input> <python-version> <lock> [extra uv args...]
  local input=$1 python=$2 lock=$3
  shift 3
  local tmp
  tmp=$(mktemp)
  cp "$lock" "$tmp"
  if ! uv pip compile "$input" "$@" --generate-hashes --python-version "$python" \
       --python-platform x86_64-unknown-linux-gnu -o "$tmp" -q; then
    echo "FAIL: $input can no longer be resolved"; status=1; return
  fi
  # Compare everything except the header (it names the output path).
  if diff -u <(grep -v '^#' "$lock") <(grep -v '^#' "$tmp") > "$tmp.diff"; then
    echo "ok   $lock"
  else
    head -40 "$tmp.diff"
    echo "FAIL: $lock is out of date - regenerate it with the command at its top"; status=1
  fi
  rm -f "$tmp" "$tmp.diff"
}

check gateway/requirements.in 3.13 gateway/requirements.txt
check analysis/requirements.in 3.13 analysis/requirements.txt
check requirements-dev.in 3.13 requirements-dev.txt
check rasa/requirements.in 3.10 rasa/requirements.txt --override rasa/overrides.txt
check rasa/actions/requirements.in 3.10 rasa/actions/requirements.txt
exit $status
