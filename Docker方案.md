# router-lab Docker 部署方案

> 2026-08-18 · 分级：Tier 1 模型网关（纯 Python，推荐先做）；Tier 2 agent CLIs（重，谨慎）

## 分级说明

- **Tier 1 · 模型 API 网关**：gateway.py + lib（llm_client/obs/budget/ratelimit）+ config，纯 Python 无本地 CLI 依赖，一个镜像搞定
- **Tier 2 · agent CLIs**：需装 node + 8 个 coding CLI + key + 代理，镜像大、密钥敏感 → **不建议入镜像**，agent 路由留本地，云端只跑模型网关

## Tier 1 Dockerfile

```dockerfile
FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -i https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt
COPY lib/ ./lib/
COPY config/ ./config/
COPY gateway.py router_v1.py envcheck.py .
ENV GATEWAY_HOST=0.0.0.0
EXPOSE 8123
CMD ["python", "gateway.py"]
```

## docker-compose.yml

```yaml
services:
  gateway:
    build: .
    ports: ["8123:8123"]
    environment:            # key 走 env 注入，不写进镜像
      DEEPSEEK_API_KEY: ${DEEPSEEK_API_KEY}
      DASHSCOPE_API_KEY: ${DASHSCOPE_API_KEY}
      DASHSCOPE_BASE_URL: ${DASHSCOPE_BASE_URL}
      MOONSHOT_API_KEY: ${MOONSHOT_API_KEY}
      GEMINI_API_KEY: ${GEMINI_API_KEY}
      ROUTER_BUDGET: "10"
  redis:
    image: redis:7-alpine     # 分布式限流后端（ratelimit 自动降级本地）
```

## 关键点

- **key 不进镜像**：全部用环境变量/Secrets 注入
- **requirements.txt**：openai、pyyaml（不需要 litellm）
- **ratelimit**：本地装 redis 则用 Redis 后端，否则自动降级本地
- **验证**：`docker compose up` → `curl localhost:8123/v1/chat/completions`（model=judge）

## Tier 2（若坚持容器化 agent）

- 需装 node + claude/codex/qwen/kimi/hermes/opencode/cline + key + 代理
- 建议：镜像内只装模型网关，agent CLIs 作为"sidecar"或留宿主机，网关通过 subprocess 调宿主机 CLI
- 密钥敏感度高，优先 Secrets 注入而非镜像内文件

## 验证命令

```bash
docker compose up -d
curl -s localhost:8123/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"judge","messages":[{"role":"user","content":"用一句话介绍路由"}]}'
docker compose down
```
