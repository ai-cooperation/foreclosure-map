# 法拍物件地圖系統 — OpenSpec

## Context

法拍屋資訊散落在司法院 22 個法院的查詢系統中，每次只能查一個法院，且土地物件只有地號沒有座標，需要人工逐筆到地籍圖系統查詢位置。本系統將這個流程全自動化：每週爬取全台法拍公告 → 自動轉換座標 → 在地圖上標示所有物件，並保留歷史資料追蹤物件狀態變化。

---

## 1. 系統架構

```
acmacmini2 (cron 每週一次)
├── 1. scraper/        爬取 22 法院法拍公告 (Playwright)
├── 2. geocoder/       地號→座標 / 地址→座標
├── 3. builder/        產出 data/ JSON 檔案
└── 4. git push        推送到 GitHub → Pages 自動部署

GitHub Repository: foreclosure-map
├── index.html         Leaflet 靜態地圖網頁
├── css/style.css
├── js/app.js          地圖邏輯、篩選、彈窗
├── data/
│   ├── current.json   當期資料（網頁讀取用）
│   ├── weeks/
│   │   ├── 2026-W07.json
│   │   └── ...
│   └── history.json   物件歷史追蹤索引
└── scripts/           爬取 & 處理腳本（也放 repo 方便版控）
    ├── scrape.py       主爬蟲
    ├── geocode.py      座標轉換
    ├── build.py        資料整合 & JSON 產出
    ├── run.sh          cron 入口腳本
    └── requirements.txt
```

公開網址：`https://ai-cooperation.github.io/foreclosure-map/`

---

## 2. 資料爬取模組 (scraper)

### 2.1 目標網站
- URL: `https://aomp109.judicial.gov.tw/judbp/wkw/WHD1A02.htm`
- 架構: iframe (V1.htm 查詢表單 / V2.htm 結果列表)
- 提交方式: JavaScript `formUtil.submitTo()`, POST → AJAX JSON 回傳
- 無 CAPTCHA，無明確 rate limit

### 2.2 技術選型
- **Python + Playwright** (非 Selenium)
  - 理由: 更現代、API 更簡潔、原生支援 iframe 切換、headless 效能好
  - acmacmini2 上 `pip install playwright && playwright install chromium`

### 2.3 爬取流程 (實際實作)

**重要發現**: 網站不是靜態 HTML 表格，而是 AJAX API：
- 表單提交後呼叫 `WHD1A02/QUERY.htm`，回傳 JSON
- 爬蟲使用 `page.on("response", handler)` 攔截 API 回應
- API 回傳已包含 `latitude`/`longitude` 欄位（部分物件有座標）

```python
for court in 22_COURTS:
    1. page.goto(BASE_URL)，切入 v1 iframe
    2. 選擇法院 select[name='court']
    3. 選擇標的類型 input[name='proptype'] value='C51C52'
    4. 選擇一般程序 input[name='saletype'] value='1'
    5. page.on("response", handler) 攔截 AJAX
    6. v1.evaluate("doSwitch()") 觸發查詢
    7. 從 captured_responses 取得 JSON data
    8. 自動翻頁 (v2 iframe doPageQuery())
    9. 每法院間隔 3 秒
```

### 2.4 法院代碼 (實測取得)
```
TPD=臺灣臺北, PCD=新北, SLD=士林, TYD=桃園, SCD=新竹,
MLD=苗栗, TCD=臺中, NTD=南投, CHD=彰化, ULD=雲林,
CYD=嘉義, TND=臺南, CTD=橋頭, KSD=高雄, PTD=屏東,
TTD=臺東, HLD=花蓮, ILD=宜蘭, KLD=基隆, PHD=澎湖,
KMD=金門, LCD=連江
```

### 2.5 API 回傳欄位
```
crm, crmyy, crmid, crmno  (案號)
saledate, salenostr, saleno (拍賣日/拍次)
hsimun, ctmd               (縣市/鄉鎮)
budadd                     (門牌地址)
sec, subsec, landno        (段/小段/地號)
summinprc, summinprcstr    (底價)
area3str                   (面積)
checkynstr, emptyynstr     (點交/空屋)
latitude, longitude        (座標，部分有)
c5x                        (C51=土地, C52=房屋)
filenm, para               (公告連結參數)
```

---

