# 模型路由 + Agent 路由学习方案

> 版本 v2 · 2026-08-18 · 已批准开工（4 阶段 · 10 课）

## 0. 方案总览

### 0.1 定位

- 一门个人学习课，目标是从零实现"模型路由 + agent 路由"统一系统，终局是 `router.py` 统一入口。
- 两个路由对象，一套心智：
  - 模型路由：路由对象是 LLM API 请求，分派给 DeepSeek / 阿里云百炼 Qwen / Kimi（Moonshot）三家。
  - Agent 路由：路由对象是任务，分派给本机 claude CLI（2.1.233）与 codex CLI（0.147.0）无头执行。
- 测试模型限三家，执行端限两 CLI。Kimi 属后补，是"新 provider 接入演练"，不是范围蔓延。
- `cc/model_lesson18.py` 只做阶段 0 精读拆解参考，不直接改造；本课程代码全部新写在 `router-lab/`。

### 0.2 资源分工

- 本机全 CPU（WSL2），模型全走远程 API，任何一课都能在普通笔记本上跑通。
- 本机 claude CLI 与 codex CLI 当前都跑在 DeepSeek 上（claude 走 `ANTHROPIC_BASE_URL=https://api.deepseek.com`，四槽映射 `deepseek-v4-flash[1m]`；codex 走 `model_provider=deepseek` + `DEEPSEEK_API_KEY`）。这条链路是本课最重要的教学现场：路由系统要能亲眼看清它。
- Python 统一用 `python3`（openai 包齐备）。系统 python3 没有 openai 包，不用。

### 0.3 网络与镜像约定

- pip 已配国内镜像，新包直接装。
- 三家 API 均需直连外网：DeepSeek `api.deepseek.com`、阿里云专属 MaaS 端点（`~/.config/aliyun/cc.env` 的 `DASHSCOPE_BASE_URL`）、Moonshot `api.moonshot.cn`（后补）。
- WSL2 偶发 DNS/连通问题，失败排查先 `curl -I` 端点再怪代码。

### 0.4 交付约定

- 每课固定四步协议：**先给命令 → 给出预计输出 → 再执行 → 结果可验证**。AI 负责逐条解释。
- key 只走环境变量，绝不硬编码进代码、命令历史、日志或提交。
- 所有命令先给再跑；学员确认后才执行。
- 不用 markdown 表格，全用列表与代码块。

### 0.5 前置要求

- Python 熟练（能读懂 dict/异常/装饰器，会 subprocess 更好）。
- `DEEPSEEK_API_KEY` 已进环境（~/.bashrc）。
- `DASHSCOPE_API_KEY` + `DASHSCOPE_BASE_URL` 在 `~/.config/aliyun/cc.env`，需 `source`。
- 本机已装 claude CLI 2.1.233 与 codex CLI 0.147.0。
- `MOONSHOT_API_KEY` 暂无 → Kimi 后补设计，先两家跑通全部机制，阶段 1 接入位补上。

## 1. 阶段总览（4 阶段 · 10 课）

- 阶段 0 · 地基：环境与链路（2 课）→ 产出 `envcheck.py` 五端可用性矩阵
- 阶段 1 · 模型路由：统一调用 + 手动/轮询/降级 + LLM 裁判（3 课）→ 产出 `router_v1.py`
- 阶段 2 · Agent 路由：claude/codex CLI 执行端（3 课）→ 产出 `agent_router.py`
- 阶段 3 · 统一网关 + 生产优化（2 课）→ 产出 `gateway.py` + `router.py` 终局

目录规划（阶段 0 建）：

```
~/cc/router-lab/
  envcheck.py            # 阶段0 验收成品
  config/providers.yaml  # 提供方：base_url / key 来源 / 启停
  config/models.yaml     # 模型表：质量档 / 价格 / 上下文 / 降级链
  lib/llm_client.py      # 统一 OpenAI 兼容调用层（阶段1）
  lib/agent_client.py    # 两 CLI 无头封装层（阶段2）
  lib/obs.py             # 统一观测字段 + 落盘
  router_v1.py
  agent_router.py
  gateway.py router.py   # 终局统一入口
  tasks/  data/
```

## 2. 阶段 0 · 地基：环境与链路（2 课）

### 课 0.1 环境盘点 + 三家 API 打通 + 链路认知

