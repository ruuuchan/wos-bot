#!/usr/bin/env bash
# 最新に更新して、TimeTreeのデータ（a と b）をまとめて確認する
bash <(curl -fsSL https://raw.githubusercontent.com/ruuuchan/wos-bot/main/deploy/install.sh)
for c in ${1:-a_4297}; do
  echo; echo "===== $c ====="
  /opt/wosbot/venv/bin/python /opt/wosbot/src/deploy/debug_timetree.py "$c" 2026 11
done
