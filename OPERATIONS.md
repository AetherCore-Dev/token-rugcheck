# Token RugCheck MCP — Operations Skills Guide
# 完整运维技能手册：上线、更新、运维、验证

## 目录
- [1. 项目架构](#1-项目架构)
- [2. 首次上线部署](#2-首次上线部署)
- [3. 一键更新镜像并验证](#3-一键更新镜像并验证)
- [4. 端到端测试验证](#4-端到端测试验证)
- [5. 日常运维](#5-日常运维)
- [6. 故障排查](#6-故障排查)
- [7. 关键配置参考](#7-关键配置参考)
- [8. 监控 (Prometheus)](#8-监控-prometheus)
- [9. 踩坑记录](#9-踩坑记录)

---

## 1. 项目架构

### 1.1 系统拓扑

```
用户 / AI Agent
    │  HTTPS (443, TLS 1.3)
    ▼
Cloudflare CDN  ← 域名: api.aethercore.dev
    │              证书: *.aethercore.dev (Let's Encrypt, Cloudflare 自动管理)
    │              模式: Proxied (橙色云朵)
    │  HTTP (80)   回源到宿主机 80 端口
    ▼
┌─────────────────────────────────────────────────┐
│  Server: 45.32.54.209 (Ubuntu 22.04)             │
│                                                  │
│  Docker Compose                                  │
│  ┌────────────────────────────────────────────┐  │
│  │ ag402-gateway (Dockerfile.gateway)         │  │
│  │   0.0.0.0:80 → container:8001             │  │
│  │   - x402 支付验证 (production: 链上验证)    │  │
│  │   - 重放攻击防护 (SQLite 持久化)           │  │
│  │   - IP 限流 (60/min)                       │  │
│  │   - 依赖: ag402-core[crypto] (solana/solders)│ │
│  │            │                               │  │
│  │            │ Docker internal network        │  │
│  │            ▼                               │  │
│  │ audit-server (Dockerfile)                  │  │
│  │   0.0.0.0:8000 → container:8000           │  │
│  │   - 三层审计报告 (Action/Analysis/Evidence) │  │
│  │   - 三数据源 (RugCheck + GoPlus + DexScreener)││
│  │   - TTL 缓存 (3s, LRU 5000)              │  │
│  │   - 免费每日额度 + 付费 per-min 限流       │  │
│  │   - Prometheus /metrics 端点              │  │
│  └────────────────────────────────────────────┘  │
│                                                  │
│  Firewall (ufw): 22, 80, 8000 open              │
└──────────────────────────────────────────────────┘
```

### 1.2 端口映射

| 宿主机端口 | 容器端口 | 服务 | 用途 |
|-----------|---------|------|------|
| 80 | 8001 | ag402-gateway | **公网入口** (Cloudflare 回源到这里) |
| 8000 | 8000 | audit-server | 内部调试 (直接访问审计API, 不经过支付网关) |

### 1.3 Solana Devnet 钱包

| 角色 | 公钥 | 用途 |
|------|------|------|
| 卖家 (收款) | `fisJvtob3HfaTWoCynHLp9McFoFZ2gL3VEiA4p4QnNm` | AG402_ADDRESS, 收取审计费 |
| 买家 (付款) | `3mjBQwePFP4PJLTwkZZufLa9Fq7BxfzPMr2Fcqecgizi` | 测试用付款钱包 |

### 1.4 关键文件

```
/opt/token-bugcheck/           ← 服务器项目根目录
├── docker-compose.yml          ← 服务编排 (2 services)
├── Dockerfile                  ← audit-server 镜像
├── Dockerfile.gateway          ← ag402-gateway 镜像 (含 crypto 依赖)
├── .env                        ← 运行时配置 (不入 git)
├── .env.example                ← 配置模板
├── src/rugcheck/
│   ├── main.py                 ← audit-server 入口
│   ├── gateway.py              ← ag402-gateway 入口 (支持 production 模式)
│   ├── server.py               ← FastAPI 路由 (/audit, /health, /stats)
│   ├── config.py               ← 环境变量加载
│   ├── cache.py                ← TTL LRU 缓存
│   ├── models.py               ← Pydantic 数据模型
│   ├── engine/risk_engine.py   ← 10 条风险规则
│   └── fetchers/               ← 上游 API 适配器
│       ├── aggregator.py       ← 并发抓取 + 合并
│       ├── goplus.py           ← GoPlus Security API
│       ├── rugcheck.py         ← RugCheck.xyz API
│       └── dexscreener.py      ← DexScreener API
├── devnet_buyer_test.py        ← Devnet E2E 买方测试脚本
└── examples/demo_agent.py      ← Mock 模式演示
```

---

## 2. 首次上线部署

### 2.1 服务器初始化

```bash
ssh root@45.32.54.209

# 安装 Docker (如果没有)
curl -fsSL https://get.docker.com | sh
docker --version    # 确认 >= 24.0
docker compose version  # 确认 >= 2.20

# 防火墙
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 8000/tcp    # 可选: 直接调试审计服务
ufw enable
ufw status
```

### 2.2 拉取代码

```bash
cd /opt
git clone https://github.com/AetherCore-Dev/token-bugcheck.git
cd token-bugcheck
```

### 2.3 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env`，必须修改的项：

```bash
# 卖家收款地址 (你的 Solana 公钥)
AG402_ADDRESS=fisJvtob3HfaTWoCynHLp9McFoFZ2gL3VEiA4p4QnNm

# 模式: production = 链上验证, test = mock 支付
X402_MODE=production
X402_NETWORK=devnet

# 链上验证所需的私钥和 RPC (production 模式必填)
SOLANA_PRIVATE_KEY=<base58_private_key>
SOLANA_RPC_URL=https://api.devnet.solana.com
```

### 2.4 构建并启动

```bash
docker compose build --no-cache
docker compose up -d

# 等待健康检查通过 (~30s)
sleep 30
docker ps   # 确认两个容器都是 healthy/running
```

### 2.5 Cloudflare 配置

1. DNS 添加 A 记录: `api` → `45.32.54.209`，**Proxy status: Proxied** (橙色云朵)
2. SSL/TLS → 加密模式: **Flexible** (Cloudflare→Origin 用 HTTP)
3. 不需要设置 Origin Rules，Cloudflare 默认回源到 80 端口

### 2.6 验证上线

```bash
# 在任意机器执行
curl -s https://api.aethercore.dev/health | python3 -m json.tool
# 预期: {"status": "healthy", "mode": "production", ...}
```

---

## 3. 一键更新镜像并验证

### 3.1 完整更新流程 (复制即用)

```bash
ssh root@45.32.54.209

cd /opt/token-bugcheck

# ---- Step 1: 拉取最新代码 ----
git pull origin main

# ---- Step 2: 停止 → 重建 → 启动 ----
docker compose down
docker compose build --no-cache
docker compose up -d

# ---- Step 3: 等待健康检查 (约 30-40 秒) ----
echo "等待服务启动..."
sleep 35

# ---- Step 4: 验证 ----
echo "=== 容器状态 ==="
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

echo ""
echo "=== 审计服务健康 ==="
curl -s http://localhost:8000/health | python3 -m json.tool

echo ""
echo "=== 支付网关健康 ==="
curl -s http://localhost:80/health | python3 -m json.tool

echo ""
echo "=== Cloudflare 域名 ==="
curl -s https://api.aethercore.dev/health | python3 -m json.tool

echo ""
echo "=== 402 支付墙 ==="
curl -s -w "\nHTTP_STATUS: %{http_code}\n" \
  https://api.aethercore.dev/audit/DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263

echo ""
echo "=== 直连审计测试 (绕过支付网关) ==="
curl -s http://localhost:8000/audit/DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263 \
  | python3 -c "import json,sys; r=json.load(sys.stdin); print(f\"Token: {r['evidence']['token_name']}, Risk: {r['action']['risk_level']}, Score: {r['action']['risk_score']}, Sources: {r['metadata']['data_sources']}\")"

echo ""
echo "=== Devnet 链上支付 E2E 测试 ==="
GATEWAY_URL=https://api.aethercore.dev python3 devnet_buyer_test.py
```

### 3.2 快速更新 (跳过无变化的镜像)

如果只改了代码没改依赖，可以省去 `--no-cache`:

```bash
cd /opt/token-bugcheck
git pull origin main
docker compose down && docker compose build && docker compose up -d
```

### 3.3 只更新单个服务

```bash
# 只重建 gateway (改了 gateway.py 或 Dockerfile.gateway)
docker compose build --no-cache ag402-gateway
docker compose up -d ag402-gateway

# 只重建 audit-server (改了 server.py, engine, fetchers 等)
docker compose build --no-cache audit-server
docker compose down && docker compose up -d    # 需要重启因为 gateway depends_on audit-server
```

---

## 4. 端到端测试验证

### 4.1 逐层验证清单

从内到外验证，快速定位问题所在层级:

```bash
# ---- L1: 容器内部 (确认应用本身正常) ----
docker exec token-bugcheck-audit-server-1 \
  python -c "import httpx; r=httpx.get('http://localhost:8000/health'); print('L1 audit:', r.status_code)"

docker exec token-bugcheck-ag402-gateway-1 \
  python -c "import httpx; r=httpx.get('http://localhost:8001/health'); print('L1 gateway:', r.status_code)"

# ---- L2: 宿主机端口 (确认 Docker 端口映射正常) ----
curl -sf http://localhost:8000/health > /dev/null && echo "L2 audit:8000 OK" || echo "L2 audit:8000 FAIL"
curl -sf http://localhost:80/health > /dev/null && echo "L2 gateway:80 OK" || echo "L2 gateway:80 FAIL"

# ---- L3: 外部 IP (确认防火墙放行) ----
curl -sf http://45.32.54.209:80/health > /dev/null && echo "L3 IP:80 OK" || echo "L3 IP:80 FAIL"

# ---- L4: 域名 HTTPS (确认 Cloudflare 正常) ----
curl -sf https://api.aethercore.dev/health > /dev/null && echo "L4 HTTPS OK" || echo "L4 HTTPS FAIL"
```

### 4.2 功能验证

```bash
# 健康检查
curl -s https://api.aethercore.dev/health | python3 -m json.tool

# 402 支付墙 (不带凭证应返回 402)
curl -s -o /dev/null -w "%{http_code}" https://api.aethercore.dev/audit/DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263
# 预期: 402

# 直连审计 (绕过支付网关, 测试审计逻辑)
curl -s http://localhost:8000/audit/DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263 | python3 -m json.tool

# 统计信息
curl -s http://localhost:8000/stats | python3 -m json.tool

# 网关指标
curl -s https://api.aethercore.dev/health | python3 -c "
import json,sys
d = json.load(sys.stdin)
m = d['metrics']
print(f\"Mode: {d['mode']}\")
print(f\"Uptime: {d['uptime_seconds']:.0f}s\")
print(f\"Total: {m['requests_total']}, Paid: {m['payments_verified']}, Rejected: {m['payments_rejected']}, 402s: {m['challenges_issued']}\")
"
```

### 4.3 Devnet 链上支付 E2E 测试

```bash
# 在服务器上运行 (服务器网络可直连 Solana devnet RPC)
cd /opt/token-bugcheck
GATEWAY_URL=https://api.aethercore.dev python3 devnet_buyer_test.py

# 预期输出:
# [PASS] 402 Payment Required received correctly
# [PASS] Audit report received! (status 200, ~3s)
# [PAY]  TX hash: <solana_tx_hash>
# [PAY]  Solscan: https://solscan.io/tx/<hash>?cluster=devnet
# ALL TESTS PASSED
```

### 4.4 钱包余额检查

```bash
# 买家 USDC
curl -s https://api.devnet.solana.com -X POST \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"getTokenAccountsByOwner","params":["3mjBQwePFP4PJLTwkZZufLa9Fq7BxfzPMr2Fcqecgizi",{"mint":"4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU"},{"encoding":"jsonParsed"}]}' \
  | python3 -c "import json,sys; t=json.load(sys.stdin)['result']['value'][0]['account']['data']['parsed']['info']['tokenAmount']; print('Buyer:', t['uiAmountString'], 'USDC')"

# 卖家 USDC
curl -s https://api.devnet.solana.com -X POST \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"getTokenAccountsByOwner","params":["fisJvtob3HfaTWoCynHLp9McFoFZ2gL3VEiA4p4QnNm",{"mint":"4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU"},{"encoding":"jsonParsed"}]}' \
  | python3 -c "import json,sys; t=json.load(sys.stdin)['result']['value'][0]['account']['data']['parsed']['info']['tokenAmount']; print('Seller:', t['uiAmountString'], 'USDC')"

# 买家 SOL (gas)
curl -s https://api.devnet.solana.com -X POST \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"getBalance","params":["3mjBQwePFP4PJLTwkZZufLa9Fq7BxfzPMr2Fcqecgizi"]}' \
  | python3 -c "import json,sys; print('Buyer SOL:', json.load(sys.stdin)['result']['value'] / 1e9)"
```

### 4.5 Devnet 充值 (余额不足时)

```bash
# 充值 1 SOL (gas)
curl -s https://api.devnet.solana.com -X POST \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"requestAirdrop","params":["3mjBQwePFP4PJLTwkZZufLa9Fq7BxfzPMr2Fcqecgizi", 1000000000]}'

# Devnet USDC: 需要通过 spl-token-faucet 或浏览器 https://faucet.solana.com
```

### 4.6 本地 Mac 测试 (ag402 CLI)

```bash
# 安装
pip install "ag402-core[crypto]" httpx

# 注意: 需要本地网络能连通 Solana devnet RPC
# 如果公司网络阻断, 换用可达的 RPC:
#   export SOLANA_RPC_URL=https://devnet.helius-rpc.com/?api-key=<your-free-key>

export X402_MODE=production
export X402_NETWORK=devnet
export SOLANA_PRIVATE_KEY=<your_devnet_buyer_private_key>
export SOLANA_RPC_URL=https://api.devnet.solana.com

# 付费查询
ag402 pay https://api.aethercore.dev/audit/DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263

# 如果 get_latest_blockhash 失败 → 本地网络无法连 Solana RPC
# 解决: 换 RPC 或在服务器上运行 devnet_buyer_test.py
```

---

## 5. 日常运维

### 5.1 查看状态

```bash
cd /opt/token-bugcheck

# 容器状态
docker compose ps

# 实时日志 (Ctrl+C 退出)
docker compose logs -f

# 单服务日志
docker compose logs audit-server --tail 100
docker compose logs ag402-gateway --tail 100

# 资源占用
docker stats --no-stream
```

### 5.2 重启服务

```bash
# 重启全部
docker compose restart

# 只重启网关
docker compose restart ag402-gateway

# 完全重建重启
docker compose down && docker compose up -d
```

### 5.3 模式切换

```bash
cd /opt/token-bugcheck

# 切到 test 模式 (mock 支付, 无链上交互)
sed -i 's/X402_MODE=production/X402_MODE=test/' .env
docker compose down && docker compose up -d

# 切到 production 模式 (链上验证)
sed -i 's/X402_MODE=test/X402_MODE=production/' .env
docker compose down && docker compose up -d

# 验证当前模式
curl -s https://api.aethercore.dev/health | python3 -c "import json,sys; print('Mode:', json.load(sys.stdin)['mode'])"
```

### 5.4 清理磁盘

```bash
# 查看磁盘
df -h /

# 清理旧镜像
docker image prune -f

# 深度清理 (包括未使用的 volume, 谨慎使用)
docker system prune -f
```

### 5.5 查看交易历史 (从网关日志)

```bash
# 查看所有支付验证记录
docker compose logs ag402-gateway 2>&1 | grep "\[VERIFY\]"

# 查看成功的代理转发
docker compose logs ag402-gateway 2>&1 | grep "\[GATEWAY\] Payment verified"
```

---

## 6. 故障排查

### 6.1 快速诊断流程

```
服务不可用?
  │
  ├─ docker ps → 容器没在跑?
  │    └─ docker compose logs <service> --tail 30  → 看崩溃原因
  │
  ├─ curl localhost:80/health → 超时?
  │    └─ 端口映射问题, 检查 docker-compose.yml ports
  │
  ├─ curl https://api.aethercore.dev/health → 超时?
  │    ├─ curl 45.32.54.209:80/health → 能通?
  │    │    └─ Cloudflare 配置问题 (DNS / SSL 模式)
  │    └─ 也不通?
  │         └─ ufw status → 80 端口没放行?
  │
  └─ 402 付费后仍失败?
       └─ 看 gateway 日志中 [VERIFY] 行
```

### 6.2 常见问题速查

| 现象 | 原因 | 解决 |
|------|------|------|
| Gateway 反复重启 | `X402_MODE=production` 但缺 `SOLANA_PRIVATE_KEY` | 在 `.env` 中设置私钥, 或切回 `test` 模式 |
| Gateway 启动报 `ImportError: solana` | Docker 镜像缺 crypto 依赖 | `Dockerfile.gateway` 需包含 `pip install "ag402-core[crypto]"` |
| 审计返回 `data_completeness: partial` | 部分上游 API 超时或限流 | 检查日志中 GoPlus/RugCheck/DexScreener 的报错 |
| 审计返回 `data_completeness: unavailable` | 全部上游 API 不可用 | 检查网络; 降级报告标记 CRITICAL, 不缓存, 下次请求重试 |
| 链上支付后 gateway 返回 403 | 交易验证失败 | 检查 `SOLANA_RPC_URL` 可达性, 检查 USDC mint 地址是否匹配 devnet |
| `ag402 pay` 本地失败 | 本地网络无法连 Solana RPC | 公司代理阻断, 换 RPC 或在服务器运行测试 |
| HTTPS 502 Bad Gateway | Cloudflare 连不到 origin 80 端口 | 检查 `ufw allow 80/tcp`, 检查容器是否 running |
| HTTPS 521 Web Server Down | 服务器 80 端口无响应 | `docker compose up -d` 启动服务 |
| health 返回 `degraded` | 上游 API 最近有失败 | 通常自动恢复, 如果持续检查网络 |

### 6.3 查看详细错误

```bash
# Gateway 详细日志 (含支付验证细节)
docker compose logs ag402-gateway --tail 100 2>&1 | grep -E "ERROR|WARNING|VERIFY|FATAL"

# Audit server 详细日志 (含上游 API 调用)
docker compose logs audit-server --tail 100 2>&1 | grep -E "ERROR|WARNING|TIMEOUT"
```

---

## 7. 关键配置参考

### 7.1 .env 完整配置

```bash
# ===== 服务 =====
RUGCHECK_HOST=0.0.0.0
RUGCHECK_PORT=8000
RUGCHECK_LOG_LEVEL=info               # debug | info | warning | error

# ===== 缓存 =====
# TTL=3s: 防 Rug 核心 — 撤池子到链上确认 ~12 秒, 3 秒缓存
# 确保 Agent 拿到的数据最多延迟 3 秒, 同秒高并发仍走缓存
CACHE_TTL_SECONDS=3
CACHE_MAX_SIZE=5000                   # ~5000 JSON ≈ 几十 MB, 低配机器无压力

# ===== 限流 =====
FREE_DAILY_QUOTA=20                  # 免费用户每 IP 每日审计次数
PAID_RATE_LIMIT=120                  # 付费用户 (loopback) 每分钟审计次数

# ===== 上游 API 超时 (秒) =====
# 按 4.5 秒全局响应预算分级:
#   DexScreener: CDN 驱动, 最快, 最严格
#   GoPlus:      商业级安全接口, 中等容忍
#   RugCheck:    社区接口偶尔波动, 最宽容
DEXSCREENER_TIMEOUT_SECONDS=1.5
GOPLUS_TIMEOUT_SECONDS=2.5
RUGCHECK_API_TIMEOUT_SECONDS=3.5

# ===== GoPlus 认证 (可选, 提高限流上限) =====
GOPLUS_APP_KEY=
GOPLUS_APP_SECRET=

# ===== ag402 支付网关 =====
AG402_PRICE=0.05                      # 每次审计的 USDC 价格
AG402_CHAIN=solana
AG402_TOKEN=USDC
AG402_ADDRESS=<卖家公钥>               # 收款钱包地址
AG402_GATEWAY_PORT=8001               # 容器内部端口 (不是宿主机端口)

# ===== 模式 =====
X402_MODE=production                  # test = mock 支付, production = 链上验证
X402_NETWORK=devnet                   # devnet | mainnet

# ===== Solana (production 模式必填) =====
SOLANA_PRIVATE_KEY=<base58 私钥>       # 用于初始化 SolanaAdapter (验证交易)
SOLANA_RPC_URL=https://api.devnet.solana.com
```

### 7.2 四层防线参数速查

```
┌─────────────────────────────────────────────────────────────┐
│                    4.5 秒全局响应预算                        │
│                                                             │
│  第1层: 分级超时 (毫秒级防线)                               │
│    DexScreener  1.5s ─┐                                     │
│    GoPlus       2.5s ─┼─ 并行 fetch ─→ 聚合超时 4.0s       │
│    RugCheck     3.5s ─┘                                     │
│    + 每源 1 次重试 (0.3s 退避, 仅最快源有效)                │
│    + server wait_for = 4.5s                                 │
│                                                             │
│  第2层: 极短缓存 (鲜活度防线)                               │
│    TTL = 3s (防撤池子时间差)                                │
│    LRU = 5000 条 (~几十 MB)                                 │
│                                                             │
│  第3层: 并发与限流 (熔断防线)                               │
│    上游并发信号量 = 20                                      │
│    /audit 限流 (免费): 每日 20 次 (FREE_DAILY_QUOTA)        │
│    /audit 限流 (付费): 120/min (PAID_RATE_LIMIT, loopback)  │
│    /stats 限流: 10/min (per IP, 非 loopback)               │
│    /health, /metrics 无限流                                 │
│                                                             │
│  第4层: 降级响应 (支付保护防线)                             │
│    全源失败 → 200 + data_completeness="unavailable"         │
│    聚合超时 → 200 + data_completeness="unavailable"         │
│    降级报告: is_safe=false, risk_score=100, CRITICAL        │
│    降级报告: degraded=true (顶层字段, 客户端可识别)         │
│    降级报告不缓存 → 下次请求重试上游                        │
└─────────────────────────────────────────────────────────────┘
```

### 7.3 docker-compose.yml 端口映射

```yaml
services:
  audit-server:
    ports:
      - "8000:8000"       # 调试端口, 可直接访问审计 API

  ag402-gateway:
    ports:
      - "80:8001"         # 公网入口, Cloudflare 回源到这里
    environment:
      - AG402_TARGET_URL=http://audit-server:8000   # Docker 内部通信
```

### 7.4 Cloudflare 设置

| 配置项 | 值 |
|--------|-----|
| DNS Record | A `api` → `45.32.54.209` |
| Proxy Status | **Proxied** (橙色云朵) |
| SSL/TLS Mode | **Flexible** (CF→Origin 用 HTTP) |
| Always Use HTTPS | On |

---

## 8. 监控 (Prometheus)

### 8.1 /metrics 端点

审计服务暴露标准 Prometheus 指标：

```bash
curl http://localhost:8000/metrics
```

### 8.2 可用指标

| 指标名 | 类型 | 标签 | 说明 |
|--------|------|------|------|
| `rugcheck_requests_total` | Counter | method, path, status | HTTP 请求总数 |
| `rugcheck_request_duration_seconds` | Histogram | method, path | 请求耗时 |
| `rugcheck_upstream_success_total` | Counter | source | 上游成功次数 |
| `rugcheck_upstream_failure_total` | Counter | source | 上游失败次数 |
| `rugcheck_cache_hits_total` | Counter | — | 缓存命中 |
| `rugcheck_cache_misses_total` | Counter | — | 缓存未命中 |

### 8.3 Grafana 接入建议

1. Prometheus `scrape_configs` 添加:
   ```yaml
   - job_name: 'rugcheck'
     static_configs:
       - targets: ['localhost:8000']
     metrics_path: '/metrics'
     scrape_interval: 15s
   ```

2. 推荐 Dashboard 面板:
   - **QPS**: `rate(rugcheck_requests_total[1m])`
   - **P99 延迟**: `histogram_quantile(0.99, rate(rugcheck_request_duration_seconds_bucket[5m]))`
   - **上游成功率**: `rate(rugcheck_upstream_success_total[5m]) / (rate(rugcheck_upstream_success_total[5m]) + rate(rugcheck_upstream_failure_total[5m]))`
   - **缓存命中率**: `rate(rugcheck_cache_hits_total[5m]) / (rate(rugcheck_cache_hits_total[5m]) + rate(rugcheck_cache_misses_total[5m]))`

---

## 9. 踩坑记录

部署过程中实际遇到的问题, 避免重复踩坑:

### 8.1 gateway.py 不支持 production 模式 [已修复]

**问题**: 原始代码 `gateway.py` 创建 `X402Gateway` 时不传 `verifier`, 导致 `X402_MODE=production` 时直接崩溃:
```
ValueError: Production mode requires an explicit PaymentVerifier
```

**修复**: 在 `gateway.py` 中增加逻辑, 当 `X402_MODE=production` 时自动构建 `PaymentVerifier(provider=SolanaAdapter(...))`.

### 8.2 Docker 镜像缺 Solana crypto 依赖 [已修复]

**问题**: `pyproject.toml` 依赖列表只有 `ag402-core`, 没有 `ag402-core[crypto]`. Docker 镜像不含 `solana/solders/base58`, production 模式无法初始化 `SolanaAdapter`.

**修复**: `Dockerfile.gateway` 增加 `pip install "ag402-core[crypto]"`.

### 8.3 没有 Nginx 也能工作

**方案**: 不装 Nginx, 直接 Docker 映射 `80:8001`, Cloudflare 回源 80. 链路最短, 无额外进程.

### 8.4 本地 Mac 无法连 Solana devnet RPC

**现象**: `ag402 pay` 在 Mac 上执行时 `get_latest_blockhash` 三次重试失败.

**原因**: 公司网络/代理对 `api.devnet.solana.com` 做 SSL 中间人拦截 (`SSL error: wrong version number`).

**绕过**: 在服务器上运行 `devnet_buyer_test.py`, 或本地换用其他 RPC (Helius/Alchemy).

### 8.5 uvloop 与 aiosqlite 不兼容

**问题**: ag402 的 `PersistentReplayGuard` 使用 `aiosqlite`, 与 `uvloop` 事件循环不兼容, 导致 gateway 崩溃.

**防护**: `Dockerfile.gateway` 设置 `ENV UVLOOP_INSTALL=0`, `gateway.py` 同时在 Python 层面阻断 uvloop.
