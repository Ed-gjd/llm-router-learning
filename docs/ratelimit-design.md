# 分布式限流方案（课4.x）

> 背景仓库：router-lab。本方案把现有「进程内 RPM 滑动窗口」（`lib/llm_client._throttle`）
> 升级为「多实例共享配额」的分布式限流，并补齐网关入口限流与 agent 并发控制。

## 1. 现状与问题

现状：

- `lib/llm_client.py` 用模块级 `_call_win` 记录调用时间戳，做 60s 滑动窗口 RPM 限速（Kimi RPM=3 在用）。
- `gateway.py` 是唯一 HTTP 入口，无任何限流：谁都能打爆账单，agent 可无限并发拉起。
- 观测 `lib/obs.py` 已统一 `data/router.jsonl`，但限流本身不可观测。

问题（为什么需要"分布式"）：

- 网关多实例部署（多台机器 / 多个容器）后，每个进程各自计数，**配额互不共享** → 组织级 RPM 被打穿。
- agent（claude/codex/qwen…）在并发任务下可无限并发，触发上游限速、资源争抢。
- 没有统一的 429 语义与 Retry-After，客户端无法优雅退避。

## 2. 目标与非目标

目标：

- 多实例共享配额：一个 Redis 后端，N 个网关实例共用一个额度池。
- 分层限流：入口（IP / 全局）→ 调度（provider / model 配额）→ 执行（agent 并发）。
- 原子与低延迟：限流判定 + 计数在一个 Redis 命令内完成（Lua），无竞态。
- 优雅降级：Redis 不可用时本地兜底（fail-open），不阻断主链路；策略可切 fail-closed。
- 可观测：放行/拒绝/等待时长/剩余额度全部落观测，可复盘可告警。

非目标：

- 精确一致性（见 §6：分布式限流本质是近似一致）。
- 复杂风控（验证码、指纹、行为分析）。
- 跨数据中心强一致（本方案单 Redis 主从即可）。

## 3. 总体架构

```
客户端 ──► 网关 gateway.py（限流中间件）
                │  维度: ip / global → 429 + Retry-After
                ▼
          router_v1 路由（judge / fallback / round-robin）
                │  维度: provider / model（RPM 配额）
                ▼
   ┌─────────────────────────────┐
   │  Redis（共享额度池，Lua 原子）  │
   └─────────────────────────────┘
        ▲            ▲
        │            └── 执行层：agent 并发信号量（租约式）
        └── 调度层：provider RPM / token bucket
```

四层职责：

| 层 | 位置 | 维度 | 策略 |
|---|---|---|---|
| 入口层 | gateway.py 中间件 | ip、全局、API key | 滑动窗口计数，拒绝 → 429 |
| 调度层 | llm_client / router_v1 | provider、model、tier | 滑动窗口（RPM）+ 令牌桶（突发） |
| 执行层 | gateway agent 路径 | agent 并发 | 租约式分布式信号量 |
| 客户端层 | 调用方 | — | 指数退避 + jitter 重试 |

## 4. 核心算法选型

| 算法 | 内存 | 精度 | 突发 | 复杂度 | 结论 |
|---|---|---|---|---|---|
| 固定窗口计数 | O(1) | 边界双倍放行 | 窗口内全突发 | 1 命令 | 粗粒度可用 |
| 滑动窗口计数（混合） | O(1) | 近似精确 | 受窗口限制 | 1 Lua | **默认** |
| 滑动窗口日志 | O(n) | 精确 | 受窗口限制 | 1 Lua | 高精度低配才用 |
| 令牌桶 | O(1) | 平滑 | **允许突发（桶容量）** | 1 Lua | 突发平滑用 |
| GCRA | O(1) | 精确间隔 | 无 | 1 Lua | 强间隔约束 |
| 漏桶 | O(1) | 平滑 | 排队 | 需队列 | 削峰用 |

选型结论：

- **RPS/RPM 配额**（入口层、provider 配额）→ 滑动窗口计数：内存 O(1)，误差可控，Lua 原子。
- **突发平滑**（model 级 QPS，如瞬间并发）→ 令牌桶：桶容量 = 允许的突发量。
- **agent 并发** → 租约式信号量：hash 存持有者 + 租约到期时间，过期自动回收，防"进程死了计数不归还"。

### 4.1 滑动窗口计数（Lua）

窗口按 epoch 对齐，`count = 上一窗口计数 × 重叠权重 + 当前窗口计数`，O(1) 内存：

