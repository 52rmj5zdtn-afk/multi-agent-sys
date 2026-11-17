# Ollama 生产部署指南（Linux）

> 目标：在内网 Linux 服务器上部署 Ollama，作为本项目 LLM 的**离线降级通道**
> （云端 Kimi/Qwen 全部不可用时的最后兜底，以及涉密工单的本地化推理选项）。

## 一、架构位置

```
用户提问 → FastAPI → LangGraph
                 ├─ 复杂诊断 → kimi-k3（云端）─┐
                 ├─ 简单问答 → qwen-turbo ───┤ 全挂/超时 → fallback.py
                 │                            ├─ qwen-plus（云端降级）
                 │                            └─ ollama（离线兜底, qwen2.5:14b）
                 └─ 报表 → SQL（不调 LLM）
```

## 二、安装（Ubuntu 22.04/24.04，一键脚本）

```bash
# 项目根目录执行
sudo bash scripts/deploy_ollama.sh
```

脚本内容（也可手动分步执行）：

```bash
#!/usr/bin/env bash
# 1. 安装 Ollama
curl -fsSL https://ollama.com/install.sh | sh

# 2. 拉取生产模型（约 9GB；显存不足改 qwen2.5:7b 约 4.7GB）
ollama pull qwen2.5:14b
ollama pull qwen2.5:7b        # 备用轻量

# 3. 验证 OpenAI 兼容端点
curl http://localhost:11434/v1/models

# 4. 开机自启（install.sh 已注册 systemd，确认状态）
systemctl status ollama
```

## 三、GPU 支持（强烈推荐，CPU 推理 14B 模型约 10-30s/token）

```bash
# NVIDIA: 装驱动后 Ollama 自动使用 GPU
nvidia-smi    # 确认驱动
# 显存要求: 7B≈8GB / 14B≈16GB / 32B≈24GB
# 多卡/多实例: OLLAMA_NUM_PARALLEL=2（systemd override）
sudo systemctl edit ollama
# [Service]
# Environment="OLLAMA_NUM_PARALLEL=2"
sudo systemctl restart ollama
```

## 四、项目侧配置

`.env`（或生产环境 K8s ConfigMap）：

```env
OLLAMA_BASE_URL=http://<ollama主机IP>:11434/v1
OLLAMA_API_KEY=ollama                  # Ollama 不校验，占位
OFFLINE_ENABLED=true
OFFLINE_LLM_MODEL=qwen2.5:14b
# 极端离线模式（内网全断）：把 fallback 也指向本地
# FALLBACK_MODEL=qwen2.5:14b
```

路由器按模型名自动分流（`src/model/router.py::_get_client`）：
模型名含 `kimi/moonshot` → Kimi；`qwen-` → DashScope；等于 `OFFLINE_LLM_MODEL` 或含 `ollama` → Ollama。

## 五、Docker Compose 方式（可选，与主 compose 同机）

```yaml
# deployment/docker-compose.yml 已含 ollama 服务（profile=local-llm）
docker compose --profile local-llm up -d ollama
docker compose exec ollama ollama pull qwen2.5:14b
```

## 六、验证

```bash
# 1. Ollama 本体
curl http://<IP>:11434/api/tags
# 2. OpenAI 兼容接口（项目使用的协议）
curl http://<IP>:11434/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen2.5:14b","messages":[{"role":"user","content":"用一句话解释激光虚焊"}],"max_tokens":100}'
# 3. 项目内端到端
python -c "import asyncio; from src.model.router import router; \
print(asyncio.run(router.chat([{'role':'user','content':'hi'}], model='qwen2.5:14b')).content)"
```

## 七、运维要点

| 事项 | 命令/方法 |
|------|----------|
| 模型存放 | `/usr/share/ollama/.ollama/models`（磁盘预留 30GB+） |
| 换模型 | `ollama pull <m>` 后改 `.env` 的 `OFFLINE_LLM_MODEL` |
| 内网离线仓库 | 有网机 `ollama pull` 后打包 models 目录，拷贝到离线机 |
| 访问控制 | Ollama 默认无鉴权，**不要暴露公网**；跨机访问用防火墙白名单只放行后端服务器 |
| 日志 | `journalctl -u ollama -f` |
| 版本升级 | `curl -fsSL https://ollama.com/install.sh \| sh`（原地升级） |

## 八、远程访问排错（重要）

若项目服务器（Windows/其他机器）访问 `http://<VM_IP>:11434` 不通，但 VM 本机 `curl localhost:11434` 正常：

```bash
# 原因：Ollama 默认只监听 127.0.0.1
sudo systemctl edit ollama
# 输入：
# [Service]
# Environment="OLLAMA_HOST=0.0.0.0:11434"
sudo systemctl daemon-reload
sudo systemctl restart ollama

# 防火墙放行（仅项目后端服务器）
sudo ufw allow from <后端服务器IP> to any port 11434 proto tcp

# 验证（在项目服务器上执行）
curl http://<VM_IP>:11434/v1/models
```
