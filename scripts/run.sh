#!/bin/bash
# 法拍物件地圖 — 每週自動更新腳本
# cron: 0 6 * * 1 (每週一 06:00)
set -euo pipefail

cd /home/ac-macmini2/foreclosure-map
WEEK=$(date +%Y-W%W)
LOG="/tmp/foreclosure-map-${WEEK}.log"
STATUS_FILE="/tmp/foreclosure-map-status.json"
MIN_COUNT_RATIO=0.5  # 新資料至少要有上期 50% 的數量

# 載入環境變數 (SMTP, API keys, TG_BOT_TOKEN, TG_CHAT_ID)
if [ -f ~/.env ]; then
    source ~/.env
fi

log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG"; }

tg_notify() {
    local msg="$1"
    if [ -n "${TG_BOT_TOKEN:-}" ] && [ -n "${TG_CHAT_ID:-}" ]; then
        curl -s -X POST "https://api.telegram.org/bot${TG_BOT_TOKEN}/sendMessage" \
            -d chat_id="${TG_CHAT_ID}" \
            -d parse_mode="HTML" \
            -d text="${msg}" > /dev/null 2>&1 || true
    fi
}

write_status() {
    local status="$1" msg="$2"
    python3 -c "
import json
from datetime import datetime
d = {'status': '$status', 'message': '$msg', 'week': '$WEEK', 'updated_at': datetime.now().isoformat()}
try:
    meta = json.load(open('data/current.json'))['meta']
    d['total'] = meta.get('total_count', 0)
    d['geocoded'] = meta.get('geocode_success', 0)
except: pass
json.dump(d, open('$STATUS_FILE', 'w'))
" 2>/dev/null || true
}

log "=== 法拍地圖更新開始 ${WEEK} ==="

# 取得上期物件數 (用於最低數量驗證)
PREV_COUNT=$(python3 -c "import json; print(json.load(open('data/current.json'))['meta']['total_count'])" 2>/dev/null || echo "0")
log "上期物件數: ${PREV_COUNT}"

# Step 1: 爬取全台 22 法院
log "Step 1: 爬取法拍公告..."
if ! python3 -u scripts/scrape.py --output "data/raw/${WEEK}.json" --delay 3 2>&1 | tee -a "$LOG"; then
    log "[ERROR] 爬取失敗"
    write_status "error" "爬取失敗"
    tg_notify "❌ <b>法拍地圖更新失敗</b>
${WEEK} 爬取階段錯誤
詳見: /tmp/foreclosure-map-${WEEK}.log"
    exit 1
fi

# Step 1.5: 最低數量驗證 — 防止異常資料覆蓋
NEW_RAW_COUNT=$(python3 -c "import json; print(json.load(open('data/raw/${WEEK}.json'))['meta']['total_count'])" 2>/dev/null || echo "0")
log "本期爬取數: ${NEW_RAW_COUNT} (上期: ${PREV_COUNT})"

if [ "$PREV_COUNT" -gt 0 ] && [ "$NEW_RAW_COUNT" -gt 0 ]; then
    MIN_REQUIRED=$(python3 -c "print(int(${PREV_COUNT} * ${MIN_COUNT_RATIO}))")
    if [ "$NEW_RAW_COUNT" -lt "$MIN_REQUIRED" ]; then
        log "[ERROR] 爬取數量異常: ${NEW_RAW_COUNT} < ${MIN_REQUIRED} (上期 ${PREV_COUNT} 的 50%)"
        write_status "error" "爬取數量異常 ${NEW_RAW_COUNT}/${PREV_COUNT}"
        tg_notify "⚠️ <b>法拍地圖更新中止</b>
${WEEK} 爬取數量異常
本期: ${NEW_RAW_COUNT} 筆 (上期: ${PREV_COUNT} 筆)
低於安全門檻 (50%)，已保留上期資料
詳見: /tmp/foreclosure-map-${WEEK}.log"
        exit 1
    fi
fi

FAILED_COURTS=$(python3 -c "import json; f=json.load(open('data/raw/${WEEK}.json'))['meta'].get('courts_failed',[]); print(','.join(f) if f else 'none')" 2>/dev/null || echo "?")
log "失敗法院: ${FAILED_COURTS}"

# Step 2: 座標轉換
log "Step 2: 座標轉換..."
if ! python3 -u scripts/geocode.py --input "data/raw/${WEEK}.json" --output "data/geocoded/${WEEK}.json" 2>&1 | tee -a "$LOG"; then
    log "[ERROR] 座標轉換失敗"
    write_status "error" "座標轉換失敗"
    tg_notify "❌ <b>法拍地圖更新失敗</b>
${WEEK} 座標轉換階段錯誤"
    exit 1
fi

# Step 3: 整合資料、產出 current.json
log "Step 3: 資料整合..."
python3 scripts/build.py --input "data/geocoded/${WEEK}.json" 2>&1 | tee -a "$LOG"

# Step 4: 推送到 GitHub
log "Step 4: 推送到 GitHub..."
git add data/current.json data/history.json "data/weeks/${WEEK}.json" scripts/
git diff --cached --quiet && { log "無變更，跳過 commit"; } || {
    git commit -m "weekly update ${WEEK}

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
    git push origin main 2>&1 | tee -a "$LOG"
}

# Step 5: Email 通知
log "Step 5: 發送通知..."
python3 scripts/notify.py 2>&1 | tee -a "$LOG" || true

# 統計 & Telegram 通知
TOTAL=$(python3 -c "import json; print(json.load(open('data/current.json'))['meta']['total_count'])" 2>/dev/null || echo "?")
GEOCODED=$(python3 -c "import json; print(json.load(open('data/current.json'))['meta'].get('geocode_success', '?'))" 2>/dev/null || echo "?")

write_status "ok" "更新完成"

FAIL_MSG=""
if [ "$FAILED_COURTS" != "none" ] && [ "$FAILED_COURTS" != "?" ]; then
    FAIL_MSG="
⚠️ 失敗法院: ${FAILED_COURTS}"
fi

tg_notify "✅ <b>法拍地圖更新完成</b>
📅 ${WEEK}
📊 物件: ${TOTAL} 筆 (有座標: ${GEOCODED})${FAIL_MSG}
🔗 https://ai-cooperation.github.io/foreclosure-map/"

log "=== 更新完成! ${WEEK} 共 ${TOTAL} 筆物件 ==="
