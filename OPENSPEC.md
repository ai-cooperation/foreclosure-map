# 法拍物件地圖 — OpenSpec

## Context

法拍屋資訊散落在司法院 22 個法院的查詢系統中，每次只能查一個法院，且土地物件只有地號沒有座標，需要人工逐筆到地籍圖系統查詢位置。本系統將這個流程全自動化：每週爬取全台法拍公告 → 自動轉換座標 → 在地圖上標示所有物件，並保留歷史資料追蹤物件狀態變化。

公開網址：https://ai-cooperation.github.io/foreclosure-map/

---

## 1. 系統架構

```
acmacmini2 (cron 每週二 10:00)
├── scrape.py      爬取 22 法院法拍公告 (Playwright, pageSize=999)
├── geocode.py     土地: twland API / 房屋: NLSC API
├── build.py       產出 current.json + 歷史追蹤
├── git push       推送到 GitHub → Pages 自動部署
├── notify.py      Email 通知
└── run.sh         cron 入口 + 數量驗證 + Telegram 通知

GitHub Repository: ai-cooperation/foreclosure-map
├── index.html         Leaflet 靜態地圖網頁
├── css/style.css      樣式
├── js/app.js          地圖邏輯、篩選、彈窗
├── data/
│   ├── current.json   當期資料 (網頁讀取用, ~7,600 筆有座標)
│   ├── weeks/         各週快照 (2026-W07.json, ...)
│   └── history.json   物件歷史追蹤索引
├── scripts/
│   ├── scrape.py      主爬蟲 (457 行)
│   ├── geocode.py     座標轉換 (364 行)
│   ├── build.py       資料整合 (231 行)
│   ├── notify.py      Email 通知 (123 行)
│   ├── run.sh         cron 入口 + Telegram 通知 (127 行)
│   └── requirements.txt
├── notify-emails.txt  通知收件人清單
└── .gitignore         排除 .env, cache/, raw/, geocoded/
```

### 1.1 為何部署在 acmacmini2

| 考量 | 說明 |
|------|------|
| 常駐開機 | 伺服器角色，不會休眠關機 |
| 儲存充裕 | 916GB 硬碟，僅使用 19GB |
| 負載最輕 | 僅跑 Happy Coder + SSH monitor |
| 環境就緒 | Python3 + Playwright + Chromium 已安裝 |
| Git 推送 | SSH deploy key (write access) 已設定 |

---

## 2. 資料爬取 (scrape.py)

### 2.1 目標網站
- URL: `https://aomp109.judicial.gov.tw/judbp/wkw/WHD1A02.htm`
- 架構: frameset 內含兩個 iframe
  - `v1` (V1.htm): 查詢表單 (法院選單、標的類型、拍賣程序)
  - `v2` (V2.htm): 結果列表 (分頁、匯出、翻頁控制)
- 資料取得: 攔截 AJAX `WHD1A02/QUERY.htm` 回傳的 JSON
- 無 CAPTCHA，無明確 rate limit

### 2.2 核心技術: pageSize=999

法拍系統 AJAX API 的 `pageSize` 參數可由前端控制。預設值為 15 (每頁 15 筆)，V2.htm 的 UI 下拉選單支援到 999。

**v1 (舊版)**: 逐頁翻頁，每法院 20-50 個 AJAX 請求，22 法院需 30+ 分鐘。
**v2 (現行)**: 設定 `pageSize=999`，每法院 1-2 個 AJAX 請求，22 法院約 12 分鐘。

```python
# 關鍵步驟: 在 v2 iframe 中修改 pageSize 並重新查詢
v2.evaluate("""
    document.querySelector('input[name=pageSize]').value = 999;
    document.querySelector('input[name=pageNum]').value = 1;
    doPageQuery();
""")
```

Server 完全接受 `pageSize=999`，回傳欄位與 `pageSize=15` 完全相同 (51 個欄位)。

