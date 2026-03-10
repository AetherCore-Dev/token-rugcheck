# Token RugCheck MCP — 运维手册

> 唯一的部署和运维文档。涵盖首次部署、日常更新、ag402 依赖升级、故障排查。

## 目录
- [1. 架构概览](#1-架构概览)
- [2. 首次部署](#2-首次部署)
- [3. 分支策略与发布流程](#3-分支策略与发布流程)
- [4. 日常更新（代码 + ag402 依赖）](#4-日常更新代码--ag402-依赖)
- [5. 数据备份](#5-数据备份)
- [6. 日常运维](#6-日常运维)
- [7. 故障排查](#7-故障排查)
- [8. 配置参考](#8-配置参考)
- [9. 安全修复记录](#9-安全修复记录)
- [10. 已知问题](#10-已知问题)
- [脚本速查](#脚本速查)

---

## 1. 架构概览

```
Client (AI Agent)
  │ HTTPS (TLS 终止于 Cloudflare)
  ▼
Cloudflare CDN (SSL termination, DDoS protection)
  │ HTTP → :80 (Flexible 模式)
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

### 组件说明

| 组件 | 端口 | 说明 |
|------|------|------|
| **rugcheck-audit** | `127.0.0.1:8000`（仅本机） | FastAPI 审计服务，不对外暴露 |
| **ag402-gateway** | `:80`（对外） | 支付网关，验证 USDC 链上支付后转发请求到审计服务 |
| **ag402-data** | — | Docker volume，存储 SQLite 重放保护数据（**必须备份**） |

### Cloudflare SSL 方案

本项目采用 **Cloudflare Flexible** 模式：

```
客户端 ──HTTPS──▶ Cloudflare ──HTTP:80──▶ 源站 Docker
```

| 模式 | 客户端→Cloudflare | Cloudflare→源站 | 源站要求 | 适用场景 |
|------|-------------------|-----------------|---------|---------|
| **Flexible** ✅ 采用 | HTTPS | HTTP:80 | 无需 TLS 证书 | 单服务 API，Cloudflare 做 SSL 终止 |
| Full | HTTPS | HTTPS:443 | 需自签证书 | 需要端到端加密 |
| Full (strict) | HTTPS | HTTPS:443 | 需 CA 签发证书 | 金融级安全要求 |

**选择 Flexible 的理由**：

1. 源站只暴露 HTTP:80，无需配置 TLS 证书，无需 Nginx/Caddy 反代
2. 客户端到 Cloudflare 全程 HTTPS 加密，满足安全需求
3. 减少运维复杂度，避免证书续期等问题
4. Cloudflare 已提供 DDoS 防护和 WAF

> **重要**：如果你之前将 Cloudflare SSL/TLS 改为 Full 或 Full (strict)，**必须改回 Flexible**，否则 Cloudflare 无法连接到源站（源站没有 HTTPS），会导致 5xx 错误。

---

## 2. 首次部署

### 前置条件

| 项目 | 要求 |
|------|------|
| 服务器 | Ubuntu 22.04+，1 vCPU / 1GB RAM 即可 |
| SSH | 本地已配置 `ssh root@<IP>` 免密登录 |
| 域名 | 一级子域名（如 `rugcheck.aethercore.dev`），Cloudflare 代理 |
| 钱包 | Solana 地址（收款用），mainnet 需确保有 USDC ATA |

### 一键部署（推荐）

```bash
bash scripts/deploy-oneclick.sh
```

交互式引导，依次完成：SSH 连接 → 服务器初始化 → 环境配置 → Docker 部署 → 健康检查 → 5 层验证。

脚本会自动：
- 安装 Docker 和防火墙
- 克隆代码到 `/opt/token-rugcheck`
- 生成 `.env` 配置文件
- 构建镜像并启动服务（含最新 ag402 依赖）
- 运行 5 层验证

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
ssh root@<IP> "cd /opt/token-rugcheck && \
  docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build"

# 4. 验证
bash scripts/verify.sh --server-ip <IP> --domain <domain>
```

### Cloudflare 配置（必做）

1. **DNS**：A 记录 → 服务器 IP，开启代理（橙色云朵）
2. **SSL/TLS** → **Flexible**
3. **注意**：免费版只支持一级子域 `*.example.com`，不支持 `*.*.example.com`

> **提示**：DNS 变更后通常 1-5 分钟生效，可通过 `curl -s https://<domain>/health` 验证。

### 首次部署后检查清单

- [ ] `curl -s https://<domain>/health` 返回 200
- [ ] `curl -s https://<domain>/v1/audit/<mint>` 返回 402（支付墙生效）
- [ ] Cloudflare SSL/TLS 已设为 Flexible
- [ ] 卖家钱包有 USDC ATA（mainnet 必须）
- [ ] 配置 UptimeRobot 监控 `https://<domain>/health`

---

## 3. 分支策略与发布流程

### 分支结构

```
main          ← 生产分支，只接受经过验证的 PR 合入，每次合入打 tag
  └── develop ← 集成分支，功能分支的汇集地，对应 devnet 环境
        └── feature/xxx ← 每个新功能独立分支，本地 test 模式开发
        └── fix/xxx     ← Bug 修复分支
```

**原则：**
- 禁止直接 `git push origin main`（在 GitHub Settings → Branches 中启用分支保护）
- 生产服务器**只**部署打了 tag 的版本，**不**自动跟踪 `main` HEAD
- `quick-update.sh` 的第三个参数指定 tag，不传则默认 `main` 最新（用于紧急修复）

### 日常开发流程

```
1. 从 develop 创建功能分支
   git checkout develop && git pull
   git checkout -b feature/my-new-feature

2. 本地开发，用 test 模式验证
   # Docker Compose 从 .env 文件读取环境变量，不能在命令行直接注入
   # 先基于 .env.example 生成本地 test 用 .env：
   cp .env.example .env                     # 复制模板（默认已是 X402_MODE=test）
   # 确认/修改 AG402_ADDRESS 为你的钱包地址，然后：
   docker compose up

3. 完成后 PR → develop（代码审查）

4. develop 在 devnet 环境集成测试通过

5. PR → main（代码审查 + CI 绿灯）

6. 合入后立即打 tag
   git tag -a v1.x.x -m "feat: 描述新功能"
   git push origin v1.x.x

7. 确认后人工部署到生产
   bash scripts/quick-update.sh <IP> <domain> v1.x.x
```

### 环境与分支对应关系

| 环境 | `X402_NETWORK` | 对应分支 | 说明 |
|------|----------------|----------|------|
| 本地 test | `mock` | `feature/*` | Mock 支付，无需链上，快速迭代 |
| 本地/云 devnet | `devnet` | `develop` | 真实交易逻辑，使用测试 SOL |
| 生产 mainnet | `mainnet` | `main` (tag) | 真实 USDC 支付 |

### 版本 Tag 规范

遵循语义化版本 `vMAJOR.MINOR.PATCH`：
- `PATCH`：bug 修复、文档更新、运维改进
- `MINOR`：向后兼容的新功能
- `MAJOR`：破坏性变更（API 不兼容、架构重构）

```bash
# 打 tag 并推送
git tag -a v1.2.0 -m "feat: add token comparison endpoint"
git push origin v1.2.0

# 查看所有 tag
git tag -l --sort=-version:refname | head -10
```

### 设置 GitHub 分支保护（推荐立即配置）

进入 GitHub → Settings → Branches → Add rule：
- Branch name pattern: `main`
- ✅ Require a pull request before merging
- ✅ Require approvals: 1
- ✅ Do not allow bypassing the above settings

---

## 4. 日常更新（代码 + ag402 依赖）

### 快速更新（推荐）

**推荐方式：部署指定 tag（稳定可控）**

```bash
# 部署指定 tag（推荐，生产必用）
bash scripts/quick-update.sh <server-ip> <domain> v1.2.0

# 示例
bash scripts/quick-update.sh 140.82.49.221 rugcheck.aethercore.dev v1.2.0
```

**紧急修复：部署 main 最新（省略 tag 参数）**

```bash
bash scripts/quick-update.sh 140.82.49.221 rugcheck.aethercore.dev
```

脚本自动执行：拉取指定 ref → 备份数据 → 重新构建（含 ag402 依赖更新） → 重启 → 健康检查 → 验证 → 输出回滚命令。

### 回滚

部署失败或服务异常时，使用更新结束时脚本打印的**回滚命令**：

```
  回滚命令:
    bash scripts/quick-update.sh 140.82.49.221 "rugcheck.aethercore.dev" abc1234
```

或手动回滚到上一个稳定 tag：

```bash
bash scripts/quick-update.sh 140.82.49.221 rugcheck.aethercore.dev v1.1.0
```

部署历史记录在服务器 `/opt/token-rugcheck/.deploy_history`，可查阅历次部署的 ref 和 commit。

### 完整重新部署

如需重新配置 `.env`（比如改钱包地址、改价格、改模式），使用一键部署脚本：

```bash
bash scripts/deploy-oneclick.sh
```

此脚本用于**首次部署或完整重新配置**（如修改钱包地址、价格、运行模式），始终拉取 `main` HEAD。日常代码更新请使用 `quick-update.sh`。

### 仅更新 ag402 依赖

如果只想升级 ag402 库（不改业务代码），SSH 到服务器执行：

```bash
ssh root@<IP>
cd /opt/token-rugcheck

# 强制重新构建（不使用缓存，确保拉取最新 ag402）
docker compose -f docker-compose.yml -f docker-compose.prod.yml build --no-cache
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

# 验证 ag402 版本
docker compose exec ag402-gateway pip show ag402-core ag402-mcp
```

### 更新流程中脚本的自动处理

| 场景 | 脚本行为 |
|------|---------|
| 指定 tag 部署 | `git fetch --tags` + `git checkout <tag>`，精确锁定版本 |
| 指定分支部署 | `git checkout <branch>` + `git pull`，获取分支最新 |
| 服务器有未提交文件导致 git pull 冲突 | 自动 `git stash --include-untracked` 后重试 |
| 旧容器未完全停止 | `docker compose down --remove-orphans` + 强制清理残留容器 |
| 端口被占用（80/8000） | 自动检测并 kill 占用进程后重试 |
| Docker build 缓存导致 ag402 不更新 | 使用 `--no-cache` 强制重建 |
| 部署完成 | 记录 ref/commit 到 `.deploy_history`，打印回滚命令 |

---

## 5. 数据备份

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

## 6. 日常运维

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

# 查看 ag402 依赖版本
docker compose exec ag402-gateway pip show ag402-core ag402-mcp

# 清理磁盘
docker system prune -f
```

### 监控

- `/health` — 健康检查（200=正常, 503=降级），Docker 通过 `curl -sf` 轮询
- `/stats` — 请求统计（仅 loopback 可访问）
- `/metrics` — Prometheus 指标（仅 loopback 可访问），路径已归一化防止基数爆炸
- 推荐配置 UptimeRobot 监控 `https://<domain>/health`
- 生产环境建议设 `RUGCHECK_PRODUCTION=true` 禁用 `/docs`

---

## 7. 故障排查

### 诊断流程

```
容器是否在运行？
  ├─ 否 → docker compose logs --tail 50
  └─ 是 → 端口是否可达？
           ├─ localhost:8000 不通 → 审计服务崩溃，查看 rugcheck-audit 日志
           ├─ localhost:80 不通 → 网关崩溃，查看 ag402-gateway 日志
           └─ 端口正常 → 外部能访问吗？
                         ├─ IP:80 不通 → 防火墙问题 (ufw status)
                         └─ 域名不通 → 检查 Cloudflare DNS 和 SSL 配置
```

### 常见问题

| 问题 | 原因 | 解决 |
|------|------|------|
| **端口已被占用 (port is already allocated)** | 旧容器未完全清理 | `docker ps -a \| grep token` 找到残留容器 → `docker rm -f <id>` → 重新启动 |
| **git pull 冲突 (Aborting)** | 服务器有未提交/未跟踪文件 | `git stash --include-untracked && git pull origin main`（脚本已自动处理） |
| 容器秒退 | uvloop 与 aiosqlite 冲突 | 确认 Dockerfile 或 .env 有 `UVLOOP_INSTALL=0` |
| health 报 degraded | 启动后无请求，超时误判 | 正常现象，首次请求后恢复 |
| 402 但付款后仍 403 | 缺少 USDC ATA | devnet: `spl-token create-account` 创建 USDC ATA |
| HTTPS 证书错误 | 使用了二级子域名 | 改用一级子域名（Cloudflare 免费版限制） |
| **域名 HTTPS 返回 5xx** | Cloudflare SSL 设为 Full/Full(strict) 但源站无 TLS | **改回 Flexible**（Cloudflare → SSL/TLS → Flexible） |
| Docker build OOM | 服务器内存不足 | `dd if=/dev/zero of=/swapfile bs=1M count=2048 && swapon /swapfile` |
| gateway 启动异常 | 无 SOLANA_PRIVATE_KEY | gateway.py 已有 fallback，正常 |
| ag402 版本过旧 | Docker build 使用了缓存 | `docker compose build --no-cache` 强制重建 |

### 紧急恢复

如果服务完全无法启动，执行以下步骤：

```bash
ssh root@<IP>
cd /opt/token-rugcheck

# 1. 暴力清理所有相关容器和网络
docker compose -f docker-compose.yml -f docker-compose.prod.yml down --remove-orphans --volumes=false
docker ps -a --filter "name=token-rugcheck" -q | xargs -r docker rm -f
docker network prune -f

# 2. 确认端口释放
ss -tlnp | grep -E ':80 |:8000 '
# 如果仍被占用：
# lsof -i :80 和 lsof -i :8000 找到 PID 并 kill

# 3. 清理 git 状态，恢复到上一个稳定 tag
git stash --include-untracked
git clean -fd
git fetch --tags origin
git checkout v1.x.x  # 替换为实际的稳定 tag，参考 .deploy_history

# 4. 重建并启动
docker compose -f docker-compose.yml -f docker-compose.prod.yml build --no-cache
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

# 5. 验证
docker compose ps
curl -s http://localhost:80/health
curl -s http://localhost:8000/health
```

---

## 8. 配置参考

### .env 关键变量

| 变量 | 必需 | 说明 | 示例 |
|------|------|------|------|
| `X402_MODE` | ✅ | 运行模式 | `test` / `production` |
| `X402_NETWORK` | ✅ | Solana 网络 | `devnet` / `mainnet` / `mock` |
| `AG402_ADDRESS` | ✅ | 收款 Solana 地址 | `YourSo1ana...` |
| `AG402_PRICE` | ✅ | 单次审计价格 (USDC) | `0.02` |
| `SOLANA_RPC_URL` | 生产强烈推荐 | Solana RPC 节点。**默认公共节点有速率限制，高并发时支付验证会超时**；生产建议使用私有节点（Helius 免费层即可） | `https://mainnet.helius-rpc.com/?api-key=xxx` |
| `SOLANA_PRIVATE_KEY` | 买方测试用 | 私钥（绝勿提交 Git） | — |
| `RUGCHECK_PRODUCTION` | 生产推荐 | 禁用 /docs /redoc | `true` / `false` |
| `UVICORN_WORKERS` | 可选 | worker 进程数 | `1`（默认） |
| `UVICORN_LIMIT_CONCURRENCY` | 可选 | 最大并发连接数 | `0`（无限制） |
| `UVLOOP_INSTALL` | ✅ | 必须设为 0 | `0` |
| `GOPLUS_APP_KEY` | 可选 | GoPlus API key | — |
| `GOPLUS_APP_SECRET` | 可选 | GoPlus API secret | — |

---

## 9. 安全修复记录

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

### v0.1.2 安全与运维加固 (2025-07)

修复了 7 个安全/运维问题，测试从 117 → 118 个：

| # | 修复内容 | 文件 | 严重度 | 说明 |
|---|----------|------|--------|------|
| S6 | **CF-Connecting-IP 信任模型** | `server.py` | 🔴 高 | 原行为：无条件信任 `CF-Connecting-IP`/`X-Forwarded-For`，攻击者可伪造 IP 绕过限流。现行为：仅当 socket peer 属于 Cloudflare IP 段或 loopback 时才读取代理头 |
| S7 | **健康检查改用 curl** | `Dockerfile`, `Dockerfile.gateway`, `docker-compose.yml` | 🟡 中 | Docker healthcheck 从 Python 脚本改为 `curl -sf`，减少启动开销，添加 `start_period: 10s` |
| S8 | **生产环境禁用 /docs** | `config.py`, `server.py` | 🟡 中 | `RUGCHECK_PRODUCTION=true` 时关闭 `/docs`、`/redoc`、`/openapi.json`，防止 API 文档泄露 |
| S9 | **Prometheus 路径归一化** | `server.py` | 🟡 中 | 未知路径统一返回 `"other"`，防止恶意请求导致 Prometheus 基数爆炸 |
| S10 | **降级报告短缓存** | `server.py`, `cache.py` | 🟡 中 | 降级（不完整）报告 TTL 仅 10 秒（而非正常 TTL），上游恢复后快速获取新数据 |
| S11 | **uvicorn 多 worker 支持** | `main.py` | 🟢 低 | 支持 `UVICORN_WORKERS` 环境变量，>1 时使用 factory 模式 |
| S12 | **DailyQuota 定时清理** | `server.py` | 🟢 低 | 后台任务每小时清理非当天的配额条目，防止内存无限增长 |

**新增环境变量**：`RUGCHECK_PRODUCTION`、`UVICORN_WORKERS`、`UVICORN_LIMIT_CONCURRENCY`

---

### v0.1.3 主网上线 + 买方测试脚本 (2026-03)

主网正式上线，地址 `https://rugcheck.aethercore.dev`。新增 `mainnet_buyer_test.py` 命令行审计工具。

| # | 修改内容 | 文件 | 说明 |
|---|----------|------|------|
| F1 | **mainnet_buyer_test.py 改为 CLI 工具** | `mainnet_buyer_test.py` | 从硬编码 3 个 case 改为 `argparse` 接收任意 mint 地址；支持单个或多个代币；支持 `--gateway` 指定网关 |
| F2 | **多源私钥加载** | `mainnet_buyer_test.py` | 不再强制 `export SOLANA_PRIVATE_KEY`；按优先级尝试：环境变量 → `~/.ag402/.env` → 解密 `~/.ag402/wallet.key`（ag402 setup 加密钱包） |
| F3 | **修复 wallet.key 解密调用** | `mainnet_buyer_test.py` | 修复 `decrypt_private_key` 参数顺序错误：原来传 `(path, password)`，实际 API 是 `(password, encrypted_data_dict)`；需先调用 `load_encrypted_wallet(path)` 加载字典 |
| F4 | **SOL 余额预检** | `mainnet_buyer_test.py` | Step 3 新增原生 SOL 余额检查（通过 Solana RPC），不足 0.01 SOL 时提前报错而非让交易失败 |
| F5 | **SOL 余额属性名修复** | `mainnet_buyer_test.py` | `SolanaAdapter` 内部属性是 `_keypair`（带下划线），修复 `provider.keypair` → `provider._keypair` |
| F6 | **InsufficientFundsForRent 错误识别** | `mainnet_buyer_test.py` | 交易失败时检测 `InsufficientFundsForRent` 错误，给出明确的 "SOL 不足" 提示而非通用 FAIL |
| F7 | **README 重写** | `README.md` | 以正式上线为核心重写，突出体验地址、快速上手流程、API 响应示例 |

**主网验证结果**：

- BONK (`DezXAZ...B263`) — ✅ 审计成功，正确识别为 SAFE（risk_score=3）
- TRUMP (`6p6x...GiPN`) — 交易确认超时（Solana 网络问题，非服务端问题）

---

### v0.1.4 分支保护 + 部署安全加固 (2026-03)

新增分支策略文档，加固部署脚本安全性：

| # | 修改内容 | 文件 | 类型 | 说明 |
|---|----------|------|------|------|
| D1 | **新增分支策略与发布流程文档** | `OPERATIONS.md` | 📖 文档 | 新增第 3 节：main/develop/feature 分支规范、环境与分支对应关系、语义化 tag 规范、GitHub 分支保护配置步骤 |
| D2 | **quick-update.sh 支持指定 tag/分支/commit 部署** | `quick-update.sh` | 🔧 功能 | 新增第三个参数 `[ref]`，支持精确锁定版本（`v1.2.0`）、分支（`develop`）或 commit hash；默认仍为 `main` |
| D3 | **quick-update.sh 部署历史记录** | `quick-update.sh` | 🔧 功能 | 每次部署后写入服务器 `.deploy_history`（已加入 `.gitignore`），记录时间、ref、commit、前一个 commit |
| D4 | **quick-update.sh 自动打印回滚命令** | `quick-update.sh` | 🔧 功能 | 部署成功后打印回滚命令；通过 `trap EXIT` 确保构建失败、健康检查超时等场景也能输出回滚命令 |
| S13 | **GIT_REF 输入校验防止 shell 注入** | `quick-update.sh` | 🔴 安全 | `[ref]` 参数经白名单正则校验（`^[a-zA-Z0-9._/-]+$`），拒绝含特殊字符的输入，防止通过参数在远端服务器执行任意命令 |
| D5 | **deploy-oneclick.sh 加入职责说明** | `deploy-oneclick.sh` | 📖 文档 | Phase 5 新增提示，明确此脚本用于首次部署/完整重配，日常更新应使用 `quick-update.sh <IP> <domain> <tag>` |
| D6 | **setup-server.sh 加入职责注释** | `setup-server.sh` | 📖 文档 | 说明 `setup-server.sh` 始终拉取 main HEAD（初始化路径），与 tag 锁定的 `quick-update.sh` 职责边界明确 |
| D7 | **.gitignore 新增 .deploy_history** | `.gitignore` | 🔒 安全 | 部署历史（含 commit hash 记录）加入忽略列表，防止运维日志意外提交到公开仓库 |

### v0.1.5 文档修正与上线前风险提示 (2026-03)

修正文档错误，补充上线前必知的三项风险：

| # | 修改内容 | 文件 | 类型 | 说明 |
|---|----------|------|------|------|
| D8 | **修正本地 test 模式启动命令** | `OPERATIONS.md` | 📖 文档 | Docker Compose 从 `.env` 读取变量，不支持命令行内联注入；改为 `cp .env.example .env && docker compose up` 的正确用法 |
| D9 | **SOLANA_RPC_URL 生产风险说明** | `OPERATIONS.md` | 📖 文档 | 默认公共 RPC 有速率限制，高并发时支付验证超时；配置参考表格新增说明，推荐使用 Helius 私有节点 |
| D10 | **ag402 未锁版本风险** | `OPERATIONS.md` | 📖 文档 | 已知问题表格新增 A8：`--no-cache` 构建每次拉最新 ag402，上游破坏性变更可能导致部署失败 |
| D11 | **上线前必做事项补充** | `OPERATIONS.md` | 📖 文档 | 需要人工处理表格新增：卖家钱包 USDC ATA 创建（首笔付款前必须）、私有 RPC 配置说明 |

---

## 10. 已知问题

### ag402 库的问题（上游，需等待更新）

| # | 问题 | 严重度 | 规避方式 |
|---|------|--------|---------|
| A1 | `ag402 serve` 硬编码 host=127.0.0.1 | 🔴 高 | 自建 `gateway.py` 绕过 CLI |
| A2 | uvloop 与 aiosqlite 冲突 | 🔴 高 | `UVLOOP_INSTALL=0` |
| A3 | 缺 USDC ATA 时报 `InsufficientFundsForRent` | 🟡 中 | 确保买方钱包有 ≥0.01 SOL；`mainnet_buyer_test.py` 已增加 SOL 预检和明确提示 |
| A4 | ~~无私钥时 get_provider() 异常~~ | ✅ 已修复 | 生产模式直接拒绝启动 (S1) |
| A5 | 无收入报表 API | 🟢 低 | 解析日志 |
| A6 | Solana 主网交易确认超时导致 403 | 🟡 中 | 网络瞬时问题，重试即可；网关返回 `Payment not confirmed on-chain` |
| A7 | `ag402 run` 不自动解密 `wallet.key` | 🟡 中 | `mainnet_buyer_test.py` 已自行实现多源私钥加载（F2/F3） |
| A8 | ag402 依赖未锁定版本，`--no-cache` 构建每次拉最新 | 🟡 中 | 上游发布破坏性变更时会导致构建失败或行为异常；如遇异常可在 Dockerfile 中临时锁定版本（如 `ag402-mcp==x.y.z`）后回滚 |

### 需要人工处理的一次性事项

| 事项 | 说明 |
|------|------|
| 旋转 devnet 私钥 | 旧私钥曾在 Git 历史中，建议生成新密钥对 |
| **卖家钱包创建 USDC ATA** | 首笔真实收款前必须完成，否则买家交易报 `InsufficientFundsForRent`（swap 一次 USDC 即可自动创建） |
| **配置私有 Solana RPC** | 生产环境强烈推荐，避免公共节点限速导致支付验证超时；推荐 [Helius](https://helius.dev) 免费层 |
| 配置 crontab 定时备份 | `0 3 * * * cd /opt/token-rugcheck && bash scripts/backup-data.sh` |
| 部署 UptimeRobot | 监控 `https://<domain>/health` |

---

## 脚本速查

| 脚本 | 功能 | 用法 |
|------|------|------|
| `deploy-oneclick.sh` | 一键部署（**首次部署或完整重新配置**，始终拉取 main HEAD） | `bash scripts/deploy-oneclick.sh` |
| `quick-update.sh` | 快速更新（支持 tag/分支/commit，含回滚） | `bash scripts/quick-update.sh <IP> [domain] [ref]` |
| `backup-data.sh` | 数据备份 | `bash scripts/backup-data.sh` |
| `setup-server.sh` | 服务器初始化 | 被 deploy-oneclick 调用 |
| `generate-env.sh` | 生成 .env | 被 deploy-oneclick 调用 |
| `verify.sh` | 5 层部署验证 | `bash scripts/verify.sh --server-ip <IP>` |

### quick-update.sh 常用命令速查

```bash
# 部署指定 tag（生产推荐）
bash scripts/quick-update.sh <IP> <domain> v1.2.0

# 部署 main 最新（紧急修复用）
bash scripts/quick-update.sh <IP> <domain>

# 部署 develop 分支（预发测试）
bash scripts/quick-update.sh <IP> "" develop

# 回滚到指定版本
bash scripts/quick-update.sh <IP> <domain> v1.1.0

# 查看部署历史（在服务器上执行）
cat /opt/token-rugcheck/.deploy_history
```
