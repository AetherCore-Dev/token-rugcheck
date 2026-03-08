#!/usr/bin/env bash
# =============================================================================
# deploy-oneclick.sh — 一键部署 Token RugCheck MCP (从零到上线)
#
# 用法:
#   bash scripts/deploy-oneclick.sh
#
# 特性:
#   - 交互式收集必要配置（需要人工时停下来指导）
#   - 自动完成所有可自动化的步骤
#   - 每一步都有详细进度输出
#   - 失败时给出明确的排查建议
#   - 支持全新部署和增量更新
#
# 前置条件:
#   - macOS/Linux 本地环境
#   - SSH 免密登录已配置 (ssh root@IP 免密码)
#   - 域名已通过 Cloudflare 配置 (可选, 脚本会检测)
# =============================================================================
set -euo pipefail

# --- Colors ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# --- Helpers ---
info()    { printf "${BLUE}[INFO]${NC}  %s\n" "$1"; }
ok()      { printf "${GREEN}[PASS]${NC}  %s\n" "$1"; }
fail()    { printf "${RED}[FAIL]${NC}  %s\n" "$1"; }
warn()    { printf "${YELLOW}[WARN]${NC}  %s\n" "$1"; }
step()    { printf "\n${BOLD}${CYAN}━━━ Step %s: %s ━━━${NC}\n\n" "$1" "$2"; }
divider() { printf "\n${BLUE}════════════════════════════════════════════════════════════${NC}\n"; }
human()   { printf "\n${YELLOW}${BOLD}🔧 需要人工操作:${NC}\n"; printf "${YELLOW}   %s${NC}\n" "$@"; printf "\n"; }
bail()    { fail "$1"; exit 1; }

# --- Script dir ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_DIR="/opt/token-rugcheck"
COMPOSE_FILES="-f docker-compose.yml -f docker-compose.prod.yml"

# --- Remote exec helper ---
remote() {
    ssh -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new "root@${SERVER_IP}" "$@"
}

# =============================================================================
# Banner
# =============================================================================
printf "\n"
printf "${BOLD}${CYAN}"
printf "╔══════════════════════════════════════════════════════════╗\n"
printf "║        Token RugCheck MCP — 一键部署脚本                ║\n"
printf "║        从零到上线，自动化 + 人工指导                     ║\n"
printf "╚══════════════════════════════════════════════════════════╝\n"
printf "${NC}\n"

# =============================================================================
# Phase 1: 交互式收集配置
# =============================================================================
step "1/8" "收集部署配置"

# 1.1 Server IP
read -rp "$(printf "${BOLD}服务器 IP${NC} (例: 140.82.49.221): ")" SERVER_IP
if [[ -z "$SERVER_IP" ]]; then
    bail "服务器 IP 不能为空"
fi
if ! echo "$SERVER_IP" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then
    bail "无效的 IP 格式: $SERVER_IP"
fi

# 1.2 Deployment mode
printf "\n${BOLD}部署模式:${NC}\n"
printf "  1) ${GREEN}production${NC} — 主网真实 USDC 支付 (推荐)\n"
printf "  2) ${YELLOW}devnet${NC}     — Devnet 测试网支付\n"
printf "  3) ${BLUE}test${NC}       — Mock 模拟支付 (开发用)\n"
read -rp "$(printf "选择 [1/2/3, 默认 1]: ")" MODE_CHOICE
case "${MODE_CHOICE:-1}" in
    1) MODE="production" ;;
    2) MODE="devnet" ;;
    3) MODE="test" ;;
    *) bail "无效选择: $MODE_CHOICE" ;;
esac
info "部署模式: $MODE"

# 1.3 Wallet address
read -rp "$(printf "${BOLD}卖家 Solana 钱包地址${NC} (收款地址): ")" WALLET_ADDRESS
if [[ -z "$WALLET_ADDRESS" ]]; then
    bail "钱包地址不能为空"
fi
if ! echo "$WALLET_ADDRESS" | grep -qE '^[1-9A-HJ-NP-Za-km-z]{32,44}$'; then
    bail "无效的 Solana 地址格式: $WALLET_ADDRESS"
fi

# 1.4 Price
read -rp "$(printf "${BOLD}每次审计价格 (USDC)${NC} [默认 0.02]: ")" PRICE
PRICE="${PRICE:-0.02}"