### 2.3 爬取流程

```python
for court_code, court_name in 22_COURTS.items():
    # Step 1: 開啟主頁面 (timeout=60s)
    page.goto(BASE_URL, wait_until="load", timeout=60000)
    page.wait_for_timeout(3000)

    # Step 2: 在 v1 iframe 設定查詢條件
    v1 = page.frame("v1")
    v1.select_option("select[name='court']", court_code)
    v1.click("input[name='proptype'][value='C51C52']")  # 土地+房屋
    v1.click("input[name='saletype'][value='1']")        # 一般程序

    # Step 3: 攔截 AJAX 回應 + 觸發查詢
    page.on("response", handle_response)  # 攔截 WHD1A02/QUERY.htm JSON
    v1.evaluate("doSwitch()")             # 觸發查詢 (預設 pageSize=15)
    page.wait_for_timeout(8000)

    # Step 4: 如果 totalNum > 15，用 pageSize=999 重新查詢
    if total > 15:
        v2 = page.frame("v2")
        v2.evaluate("""
            document.querySelector('input[name=pageSize]').value = 999;
            document.querySelector('input[name=pageNum]').value = 1;
            doPageQuery();
        """)
        page.wait_for_timeout(15000)  # 大量資料需較長等待

    # 每法院間隔 3 秒
    time.sleep(3)
```

### 2.4 重試機制

每個法院最多 3 次嘗試，指數退避:

| 嘗試 | 失敗後等待 | 觸發重試的情況 |
|------|-----------|--------------|
| 1 | 10 秒 | Page.goto 逾時 (60s)、找不到 iframe、無 API 回應 |
| 2 | 20 秒 | 同上 |
| 3 | (放棄) | 記入 `courts_failed` 清單 |

### 2.5 22 法院代碼

| 代碼 | 法院 | 代碼 | 法院 |
|------|------|------|------|
| TPD | 臺灣臺北 | TND | 臺灣臺南 |
| PCD | 臺灣新北 | CTD | 臺灣橋頭 |
| SLD | 臺灣士林 | KSD | 臺灣高雄 |
| TYD | 臺灣桃園 | PTD | 臺灣屏東 |
| SCD | 臺灣新竹 | TTD | 臺灣臺東 |
| MLD | 臺灣苗栗 | HLD | 臺灣花蓮 |
| TCD | 臺灣臺中 | ILD | 臺灣宜蘭 |
| NTD | 臺灣南投 | KLD | 臺灣基隆 |
| CHD | 臺灣彰化 | PHD | 臺灣澎湖 |
| ULD | 臺灣雲林 | KMD | 福建金門 |
| CYD | 臺灣嘉義 | LCD | 福建連江 |

### 2.6 API 回傳格式

```json
{
  "pageInfo": {
    "totalNum": 405,
    "pageSize": 999,
    "pageNum": 1
  },
  "data": [
    {
      "c5x": "C51",
      "crm": "113年度執字第567號",
      "crmyy": "113", "crmid": "執", "crmno": "567",
      "saledate": "11502018",
      "salenostr": "第1拍", "saleno": 1,
      "hsimun": "宜蘭縣", "ctmd": "礁溪鄉",
      "budadd": "",
      "sec": "玉石", "subsec": "", "landno": "564",
      "summinprc": 3500000, "summinprcstr": "3,500,000",
      "area3str": "250.7",
      "checkynstr": "點交", "emptyynstr": "空地",
      "rrange": "全部",
      "latitude": "", "longitude": "",
      "filenm": "abc123.pdf"
    }
  ]
}
```

共 51 個欄位，完整欄位對應:

