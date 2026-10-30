#!/usr/bin/env bash
# WOS申請ボットを VPS に入れる／更新するスクリプト（Ubuntu用）
# ・専用ユーザー wosbot だけで動き、ほかのアプリのファイルには触れない
# ・外からの接続口（ポート）は開けない
# ・触るのは /opt/wosbot、/etc/wosbot.env、wosbot サービスだけ
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "root で実行してください（先頭に sudo を付ける）"; exit 1; }

REPO="https://github.com/ruuuchan/wos-bot"
APP=/opt/wosbot
ENVF=/etc/wosbot.env

# 足りないものだけ入れる（入っているものは更新しない＝ほかのアプリに影響を出さない）
need=()
for p in python3-venv fonts-noto-cjk git; do
  dpkg -s "$p" >/dev/null 2>&1 || need+=("$p")
done
if [ ${#need[@]} -gt 0 ]; then
  echo "== 必要なものを入れています: ${need[*]} =="
  apt-get update -qq
  apt-get install -y -qq --no-upgrade "${need[@]}" >/dev/null
fi

id wosbot >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin wosbot

echo "== コードを取得しています =="
if [ -d "$APP/src/.git" ]; then
  git -C "$APP/src" pull -q
else
  mkdir -p "$APP"
  git clone -q "$REPO" "$APP/src"
fi
[ -d "$APP/venv" ] || python3 -m venv "$APP/venv"
"$APP/venv/bin/pip" install -q -r "$APP/src/requirements.txt"
chown -R root:root "$APP"   # ボット自身はコードを書き換えられない

if [ ! -f "$ENVF" ]; then
  echo
  read -rsp "Discordのボットトークンを貼り付けてEnter（画面には出ません）: " T < /dev/tty; echo
  read -rp  "テスト用サーバーのID: " G < /dev/tty
  umask 077
  printf 'DISCORD_TOKEN=%s\nGUILD_ID=%s\nDB_PATH=/var/lib/wosbot/data.db\n' "$T" "$G" > "$ENVF"
fi

cat > /etc/systemd/system/wosbot.service <<UNIT
[Unit]
Description=WOS 申請ボット
After=network-online.target
Wants=network-online.target

[Service]
User=wosbot
Group=wosbot
EnvironmentFile=$ENVF
WorkingDirectory=$APP/src
ExecStart=$APP/venv/bin/python bot.py
Restart=always
RestartSec=10
StateDirectory=wosbot
MemoryMax=300M
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
PrivateDevices=true
ProtectKernelTunables=true
ProtectControlGroups=true
RestrictSUIDSGID=true

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable -q wosbot
systemctl restart wosbot
sleep 5
echo
if systemctl is-active -q wosbot; then
  echo "✅ ボットが起動しました。Discordでオンラインになっているか確認してください"
else
  echo "⚠️ 起動に失敗しました。下のログをClaudeに送ってください"
fi
journalctl -u wosbot -n 15 --no-pager