# 1.5 Domain
read -rp "$(printf "${BOLD}域名${NC} (例: rugcheck.aethercore.dev, 留空跳过): ")" DOMAIN
DOMAIN="${DOMAIN:-}"

# 1.6 Private key (non-test modes)
PRIVATE_KEY=""
if [ "$MODE" != "test" ]; then
    info "说明: 卖家私钥是可选的。Gateway 通过链上只读 RPC 验证支付，不需要签名交易。"
    info "      如果你不提供，系统会自动使用只读验证模式。"
    read -rsp "$(printf "${BOLD}Solana 私钥${NC} (base58, 可选, 回车跳过): ")" PRIVATE_KEY
    printf "\n"
    if [ -n "$PRIVATE_KEY" ]; then
        ok "私钥已读取 (${#PRIVATE_KEY} 字符)"
    else
        info "未提供私钥, 将使用只读验证模式"
    fi
fi

# 1.7 RPC URL
RPC_URL=""
if [ "$MODE" = "production" ]; then
    read -rp "$(printf "${BOLD}Mainnet RPC URL${NC} [默认: https://api.mainnet-beta.solana.com]: ")" RPC_URL
    RPC_URL="${RPC_URL:-https://api.mainnet-beta.solana.com}"
elif [ "$MODE" = "devnet" ]; then
    RPC_URL="https://api.devnet.solana.com"
fi

# Summary
divider
printf "\n${BOLD}部署配置确认:${NC}\n"
printf "  服务器:     ${GREEN}%s${NC}\n" "$SERVER_IP"
printf "  模式:       ${GREEN}%s${NC}\n" "$MODE"
printf "  钱包:       ${GREEN}%s${NC}\n" "${WALLET_ADDRESS:0:12}...${WALLET_ADDRESS: -4}"
printf "  价格:       ${GREEN}%s USDC${NC}\n" "$PRICE"
printf "  域名:       ${GREEN}%s${NC}\n" "${DOMAIN:-无(仅 IP 访问)}"
printf "  私钥:       ${GREEN}%s${NC}\n" "$([ -n "$PRIVATE_KEY" ] && echo '已提供' || echo '未提供(只读模式)')"
if [ -n "$RPC_URL" ]; then
    printf "  RPC URL:    ${GREEN}%s${NC}\n" "$RPC_URL"
fi
divider

read -rp "$(printf "\n确认以上配置开始部署? [Y/n]: ")" CONFIRM
if [[ "${CONFIRM:-Y}" =~ ^[Nn] ]]; then
    info "部署已取消"
    exit 0
fi

# =============================================================================
# Phase 2: SSH 连通性检测
# =============================================================================
step "2/8" "检测 SSH 连接"

info "测试 SSH 连接到 root@${SERVER_IP}..."
if remote "echo 'SSH_OK'" 2>/dev/null | grep -q "SSH_OK"; then
    ok "SSH 连接正常"
else
    fail "无法 SSH 到 root@${SERVER_IP}"
    human \
        "请确保 SSH 免密登录已配置:" \
        "  ssh-copy-id root@${SERVER_IP}" \
        "或手动将公钥添加到服务器的 ~/.ssh/authorized_keys" \
        "" \
        "测试: ssh root@${SERVER_IP} 'echo ok'" \
        "" \
        "配置完成后重新运行本脚本。"
    exit 1
fi

# =============================================================================
# Phase 3: 服务器初始化
# =============================================================================
step "3/8" "服务器初始化 (Docker, 防火墙, 代码)"

info "运行服务器初始化脚本..."
SETUP_EXIT=0
SETUP_OUTPUT=$(remote 'bash -s' < "$SCRIPT_DIR/setup-server.sh" 2>&1) || SETUP_EXIT=$?

echo "$SETUP_OUTPUT" | while IFS= read -r line; do
    case "$line" in
        OK\|*)   printf "  ${GREEN}✓${NC} %s\n" "$line" ;;
        FAIL\|*) printf "  ${RED}✗${NC} %s\n" "$line" ;;
        SKIP\|*) printf "  ${YELLOW}→${NC} %s\n" "$line" ;;
        INFO\|*) printf "  ${BLUE}ℹ${NC} %s\n" "$line" ;;
        *)       printf "  %s\n" "$line" ;;
    esac