**目标**：跑通 DeepSeek + Qwen 两家 API，亲眼确认 claude/codex 的 DeepSeek 后端链路，建立"厂商差异六维度"心智。

**核心概念**：

- 厂商差异六维度：接口格式 / base_url / 模型命名档位 / 能力差异 / 价格计价 / 限流稳定
- 三家都 OpenAI 兼容 → 统一调用层可行
- 请求的模型 ≠ 实际驱动模型（观测要记）

**命令/代码**（在 `cc/router-lab/`，用 `.venv`）：
```bash
mkdir -p ~/cc/router-lab/{config,lib,tasks,data}
cd ~/cc/router-lab
source ~/.config/aliyun/cc.env
.venv/bin/python3 -c "import openai; print(openai.__version__)"
# 三家各发一条消息（Kimi 槽显示缺 key 跳过）
```

**预期结果**：两家各返回一条模型回复，`model` 字段以 API 返回为准；Kimi 槽输出"缺 key 待补"而非报错。

**验收点**：

1. DeepSeek、Qwen 各成功返回一条回复，模型名打印出来；
2. Kimi 槽优雅显示缺 key，不抛异常；
3. 能口述 claude/codex 当前实际驱动模型是什么。

### 课 0.2 双 CLI 无头打通 + 运行时换模型/换基地址

**目标**：`claude -p` 与 `codex exec` 无头调用跑通，并学会运行时实时切模型/切基地址。

**核心概念**：

- claude：`--model` 换模型；基地址/密钥走环境变量前缀覆盖
- codex：`-m` 换模型；`-c model_provider=<config里预定义的供应商>` 换基地址
- "agent 路由"的底层 = 替 CLI 在运行时选 model + base_url + key

**命令/代码**：
```bash
claude -p "用一句话介绍什么是路由" --output-format json --model haiku
# 运行时换基地址：
ANTHROPIC_BASE_URL=https://api.deepseek.com ANTHROPIC_AUTH_TOKEN=$DEEPSEEK_API_KEY \
  claude -p "hi" --model deepseek-chat --output-format json
codex exec --json -m deepseek-chat "用一句话介绍什么是路由"
codex exec --json -m deepseek-chat -c model_provider=deepseek "hi"
```

**预期结果**：两条 CLI 各返回结构化 JSON；`claude --model haiku` 实际驱动仍是 flash，观测到"请求名≠实际驱动"；`codex -c` 切换不报错。

**验收点**：

1. 两 CLI 无头调用各返回一次成功 JSON；
2. claude 用 env 前缀换 base_url/key 成功；
3. codex 用 `-c model_provider` 切换成功；能说出换基地址的本质是什么。

**阶段 0 验收（里程碑 A）**：`envcheck.py` 一次输出五端可用性矩阵；Kimi 显示缺 key 不报错；能口述双 CLI 的 DeepSeek 链路。

## 3. 阶段 1 · 模型路由：统一调用 + 路由动作 + LLM 裁判（3 课）

### 课 1.1 统一 OpenAI 兼容调用层 + 模型表

**目标**：把厂商差异变成数据——一张模型表 + 一个统一调用层。

**核心概念**：

- `LLMClient` 统一封装三家（base_url/api_key 注入、标准响应 + 观测字段）
- `config/providers.yaml`：provider / base_url / key 来源 / 启停
- `config/models.yaml`：模型 / 质量档 / 价格 / 上下文 / 降级链

**命令/代码**：写 `lib/llm_client.py` + 两个 yaml，用一张模型表跑一次三家调用（Kimi 缺 key 跳过）。

**预期结果**：同一段代码调三家；模型表里加一行就多一路。

**验收点**：

1. 同一调用层吃下两家（Kimi 槽位占位）；
2. 模型表变更不触发代码改动；
3. 每次调用返回统一结构含 provider/model/token/耗时。

### 课 1.2 路由动作：手动/轮询/降级 + LLM 裁判

**目标**：实现手动指定、轮询、故障降级三条路由动作，再加 LLM 裁判按任务难度分派。

**核心概念**：

- 手动指定 `--provider/--model`；round-robin 轮询；fallback 降级链
- LLM 裁判：用便宜档模型（flash）判任务难度 → 输出 JSON 分派结论
- 故障注入 `--inject-failure` 演练，不破坏真实 key

**命令/代码**：写 `router_v1.py`，跑手动/轮询/降级/裁判四种模式。

