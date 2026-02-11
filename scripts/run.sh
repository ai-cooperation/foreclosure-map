#!/bin/bash
# 法拍物件地圖 — 每週自動更新腳本
# cron: 0 6 * * 1 (每週一 06:00)
set -euo pipefail

cd /home/ac-macmini2/foreclosure-map
WEEK=$(date +%Y-W%W)
LOG="/tmp/foreclosure-map-${WEEK}.log"

log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG"; }

log "=== 法拍地圖更新開始 ${WEEK} ==="

# 載入環境變數 (SMTP, API keys)
if [ -f ~/.env ]; then
    source ~/.env
fi

# Step 1: 爬取全台 22 法院
log "Step 1: 爬取法拍公告..."
python3 -u scripts/scrape.py --output "data/raw/${WEEK}.json" --delay 3 2>&1 | tee -a "$LOG"
SCRAPE_EXIT=$?

if [ $SCRAPE_EXIT -ne 0 ]; then
    log "[ERROR] 爬取失敗 (exit $SCRAPE_EXIT)"
    python3 scripts/notify.py --error "爬取失敗" 2>/dev/null || true
    exit 1
fi

# Step 2: 座標轉換
log "Step 2: 座標轉換..."
python3 -u scripts/geocode.py --input "data/raw/${WEEK}.json" --output "data/geocoded/${WEEK}.json" 2>&1 | tee -a "$LOG"

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

# 統計
TOTAL=$(python3 -c "import json; print(json.load(open('data/current.json'))['meta']['total_count'])" 2>/dev/null || echo "?")
log "=== 更新完成! ${WEEK} 共 ${TOTAL} 筆物件 ==="
