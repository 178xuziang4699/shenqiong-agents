# 安全政策

## 密钥与数据

本项目**不把任何凭据写进代码库**。以下内容由 `.gitignore` 排除，请确认从未提交：

- `var/admin_token.txt`：管理后台超管 / 只读令牌
- `var/llm_runtime.json`、`.env`：模型 API Key 与运行配置
- `var/agent.db*`、备份：用户会话、聊天记录、手机号等数据
- `mobile/agent.keystore`：Android 签名私钥
- `*.apk`、`output/`、`logs/`：构建与运行产物

首次部署时 `deploy.sh` 会生成随机令牌并写入 600 权限的 `.env`；
模型密钥只从 `AP_LLM_API_KEY` 环境变量读取，或经网页「模型状态」临时存入进程内存。

## 部署建议

- 管理后台不要直接暴露公网；放在反向代理 / VPN 之后，并定期轮换令牌。
- 需要查看用户聊天原文时使用超管令牌，所有读取都会写入 `query_audit` 留痕。
- 升级迭代分析仅处理 `consent=1` 的会话；用户撤回同意后其会话立即不再参与。
- 生产环境建议把 `AP_CONFIRM_MODE` 设为 `prompt`，并切换到 PostgreSQL。
- Android 客户端请使用**你自己生成**的 keystore，通过 `AP_KEYSTORE_STOREPASS` /
  `AP_KEYSTORE_KEYPASS` 提供口令；仓库里的 `changeit` 只是占位符。

## 报告漏洞

请不要为安全问题开公开 Issue。请通过私有渠道联系维护者，附上复现步骤、
影响范围与建议修复，确认后再公开披露。