**预期结果**：降级演练两跳回退到 flash 档；裁判输出结构化分派 JSON；观测字段含 route_hint。

**验收点**：

1. 手动/轮询/降级三动作全过；
2. 裁判用 JSON schema 约束可解析；
3. 故障演练不碰真实 key。

### 课 1.3 成本核算 + 观测打点 + Kimi 后补接入

**目标**：算清"路由到底省了多少钱"，观测落盘；拿到 Moonshot key 后只改 config 完成 Kimi 接入。

**核心概念**：

- 单价表统一换算 → 成本核算（复刻"若全用 max"对比）
- 统一观测字段 `kind=llm|agent`，落盘 `data/router.jsonl`
- 配置驱动扩展：新 provider = 加两行 yaml，不改代码

**命令/代码**：写成本模块 + 观测；Kimi key 到位后只在 yaml 加行。

**预期结果**：一批任务跑出"路由后成本 vs 全用 max"对比；Kimi 接入后同代码可调用。

**验收点**：

1. 成本报告数值可复现；
2. Kimi 接入后路由不炸、能降级；
3. 观测日志含完整字段。

**阶段 1 验收（里程碑 B）**：`router_v1.py` 手动/轮询/降级/裁判/成本全过验收点；Kimi 接入位就绪。

## 4. 阶段 2 · Agent 路由：claude/codex CLI 执行端（3 课）

### 课 2.1 AgentClient 封装 + 统一结果结构

**目标**：把两 CLI 封装成"可路由的执行端"，输出统一 JSON 结果。

**核心概念**：

- `AgentClient` 用 subprocess 包装 `claude -p` / `codex exec`，**必须继承 os.environ**
- `AgentResult`：exit_code / 输出 / 耗时 / 实际驱动模型
- `codex exec --json` 是 NDJSON 逐行解析；`claude --output-format json` 顶层可能包一层

**命令/代码**：写 `lib/agent_client.py`，各跑一次封装调用。

**预期结果**：两 CLI 都返回统一 AgentResult，含实际驱动模型字段。

**验收点**：

1. 两 CLI 封装调用成功；
2. NDJSON/包装 JSON 解析正确；
3. 记录"请求模型≠实际驱动模型"。

### 课 2.2 任务分派策略：模型 + agent 合流

**目标**：一个入口判断"这任务该走模型 API 还是 claude/codex agent"，并支持运行时切换模型/基地址。

**核心概念**：

- 任务类型判据：纯问答 → API；改代码/写文件 → agent
- 分派策略：规则 + 裁判；并行与超时
- 运行时切换接入：agent 调用时用课0.2 的换模型/换基地址能力

**命令/代码**：写 `agent_router.py`，一批混合任务自动分派。

**预期结果**：纯问答走 API、代码任务走 CLI；输出统一 JSON + route_hint。

**验收点**：

1. 混合任务集自动分派正确；
2. 有超时/失败处理；
3. 每次决策可解释（route_hint）。

### 课 2.3 结果验证 + 安全护栏

**目标**：验证 agent 产物是否符合预期；讲清无头调用的授权语义。

**核心概念**：

- 产物校验：期望文件/输出格式检查，失败重试
- 授权语义：claude `--dangerously-skip-permissions` vs codex `--approve-for-me`/`--sandbox workspace-write`——谁是"跳过一切"谁是"自动同意但沙箱内"
- 只读沙箱演练：在不该写文件的任务上验证护栏

**命令/代码**：验证器 + 护栏演示。

**预期结果**：错误产物被重试/标记；护栏阻止越权写。

**验收点**：

1. 验证器能抓出产物缺失；
2. 能说清两 CLI 授权旗标的真实语义；
3. 护栏演练通过。

**阶段 2 验收（里程碑 E）**：`agent_router.py` 把真实任务分派给 claude/codex，统一 JSON 结果 + 验证 + 护栏演示。

## 5. 阶段 3 · 统一网关 + 生产优化（2 课）

### 课 3.1 OpenAI 兼容 HTTP 网关 + 全链路打点

**目标**：把模型路由 + agent 路由收编进一个统一 base_url 的网关，curl 直达，全链路可观测。

**核心概念**：

- OpenAI 兼容代理：一个 `/v1/chat/completions` 收编三家模型 + 两 CLI（agent 任务转 CLI）
- 全链路打点：模型路由与 agent 路由共用观测字段，落盘 `data/router.jsonl`
- 对照 LiteLLM 设计：配置驱动模型表、降级、成本——我们造的是它的迷你版