| 欄位 | 說明 | 對應輸出 |
|------|------|---------|
| c5x | C51=土地, C52=房屋 | type |
| crm, crmyy, crmid, crmno | 案號 | case_no, id |
| saledate | 拍賣日 (YYYYMMDD 或 7 碼民國年) | auction_date |
| salenostr, saleno | 拍次 | auction_round |
| hsimun | 縣市 | county |
| ctmd | 鄉鎮市區 | district |
| budadd | 門牌地址 | address (房屋) |
| sec, subsec, landno | 段/小段/地號 | land_section, land_no (土地) |
| summinprc, summinprcstr | 底價 | min_price |
| area3str | 面積 (坪) | area |
| checkynstr | 點交/不點交 | delivery |
| emptyynstr | 空屋/空地 | vacant |
| rrange | 權利範圍 | rrange |
| latitude, longitude | 座標 (部分有) | coordinates (judicial_api) |
| filenm | PDF 檔名 | detail_url (DO_VIEWPDF.htm) |

### 2.7 輸出
`data/raw/YYYY-WNN.json` — 約 9,000 筆物件/週

### 2.8 效能比較

| 方式 | 每法院請求數 | 22 法院總請求 | 總時間 |
|------|------------|-------------|--------|
| v1 逐頁 (pageSize=15) | 20-50 | 400+ | 30+ 分鐘 |
| **v2 (pageSize=999)** | **1-2** | **22-44** | **~12 分鐘** |

### 2.9 法拍系統其他可用端點 (備用)

| 端點 | 方法 | 用途 |
|------|------|------|
| `WHD1A02/QUERY.htm` | POST | AJAX 查詢 (目前使用) |
| `WHD1A02/EXPORT.htm` | POST | 產生 Excel 匯出檔 |
| `WHD1A02/DOWNLOAD` | POST | 下載匯出的 Excel 檔 |
| `WHD1A02/DO_VIEWPDF.htm` | GET | 公告原文 PDF |

---

## 3. 座標轉換 (geocode.py)

### 3.1 土地物件 → twland.ronny.tw (免費免 key)

```
GET https://twland.ronny.tw/index/search?lands[]=宜蘭縣,玉石段,564
→ properties.xcenter, ycenter  (中心點經緯度)
→ geometry.coordinates         (地籍邊界多邊形 GeoJSON)
```

- 成功率: **79%** (5,506/6,970)
- 限制: 資料為 2015 年前地籍圖，重測後新段名查不到
- 多筆結果時以鄉鎮名稱匹配正確地段

### 3.2 房屋物件 → NLSC MapSearch API (免費免 key)

```
POST https://api.nlsc.gov.tw/MapSearch/QuerySearch
Headers:
  User-Agent: Mozilla/5.0 ...
  Content-Type: application/x-www-form-urlencoded; charset=UTF-8
  Referer: https://maps.nlsc.gov.tw/
Body: word={double-URL-encoded address}&feedback=XML&center=121.000000,24.000000
```

回傳 XML:
```xml
<RESULT>
  <ITEM>
    <LOCATION>121.543,25.041</LOCATION>
    <REMARK>門牌</REMARK>
  </ITEM>
</RESULT>
```

- 成功率: **99.5%** (2,112/2,122)
- 資料來源: 戶政門牌資料庫 (台灣最權威地址資料，門牌等級精確度)
- **關鍵技術**: 地址需雙重 URL encode

```python
import urllib.parse
encoded = urllib.parse.quote(urllib.parse.quote(address))
# "臺北市大安區" → "%25E8%2587%25BA%25E5%258C..." (雙重編碼)
data = f"word={encoded}&feedback=XML&center=121.000000,24.000000"
```

- 優先取 `REMARK` 含「門牌」的結果，其次接受任何有座標結果
- 回傳座標為 WGS84 (EPSG:4326)，格式 `longitude,latitude`

### 3.3 座標快取

```
scripts/cache/  (.gitignore, 保留本地)
├── land_cache.json      土地快取 (~5,500 筆)
└── address_cache.json   地址快取 (~1,600 筆)
```