done

if [ "$SETUP_EXIT" -eq 0 ]; then
    ok "服务器初始化完成 (全新安装)"
elif [ "$SETUP_EXIT" -eq 2 ]; then
    ok "服务器已就绪 (已更新到最新代码)"
else
    fail "服务器初始化失败"
    human \
        "请 SSH 登录服务器排查:" \
        "  ssh root@${SERVER_IP}" \
        "  # 检查 Docker 是否安装" \
        "  docker --version" \
        "  docker compose version" \
        "  # 检查代码目录" \
        "  ls -la /opt/token-rugcheck/" \
        "" \
        "修复后重新运行本脚本。"
    exit 1
fi

# =============================================================================
# Phase 4: 生成配置并上传
# =============================================================================
step "4/8" "生成 .env 配置"

ENV_FILE="/tmp/token-rugcheck-$(date +%s).env"

# Build generate-env.sh args
GEN_ARGS=(
    --mode "$MODE"
    --address "$WALLET_ADDRESS"
    --price "$PRICE"
    --output "$ENV_FILE"
)
if [ -n "$PRIVATE_KEY" ]; then
    GEN_ARGS+=(--private-key "$PRIVATE_KEY")
fi
if [ -n "$RPC_URL" ]; then
    GEN_ARGS+=(--rpc-url "$RPC_URL")
fi

info "生成 .env 文件..."
GEN_OUTPUT=$(bash "$SCRIPT_DIR/generate-env.sh" "${GEN_ARGS[@]}" 2>&1)
GEN_EXIT=$?

if [ "$GEN_EXIT" -ne 0 ]; then
    fail "生成 .env 失败:"
    echo "$GEN_OUTPUT"
    rm -f "$ENV_FILE"
    exit 1
fi
ok "配置文件已生成"

info "上传 .env 到服务器..."
scp -o ConnectTimeout=15 "$ENV_FILE" "root@${SERVER_IP}:${PROJECT_DIR}/.env"
rm -f "$ENV_FILE"
ok "配置已上传到 ${SERVER_IP}:${PROJECT_DIR}/.env"

# Clear private key from memory
PRIVATE_KEY=""

# =============================================================================
# Phase 5: 构建并部署
# =============================================================================
step "5/8" "构建 Docker 镜像 & 启动服务"

info "拉取最新代码..."
GIT_PULL_OUTPUT=$(remote "cd $PROJECT_DIR && git pull origin main 2>&1") || true
if echo "$GIT_PULL_OUTPUT" | grep -qE "Aborting|CONFLICT|error:|fatal:"; then
    warn "git pull 失败，尝试自动 stash 后重试..."
    remote "cd $PROJECT_DIR && git stash --include-untracked && git pull origin main" 2>&1 | tail -5
    GIT_RETRY_EXIT=$?
    if [ "$GIT_RETRY_EXIT" -ne 0 ]; then
        fail "git pull 重试失败！请手动解决："
        fail "  ssh root@${SERVER_IP}"
        fail "  cd $PROJECT_DIR && git status"
        exit 1
    fi
    ok "git stash + pull 成功"
else
    echo "$GIT_PULL_OUTPUT" | tail -3
    ok "代码已更新"
fi

info "停止旧服务..."
remote "cd $PROJECT_DIR && docker compose $COMPOSE_FILES down --remove-orphans --timeout 30" 2>&1 || true

# 强制清理残留容器
info "清理残留容器..."
remote "docker ps -a --filter 'name=token-rugcheck' -q | xargs -r docker rm -f" 2>&1 || true

# 清理残留的 docker-proxy 进程和 Docker 网络
# docker compose down 后 docker-proxy 可能残留，导致 Docker daemon 认为端口仍被占用
# 即使 ss -tlnp 看不到监听，Docker 内部状态也可能不一致
info "清理 Docker 网络和端口残留..."
remote "pkill -9 -f docker-proxy 2>/dev/null" 2>/dev/null || true
remote "docker network prune -f 2>/dev/null" 2>/dev/null || true
sleep 2

