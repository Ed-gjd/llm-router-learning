# LLM Router 学习课程：模型路由 + Agent 路由

> LLM Router Learning — Model Routing & Agent Routing
>
> 版本 v2 · 2026-08-19 · 一套自研路由系统的实战代码库：一个统一入口分派任务到多家模型 / 多个 coding agent，全链路可观测、有护栏。
> 课程总纲（4 阶段 10 课路线）见《[模型路由+Agent路由学习方案.md](模型路由+Agent路由学习方案.md)》。

---

## 一、这是什么

router-lab 是一个自建的路由系统，解决两件事：

1. **模型路由**：把一次 LLM API 请求，按任务难度/成本/可用性分派给 DeepSeek / Qwen(百炼) / Kimi(Moonshot) 等模型，失败自动降级。
2. **Agent 路由**：把"要动手干活的代码任务"分派给本机已装的 coding agent CLI（claude / codex / qwen / kimi / hermes / opencode / cline / gemini / dsh）。

一切路由决策都落盘到 `data/router.jsonl`，可用 `report.py` 看报告、`panel.py` 看 Web 面板。

## 二、环境准备

- Python 3.13 venv（装有 openai、pyyaml）；下文 `python` 均指该 venv 解释器
- 各家 API key 走环境变量，不硬编码：`DEEPSEEK_API_KEY` / `DASHSCOPE_API_KEY` + `DASHSCOPE_BASE_URL` / `MOONSHOT_API_KEY` / `GEMINI_API_KEY`
  - 建议写入 `~/.bashrc` 或本地 `.env`（已被 gitignore），运行前 `source`；别写进代码
- coding agent CLIs 装在本机 PATH（claude/codex/qwen/kimi/hermes/opencode/cline/dsh…）

每次操作前：

```bash
source .env   # 若按 .env 组织 key；否则按你的实际配置来源加载
```

## 三、快速开始

```bash
# 1) 探测五端可用性（模型 API ×3 + agent CLI ×2）
python envcheck.py

# 2) 模型路由：让裁判按任务难度自动选模型
python router_v1.py --strategy judge --task "写一个快速排序"

# 3) agent 路由：一句话分发（问答走模型 API，动手走 agent）
python router.py "用一句话介绍路由"
python router.py "写一个快速排序脚本"

# 4) 看观测报告 / Web 面板
python report.py
python panel.py            # 打开 http://127.0.0.1:8124
```

## 四、核心工具

### envcheck.py —— 五端可用性矩阵
```bash
python envcheck.py                # API 端真调、CLI 端查配置
python envcheck.py --cli-probe    # CLI 端也真实调用一次
python envcheck.py --list-models  # 列各家 API 可用模型
```

### router_v1.py —— 模型路由（四种策略）
```bash
python router_v1.py --strategy manual --provider deepseek --model deepseek-v4-flash --task "..."
python router_v1.py --strategy round-robin --task "..." --times 3
python router_v1.py --strategy fallback --model deepseek-v4-flash --task "..." --inject-failure
python router_v1.py --strategy judge --task "..."    # 便宜模型判难度→选档→降级
```

### agent_router.py —— 任务分派到 agent
```bash
python agent_router.py --task "写个排序" --agent codex   # 强制指定 agent
python agent_router.py --task "用一句话介绍路由"           # 自动判断：问答→模型API，动手→agent
python agent_router.py --task "..." --model deepseek-v4-flash  # 运行时换模型
```

### router.py —— 一句话分发入口
```bash
python router.py "用一句话介绍路由"
python router.py "写一个快速排序脚本"
```
返回：结果 + 执行端 + 模型 + 成本 + 决策原因（route_hint）。

### compare.py —— 策略评测平台
```bash
python compare.py
```
固定任务集 × 多条策略批量跑分，输出成功率/成本/延迟 + 裁判命中率。改 `STRATEGIES` 数组加策略。

### report.py / panel.py —— 观测
```bash
python report.py    # 文本报告：各端次数/成本/占比/延迟/成功率 + 降级链
python panel.py     # Web 面板 http://127.0.0.1:8124
```

### cost_report.py —— 成本对比
```bash
python cost_report.py    # 路由后 vs 全用 max 的节省
```

### gateway.py —— OpenAI 兼容 HTTP 网关
```bash
GATEWAY_HOST=127.0.0.1 python gateway.py &   # 起在 8123
curl http://127.0.0.1:8123/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"judge","messages":[{"role":"user","content":"用一句话介绍路由"}]}'
# model="agent:codex" 走 agent；同一端点收编模型 API + agent CLI
```