**命令/代码**：写 `gateway.py`，一条 curl 打统一端点。

**预期结果**：curl 返回 OpenAI 兼容响应；`data/router.jsonl` 记录完整决策。

**验收点**：

1. 网关一条命令起，curl 通；
2. 两类记录（kind=llm|agent）在同一日志可查；
3. key 不进日志、不硬编码。

### 课 3.2 策略对比 + 自评估 + 毕业项目 + 开源对照

**目标**：把规则/裁判/agent 路由横向对比，做自评估；对照最新开源实现收尾。

**核心概念**：

- 策略对比：同一任务集跑多种策略，输出命中率/成本/延迟
- 自评估：路由决策正确性怎么衡量（ground truth 从哪来）
- 开源对照：claude-code-router / LiteLLM / semantic-router 的逆向学习——我们做到哪、它们的商业级在哪

**命令/代码**：评估脚本 + 对照笔记。

**预期结果**：一份策略对比报告；一份"我的实现 vs 开源"差距清单。

**验收点**：

1. 任意两策略出对比报告；
2. 自评估指标明确；
3. 能讲出开源方案的三个可借鉴点。

**阶段 3 验收（里程碑 F）**：`gateway.py` + `router.py` 统一入口可用；毕业项目过检。

## 6. 毕业项目（3 选 1，各带验收标准）

- **A 统一网关 + 全链路观测**：同一任务可被策略路由到 ≥3 种执行端；`data/router.jsonl` 覆盖两类记录；能回答"今天哪家最便宜、哪条降级链触发过"。
- **B 自然语言任务分发入口**：混合任务集自动分派正确率 ≥80%；agent 路径带安全护栏；每决策可解释（route_hint）。
- **C 路由策略评测平台**：任意两策略出对比报告；新增一家模型（Kimi）后跑同一套评测集得到可对比数据；报告不含敏感信息。

## 7. 里程碑验收

- 里程碑 A（阶段 0 完）：`envcheck.py` 五端矩阵，Kimi 显示缺 key 不报错；能口述双 CLI 的 DeepSeek 链路。
- 里程碑 B（阶段 1 完）：`router_v1.py` 手动/轮询/降级/裁判/成本全过；Kimi 接入位就绪。
- 里程碑 E（阶段 2 完）：`agent_router.py` 真实任务分派 + 统一 JSON + 验证 + 护栏。
- 里程碑 F（阶段 3 + 毕业项目）：`gateway.py` + `router.py` 统一入口可用；毕业项目过检。

## 8. 教学协议（每课五段 + 四步）

每课五段：**目标 → 核心概念 → 命令/代码 → 预期结果（真实格式样例，数值允许不同，结构必须一致）→ 验收点（全过才进下一课）**。

对应"先给命令 → 预计输出 → 再执行 → 验证"。

## 9. 坑与注意点

1. key 加载姿势不同：DEEPSEEK 在 bashrc（新 shell 生效）；DASHSCOPE 在 cc.env 必须 source；永不硬编码。
2. claude/codex 都跑在 DeepSeek：观测记录"请求的模型 ≠ 实际驱动模型"。
3. codex 0.147.0 无 `--full-auto`，用 `--sandbox workspace-write --approve-for-me`。
4. 统一用 `cc/.venv/bin/python3`（系统 python3 无 openai 包）。
5. DASHSCOPE_BASE_URL 是阿里云专属 MaaS 端点，勿覆盖。
6. 成本单价三家单位/币种可能不同，先统一换算。
7. 故障演练用 `--inject-failure`，不破坏真实 key。
8. subprocess 调 CLI 必须继承 os.environ（含 ANTHROPIC_BASE_URL、DEEPSEEK_API_KEY）。
9. `claude -p --output-format json` 顶层可能包一层；`codex exec --json` 是 NDJSON 逐行解析。
10. 观测字段从阶段 1 就统一（kind=llm|agent），否则阶段 3 对不齐。
11. 非 git 仓库；若 git init，.gitignore 必须含 *.env/cc.env/*key*/data/。

## 10. 下一步

本方案已批准开工。当前进行：建目录骨架 → 阶段 0 课 0.1 逐课走"先命令 → 预计输出 → 执行 → 验证"。