快取邏輯:
- 首次查詢: 呼叫 API → 存入快取
- 後續查詢: 快取命中 → 直接使用，不呼叫 API
- 快取格式: `{ "地址": { "lat": 25.04, "lng": 121.54, "source": "nlsc", "cached_at": "..." } }`
- 已有 `geocode_status == "ok"` 的物件 (司法院 API 已提供座標) 直接跳過

### 3.4 整體成功率 (W07)

| 類型 | 成功 | 總數 | 成功率 |
|------|------|------|--------|
| 土地 | 5,506 | 6,970 | 79.0% |
| 房屋 | 2,112 | 2,122 | 99.5% |
| **合計** | **7,618** | **9,092** | **83.8%** |

失敗的土地物件主因: 2015 後地籍重測新段名。
失敗的房屋物件 (~10 筆): 地址欄位為「無」或「.」。

---

## 4. 資料整合 (build.py)

### 4.1 功能
1. 讀取 geocoded JSON
2. 過濾無座標物件 (只保留 geocode_status=ok)
3. 更新 history.json (物件追蹤)
4. 產出 `data/current.json` + `data/weeks/YYYY-WNN.json`

### 4.2 歷史追蹤邏輯
- 新物件: 記錄 first_seen, status=active
- 既有物件: 更新 last_seen, 記錄拍次/底價變化
- 消失物件: 標記 status=sold_or_withdrawn

### 4.3 current.json 格式 (前端讀取用)

```json
{
  "meta": {
    "generated_at": "2026-02-16T11:08:57",
    "week": "2026-W07",
    "total_count": 7618,
    "land_count": 5506,
    "building_count": 2112,
    "courts_scraped": 22,
    "geocode_success": 7618,
    "geocode_failed": 1474
  },
  "features": [
    {
      "id": "TPD-113-執-1234-1-C52",
      "type": "building",
      "court": "臺灣臺北地方法院",
      "court_code": "TPD",
      "case_no": "113年度執字第1234號",
      "auction_date": "2026-02-18",
      "auction_round": 2,
      "location": "臺北市大安區忠孝東路三段100號5樓",
      "county": "臺北市",
      "district": "大安區",
      "min_price": 15200000,
      "area": 85.3,
      "delivery": "yes",
      "vacant": "no",
      "rrange": "全部",
      "detail_url": "https://aomp109.judicial.gov.tw/.../DO_VIEWPDF.htm?filenm=...",
      "address": "臺北市大安區忠孝東路三段100號5樓",
      "coordinates": { "lat": 25.041, "lng": 121.536, "source": "nlsc" },
      "geocode_status": "ok",
      "history": {
        "first_seen": "2026-W03",
        "rounds": [
          { "week": "2026-W03", "round": 1, "price": 19000000 },
          { "week": "2026-W07", "round": 2, "price": 15200000 }
        ]
      }
    }
  ]
}
```

### 4.4 物件 ID 生成規則

```python
id = f"{court_code}-{crmyy}-{crmid}-{crmno}-{saleno}-{c5x}"
# 範例: "TPD-113-執-1234-1-C52"
# court_code: 法院代碼
# crmyy: 案件年度 (民國)
# crmid: 案件類別 (執、司執、拍等)
# crmno: 案件編號
# saleno: 拍次
# c5x: C51(土地) 或 C52(房屋)
```

---

## 5. 前端網頁

### 5.1 技術棧
- Leaflet 1.9.4 (地圖框架)
- Leaflet.markercluster 1.4.1 (標記聚合)
- NLSC WMTS 底圖 (免費免 key)
  - `https://wmts.nlsc.gov.tw/wmts/EMAP5_OPENDATA/default/GoogleMapsCompatible/{z}/{y}/{x}`
- 純靜態 HTML/CSS/JS，無框架依賴
- GitHub Pages 部署