```lua
-- KEYS[1]=计数键  ARGV: limit, window, cost
-- 用服务器时间（redis.call('TIME')）消除客户端时钟偏差
local now = tonumber(redis.call('TIME')[1])
local window = tonumber(ARGV[2])
local idx = math.floor(now / window)                    -- 当前窗口序号
local cur_idx = tonumber(redis.call('GET', KEYS[1]..':idx') or '0')
local prev = tonumber(redis.call('GET', KEYS[1]..':prev') or '0')
local curr = tonumber(redis.call('GET', KEYS[1]..':curr') or '0')
if idx > cur_idx then                                    -- 滚动到新窗口
    if idx - cur_idx == 1 then redis.call('SET', KEYS[1]..':prev', curr)
    else redis.call('SET', KEYS[1]..':prev', 0) end
    redis.call('SET', KEYS[1]..':curr', 0)
    redis.call('SET', KEYS[1]..':idx', idx)
end
local overlap = 1 - (now - idx * window) / window
if prev * overlap + curr + tonumber(ARGV[3]) > tonumber(ARGV[1]) then
    return {0, math.ceil(prev * overlap + curr + tonumber(ARGV[3]) - tonumber(ARGV[1]))}
end
redis.call('INCRBY', KEYS[1]..':curr', tonumber(ARGV[3]))
redis.call('PEXPIRE', KEYS[1]..':curr', window * 2000)
return {1, 0}
```

> **retry_after 换算**：滑动窗口拒绝时脚本返回超量 `extra`，网关按
> `extra × window / limit` 估算还需等待的秒数（线性近似，窗口滚动即恢复）；
> 令牌桶返回精确的 `(cost - tokens) / rate` 秒；并发租约返回 `lease` 秒。
> 客户端统一按 `Retry-After` 头退避，不必关心内部算法。

### 4.2 令牌桶（Lua）

```lua
local now = tonumber(redis.call('TIME')[1])
local d = redis.call('HMGET', KEYS[1], 'tokens', 'ts')    -- rate, burst, cost 在 ARGV
local tokens = math.min(burst, tonumber(d[1] or burst) + (now - tonumber(d[2] or now)) * rate)
if tokens < cost then
    return {0, math.ceil((cost - tokens) / rate)}          -- 需等多少秒
end
redis.call('HMSET', KEYS[1], 'tokens', tokens - cost, 'ts', now)
redis.call('PEXPIRE', KEYS[1], 120000)
return {1, tokens - cost}
```

### 4.3 并发租约（Lua）

```lua
-- 持有者集合 hash: holder -> 租约到期时间戳；过期 holder 先清理再计数
for _, h in ipairs(redis.call('HKEYS', KEYS[1])) do
    if tonumber(redis.call('HGET', KEYS[1], h)) < now then
        redis.call('HDEL', KEYS[1], h)
    end
end
if redis.call('HLEN', KEYS[1]) >= max then return {0, redis.call('HLEN', KEYS[1])} end
redis.call('HSET', KEYS[1], holder, now + lease)
redis.call('PEXPIRE', KEYS[1], lease * 2 + 1000)
return {1, redis.call('HLEN', KEYS[1])}
```

长任务由持有者定期续租（gateway 里起一个续租线程），进程崩溃后租约自然过期回收。

**为什么用 Lua**：判定 + 计数必须原子（否则并发下超卖）；Lua 脚本单次 RTT 完成，且可用 `redis.call('TIME')` 取服务器时间，客户端时钟漂移不影响判定。

## 5. Redis 键设计

```
rl:{algo}:{dimension}:{key}[:{sub}]
    algo      = sw(滑动窗口) | tb(令牌桶) | sem(并发)
    dimension = ip | global | provider | model | agent | user
    子键      = idx / prev / curr / tokens / ts / 持有者id
TTL：全部键设过期（窗口 2 倍 / 120s），防止无限增长。
```

示例：`rl:sw:provider:kimi`、`rl:tb:model:deepseek-chat`、`rl:sem:agent:claude`。

> **Redis Cluster 注意**：`rl:sw:{dim}:{key}` 与子键 `rl:sw:{dim}:{key}:idx`
> 是不同物理键，哈希槽不同，Lua 内访问子键会报 CROSSSLOT。两条路线：
> ① 单 Redis 主从（本方案默认，无需处理）；
> ② 上 Cluster 时把 key 写成 hash tag 形式 `rl:sw:{dim}:{{key}}[:idx]`，
> 让主键与子键落入同一 slot（§11 的"当前设计满足 Cluster 单 key 要求"
> 仅对单主从成立，上 Cluster 前需按此改造）。

## 6. 一致性、故障与降级

