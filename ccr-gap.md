# 对标 claude-code-router（ccr）差距清单

> 课4.1 产出 · 2026-08-18 · 来源：克隆 musistudio/claude-code-router 源码 + 官方文档（routing.md / observability.md）

## ccr 有、我们没有的

1. **条件路由规则引擎**：condition + rewrite、按优先级排序、first-match 生效；我们只有 classify 关键词 + LLM 裁判，没有可配置规则表
2. **Subagent 级路由**：`<CCR-SUBAGENT-MODEL>provider/model</CCR-SUBAGENT-MODEL>` 标签注入，Claude Code 的子任务/Workflow 各自选模型；模型带 Description 引导选择；我们只能整任务路由
3. **工具协议桥**：codex `apply_patch`(freeform) ↔ `virtual_apply_patch`(function tool) 互转，让非 GPT 模型能走 codex 编辑；我们没有
4. **请求级观测**：resolved provider/model、请求/响应体（all/errors/none 采样）、逐工具调用 trace、cost estimate、按 provider/模型/凭据筛选；我们只存统一结果字段
5. **仪表盘 UI**：Desktop(App) + Web 观测页 + 日志筛选；我们只有 report.py 文本
6. **provider preset 一键导入**（Kimi 内置等）、billing header 自动清理

## 我们比 ccr 强的

- 真正多厂商直连路由（三家 OpenAI 兼容直接调 API），ccr 主要是 Claude Code 的网关角色
- 降级链 + LLM 裁判 + 成本核算已落地（cost_report / compare.py）
- 观测字段统一（router.jsonl，kind=llm|agent）
- 分布式限流（lib/ratelimit）

## 补的优先级

- P0 请求/响应体采样进观测（诊断用，低成本）
- P1 Web 观测面板（课4.2）
- P2 条件规则引擎（让路由规则可配置、可排序）
- P3 工具协议桥 / subagent 标签路由（高成本，暂不做）
