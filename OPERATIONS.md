# Token RugCheck MCP — 运维手册

> 唯一的部署和运维文档。涵盖首次部署、日常更新、故障排查。

## 目录
- [1. 架构概览](#1-架构概览)
- [2. 首次部署](#2-首次部署)
- [3. 日常更新](#3-日常更新)
- [4. 数据备份](#4-数据备份)
- [5. 日常运维](#5-日常运维)
- [6. 故障排查](#6-故障排查)
- [7. 配置参考](#7-配置参考)
- [8. 已知问题](#8-已知问题)

---

## 1. 架构概览

```
Client (AI Agent)
  │ HTTPS
  ▼
Cloudflare CDN (SSL termination, DDoS protection)
  │ HTTP → :80
  ▼
┌─────────────────────────────────────────────┐
│  Docker Compose                             │
│                                             │
│  ┌─────────────┐     ┌──────────────────┐   │
│  │ ag402-gateway│────▶│ rugcheck-audit   │   │
│  │ :80 (public) │     │ :8000 (internal) │   │
│  │ 支付验证     │     │ 审计引擎         │   │
│  └──────┬──────┘     └──────────────────┘   │
│         │                                    │
│    ag402-data (SQLite volume, 重放保护)       │
└─────────────────────────────────────────────┘
```

- **rugcheck-audit** — FastAPI 审计服务，绑定 127.0.0.1:8000（不对外）
- **ag402-gateway** — 支付网关，:80 对外，验证 USDC 支付后转发请求到审计服务
- **ag402-data** — Docker volume，存储 SQLite 重放保护数据（**必须备份**）

---

## 2. 首次部署

### 前置条件

| 项目 | 要求 |
|------|------|
| 服务器 | Ubuntu 22.04+，1 vCPU / 1GB RAM 即可 |
| 域名 | 一级子域名（如 `rugcheck.aethercore.dev`），Cloudflare 代理 |
| 钱包 | Solana 地址（收款用），devnet 需要先创建 USDC ATA |

### 一键部署（推荐）

```bash
bash scripts/deploy-oneclick.sh
```

交互式引导，依次完成：SSH 连接 → 服务器初始化 → 环境配置 → Docker 部署 → 健康检查 → 5 层验证。

### 手动部署

```bash
# 1. 初始化服务器（Docker + 防火墙 + 克隆代码）
scp scripts/setup-server.sh root@<IP>:/tmp/
ssh root@<IP> "bash /tmp/setup-server.sh"

# 2. 生成 .env
bash scripts/generate-env.sh \
  --mode production \
  --address <your_solana_address> \
  --price 0.02 \
  --output .env.production

# 3. 上传 .env 并部署
scp .env.production root@<IP>:/opt/token-rugcheck/.env
ssh root@<IP> "cd /opt/token-rugcheck && docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build"

# 4. 验证
bash scripts/verify.sh --server-ip <IP> --domain <domain>
```

### Cloudflare 配置

1. A 记录 → 服务器 IP，开启代理（橙色云朵）
2. SSL/TLS → Full (strict)
3. **注意**：免费版只支持一级子域 `*.example.com`，不支持 `*.*.example.com`

---

## 3. 日常更新

代码修改后，一条命令完成更新：

```bash
bash scripts/quick-update.sh <server-ip> [domain]

# 示例
bash scripts/quick-update.sh 140.82.49.221 rugcheck.aethercore.dev
```

自动执行：拉取代码 → 备份数据 → 重新构建 → 重启 → 健康检查 → 验证。

---

## 4. 数据备份

ag402-data SQLite 存储支付重放保护数据，丢失会导致重复支付攻击。

```bash
# 手动备份
bash scripts/backup-data.sh

# 只保留最近 7 份
bash scripts/backup-data.sh --keep 7

# 备份并 SCP 到远程
BACKUP_REMOTE_HOST=backup-server bash scripts/backup-data.sh --remote scp

# 定时备份（crontab）
0 3 * * * cd /opt/token-rugcheck && bash scripts/backup-data.sh >> /var/log/rugcheck-backup.log 2>&1
```

---

## 5. 日常运维

```bash
# SSH 到服务器
ssh root@<IP>
cd /opt/token-rugcheck

# 查看状态
docker compose ps
docker compose logs --tail 50

# 重启
docker compose restart

# 切换模式（修改 .env 后）
docker compose down && docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

# 改价格
sed -i 's/AG402_PRICE=.*/AG402_PRICE=0.05/' .env
docker compose restart ag402-gateway

# 查看交易历史
docker compose logs ag402-gateway | grep "payment"

# 清理磁盘
docker system prune -f
```

### 监控

- `/health` — 健康检查（200=正常, 503=降级）
- `/stats` — 请求统计（仅 loopback 可访问）
- `/metrics` — Prometheus 指标（仅 loopback 可访问）
- 推荐配置 UptimeRobot 监控 `https://<domain>/health`

---

## 6. 故障排查

### 诊断流程

```
容器是否在运行？
  ├─ 否 → docker compose logs --tail 50
  └─ 是 → 端口是否可达？
           ├─ localhost:8000 不通 → 审计服务崩溃，查看 rugcheck-audit 日志
           ├─ localhost:80 不通 → 网关崩溃，查看 ag402-gateway 日志
           └─ 端口正常 → 外部能访问吗？
                         ├─ IP:80 不通 → 防火墙问题 (ufw status)
                         └─ 域名不通 → Cloudflare DNS 或 SSL 配置
```

### 常见问题

| 问题 | 原因 | 解决 |
|------|------|------|
| 容器秒退 | uvloop 与 aiosqlite 冲突 | 确认 .env 有 `UVLOOP_INSTALL=0` |
| health 报 degraded | 启动后无请求，超时误判 | 正常现象，首次请求后恢复 |
| 402 但付款后仍 403 | 缺少 USDC ATA | devnet: `spl-token create-account` 创建 USDC ATA |
| HTTPS 证书错误 | 使用了二级子域名 | 改用一级子域名（Cloudflare 免费版限制） |
| Docker build OOM | 服务器内存不足 | `dd if=/dev/zero of=/swapfile bs=1M count=2048 && swapon /swapfile` |
| gateway 启动异常 | 无 SOLANA_PRIVATE_KEY | gateway.py 已有 fallback，正常 |

---

## 7. 配置参考

### .env 关键变量

| 变量 | 必需 | 说明 | 示例 |
|------|------|------|------|
| `X402_MODE` | ✅ | 运行模式 | `test` / `devnet` / `production` |
| `X402_NETWORK` | ✅ | Solana 网络 | `devnet` / `mainnet-beta` |
| `AG402_ADDRESS` | ✅ | 收款 Solana 地址 | `YourSo1ana...` |
| `AG402_PRICE` | ✅ | 单次审计价格 (USDC) | `0.02` |
| `SOLANA_RPC_URL` | devnet 可选 | 自定义 RPC | `https://api.devnet.solana.com` |
| `SOLANA_PRIVATE_KEY` | 买方测试用 | 私钥（绝勿提交 Git） | — |
| `UVLOOP_INSTALL` | ✅ | 必须设为 0 | `0` |
| `GOPLUS_APP_KEY` | 可选 | GoPlus API key | — |
| `GOPLUS_APP_SECRET` | 可选 | GoPlus API secret | — |

---

## 8. 安全修复记录

### v0.1.1 安全加固 (2025-06)

修复了 4 个安全问题，新增 1 个测试模块，测试从 78 → 117 个：

| # | 修复内容 | 文件 | 严重度 | 说明 |
|---|----------|------|--------|------|
| S1 | **生产模式网关不再静默降级为 mock** | `gateway.py` | 🔴 高 | 原行为：生产模式下 PaymentVerifier 初始化失败时自动降级为 mock（免费访问）。现行为：`sys.exit(1)` 拒绝启动，防止付费 API 被免费访问 |
| S2 | **缓存深拷贝防止共享状态污染** | `cache.py` | 🟡 中 | `get()`/`set()` 现在使用 `model_copy(deep=True)`，防止调用者修改 metadata 后污染缓存中的原始数据 |
| S3 | **未知 IP 不再绕过限流** | `server.py` | 🟡 中 | 原行为：`request.client` 为 None 时 `"unknown"` IP 被无条件放行。现行为：归入 `__unknown__` 统一桶限流 |
| S4 | **代理真实 IP 解析** | `server.py` | 🟡 中 | 新增 `_resolve_client_ip()` 函数，按 `CF-Connecting-IP` → `X-Forwarded-For` → `request.client` 优先级解析，确保 Cloudflare 后的限流按真实 IP 生效 |
| S5 | **PLACEHOLDER_ADDRESS 统一导出** | `config.py` | 🟢 低 | 占位地址常量从 `config.py` 导出，`gateway.py` 不再维护副本 |

**新增测试**：

| 测试文件 | 新增测试数 | 覆盖内容 |
|----------|:---:|----------|
| `test_security.py` | +7 | 网关生产模式崩溃、缓存深拷贝（get/set）、CF/XFF IP 解析、未知 IP 限流、占位地址导出 |
| `test_payment_security.py` | +8 (新文件) | 402 挑战、伪造支付凭据拒绝、欠付拒绝、错误地址拒绝、重放攻击拒绝、过期凭据拒绝 |

---

## 9. 已知问题

### ag402 库的问题（上游，需等待更新）

| # | 问题 | 严重度 | 规避方式 |
|---|------|--------|---------|
| A1 | `ag402 serve` 硬编码 host=127.0.0.1 | 🔴 高 | 自建 `gateway.py` 绕过 CLI |
| A2 | uvloop 与 aiosqlite 冲突 | 🔴 高 | `UVLOOP_INSTALL=0` |
| A3 | 缺 USDC ATA 时 403 无明确错误 | 🟡 中 | 文档提醒先创建 ATA |
| A4 | ~~无私钥时 get_provider() 异常~~ | ✅ 已修复 | 生产模式直接拒绝启动 (S1) |
| A5 | 无收入报表 API | 🟢 低 | 解析日志 |

### 需要人工处理的一次性事项

| 事项 | 说明 |
|------|------|
| 旋转 devnet 私钥 | 旧私钥曾在 Git 历史中，建议生成新密钥对 |
| 配置 crontab 定时备份 | `0 3 * * * cd /opt/token-rugcheck && bash scripts/backup-data.sh` |
| 部署 UptimeRobot | 监控 `https://<domain>/health` |

---

## 脚本速查

| 脚本 | 功能 | 用法 |
|------|------|------|
| `deploy-oneclick.sh` | 一键部署（交互式） | `bash scripts/deploy-oneclick.sh` |
| `quick-update.sh` | 快速更新 | `bash scripts/quick-update.sh <IP> [domain]` |
| `backup-data.sh` | 数据备份 | `bash scripts/backup-data.sh` |
| `setup-server.sh` | 服务器初始化 | 被 deploy-oneclick 调用 |
| `generate-env.sh` | 生成 .env | 被 deploy-oneclick 调用 |
| `verify.sh` | 5 层部署验证 | `bash scripts/verify.sh --server-ip <IP>` |