### 5.2 地圖功能
- 房屋: 紅色圓形 marker
- 土地: 綠色圓形 marker + 地籍邊界多邊形
- MarkerCluster 聚合 (縮小時顯示數字，放大時展開)
- 預設: 全台灣 zoom 8, center [23.7, 120.9]

### 5.3 篩選控制 (左側 sidebar)
- 物件類型: 全部 / 房屋 / 土地
- 法院: 下拉選單 (動態填充)
- 縣市: 下拉選單 (動態填充)
- 拍別: 1拍~4拍 / 5拍以上
- 底價範圍: 最低~最高 (萬元)
- 點交: 全部 / 點交 / 不點交
- 權利範圍: 全部 / 全部持分 / 部分持分

### 5.4 物件彈窗 (點擊 marker)
顯示: 法院、案號、類型、坐落、面積、底價、拍別、拍賣日、權利範圍、點交、空屋/空地、歷史拍次紀錄、公告原文 PDF 連結

### 5.5 RWD
- 桌面: 篩選列在左側 sidebar
- 手機: sidebar 收合為漢堡選單，地圖全螢幕

---

## 6. 自動化排程

### 6.1 Cron (acmacmini2)
```
0 10 * * 2  /home/ac-macmini2/foreclosure-map/scripts/run.sh >> /tmp/foreclosure-map-cron.log 2>&1
```
每週二 10:00 執行。

### 6.2 run.sh 流程

```
Step 1:   scrape.py     爬取 22 法院 → data/raw/YYYY-WNN.json
Step 1.5: 數量驗證      新資料 < 上期 50% → 中止，保留舊資料
Step 2:   geocode.py    座標轉換 → data/geocoded/YYYY-WNN.json
Step 3:   build.py      資料整合 → data/current.json + weeks/
Step 4:   git push      推送到 GitHub Pages
Step 5:   notify.py     Email 通知
Step 6:   Telegram      成功/失敗通知 (含失敗法院清單)
```

### 6.3 最低數量安全閥

```bash
MIN_COUNT_RATIO=0.5  # 新資料至少要有上期 50% 的數量

PREV_COUNT=$(上期 current.json 的 total_count)
NEW_RAW_COUNT=$(本期 raw JSON 的 total_count)
MIN_REQUIRED=$((PREV_COUNT * 0.5))

if NEW_RAW_COUNT < MIN_REQUIRED:
    → 寫入 status JSON (error)
    → Telegram 告警: "爬取數量異常 N/M，已保留上期資料"
    → exit 1  (不覆蓋 current.json)
```

防止因網站異常導致少量資料覆蓋正常資料 (如 W07 初次爬取僅 245 筆 vs 上期 7,456 筆)。

### 6.4 錯誤處理
- 任一步驟失敗 → 寫入 status JSON + Telegram 告警 + 中止
- 狀態檔: `/tmp/foreclosure-map-status.json`
- 日誌檔: `/tmp/foreclosure-map-YYYY-WNN.log`
- cron 日誌: `/tmp/foreclosure-map-cron.log`

### 6.5 通知

| 管道 | 觸發 | 內容 |
|------|------|------|
| Telegram | 每次執行 (成功/失敗/數量異常) | 物件數、座標數、失敗法院、網站連結 |
| Email | 每次成功 | 週報 + 統計 + 網站連結 |

Telegram 通知範例:
```
✅ 法拍地圖更新完成
📅 2026-W07
📊 物件: 7618 筆 (有座標: 7618)
🔗 https://ai-cooperation.github.io/foreclosure-map/
```

異常中止範例:
```
⚠️ 法拍地圖更新中止
2026-W08 爬取數量異常
本期: 245 筆 (上期: 7618 筆)
低於安全門檻 (50%)，已保留上期資料
```

---

## 7. 監控整合

### 7.1 Telegram Bot `/check` 指令
acmacmini2 的 check-services.sh 包含法拍地圖狀態檢查:
```
法拍地圖 (週二 cron)：
✅ 上次更新: 2026-W07 (2026-02-16T11:08)
   物件: 7618 筆
```