- **近似一致**：跨实例的两次并发请求可能同时判定"未超限"，最终计数会略超。滑动窗口计数误差在 O(并发数)，对限流可接受；要严格可上 Redis 事务/Lua + 每个请求先扣后查。
- **Redis 故障**：默认 `fail: local`（fail-open，降级到进程内限速，保住主链路）；对 provider 配额可配 `fail: closed`（宁可拒绝也不打爆账单）。降级决策矩阵：

  | fail 配置 | Redis 正常 | Redis 故障 | 适用场景 |
  |---|---|---|---|
  | `local` | Redis 共享配额 | 本地兜底，放行不中断 | 入口 ip/global、agent 并发（默认） |
  | `closed` | Redis 共享配额 | 一律 429 + Retry-After | provider 配额（打爆账单 > 误杀请求） |

- **降级自动恢复**：本地兜底期间每 30s 探测一次 Redis（`PING` + 一次真实限流 eval），恢复后自动切回共享配额并打日志。当前实现是"一次故障永久降级"（`_call_backend` 直接换 LocalBackend），设计上应改为状态机：`active → degraded → probing → active`，避免 Redis 抖动后长期失去共享配额。
- **时钟偏差**：脚本内 `redis.call('TIME')`，客户端时钟只用于租约续期（偏差容忍度高）。
- **客户端重试**：429 带 `Retry-After`，客户端指数退避 + 随机 jitter，避免惊群。
- **网络抖动**：超时设短（如 300ms）并做一次重试，Redis 慢不拖慢请求主路径。

## 7. 本仓库接入点

| 文件 | 改动 |
|---|---|
| `config/ratelimit.yaml` | 新增：后端模式、规则表（维度/算法/限额） |
| `lib/ratelimit.py` | 新增：限流器（Redis Lua + 本地兜底），提供 `check / wait / acquire` |
| `lib/llm_client.py` | `_throttle` 改调 `limiter.provider_throttle`（保留等待语义） |
| `gateway.py` | 中间件：`ip`/`global` 超限 → 429 + Retry-After；agent 路径加并发租约 |

> **单例纪律**：全进程必须共用模块级 `rl.limiter` 单例。`gateway.py` 当前自建
> `LIMITER = rl.RateLimiter()`，与 `llm_client` 用的 `rl.limiter` 是两个实例；
> Redis 正常时两者共享配额无影响，但降级后本地计数会分池，同一进程内
> ip 维度被两个本地桶各自计数。应改为 `LIMITER = rl.limiter`（或删除自建实例）。

## 8. 观测与运营

- obs 统一字段（新增于拒绝/降级事件，`kind=rate_limit`）：
  `rate_limit_dimension / rate_limit_key / rate_limit_retry_after / rate_limit_backend`。
- 指标（从 `data/router.jsonl` 聚合）：各维度 放行数 / 拒绝数 / 平均等待秒 / 超限占比。
- 告警：拒绝率突增、provider 连续 429（配额配错）、Redis 降级事件。

> **放行事件不逐条落盘**（防日志膨胀）：放行侧以响应头 `X-RateLimit-Remaining`
> 暴露剩余额度，由监控定时采样聚合；拒绝与降级事件才写 `router.jsonl`。

## 9. 验收标准

1. 无 Redis 时行为与现状一致：本地模式限速仍生效（`python3 gateway.py` 直接可用）。
2. 有 Redis 时：两个网关进程对同一 `ip` 维度共享配额（进程 A 打满，进程 B 立即 429）。
3. 超限响应为标准 429 + `Retry-After`，客户端可按秒退避。
4. agent 并发：同时请求 3 个 agent 任务，第 3 个要么排队要么 429（取决于配置）。
5. 降级演练：停掉 Redis，网关自动切本地限速并打日志，不抛异常。

## 10. 边界情况

- **窗口边界**：滑动窗口计数在边界处为近似值，误差 ≤ 上一窗口 1 个请求；要求严格用滑动日志。
- **代理 IP**：多实例经 Nginx 转发时取 `X-Forwarded-For` 首跳，并限 `trusted_proxies`。
  注意：`gateway.py::_client_ip` 当前无条件采信 XFF 首跳，接入反代前必须补上
  "仅来自可信代理（Nginx 内网地址）的请求才采信 XFF" 的校验，
  否则客户端可伪造 `X-Forwarded-For` 直接绕过 ip 维度限流。
- **长任务租约**：agent 任务可能跑 10 分钟，租约必须续期；释放必须 `finally`。
- **大键**：并发租约的 `HKEYS` 扫描仅限单维度单 agent，规模可控；再大用 `SCAN` 或分片。
- **时钟**：租约时间戳用本地时钟，实例间时钟偏差 > 租约的一半时需 NTP 校准或全用服务器时间。

## 11. 后续演进

- 维度扩展：API key / 用户 / 团队配额，限流键与认证打通。
- Redis Cluster / 多副本：脚本要保证只访问单 key（当前设计满足）。
- 客户端 SDK：429 + Retry-After 语义内置到调用库。
- 自适应限流：按上游错误率动态收缩配额，替代固定 RPM。