# 检查端口是否已释放，未释放则重启 Docker daemon（一次性）
NEED_DOCKER_RESTART=false
for PORT in 80 8000; do
    OS_BUSY=$(remote "ss -tlnp 2>/dev/null | grep -E ':${PORT}\b'" 2>/dev/null) || OS_BUSY=""
    DOCKER_BUSY=$(remote "pgrep -f 'docker-proxy.*:${PORT}' 2>/dev/null" 2>/dev/null) || DOCKER_BUSY=""
    if [ -n "$OS_BUSY" ] || [ -n "$DOCKER_BUSY" ]; then
        warn "端口 ${PORT} 仍被占用，标记需要重启 Docker daemon"
        NEED_DOCKER_RESTART=true
    fi
done

if [ "$NEED_DOCKER_RESTART" = true ]; then
    warn "端口未能通过 kill 释放，重启 Docker daemon..."
    # 使用 nohup 在服务器后台重启 Docker，避免 SSH 连接被中断
    remote "nohup bash -c 'systemctl restart docker' >/dev/null 2>&1 &" 2>/dev/null || true
    info "等待 Docker daemon 重启和 SSH 恢复..."
    sleep 10

    # 等待 SSH 恢复连通
    SSH_WAIT=0
    SSH_MAX=60
    while [ "$SSH_WAIT" -lt "$SSH_MAX" ]; do
        if remote "echo SSH_OK" 2>/dev/null | grep -q "SSH_OK"; then
            break
        fi
        SSH_WAIT=$((SSH_WAIT + 3))
        sleep 3
        printf "  ⏳ 等待 SSH 恢复... %ds\r" "$SSH_WAIT"
    done
    printf "\n"

    if [ "$SSH_WAIT" -ge "$SSH_MAX" ]; then
        fail "Docker 重启后 SSH 连接未恢复 (${SSH_MAX}s)"
        human "请手动 SSH 登录检查: ssh root@${SERVER_IP}"
        exit 1
    fi
    ok "Docker daemon 已重启，SSH 已恢复 (${SSH_WAIT}s)"

    # 等待 Docker daemon 完全就绪
    DOCKER_WAIT=0
    while [ "$DOCKER_WAIT" -lt 30 ]; do
        if remote "docker info >/dev/null 2>&1"; then
            break
        fi
        DOCKER_WAIT=$((DOCKER_WAIT + 2))
        sleep 2
    done
    ok "Docker daemon 就绪"
fi

# 最终确认端口已释放
for PORT in 80 8000; do
    if remote "ss -tlnp 2>/dev/null | grep -qE ':${PORT}\b'" 2>/dev/null; then
        fail "端口 ${PORT} 仍被占用（即使重启 Docker 后）"
        human "请手动排查: ssh root@${SERVER_IP} 'ss -tlnp | grep :${PORT}'"
        exit 1
    fi
done
ok "所有端口已释放"

info "构建 Docker 镜像 + 更新 ag402 依赖 (首次约 2-3 分钟)..."
BUILD_START=$(date +%s)
if remote "cd $PROJECT_DIR && docker compose $COMPOSE_FILES build --no-cache" 2>&1 | tail -10; then
    BUILD_END=$(date +%s)
    ok "Docker 镜像构建完成 ($(( BUILD_END - BUILD_START ))s)"
else
    fail "Docker 构建失败"
    human \
        "请 SSH 登录服务器查看构建日志:" \
        "  ssh root@${SERVER_IP}" \
        "  cd /opt/token-rugcheck" \
        "  docker compose $COMPOSE_FILES build --no-cache 2>&1 | tail -50" \
        "" \
        "常见原因:" \
        "  - 网络问题 (pip install 超时)" \
        "  - Dockerfile 语法错误" \
        "  - Python 依赖冲突"
    exit 1
fi

info "启动服务..."
remote "cd $PROJECT_DIR && docker compose $COMPOSE_FILES up -d" 2>&1
ok "服务已启动"

# =============================================================================
# Phase 6: 等待健康检查
# =============================================================================
step "6/8" "等待服务就绪"

MAX_WAIT=120
ELAPSED=0
INTERVAL=5
AUDIT_OK=false
GW_OK=false

