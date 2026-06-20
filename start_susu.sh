#!/bin/bash
# SUSU 量化 一键启动：加载本地密钥 → 起 ngrok 固定域名 → 前台跑服务。
# 由 launchd(com.susu.quant) 守护：关终端/重登录/崩溃都会自动拉起。
# 手动用法：./start_susu.sh

cd "$(dirname "$0")" || exit 1

# 加载本地密钥（API key、访问口令、管理员密钥等）
set -a
[ -f ./susu.env ] && source ./susu.env
set +a

# ngrok 固定域名（没在跑就起；已在跑则跳过，避免重复）
if ! /usr/bin/pgrep -f "ngrok http" >/dev/null 2>&1; then
  /opt/homebrew/bin/ngrok http --url=https://drew-rubber-stimulant.ngrok-free.dev 5001 \
    >/tmp/susu_ngrok.log 2>&1 &
fi

# 前台运行 Flask（交给 launchd KeepAlive 守护）
exec /opt/anaconda3/bin/python app.py