### 7.2 狀態檔格式 (/tmp/foreclosure-map-status.json)
```json
{
  "status": "ok",
  "message": "更新完成",
  "week": "2026-W07",
  "updated_at": "2026-02-16T11:08:57",
  "total": 7618,
  "geocoded": 7618
}
```

status 可能值: `ok` / `error`

---

## 8. 外部服務依賴

| 服務 | 用途 | API Key | 費用 |
|------|------|---------|------|
| 司法院法拍系統 | 法拍公告資料 (AJAX API) | 不需要 | 免費 |
| twland.ronny.tw | 土地地號→座標+地籍邊界 | 不需要 | 免費 |
| NLSC MapSearch API | 地址→座標 (門牌等級) | 不需要 | 免費 |
| NLSC WMTS | 地圖底圖圖磚 | 不需要 | 免費 |
| GitHub Pages | 靜態網站部署 | 不需要 | 免費 |
| Gmail SMTP | Email 通知 | App Password | 免費 |
| Telegram Bot API | 即時通知 | Bot Token | 免費 |

**所有核心功能皆為免費服務，無 API 額度或費用限制。**

---

## 9. 環境設定

### 9.1 acmacmini2 必要環境
```bash
# ~/.env (不進 git)
export SMTP_USER='xxx@gmail.com'
export SMTP_PASS='xxxx xxxx xxxx xxxx'    # Gmail App Password
export TG_BOT_TOKEN='...'
export TG_CHAT_ID='...'

# Python 套件
pip install playwright requests
playwright install chromium

# SSH Key (GitHub push 用)
# git remote: git@github.com:ai-cooperation/foreclosure-map.git
```

### 9.2 .gitignore
```
.env
scripts/cache/
data/raw/
data/geocoded/
__pycache__/
```

### 9.3 Crontab
```
0 10 * * 2 /home/ac-macmini2/foreclosure-map/scripts/run.sh >> /tmp/foreclosure-map-cron.log 2>&1
```

---

## 10. 容量估算

| 項目 | 每週 | 一年 | 五年 |
|------|------|------|------|
| current.json | ~4 MB (覆蓋) | ~4 MB | ~4 MB |
| weeks/*.json | ~4 MB | ~208 MB | ~1 GB |
| history.json | 持續增長 | ~10 MB | ~40 MB |
| **合計** | ~8 MB | ~222 MB | ~1 GB |

GitHub 建議上限 5 GB，五年內無問題。

---

## 11. 已知限制

1. **twland API 資料過舊**: 2015 年前地籍圖，重測後新段名查不到 (21% 土地失敗主因)
2. **少數房屋地址無效**: ~10 筆地址欄位為「無」或「.」(原始資料問題)
3. **pageSize=999 假設**: 若法院單次查詢超過 999 筆物件，需分頁處理 (目前最大法院 TND 920 筆，尚有餘裕)
4. **司法院網站穩定性**: 週間上午時段相對穩定，早上 6:00 較不穩定 (已從週一 6:00 改為週二 10:00)

---

## 12. 版本歷史

| 日期 | 版本 | 變更 |
|------|------|------|
| 2026-02-10 | v0.1 | Phase 1+2: 爬蟲+座標+前端+GitHub Pages |
| 2026-02-11 | v0.2 | Phase 3: 全台 22 法院 8,298 筆 |
| 2026-02-12 | v0.3 | Phase 4: run.sh + cron + deploy key + Email |
| 2026-02-13 | v0.4 | rrange 欄位、PDF 連結修正 |
| 2026-02-14 | v0.5 | NLSC geocoding (房屋 0%→99.5%)、Telegram 監控 |
| 2026-02-16 | **v1.0** | scrape v2 (pageSize=999)、run.sh 安全閥、cron 改週二 10:00 |
