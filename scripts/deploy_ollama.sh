#!/usr/bin/env bash
# deploy_ollama.sh — 生产环境 Ollama 一键部署（Ubuntu/Debian）
# 用法: sudo bash scripts/deploy_ollama.sh [模型名，默认 qwen2.5:14b]
set -euo pipefail

MODEL="${1:-qwen2.5:14b}"
OLLAMA_HOST="${OLLAMA_HOST:-0.0.0.0}"   # 跨机访问需 0.0.0.0（配合防火墙白名单！）

echo "==> 1/5 安装 Ollama"
if ! command -v ollama &>/dev/null; then
    curl -fsSL https://ollama.com/install.sh | sh
else
    echo "已安装: $(ollama --version)"
fi

echo "==> 2/5 配置监听地址 ${OLLAMA_HOST}:11434"
mkdir -p /etc/systemd/system/ollama.service.d
cat > /etc/systemd/system/ollama.service.d/override.conf <<EOF
[Service]
Environment="OLLAMA_HOST=${OLLAMA_HOST}:11434"
Environment="OLLAMA_NUM_PARALLEL=2"
EOF
systemctl daemon-reload
systemctl enable --now ollama
systemctl restart ollama

echo "==> 3/5 拉取模型 ${MODEL}（约 9GB，依网络 5-20 分钟）"
ollama pull "$MODEL"

echo "==> 4/5 验证"
sleep 2
curl -sf http://localhost:11434/api/tags | grep -o '"name":"[^"]*"' | head -5
curl -sf http://localhost:11434/v1/models | head -c 200; echo

echo "==> 5/5 防火墙提示"
echo "如跨机访问，放行 11434 给后端服务器 IP："
echo "  ufw allow from <后端服务器IP> to any port 11434 proto tcp"
echo
echo "完成。项目 .env 配置："
echo "  OLLAMA_BASE_URL=http://$(hostname -I | awk '{print $1}'):11434/v1"
echo "  OFFLINE_LLM_MODEL=${MODEL}"
