# 法拍物件地圖 — 開發紀錄

## 2026-02-10 初始開發

### Session 來源
MacBook Claude Code CLI → acmacmini2 SSH

### 完成工作

#### 1. 環境設定
- acmacmini2 安裝 playwright: `pip install playwright && playwright install chromium`
- 專案目錄: `/home/ac-macmini2/foreclosure-map/`

#### 2. scrape.py 爬蟲開發
- **第一版**: 解析 DOM 表格 → 0 筆結果（失敗）
- **Debug**: 寫 debug_page.py 發現頁面是 AJAX 架構
  - 表單提交呼叫 `formUtil.submitTo()` → `WHD1A02/QUERY.htm`
  - 回傳 JSON，前端 JS 動態渲染表格
  - **意外發現: API 回傳已包含 latitude/longitude 欄位!**
- **第二版**: 改用 `page.on("response", handler)` 攔截 AJAX JSON
- **測試結果**: 宜蘭法院 (ILD) 取得 15 筆 (4 房屋 + 11 土地)
- **法院代碼修正**: 原 spec 用 TPE → 實際是 TPD

#### 3. geocode.py 座標轉換
- twland.ronny.tw API 免費查詢土地地號座標
- 測試: 15 筆中 5 筆成功 (全為土地 via twland)
- 失敗原因:
  - 5 筆土地: twland 無 2015 後重測段名 (廣興一段、新香城段、枕山一段)
  - 4 筆房屋: TGOS/Google API Key 未設定
  - 1 筆: 無段名資訊

#### 4. build.py 資料整合
- 輸出 data/current.json (5 筆有座標)
- 輸出 data/history.json
- 輸出 data/weeks/2026-W06.json

#### 5. 前端地圖
- index.html: Leaflet 地圖 + 篩選側欄
- css/style.css: RWD 響應式
- js/app.js: 標記聚合、地籍多邊形、篩選邏輯
- 底圖: NLSC WMTS (免費)

#### 6. GitHub 部署
- Repo: https://github.com/ai-cooperation/foreclosure-map
- Pages: https://ai-cooperation.github.io/foreclosure-map/
- 注意: repo 在 ai-cooperation 帳號下（非 AlanChen75）

### 測試資料
```
成功座標 (5筆):
  land  宜蘭縣礁溪鄉○○段○○小段○○○地號 → twland API
  (全部位於礁溪/員山)

失敗 (10筆):
  land  5筆 — twland 無新段名
  building  4筆 — 無 TGOS/Google key
  land  1筆 — 無段名
```

### 下一步
1. 設定 acmacmini2 GitHub SSH 金鑰 (目前 push 從 MacBook)
2. 申請 TGOS API Key (免費，審核 1-3 天)
3. 設定 Google Geocoding API Key (備用)
4. 全台 22 法院完整爬取測試
5. 寫 run.sh + cron 自動排程
6. Telegram 通知

### 重要路徑
```
專案目錄:    /home/ac-macmini2/foreclosure-map/
爬蟲:        scripts/scrape.py
座標轉換:    scripts/geocode.py
資料整合:    scripts/build.py
快取:        scripts/cache/  (不進 git)
原始資料:    data/raw/       (不進 git)
座標資料:    data/geocoded/  (不進 git)
公開資料:    data/current.json, data/weeks/, data/history.json
前端:        index.html, css/style.css, js/app.js
```

### 關鍵技術發現
1. 法拍公告系統是 AJAX 架構，不能直接解析 DOM
2. API 已回傳部分物件座標 (judicial_api source)
3. twland API 只有 2015 年前地籍圖，新段名需另外處理
4. Playwright 在 acmacmini2 (7.6GB RAM) 上運行順暢

## 2026-02-11 Phase 3 + 4 完成

### Phase 3: 全台爬取
- 修復翻頁 bug: `ht.totalCount` → `pageInfo.totalNum`
- 修復 response filter: URL 精確匹配 `/QUERY.htm`
- 全台 22 法院爬取成功: **8,298 筆** (土地 6,367 + 房屋 1,931)
- Geocode: **5,047/8,298** (60.8%) 有座標
  - 土地: 5,047/6,367 (79.3%) via twland
  - 房屋: 0/1,931 (TGOS key 待設定)
- current.json 14MB, 5,047 筆地圖物件

### Phase 4: 自動化
- run.sh 自動化腳本 (scrape → geocode → build → push → notify)
- cron job: 每週一 06:00 `0 6 * * 1`
- email 通知腳本 notify.py 已寫好
- SMTP 待設定 (需 Gmail 應用程式密碼)

### 各法院物件數
```
TPD 290  PCD 320  SLD 317  TYD 459  SCD 710  MLD 318
TCD 694  NTD 160  CHD 406  ULD 273  CYD 687  TND 870
CTD 356  KSD 277  PTD 456  TTD 199  HLD 354  ILD 349
KLD 518  PHD 236  KMD 45   LCD 4
```

### 待完成
- SMTP_USER / SMTP_PASS 設定 (Gmail 應用程式密碼)
- TGOS API Key → geocode.py 房屋座標
- twland 失敗 fallback (easymap)
