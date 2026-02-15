# 法拍物件地圖 — OpenSpec

## Context

法拍屋資訊散落在司法院 22 個法院的查詢系統中，每次只能查一個法院，且土地物件只有地號沒有座標，需要人工逐筆到地籍圖系統查詢位置。本系統將這個流程全自動化：每週爬取全台法拍公告 → 自動轉換座標 → 在地圖上標示所有物件，並保留歷史資料追蹤物件狀態變化。

公開網址：https://ai-cooperation.github.io/foreclosure-map/

---

## 1. 系統架構

```
acmacmini2 (cron 每週一 06:00)
├── scrape.py      爬取 22 法院法拍公告 (Playwright, 攔截 AJAX)
├── geocode.py     土地: twland API / 房屋: NLSC API
├── build.py       產出 current.json + 歷史追蹤
├── git push       推送到 GitHub → Pages 自動部署
├── notify.py      Email 通知
└── run.sh         Telegram 通知 (成功/失敗)

GitHub Repository: ai-cooperation/foreclosure-map
├── index.html         Leaflet 靜態地圖網頁
├── css/style.css      樣式
├── js/app.js          地圖邏輯、篩選、彈窗
├── data/
│   ├── current.json   當期資料 (網頁讀取用, ~7,500 筆有座標)
│   ├── weeks/         各週快照 (2026-W06.json, ...)
│   └── history.json   物件歷史追蹤索引
├── scripts/
│   ├── scrape.py      主爬蟲 (426 行)
│   ├── geocode.py     座標轉換 (364 行)
│   ├── build.py       資料整合 (231 行)
│   ├── notify.py      Email 通知 (123 行)
│   ├── run.sh         cron 入口 + Telegram 通知 (95 行)
│   └── requirements.txt
├── notify-emails.txt  通知收件人清單
└── .gitignore         排除 .env, cache/, raw/, geocoded/
```

---

## 2. 資料爬取 (scrape.py)

### 2.1 目標網站
- URL: `https://aomp109.judicial.gov.tw/judbp/wkw/WHD1A02.htm`
- 架構: iframe (v1 查詢表單 / v2 結果列表)
- 資料取得: 攔截 AJAX `WHD1A02/QUERY.htm` 回傳的 JSON
- 無 CAPTCHA，無明確 rate limit

### 2.2 爬取流程

```python
for court_code, court_name in 22_COURTS.items():
    page.goto(BASE_URL)
    v1 = page.frame("v1")
    v1.select_option("select[name='court']", court_code)
    v1.click("input[name='proptype'][value='C51C52']")  # 土地+房屋
    v1.click("input[name='saletype'][value='1']")        # 一般程序

    page.on("response", handle_response)  # 攔截 AJAX JSON
    v1.evaluate("doSwitch()")             # 觸發查詢

    # 自動翻頁 (v2 iframe doPageQuery())
    # 每法院間隔 3 秒
```

### 2.3 22 法院代碼

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

### 2.4 API 回傳欄位

| 欄位 | 說明 | 對應輸出 |
|------|------|---------|
| c5x | C51=土地, C52=房屋 | type |
| crm, crmyy, crmid, crmno | 案號 | case_no, id |
| saledate | 拍賣日 (YYYYMMDD 或民國) | auction_date |
| salenostr, saleno | 拍次 | auction_round |
| hsimun | 縣市 | county |
| ctmd | 鄉鎮市區 | district |
| budadd | 門牌地址 | address (房屋) |
| sec, subsec, landno | 段/小段/地號 | land_section, land_no (土地) |
| summinprc | 底價 | min_price |
| area3str | 面積 | area |
| checkynstr | 點交/不點交 | delivery |
| emptyynstr | 空屋/空地 | vacant |
| rrange | 權利範圍 | rrange |
| latitude, longitude | 座標 (部分有) | coordinates (judicial_api) |
| filenm | PDF 檔名 | detail_url (DO_VIEWPDF.htm) |

### 2.5 輸出
`data/raw/YYYY-WNN.json` — 約 8,900 筆物件/週

---

## 3. 座標轉換 (geocode.py)

### 3.1 土地物件 → twland.ronny.tw (免費免 key)

```
GET https://twland.ronny.tw/index/search?lands[]=宜蘭縣,玉石段,564
→ properties.xcenter, ycenter  (中心點經緯度)
→ geometry.coordinates         (地籍邊界多邊形 GeoJSON)
```

- 成功率: **79%** (5,408/6,847)
- 限制: 資料為 2015 年前地籍圖，重測後新段名查不到
- 多筆結果時以鄉鎮名稱匹配正確地段

### 3.2 房屋物件 → NLSC MapSearch API (免費免 key)

```
POST https://api.nlsc.gov.tw/MapSearch/QuerySearch
Headers: User-Agent, Referer: https://maps.nlsc.gov.tw/
Body: word={double-URL-encoded address}&feedback=XML&center=121,24
→ ITEM/LOCATION: longitude,latitude (WGS84)
→ ITEM/REMARK: "門牌" (建物門牌等級精確度)
```

