# router-lab AWS 迁移方案

> 2026-08-18 · 推荐地域：东京 ap-northeast-1（账号 ID 已脱敏）
> 定位：云端只跑模型 API 网关（Tier 1）；agent CLIs 留本地

## 架构

```
用户/curl ──> ALB(HTTPS) ──> ECS Fargate: gateway 容器 ──> DeepSeek / Qwen(百炼) / Kimi / Google
                                     │
                              Secrets Manager (4 个 key)
                                     │
                              EFS: router.jsonl 持久化
```

## 步骤

1. **镜像**：Tier 1 `docker build`（见 Docker方案.md）→ 推到 ECR（东京）
2. **密钥**：AWS Secrets Manager 存 DEEPSEEK_API_KEY / DASHSCOPE_API_KEY / DASHSCOPE_BASE_URL / MOONSHOT_API_KEY / GEMINI_API_KEY，Fargate 任务 env 注入（不进镜像、不进 git）
3. **运行**：ECS Fargate（0.25 vCPU / 0.5GB，~$5-10/月）或 EC2 t3.micro
4. **暴露**：ALB 自动 HTTPS → 安全组只开 443、限来源 IP；网关自带 ratelimit 防刷
5. **成本护栏**：`ROUTER_BUDGET` env 注入，超支自动拒绝调用
6. **观测**：router.jsonl 挂 EFS，或接 CloudWatch 日志
7. **agent 侧**：本地 CLIs 留本地（太重+密钥敏感）；云端只跑模型路由

## 关键点

- **东京直连 Google 无障碍**：gemini 在云上比本地稳（本地要挂代理）
- **国内 API 可达性**：东京访问 DeepSeek / 百炼 / Moonshot 国内端点均通（实测延迟可接受）
- **安全**：key 全走 Secrets Manager；安全组限来源；网关已有 IP/global/agent 限流
- **成本**：Fargate 小额任务 ~$5-10/月 + ALB ~$18/月（若纯个人测试可省 ALB，直接 EC2 公网 IP + nginx TLS）

## 两种起步方式对比

- **轻量**：一台 EC2 t3.micro（~$8/月）docker run gateway + nginx TLS + 安全组 —— 最快上线
- **规范**：ECR + Fargate + ALB + Secrets Manager —— 更工程化、可伸缩

## 验证

```bash
# 本地
docker build -t router-gateway .
# 推 ECR 后
aws ecs run-task ...   # 或 docker run -p 8123:8123 -e DEEPSEEK_API_KEY=... router-gateway
curl https://<alb>/v1/chat/completions -d '{"model":"judge","messages":[{"role":"user","content":"hi"}]}'
```