while [ "$ELAPSED" -lt "$MAX_WAIT" ]; do
    if [ "$AUDIT_OK" = false ]; then
        HTTP=$(remote "curl -s -o /dev/null -w '%{http_code}' --max-time 3 http://localhost:8000/health" 2>/dev/null) || HTTP="000"
        if [ "$HTTP" = "200" ]; then
            AUDIT_OK=true
            ok "审计服务就绪 (${ELAPSED}s)"
        fi
    fi

    if [ "$GW_OK" = false ]; then
        HTTP=$(remote "curl -s -o /dev/null -w '%{http_code}' --max-time 3 http://localhost:80/health" 2>/dev/null) || HTTP="000"
        if [ "$HTTP" = "200" ]; then
            GW_OK=true
            ok "支付网关就绪 (${ELAPSED}s)"
        fi
    fi

    if [ "$AUDIT_OK" = true ] && [ "$GW_OK" = true ]; then
        break
    fi

    sleep "$INTERVAL"
    ELAPSED=$((ELAPSED + INTERVAL))
    printf "  ⏳ 等待中... %ds (审计=%s, 网关=%s)\r" "$ELAPSED" "$AUDIT_OK" "$GW_OK"
done
printf "\n"

if [ "$AUDIT_OK" = false ] || [ "$GW_OK" = false ]; then
    fail "服务启动超时 (${MAX_WAIT}s)"
    warn "容器状态:"
    remote "docker ps --format 'table {{.Names}}\t{{.Status}}' | grep token" 2>&1 || true
    warn "最近日志:"
    remote "cd $PROJECT_DIR && docker compose logs --tail 20" 2>&1 | tail -20 || true
    human \
        "请 SSH 登录服务器排查:" \
        "  ssh root@${SERVER_IP}" \
        "  cd /opt/token-rugcheck" \
        "  docker compose logs --tail 50" \
        "" \
        "常见原因:" \
        "  - .env 配置错误 (检查 X402_MODE, AG402_ADDRESS)" \
        "  - 端口冲突 (80 端口被其他服务占用)" \
        "  - 内存不足 (free -h 查看)"
    exit 1
fi

ok "所有服务已就绪!"

# =============================================================================
# Phase 7: 自动化验证
# =============================================================================
step "7/8" "5 层自动化验证"

VERIFY_PASS=0
VERIFY_FAIL=0

# V1: 容器状态
info "[V1] 容器运行状态"
CONTAINERS=$(remote "docker ps --format '{{.Names}} {{.Status}}' | grep token" 2>&1) || true
if echo "$CONTAINERS" | grep -q "Up"; then
    ok "V1: 容器正常运行"
    VERIFY_PASS=$((VERIFY_PASS + 1))
else
    fail "V1: 容器异常"
    VERIFY_FAIL=$((VERIFY_FAIL + 1))
fi

# V2: 本地端口
info "[V2] 宿主机端口访问"
AUDIT_HTTP=$(remote "curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://localhost:8000/health" 2>/dev/null) || AUDIT_HTTP="000"
GW_HTTP=$(remote "curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://localhost:80/health" 2>/dev/null) || GW_HTTP="000"
if [ "$AUDIT_HTTP" = "200" ] && [ "$GW_HTTP" = "200" ]; then
    ok "V2: localhost:8000=$AUDIT_HTTP, localhost:80=$GW_HTTP"
    VERIFY_PASS=$((VERIFY_PASS + 1))
else
    fail "V2: localhost:8000=$AUDIT_HTTP, localhost:80=$GW_HTTP"
    VERIFY_FAIL=$((VERIFY_FAIL + 1))
fi

# V3: 外部 IP
info "[V3] 外部 IP 访问"
EXT_HTTP=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "http://${SERVER_IP}:80/health" 2>/dev/null) || EXT_HTTP="000"
if [ "$EXT_HTTP" = "200" ]; then
    ok "V3: http://${SERVER_IP}:80/health=$EXT_HTTP"
    VERIFY_PASS=$((VERIFY_PASS + 1))
else
    fail "V3: http://${SERVER_IP}:80/health=$EXT_HTTP"
    VERIFY_FAIL=$((VERIFY_FAIL + 1))
fi

