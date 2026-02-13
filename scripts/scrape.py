#!/usr/bin/env python3
"""
法拍公告爬蟲 - 從司法院法拍公告系統爬取法拍物件資料
透過攔截 AJAX API (WHD1A02/QUERY.htm) 回傳的 JSON 取得結構化資料
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# 22 個地方法院代碼 (從 debug 取得的實際 select option values)
COURTS = {
    "TPD": "臺灣臺北地方法院",
    "PCD": "臺灣新北地方法院",
    "SLD": "臺灣士林地方法院",
    "TYD": "臺灣桃園地方法院",
    "SCD": "臺灣新竹地方法院",
    "MLD": "臺灣苗栗地方法院",
    "TCD": "臺灣臺中地方法院",
    "NTD": "臺灣南投地方法院",
    "CHD": "臺灣彰化地方法院",
    "ULD": "臺灣雲林地方法院",
    "CYD": "臺灣嘉義地方法院",
    "TND": "臺灣臺南地方法院",
    "CTD": "臺灣橋頭地方法院",
    "KSD": "臺灣高雄地方法院",
    "PTD": "臺灣屏東地方法院",
    "TTD": "臺灣臺東地方法院",
    "HLD": "臺灣花蓮地方法院",
    "ILD": "臺灣宜蘭地方法院",
    "KLD": "臺灣基隆地方法院",
    "PHD": "臺灣澎湖地方法院",
    "KMD": "福建金門地方法院",
    "LCD": "福建連江地方法院",
}

BASE_URL = "https://aomp109.judicial.gov.tw/judbp/wkw/WHD1A02.htm"


def generate_item_id(court_code, crmyy, crmid, crmno, saleno="", c5x=""):
    """產生唯一物件 ID"""
    return f"{court_code}-{crmyy}-{crmid}-{crmno}-{saleno}-{c5x}".strip("-")


def parse_api_item(raw, court_code, court_name):
    """解析 API 回傳的單一物件資料"""
    # 判斷物件類型
    c5x = raw.get("c5x", "")
    if c5x == "C51":
        item_type = "land"
    elif c5x == "C52":
        item_type = "building"
    else:
        item_type = "unknown"

    # 案號
    case_no = raw.get("crm", "")
    crmyy = raw.get("crmyy", "")
    crmid = raw.get("crmid", "")
    crmno = raw.get("crmno", "")

    # 拍賣日期 (格式: YYYYMMDD 或 民國年)
    saledate_raw = raw.get("saledate", "")
    auction_date = None
    if saledate_raw:
        saledate_str = str(saledate_raw)
        if len(saledate_str) == 8:
            try:
                y = int(saledate_str[:4])
                if y < 200:
                    y += 1911
                auction_date = f"{y}-{saledate_str[4:6]}-{saledate_str[6:8]}"
            except ValueError:
                pass
        elif len(saledate_str) == 7:
            try:
                y = int(saledate_str[:3]) + 1911
                auction_date = f"{y}-{saledate_str[3:5]}-{saledate_str[5:7]}"
            except ValueError:
                pass

    # 拍次
    salenostr = raw.get("salenostr", "")
    auction_round = None
    round_match = re.search(r"第(\d+)拍", salenostr)
    if round_match:
        auction_round = int(round_match.group(1))
    else:
        saleno = raw.get("saleno")
        if saleno:
            try:
                auction_round = int(saleno)
            except (ValueError, TypeError):
                pass

    # 價格
    min_price = None
    summinprc = raw.get("summinprc")
    if summinprc:
        try:
            min_price = int(summinprc)
        except (ValueError, TypeError):
            pass
    if not min_price:
        price_str = raw.get("summinprcstr", "").replace(",", "")
        if price_str:
            try:
                min_price = int(price_str)
            except ValueError:
                pass

    # 面積
    area = None
    area_str = raw.get("area3str", "")
    if area_str:
        area_match = re.search(r"([\d.]+)", area_str.replace(",", ""))
        if area_match:
            try:
                area = float(area_match.group(1))
            except ValueError:
                pass

    # 座標 (API 已提供!)
    lat = raw.get("latitude")
    lng = raw.get("longitude")
    coordinates = None
    if lat and lng:
        try:
            lat_f = float(lat)
            lng_f = float(lng)
            if lat_f > 0 and lng_f > 0:
                coordinates = {
                    "lat": lat_f,
                    "lng": lng_f,
                    "source": "judicial_api",
                }
        except (ValueError, TypeError):
            pass

    # 地址/坐落
    budadd = raw.get("budadd", "")
    hsimun = raw.get("hsimun", "")
    ctmd = raw.get("ctmd", "")
    sec = raw.get("sec", "")
    subsec = raw.get("subsec", "")
    landno = raw.get("landno", "")

    if item_type == "land":
        location = f"{hsimun}{ctmd}{sec}段 {subsec}小段 {landno}號".replace("  ", " ").strip()
        if not sec:
            location = budadd or f"{hsimun}{ctmd}"
    else:
        location = budadd or f"{hsimun}{ctmd}"

    # 點交
    checkyn = raw.get("checkynstr", "").strip()
    if "點交" in checkyn and "不" not in checkyn:
        delivery = "yes"
    elif "不點交" in checkyn:
        delivery = "no"
    else:
        delivery = "unknown"

    # 空屋/空地
    emptyyn = raw.get("emptyynstr", "").strip()
    if emptyyn and emptyyn != "":
        vacant = "yes"
    else:
        vacant = "unknown"

    # 權利範圍
    rrange = raw.get("rrange", "").strip()

    # 公告 PDF 連結 (DO_VIEWPDF.htm 直接提供 PDF 下載)
    filenm = raw.get("filenm", "")
    detail_url = None
    if filenm:
        detail_url = f"https://aomp109.judicial.gov.tw/judbp/wkw/WHD1A02/DO_VIEWPDF.htm?filenm={filenm}"

    item = {
        "id": generate_item_id(court_code, crmyy, crmid, crmno, str(raw.get("saleno", "")), c5x),
        "type": item_type,
        "court": court_name,
        "court_code": court_code,
        "case_no": case_no,
        "auction_date": auction_date,
        "auction_round": auction_round,
        "location": location,
        "county": hsimun or None,
        "district": ctmd or None,
        "min_price": min_price,
        "area": area,
        "delivery": delivery,
        "vacant": vacant,
        "rrange": rrange or None,
        "detail_url": detail_url,
    }

    # 座標
    if coordinates:
        item["coordinates"] = coordinates
        item["geocode_status"] = "ok"

    # 土地欄位
    if item_type == "land" and sec:
        full_section = sec + "段"
        if subsec:
            full_section += subsec + "小段"
        item["land_section"] = full_section
        item["land_no"] = landno

    # 房屋欄位
    if item_type == "building" and budadd:
        item["address"] = budadd

    return item


def scrape_court(page, court_code, court_name, prop_types=None):
    """爬取單一法院的法拍公告，回傳物件列表"""
    if prop_types is None:
        prop_types = ["C51C52"]  # 房屋或土地 (一次取得全部)

    all_items = []

    for proptype in prop_types:
        print(f"\n  爬取 {court_name} ({court_code}), 標的={proptype}")

        try:
            page.goto(BASE_URL, wait_until="load", timeout=30000)
            page.wait_for_timeout(2000)

            v1 = page.frame("v1")
            if not v1:
                print(f"    [ERROR] 找不到 v1 iframe")
                continue

            v1.wait_for_selector("select", timeout=10000)

            # 選擇法院
            v1.locator("select[name='court']").select_option(value=court_code)
            v1.wait_for_timeout(500)

            # 選擇拍賣標的
            v1.locator(f"input[name='proptype'][value='{proptype}']").click()
            v1.wait_for_timeout(300)

            # 選擇一般程序
            v1.locator("input[name='saletype'][value='1']").click()
            v1.wait_for_timeout(300)

            # 攔截 API response
            captured_responses = []

            def handle_response(response):
                if response.url.endswith("/QUERY.htm") and "WHD1A02" in response.url:
                    try:
                        body = response.json()
                        captured_responses.append(body)
                    except Exception:
                        pass

            page.on("response", handle_response)

            # 點擊查詢
            v1.evaluate("doSwitch()")
            page.wait_for_timeout(5000)

            # 處理所有頁面
            page_num = 1
            total_this_type = 0

            while True:
                if not captured_responses:
                    print(f"    第 {page_num} 頁: 無 API 回應")
                    break

                resp_data = captured_responses[-1]
                captured_responses.clear()

                items_data = resp_data.get("data", [])

                if not items_data:
                    if page_num == 1:
                        print(f"    無資料")
                    break

                for raw in items_data:
                    item = parse_api_item(raw, court_code, court_name)
                    all_items.append(item)
                    total_this_type += 1

                print(f"    第 {page_num} 頁: {len(items_data)} 筆")

                # 檢查是否有下一頁
                # API 回傳分頁資訊在 pageInfo 欄位
                page_info = resp_data.get("pageInfo", {})
                total_count = page_info.get("totalNum", 0)
                page_size = page_info.get("pageSize", 15)

                if total_count == 0 or page_num * page_size >= total_count:
                    break

                # 翻頁: 在 v2 frame 中操作
                v2 = page.frame("v2")
                if v2:
                    try:
                        # 設定下一頁頁碼
                        v2.evaluate(f"""
                            document.querySelector('input[name=pageNum]').value = {page_num + 1};
                            doPageQuery();
                        """)
                        page.wait_for_timeout(3000)
                        page_num += 1
                    except Exception as e:
                        print(f"    翻頁失敗: {e}")
                        break
                else:
                    break

                if page_num > 100:
                    print(f"    [WARN] 超過 100 頁，停止")
                    break

            page.remove_listener("response", handle_response)
            print(f"    {proptype} 共 {total_this_type} 筆")

        except PlaywrightTimeout as e:
            print(f"    [ERROR] 逾時: {e}")
        except Exception as e:
            print(f"    [ERROR] 爬取失敗: {e}")

    return all_items


def main():
    parser = argparse.ArgumentParser(description="法拍公告爬蟲")
    parser.add_argument("--output", "-o", default=None, help="輸出 JSON 檔案路徑")
    parser.add_argument("--courts", "-c", nargs="+", default=None, help="指定法院代碼 (e.g. ILD TPD)")
    parser.add_argument("--headless", action="store_true", default=True, help="無頭模式")
    parser.add_argument("--delay", type=float, default=3.0, help="法院間等待秒數 (預設: 3)")
    args = parser.parse_args()

    if args.output:
        output_path = Path(args.output)
    else:
        week = datetime.now().strftime("%Y-W%W")
        output_path = Path(f"data/raw/{week}.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if args.courts:
        target_courts = {c: COURTS[c] for c in args.courts if c in COURTS}
        if not target_courts:
            print(f"[ERROR] 找不到指定法院: {args.courts}")
            print(f"可用: {', '.join(COURTS.keys())}")
            sys.exit(1)
    else:
        target_courts = COURTS

    print(f"法拍公告爬蟲啟動")
    print(f"目標法院: {len(target_courts)} 個")
    print(f"輸出: {output_path}")
    print(f"時間: {datetime.now().isoformat()}")

    all_results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless)
        context = browser.new_context(viewport={"width": 1280, "height": 900}, locale="zh-TW")
        page = context.new_page()

        for court_code, court_name in target_courts.items():
            print(f"\n{'='*50}")
            print(f"{court_name} ({court_code})")
            print(f"{'='*50}")

            items = scrape_court(page, court_code, court_name)
            all_results.extend(items)

            time.sleep(args.delay)

        browser.close()

    # 統計
    type_counts = {}
    geocoded = 0
    for item in all_results:
        t = item.get("type", "unknown")
        type_counts[t] = type_counts.get(t, 0) + 1
        if item.get("geocode_status") == "ok":
            geocoded += 1

    # 輸出
    output_data = {
        "meta": {
            "generated_at": datetime.now().isoformat(),
            "week": datetime.now().strftime("%Y-W%W"),
            "total_count": len(all_results),
            "courts_scraped": len(target_courts),
            "courts_list": list(target_courts.keys()),
            "type_counts": type_counts,
            "pre_geocoded": geocoded,
        },
        "items": all_results,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*50}")
    print(f"爬取完成!")
    print(f"總計: {len(all_results)} 筆")
    print(f"已有座標: {geocoded} 筆")
    for t, c in sorted(type_counts.items()):
        print(f"  {t}: {c}")
    print(f"輸出: {output_path}")


if __name__ == "__main__":
    main()
