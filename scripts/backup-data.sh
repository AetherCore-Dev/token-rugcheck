#!/usr/bin/env bash
# ============================================================================
# backup-data.sh — ag402 SQLite 重放保护数据备份
#
# 功能:
#   1. 从 Docker volume 中备份 ag402-data SQLite 文件
#   2. 支持本地保留最近 N 份备份（默认 30）
#   3. 可选上传到远程存储（S3/SCP）
#
# 用法:
#   bash scripts/backup-data.sh                    # 本地备份
#   bash scripts/backup-data.sh --remote scp       # 备份并 SCP 到远程
#   bash scripts/backup-data.sh --keep 7           # 只保留最近 7 份
#
# 建议 crontab:
#   0 3 * * * cd /opt/token-rugcheck-mcp && bash scripts/backup-data.sh >> /var/log/rugcheck-backup.log 2>&1
# ============================================================================

set -euo pipefail

# --- Configuration ---
BACKUP_DIR="${BACKUP_DIR:-./backups}"
KEEP_COUNT="${KEEP_COUNT:-30}"
VOLUME_NAME="token-rugcheck_ag402-data"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
REMOTE_METHOD=""

# --- Parse args ---
while [[ $# -gt 0 ]]; do
    case $1 in
        --keep)    KEEP_COUNT="$2"; shift 2 ;;
        --remote)  REMOTE_METHOD="$2"; shift 2 ;;
        --dir)     BACKUP_DIR="$2"; shift 2 ;;
        *)         echo "Unknown arg: $1"; exit 1 ;;
    esac
done

echo "=== ag402 Data Backup — $(date) ==="

# --- Step 1: Create backup directory ---
mkdir -p "$BACKUP_DIR"

# --- Step 2: Check if volume exists ---
if ! docker volume inspect "$VOLUME_NAME" &>/dev/null; then
    echo "[WARN] Docker volume '$VOLUME_NAME' not found."
    echo "[WARN] Trying alternative name patterns..."
    # Try common variations
    for alt in "ag402-data" "token-rugcheck-mcp_ag402-data" "token_rugcheck_mcp_ag402-data" "token-rugcheck_ag402-data"; do
        if docker volume inspect "$alt" &>/dev/null; then
            VOLUME_NAME="$alt"
            echo "[INFO] Found volume: $VOLUME_NAME"
            break
        fi
    done

    if ! docker volume inspect "$VOLUME_NAME" &>/dev/null; then
        echo "[ERROR] No ag402-data volume found. Is the service deployed?"
        exit 1
    fi
fi

# --- Step 3: Backup via temporary container ---
BACKUP_FILE="$BACKUP_DIR/ag402-data_${TIMESTAMP}.tar.gz"

echo "[INFO] Backing up volume '$VOLUME_NAME'..."
docker run --rm \
    -v "${VOLUME_NAME}:/data:ro" \
    -v "$(cd "$BACKUP_DIR" && pwd):/backup" \
    alpine:3.19 \
    sh -c "cd /data && tar czf /backup/ag402-data_${TIMESTAMP}.tar.gz ."

if [[ -f "$BACKUP_FILE" ]]; then
    SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
    echo "[OK] Backup created: $BACKUP_FILE ($SIZE)"
else
    echo "[ERROR] Backup file not created!"
    exit 1
fi

# --- Step 4: SQLite integrity check (optional) ---
# Extract and verify if sqlite3 is available
if command -v sqlite3 &>/dev/null; then
    TEMP_DIR=$(mktemp -d)
    tar xzf "$BACKUP_FILE" -C "$TEMP_DIR" 2>/dev/null || true
    for db_file in "$TEMP_DIR"/*.db "$TEMP_DIR"/*.sqlite; do
        if [[ -f "$db_file" ]]; then
            INTEGRITY=$(sqlite3 "$db_file" "PRAGMA integrity_check;" 2>/dev/null || echo "error")
            if [[ "$INTEGRITY" == "ok" ]]; then
                echo "[OK] SQLite integrity check passed: $(basename "$db_file")"
            else
                echo "[WARN] SQLite integrity check failed: $(basename "$db_file") — $INTEGRITY"
            fi
        fi
    done
    rm -rf "$TEMP_DIR"
fi

# --- Step 5: Rotate old backups ---
BACKUP_COUNT=$(ls -1 "$BACKUP_DIR"/ag402-data_*.tar.gz 2>/dev/null | wc -l)
if [[ "$BACKUP_COUNT" -gt "$KEEP_COUNT" ]]; then
    DELETE_COUNT=$((BACKUP_COUNT - KEEP_COUNT))
    echo "[INFO] Rotating: removing $DELETE_COUNT old backup(s), keeping $KEEP_COUNT..."
    ls -1t "$BACKUP_DIR"/ag402-data_*.tar.gz | tail -n "$DELETE_COUNT" | xargs rm -f
    echo "[OK] Rotation complete."
else
    echo "[INFO] $BACKUP_COUNT backup(s), within limit of $KEEP_COUNT."
fi

# --- Step 6: Optional remote upload ---
if [[ -n "$REMOTE_METHOD" ]]; then
    case "$REMOTE_METHOD" in
        scp)
            REMOTE_HOST="${BACKUP_REMOTE_HOST:-}"
            REMOTE_PATH="${BACKUP_REMOTE_PATH:-/opt/backups/rugcheck/}"
            if [[ -z "$REMOTE_HOST" ]]; then
                echo "[WARN] BACKUP_REMOTE_HOST not set, skipping remote upload."
            else
                echo "[INFO] Uploading to $REMOTE_HOST:$REMOTE_PATH..."
                scp "$BACKUP_FILE" "${REMOTE_HOST}:${REMOTE_PATH}" && \
                    echo "[OK] Remote upload complete." || \
                    echo "[WARN] Remote upload failed."
            fi
            ;;
        s3)
            S3_BUCKET="${BACKUP_S3_BUCKET:-}"
            if [[ -z "$S3_BUCKET" ]]; then
                echo "[WARN] BACKUP_S3_BUCKET not set, skipping S3 upload."
            else
                echo "[INFO] Uploading to s3://$S3_BUCKET/rugcheck/..."
                aws s3 cp "$BACKUP_FILE" "s3://${S3_BUCKET}/rugcheck/" && \
                    echo "[OK] S3 upload complete." || \
                    echo "[WARN] S3 upload failed."
            fi
            ;;
        *)
            echo "[WARN] Unknown remote method: $REMOTE_METHOD (supported: scp, s3)"
            ;;
    esac
fi

echo "=== Backup complete ==="