# V4: 域名 HTTPS
if [ -n "$DOMAIN" ]; then
    info "[V4] 域名 HTTPS 验证"
    DOMAIN_HTTP=$(curl -s -o /dev/null -w "%{http_code}" --max-time 15 "https://${DOMAIN}/health" 2>/dev/null) || DOMAIN_HTTP="000"
    if [ "$DOMAIN_HTTP" = "200" ]; then
        ok "V4: https://${DOMAIN}/health=$DOMAIN_HTTP"
        VERIFY_PASS=$((VERIFY_PASS + 1))
    elif [ "$DOMAIN_HTTP" = "000" ]; then
        fail "V4: 域名不可达 — 请检查 Cloudflare DNS 配置"
        VERIFY_FAIL=$((VERIFY_FAIL + 1))
        human \
            "域名 https://${DOMAIN} 不可达，请检查:" \
            "  1. 登录 Cloudflare Dashboard" \
            "  2. 确认 DNS A 记录: $(echo "$DOMAIN" | cut -d. -f1) → ${SERVER_IP}" \
            "  3. 确认 Proxy status: Proxied (橙色云朵)" \
            "  4. SSL/TLS 加密模式: Flexible（推荐，源站无需 TLS 证书）" \
            "  5. 等待 DNS 传播 (通常 1-5 分钟)"
    else
        fail "V4: https://${DOMAIN}/health=$DOMAIN_HTTP"
        VERIFY_FAIL=$((VERIFY_FAIL + 1))
    fi
else
    info "[V4] 跳过 (未配置域名)"
fi

# V5: 功能测试 — 402 支付墙
info "[V5] 功能测试"
MINT="DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"

# 402 Paywall
if [ -n "$DOMAIN" ]; then
    GW_URL="https://${DOMAIN}"
else
    GW_URL="http://${SERVER_IP}:80"
fi
PAYWALL_HTTP=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$GW_URL/v1/audit/$MINT" 2>/dev/null) || PAYWALL_HTTP="000"
if [ "$MODE" = "test" ]; then
    if [ "$PAYWALL_HTTP" = "402" ] || [ "$PAYWALL_HTTP" = "200" ]; then
        ok "V5a: 支付墙=$PAYWALL_HTTP (test 模式)"
        VERIFY_PASS=$((VERIFY_PASS + 1))
    else
        fail "V5a: 支付墙=$PAYWALL_HTTP (期望 402 或 200)"
        VERIFY_FAIL=$((VERIFY_FAIL + 1))
    fi
else
    if [ "$PAYWALL_HTTP" = "402" ]; then
        ok "V5a: 支付墙=$PAYWALL_HTTP ($MODE 模式)"
        VERIFY_PASS=$((VERIFY_PASS + 1))
    else
        fail "V5a: 支付墙=$PAYWALL_HTTP (期望 402)"
        VERIFY_FAIL=$((VERIFY_FAIL + 1))
    fi
fi

# Direct audit (bypass gateway)
AUDIT_HTTP2=$(remote "curl -s -o /dev/null -w '%{http_code}' --max-time 15 http://localhost:8000/v1/audit/$MINT" 2>/dev/null) || AUDIT_HTTP2="000"
if [ "$AUDIT_HTTP2" = "200" ]; then
    ok "V5b: 直连审计=$AUDIT_HTTP2"
    VERIFY_PASS=$((VERIFY_PASS + 1))
else
    fail "V5b: 直连审计=$AUDIT_HTTP2 (期望 200)"
    VERIFY_FAIL=$((VERIFY_FAIL + 1))
fi

# Schema validation
AUDIT_BODY=$(remote "curl -s --max-time 15 http://localhost:8000/v1/audit/$MINT" 2>/dev/null) || AUDIT_BODY="{}"
SCHEMA_OK=true
for field in contract_address action analysis evidence metadata; do
    if ! echo "$AUDIT_BODY" | grep -q "\"$field\""; then
        SCHEMA_OK=false
        break
    fi
done
if [ "$SCHEMA_OK" = true ]; then
    ok "V5c: 审计报告 schema 完整"
    VERIFY_PASS=$((VERIFY_PASS + 1))
else
    fail "V5c: 审计报告缺少必要字段"
    VERIFY_FAIL=$((VERIFY_FAIL + 1))
fi

# Invalid address → 400
INV_HTTP=$(remote "curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://localhost:8000/v1/audit/INVALID!!!" 2>/dev/null) || INV_HTTP="000"
if [ "$INV_HTTP" = "400" ]; then
    ok "V5d: 无效地址=$INV_HTTP"
    VERIFY_PASS=$((VERIFY_PASS + 1))