## 3. 座標轉換模組 (geocoder)

### 3.1 土地物件 (地號 → 座標)
**主要**: twland.ronny.tw API (免費、免 key、回傳 GeoJSON 多邊形)
```
GET https://twland.ronny.tw/index/search?lands[]=宜蘭縣,玉石段,564
→ properties.xcenter, properties.ycenter (中心點)
→ geometry.coordinates (地籍邊界多邊形)
```
- 限制: 資料為 2015 年前地籍圖，2015 後重測的段名查不到
- 已知失敗案例: 廣興一段、新香城段、枕山一段 (重測後新段名)

### 3.2 房屋物件 (地址 → 座標)
**主要**: TGOS 地址比對服務 (需申請 API Key，尚未設定)
**備用**: Google Geocoding API (需 API Key，尚未設定)
**Fallback**: 若 API 回傳已有 latitude/longitude，直接使用 (source: judicial_api)

### 3.3 座標快取
```
scripts/cache/
├── land_cache.json      { "宜蘭縣,玉石段,564": {lat, lng, polygon, cached_at} }
└── address_cache.json   { "宜蘭縣礁溪鄉溫泉路100號": {lat, lng, cached_at} }
```

---

## 4. 資料格式

### current.json (網頁載入用)
```json
{
  "meta": { "generated_at", "week", "total_count", "courts_scraped", "type_counts", "pre_geocoded" },
  "items": [
    {
      "id", "type", "court", "court_code", "case_no",
      "auction_date", "auction_round", "location",
      "county", "district", "min_price", "area",
      "delivery", "vacant", "detail_url",
      "coordinates": { "lat", "lng", "source", "polygon" },
      "geocode_status", "land_section", "land_no", "address"
    }
  ]
}
```

### history.json (物件追蹤索引)
追蹤物件拍次變化、底價趨勢、是否已拍定/撤回

---

## 5. 前端網頁 (Leaflet)

- Leaflet 1.9.4 + MarkerCluster
- NLSC WMTS 底圖 (免費免 key)
- 篩選: 物件類型、法院、縣市、拍別、點交、底價範圍
- 標記: 紅色=房屋, 綠色=土地, 土地物件顯示地籍多邊形
- RWD 響應式 (手機版漢堡選單)

---

## 6. 自動化排程 (待實作)

每週一 06:00 cron:
scrape.py → geocode.py → build.py → git push → Telegram 通知

---

## 7. 實作進度

### Phase 1: 基礎驗證 ✅
- [x] acmacmini2 安裝 Python + Playwright
- [x] scrape.py 爬取宜蘭法院 (15 筆: 4 房屋 + 11 土地)
- [x] geocode.py 座標轉換 (5/15 成功，全為土地 via twland)
- [ ] 手動驗證資料正確性

### Phase 2: 前端地圖 ✅
- [x] index.html + css/style.css + js/app.js
- [x] GitHub repo 建立 + Pages 啟用
- [x] 公開網址: https://ai-cooperation.github.io/foreclosure-map/

### Phase 3: 全台擴展 (待執行)
- [ ] scrape.py 已支援 22 法院，需完整跑一次
- [ ] build.py 歷史追蹤邏輯已寫好

### Phase 4: 自動化 (待執行)
- [ ] 寫 run.sh + 設定 cron job
- [ ] Telegram 通知
- [ ] acmacmini2 GitHub SSH 金鑰設定

### Phase 5: 優化 (待執行)
- [ ] TGOS / Google API Key 設定 (房屋座標)
- [ ] twland 失敗的 fallback (easymap browser)
- [ ] RWD 優化
- [ ] 歷史趨勢顯示

---

## 8. 已知問題

1. **GitHub 帳號**: repo 建在 ai-cooperation 帳號下，非 AlanChen75（MacBook gh CLI 登入帳號不同）
2. **acmacmini2 無 GitHub SSH**: `ssh -T git@github.com` 失敗，需加 SSH key
3. **twland 2015 限制**: 重測後新段名查無資料 (廣興一段、新香城段、枕山一段)
4. **房屋座標缺失**: TGOS/Google API Key 尚未設定，4 筆房屋全部 geocode 失敗
5. **OpenSpec 公開網址需更新**: 原設計 alanchen75.github.io → 實際 ai-cooperation.github.io
