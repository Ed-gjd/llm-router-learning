# 密钥盘点与轮换预案（课5.3）

> 2026-08-18 · 原则：key 只走环境变量/外部配置，绝不进代码、日志、git。

## 各 key 存放位置

- **DEEPSEEK_API_KEY**：`~/.bashrc` → platform.deepseek.com 重新生成后改 bashrc
- **DASHSCOPE_API_KEY / BASE_URL**：`~/.config/aliyun/cc.env`（600）→ 百炼控制台 → 改 cc.env
- **MOONSHOT_API_KEY**：`cc.env` + `~/.kimi-code/config.toml` **两处** → platform.moonshot.cn → 两处都改
- **GEMINI_API_KEY**：`cc.env` → aistudio.google.com → 改 cc.env

## 审计检查

- 代码扫描：`check_secrets.py`（找硬编码 sk-）
- 观测日志：`data/router.jsonl` 不含 key 字段（只有 provider/model/token/成本）
- git：非 git 仓库；若 init，`.gitignore` 必须含 `*.env` / `cc.env` / `*key*` / `data/`
- 全局 pre-commit 已有 git-secrets hook 兜底（`~/.git-hooks`）

## 轮换流程（通用）

1. 在对应平台控制台作废旧 key、生成新 key
2. 更新上面"位置"里所有存放点
3. 跑 `check_secrets.py` + 一次真实调用验证新 key 生效
