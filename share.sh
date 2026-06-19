#!/usr/bin/env bash
# share.sh —— 一键启动「对外开放」模式的 SUSU 服务
#   带访问口令 + AI 每日限流 + 关闭 debug，适合内网穿透发链接给朋友试用。
#   本地自用直接 `python app.py` 即可，无需本脚本。

set -u
cd "$(dirname "$0")"          # 切到脚本所在目录(项目根)

echo "================  SUSU 对外开放启动  ================"

# ---- 1) 通义千问密钥(必须) ----
if [ -z "${DASHSCOPE_API_KEY:-}" ]; then
  read -rsp "请输入通义千问 DASHSCOPE_API_KEY（输入时不显示）： " DASHSCOPE_API_KEY
  echo
fi
if [ -z "${DASHSCOPE_API_KEY}" ]; then
  echo "❌ 未提供密钥，无法启动。先到阿里云百炼控制台拿 key 再来。"
  exit 1
fi
export DASHSCOPE_API_KEY

# ---- 2) 访问口令(朋友需输入) ----
if [ -z "${SUSU_ACCESS_PASSWORD:-}" ]; then
  read -rp "设一个访问口令（直接回车用默认 susu2026）： " _pw
  SUSU_ACCESS_PASSWORD="${_pw:-susu2026}"
fi
export SUSU_ACCESS_PASSWORD

# ---- 3) 每 IP 每日 AI 次数上限 ----
if [ -z "${SUSU_DAILY_AI_LIMIT:-}" ]; then
  read -rp "每人每天最多分析几次（直接回车用默认 10）： " _lim
  SUSU_DAILY_AI_LIMIT="${_lim:-10}"
fi
export SUSU_DAILY_AI_LIMIT

# ---- 4) 对外模式：关闭 debug，绑定 0.0.0.0 ----
export SUSU_PUBLIC=1

echo "----------------------------------------------------"
echo "  访问口令      ：${SUSU_ACCESS_PASSWORD}"
echo "  每日上限      ：${SUSU_DAILY_AI_LIMIT} 次/人"
echo "  本地端口      ：5001"
echo "----------------------------------------------------"
echo "  下一步：另开一个终端运行内网穿透，拿到公网链接发给朋友："
echo "      ngrok http 5001"
echo "  然后把【公网链接 + 上面的访问口令】一起发给对方即可。"
echo "===================================================="
echo

exec python app.py
