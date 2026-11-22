#!/usr/bin/env bash
# 全量回归：所有 spike/selftest_*.py（按耗时从短到长）
set -u
cd "$(dirname "$0")/.."
PY=".venv/bin/python"
tests=(
  spike/selftest_ops.py
  spike/selftest_text.py
  spike/selftest_select.py
  spike/selftest_dpr.py
  spike/selftest_translate_panel.py
  spike/selftest_pin.py
  spike/selftest_clipboard.py
  spike/selftest_settings.py
  spike/selftest_hotkey.py
  spike/selftest_daemon.py
  spike/selftest.py
)
fail=0
for t in "${tests[@]}"; do
  echo "================================================================"
  echo ">>> $t"
  if ! timeout 300 "$PY" "$t"; then
    fail=$((fail + 1))
    echo "!!! FAILED: $t"
  fi
done
echo "================================================================"
if [ "$fail" -eq 0 ]; then
  echo "ALL SUITES PASSED"
  exit 0
fi
echo "$fail suite(s) failed"
exit 1