### check_secrets.py —— 密钥审计
```bash
python check_secrets.py    # 扫描代码/配置里的硬编码 sk- 密钥
```

## 五、配置详解（`config/`）

### providers.yaml —— 提供方
每家一行：`base_url`（OpenAI 兼容端点）、`key_env`（取哪个环境变量）、`enabled`、`rpm`（限速，如 Kimi 3）。
```yaml
deepseek:
  base_url: https://api.deepseek.com
  key_env: DEEPSEEK_API_KEY
  enabled: true
kimi:
  base_url: https://api.moonshot.cn/v1
  key_env: MOONSHOT_API_KEY
  enabled: true
  rpm: 3
```

### models.yaml —— 模型表
每个模型：`provider` / `tier`(flash/plus/max) / `price_*`(元/百万 token) / `fallback`(降级链，跨厂商按能力等价)。
```yaml
- name: deepseek-v4-flash
  provider: deepseek
  tier: flash
  price_in_per_mtok: 1.0
  price_out_per_mtok: 2.0
  fallback: [qwen-plus]
```

### agents.yaml —— coding CLI 注册表
每个 agent：`cmd` / `args`({prompt} 占位) / `parse`(解析器) / `model_flag` / `permission_auto` / `cwd` / `env`。
```yaml
qwen:
  cmd: qwen
  args: ["-p", "{prompt}", "--output-format", "json", "--auth-type", "openai"]
  parse: qwen_json
  model_flag: "-m"
  env: {OPENAI_API_KEY: "${DASHSCOPE_API_KEY}", OPENAI_BASE_URL: "${DASHSCOPE_BASE_URL}", OPENAI_MODEL: "qwen-plus"}
```

### ratelimit.yaml —— 分布式限流
`backend`(auto/redis/local) + 规则（ip 滑动窗口 / global / agent 并发租约）；provider 限额沿用 providers.yaml 的 `rpm`。

## 六、多 Agent 注册表（9 个）

- **claude** → DeepSeek（Anthropic 兼容端点）
- **codex** → DeepSeek（Responses 协议）
- **hermes** → DeepSeek（--provider deepseek）
- **qwen** → DeepSeek / DASHSCOPE（--auth-type openai）
- **opencode** → DeepSeek（-m deepseek/deepseek-v4-flash）
- **kimi** → Moonshot / DeepSeek（config.toml 双轨）
- **cline** → DeepSeek（已 auth，prompt 放最后）
- **gemini** → Google（走代理，偶发空重试）
- **dsh** → DeepSeek Harness（pnpm dsh --profile headless）

新增一家 = 在 agents.yaml 加一段配置，不改代码。

## 七、观测字段（data/router.jsonl）

每条记录：`kind`(llm/agent/cache/rate_limit) / `provider` 或 `agent` / `requested_model` / `used_model` / `tokens_in`/`out` / `cost` / `latency_ms` / `ok` / `route_hint`(决策原因) / `ts`。

## 八、安全护栏

- **限流**：IP 60/60s、全局 30/60s、agent 并发租约 2、provider RPM（kimi 3）——超限 429 + Retry-After
- **预算**：`ROUTER_BUDGET=<元>` 环境变量，累计超限拒绝调用
- **权限模式**：`run_agent(mode="default")` 保守（默认沙箱只读），`mode="auto"` 才追加该 agent 的自动批准旗标
- **密钥**：key 只走环境变量/外部配置文件；`check_secrets.py` 审计；`keys-inventory.md` 盘点与轮换

## 九、常见问题

- **claude/codex 实际驱动模型 ≠ 请求模型**：claude 走 DeepSeek（四个模型槽映射 deepseek-v4-flash[1m]）；看 `used_model` 字段
- **kimi --output-format 只支持 text/stream-json**：不支持 json
- **cline 中文 prompt 报 Unknown command**：用英文，prompt 放最后
- **gemini 墙内不稳定**：走代理仅小 prompt 稳定，空返回就重试
- **qwen 交互 TUI 烂**：只用 headless（`qwen -p "..."`）
- **代码报 ImportError**：用装有 openai 的 venv 解释器（系统 python 没装）

## 十、相关文档

- `模型路由+Agent路由学习方案.md` —— 课程总纲（4 阶段 10 课：环境→模型路由→Agent 路由→网关/毕业项目）
- `复盘.md` —— 课程全量复盘（含 8 个真实 bug 与教训）
- `ccr-gap.md` —— 对标 claude-code-router 的差距清单
- `keys-inventory.md` —— 密钥盘点与轮换
- `Docker方案.md` / `AWS迁移方案.md` —— 部署方案