else
    fail "V5d: 无效地址=$INV_HTTP (期望 400)"
    VERIFY_FAIL=$((VERIFY_FAIL + 1))
fi

# Health response content
HEALTH_BODY=$(curl -s --max-time 10 "$GW_URL/health" 2>/dev/null) || HEALTH_BODY="{}"
HEALTH_MODE=$(echo "$HEALTH_BODY" | python3 -c "import json,sys; print(json.load(sys.stdin).get('mode','unknown'))" 2>/dev/null) || HEALTH_MODE="unknown"
if [ "$MODE" = "test" ]; then
    EXPECTED_MODE="test"
else
    EXPECTED_MODE="production"
fi
if [ "$HEALTH_MODE" = "$EXPECTED_MODE" ]; then
    ok "V5e: 运行模式=$HEALTH_MODE (符合预期)"
    VERIFY_PASS=$((VERIFY_PASS + 1))
else
    warn "V5e: 运行模式=$HEALTH_MODE (期望 $EXPECTED_MODE)"
fi

# =============================================================================
# Phase 8: 部署总结
# =============================================================================
step "8/8" "部署总结"

divider
printf "\n"

if [ "$VERIFY_FAIL" -eq 0 ]; then
    printf "${GREEN}${BOLD}  ✅ 部署成功！所有验证通过 ($VERIFY_PASS/$VERIFY_PASS)${NC}\n"
else
    printf "${YELLOW}${BOLD}  ⚠️  部署完成，但有 $VERIFY_FAIL 项验证未通过${NC}\n"
fi

printf "\n"
printf "${BOLD}  访问地址:${NC}\n"
printf "    审计 API (直连):  http://%s:8000\n" "$SERVER_IP"
printf "    支付网关:         http://%s:80\n" "$SERVER_IP"
if [ -n "$DOMAIN" ]; then
    printf "    HTTPS 入口:       https://%s\n" "$DOMAIN"
fi
printf "\n"
printf "${BOLD}  配置:${NC}\n"
printf "    模式:   %s\n" "$MODE"
printf "    价格:   %s USDC / 次\n" "$PRICE"
printf "    钱包:   %s\n" "$WALLET_ADDRESS"
printf "\n"
printf "${BOLD}  快速测试:${NC}\n"
if [ -n "$DOMAIN" ]; then
    printf "    # 健康检查\n"
    printf "    curl -s https://%s/health | python3 -m json.tool\n" "$DOMAIN"
    printf "\n"
    printf "    # 402 支付墙验证\n"
    printf "    curl -s -w '\\nHTTP: %%{http_code}\\n' https://%s/v1/audit/DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263\n" "$DOMAIN"
else
    printf "    # 健康检查\n"
    printf "    curl -s http://%s:80/health | python3 -m json.tool\n" "$SERVER_IP"
fi

printf "\n"
printf "${BOLD}  Mainnet E2E 支付测试 (需要买家钱包):${NC}\n"
printf "    SOLANA_PRIVATE_KEY=<买家私钥> python3 mainnet_buyer_test.py\n"

divider

# Post-deploy suggestions
printf "\n"
if [ -n "$DOMAIN" ] && [ "$VERIFY_FAIL" -gt 0 ]; then
    human \
        "有验证未通过，请按上方提示排查修复。" \
        "修复后可手动重新验证:" \
        "  bash scripts/verify.sh --server-ip ${SERVER_IP} --domain ${DOMAIN}"
fi

if [ "$MODE" = "production" ]; then
    printf "${BOLD}${YELLOW}  ⚡ 生产模式提醒:${NC}\n"
    printf "    1. 确保卖家钱包 (%s) 有 USDC 的 ATA\n" "${WALLET_ADDRESS:0:12}..."
    printf "       (至少收到过一次 USDC 或 swap 过 USDC)\n"
    printf "    2. 建议配置 UptimeRobot 监控: https://%s/health\n" "${DOMAIN:-$SERVER_IP:80}"
    printf "    3. 定期检查日志: ssh root@%s 'cd /opt/token-rugcheck && docker compose logs --tail 50'\n" "$SERVER_IP"
    printf "\n"
fi

exit "$VERIFY_FAIL"
