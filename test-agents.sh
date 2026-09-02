#!/usr/bin/env bash
# 一次性实测全部 agent CLI（各自后端，命令均已在 2026-08-18 验证过）
# 用法: bash test-agents.sh
set +e
source ~/.config/aliyun/cc.env
# 注意：只有 gemini 需要代理（Google 墙内）；其余 7 个国内直连，不挂代理
P="用不超过10个字介绍什么是路由"

echo "===== 1) claude   （DeepSeek / Anthropic兼容）====="
timeout 90 claude -p "$P" --output-format json 2>/dev/null | python3 -c "import sys,json;d=json.load(sys.stdin);print('→',d.get('result'))" 2>/dev/null || echo "→ (空/失败)"

echo "===== 2) codex    （DeepSeek / Responses）====="
timeout 90 codex exec --json --skip-git-repo-check "$P" 2>/dev/null | grep -oE '"text":"[^"]{1,50}"' | head -1 || echo "→ (空/失败)"

echo "===== 3) hermes   （DeepSeek）====="
timeout 60 hermes -z "$P" --provider deepseek -m deepseek-v4-flash 2>/dev/null | head -1 || echo "→ (空/失败)"

echo "===== 4) qwen     （DeepSeek / openai兼容）====="
timeout 60 env OPENAI_API_KEY="$DEEPSEEK_API_KEY" OPENAI_BASE_URL="https://api.deepseek.com" OPENAI_MODEL="deepseek-v4-flash" \
  qwen -p "$P" --output-format json --auth-type openai 2>/dev/null | grep -oE '"result":"[^"]{1,50}"' | head -1 || echo "→ (空/失败)"

echo "===== 5) opencode （DeepSeek）====="
timeout 90 opencode run -m deepseek/deepseek-v4-flash --format json "$P" 2>/dev/null | grep -oE '"text":"[^"]{1,50}"' | head -1 || echo "→ (空/失败)"

echo "===== 6) kimi     （DeepSeek，config.toml 已加 deepseek-openai provider）====="
timeout 60 kimi -p "$P" --model deepseek-v4-flash --output-format text 2>/dev/null | tail -1 || echo "→ (空/失败)"

echo "===== 7) cline    （DeepSeek，prompt 必须放最后）====="
timeout 60 cline -P openai-compatible -m deepseek-v4-flash --json "describe routing in 10 words" 2>/dev/null | grep -oE '"text":"[^"]{1,50}"' | head -1 || echo "→ (空/失败)"

echo "===== 8) gemini   （Google；仅此条挂代理；若你能直连就把 env 前缀去掉）====="
timeout 60 env https_proxy=http://127.0.0.1:10080 http_proxy=http://127.0.0.1:10080 \
  gemini -p "$P" --skip-trust 2>/dev/null | tail -1 || echo "→ (空/失败)"

echo ""
echo "===== 完成。gemini 若空：隔几秒重跑 'bash test-agents.sh' 或单跑第8条 ====="