- 成功率: **99.5%** (2,048/2,058)
- 資料來源: 戶政門牌資料庫 (台灣最權威地址資料)
- 地址需雙重 URL encode: `encodeURIComponent(encodeURIComponent(addr))`
- 優先取「門牌」類型結果，其次接受任何有座標結果

### 3.3 座標快取

```
scripts/cache/  (.gitignore, 保留本地)
├── land_cache.json      土地快取 (~5,500 筆)
└── address_cache.json   地址快取 (~1,600 筆)
```

快取命中時不呼叫 API，大幅加速重複執行。

### 3.4 整體成功率

| 類型 | 成功 | 總數 | 成功率 |
|------|------|------|--------|
| 土地 | 5,408 | 6,847 | 79.0% |
| 房屋 | 2,048 | 2,058 | 99.5% |
| **合計** | **7,456** | **8,905** | **83.7%** |

失敗的土地物件主因: 2015 後地籍重測新段名。
失敗的房屋物件 (10 筆): 地址欄位為「無」或「.」。

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
    "generated_at": "2026-02-14T22:08:26",
    "week": "2026-W06",
    "total_count": 7456,
    "land_count": 5408,
    "building_count": 2048,
    "courts_scraped": 22,
    "geocode_success": 7456,
    "geocode_failed": 1449
  },
  "features": [
    {
      "id": "TPD-113-執-1234-1-C52",
      "type": "building",
      "court": "臺灣臺北地方法院",
      "case_no": "113年度執字第1234號",
      "auction_date": "2026-02-18",
      "auction_round": 2,
      "location": "臺北市大安區忠孝東路三段100號5樓",
      "county": "臺北市",
      "min_price": 15200000,
      "area": 85.3,
      "delivery": "yes",
      "vacant": "no",
      "rrange": "全部",
      "detail_url": "https://aomp109.judicial.gov.tw/.../DO_VIEWPDF.htm?filenm=...",
      "coordinates": { "lat": 25.041, "lng": 121.536, "source": "nlsc" },
      "history": {
        "first_seen": "2026-W03",
        "rounds": [
          { "week": "2026-W03", "round": 1, "price": 19000000 },
          { "week": "2026-W06", "round": 2, "price": 15200000 }
        ]
      }
    }
  ]
}
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
0 6 * * 1  /home/ac-macmini2/foreclosure-map/scripts/run.sh
```

### 6.2 run.sh 流程
```
Step 1: scrape.py    爬取 22 法院 → data/raw/YYYY-WNN.json
Step 2: geocode.py   座標轉換    → data/geocoded/YYYY-WNN.json
Step 3: build.py     資料整合    → data/current.json + weeks/
Step 4: git push     推送到 GitHub Pages
Step 5: notify.py    Email 通知
Step 6: Telegram     成功/失敗通知
```

### 6.3 錯誤處理
- 任一步驟失敗 → 寫入 status JSON + Telegram 告警 + 中止
- 狀態檔: `/tmp/foreclosure-map-status.json`
- 日誌檔: `/tmp/foreclosure-map-YYYY-WNN.log`

### 6.4 通知
| 管道 | 觸發 | 內容 |
|------|------|------|
| Telegram | 每次執行 (成功/失敗) | 物件數、座標數、網站連結 |
| Email | 每次成功 | 週報 + 統計 + 網站連結 |

---

## 7. 監控整合

### 7.1 Telegram Bot `/check` 指令
acmacmini2 的 check-services.sh 包含法拍地圖狀態檢查:
```
法拍地圖 (週一 cron)：
✅ 上次更新: 2026-W06 (2026-02-14T22:20)
   物件: 7456 筆
```

### 7.2 狀態檔格式
```json
{
  "status": "ok",
  "message": "更新完成",
  "week": "2026-W06",
  "updated_at": "2026-02-14T22:08:26",
  "total": 7456,
  "geocoded": 7456
}
```

---

## 8. 外部服務依賴

| 服務 | 用途 | API Key | 費用 |
|------|------|---------|------|
| twland.ronny.tw | 土地地號→座標 | 不需要 | 免費 |
| NLSC MapSearch API | 地址→座標 | 不需要 | 免費 |
| NLSC WMTS | 地圖底圖圖磚 | 不需要 | 免費 |
| 司法院法拍系統 | 法拍公告資料 | 不需要 | 免費 |
| GitHub Pages | 靜態網站部署 | 不需要 | 免費 |
| Gmail SMTP | Email 通知 | App Password | 免費 |
| Telegram Bot API | 即時通知 | Bot Token | 免費 |

**所有核心功能皆為免費服務，無 API 額度或費用限制。**

---

## 9. 環境設定

### acmacmini2 必要環境
```bash
# ~/.env (不進 git)
export SMTP_USER='xxx@gmail.com'
export SMTP_PASS='xxxx xxxx xxxx xxxx'    # Gmail App Password
export TG_BOT_TOKEN='xxxxxxxxxx:xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx'
export TG_CHAT_ID='xxxxxxxxxx'

# Python 套件
pip install playwright requests
playwright install chromium

# SSH Key (GitHub push 用)
# 已設定 git@github.com:ai-cooperation/foreclosure-map.git
```

### .gitignore
```
.env
scripts/cache/
data/raw/
data/geocoded/
__pycache__/
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
