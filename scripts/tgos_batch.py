#!/usr/bin/env python3
"""
TGOS 批次地址比對
- 從 geocoded JSON 匯出未 geocode 的房屋地址為 CSV
- 透過 Playwright 自動上傳到 TGOS 批次比對服務
- 結果會在 1-2 天後由 TGOS 發 email 通知下載

用法:
  # 匯出 CSV
  python3 tgos_batch.py export -i data/geocoded/2026-W06-full-v3.json

  # 上傳 CSV 到 TGOS
  python3 tgos_batch.py upload -f scripts/cache/tgos_upload.csv

  # 匯入 TGOS 結果 CSV，更新 geocoded JSON 並重建 current.json
  python3 tgos_batch.py import -r tgos_result.csv -i data/geocoded/2026-W06-full-v3.json
"""

import argparse
import csv
import json
import os
import sys
from datetime import datetime
from pathlib import Path


def export_addresses(input_path, output_csv):
    """匯出未 geocode 的房屋地址為 TGOS CSV 格式"""
    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    items = data.get("items", [])
    rows = []

    for item in items:
        # 只匯出房屋物件且未成功 geocode 的
        if item.get("type") != "building":
            continue
        if item.get("geocode_status") == "ok":
            continue

        address = item.get("address") or item.get("location") or ""
        if not address:
            continue

        item_id = item.get("id", "")
        rows.append({"id": item_id, "address": address})

    # 去重 (相同地址只上傳一次)
    seen_addrs = {}
    unique_rows = []
    for row in rows:
        if row["address"] not in seen_addrs:
            seen_addrs[row["address"]] = row["id"]
            unique_rows.append(row)

    # 寫入 CSV (BOM UTF-8, TGOS 格式)
    output_path = Path(output_csv)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "Address", "Response_Address", "Response_X", "Response_Y"])
        for i, row in enumerate(unique_rows, 1):
            writer.writerow([i, row["address"], "", "", ""])

    # 另存 ID 對應表 (address → item_id)
    mapping_path = output_path.with_suffix(".mapping.json")
    with open(mapping_path, "w", encoding="utf-8") as f:
        json.dump(seen_addrs, f, ensure_ascii=False, indent=2)

    print(f"匯出完成:")
    print(f"  CSV: {output_path} ({len(unique_rows)} 筆不重複地址)")
    print(f"  對應表: {mapping_path}")
    print(f"  (原始 {len(rows)} 筆房屋物件, 去重後 {len(unique_rows)} 筆)")

    return str(output_path)


def upload_csv(csv_path):
    """透過 Playwright 自動上傳 CSV 到 TGOS 批次比對服務"""
    from playwright.sync_api import sync_playwright

    api_key = os.environ.get("TGOS_BATCH_API_KEY", "")
    if not api_key:
        print("[ERROR] 未設定 TGOS_BATCH_API_KEY 環境變數")
        sys.exit(1)

    csv_path = os.path.abspath(csv_path)
    if not os.path.exists(csv_path):
        print(f"[ERROR] CSV 檔案不存在: {csv_path}")
        sys.exit(1)

    # 計算筆數
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        line_count = sum(1 for _ in f) - 1  # 扣掉 header
    print(f"上傳 CSV: {csv_path} ({line_count} 筆)")

    if line_count > 10000:
        print(f"[WARN] 超過 TGOS 每日上限 10,000 筆，可能失敗")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(
            viewport={"width": 1280, "height": 900}, locale="zh-TW"
        ).new_page()

        page.goto(
            "https://www.tgos.tw/TGOS/Addr/Compare",
            wait_until="load",
            timeout=30000,
        )
        page.wait_for_timeout(3000)

        # 接受 Cookie
        try:
            page.click("text=同意", timeout=3000)
            page.wait_for_timeout(1000)
        except Exception:
            pass

        # 填入 API KEY
        page.fill("input[name='apikey']", api_key)
        page.wait_for_timeout(500)

        # 選擇坐標系統 EPSG:4326
        srs_select = page.query_selector("select")
        if srs_select:
            srs_select.select_option(value="EPSG:4326")
        page.wait_for_timeout(300)

        # 上傳 CSV 檔案
        page.set_input_files("input[name='addrCsvFile']", csv_path)
        page.wait_for_timeout(1000)

        # 設定比對參數: 允許模糊比對 (預設)
        # 勾選: 忽略村里、忽略鄰
        try:
            ignore_village = page.query_selector("input[name='fuzzyIgnoreVillage']")
            if ignore_village and not ignore_village.is_checked():
                ignore_village.click()
            ignore_neighbor = page.query_selector("input[name='fuzzyIgnoreNeighborhood']")
            if ignore_neighbor and not ignore_neighbor.is_checked():
                ignore_neighbor.click()
        except Exception:
            pass

        page.wait_for_timeout(500)

        # 點擊「進行批次比對」
        submit_btn = page.query_selector("text=進行批次比對")
        if submit_btn:
            submit_btn.click()
            page.wait_for_timeout(5000)

            # 檢查結果
            result_text = page.inner_text("body")[:1000]
            print(f"\n提交結果:")
            # Look for success/error messages
            for line in result_text.split("\n"):
                line = line.strip()
                if any(
                    kw in line
                    for kw in ["成功", "失敗", "錯誤", "完成", "上傳", "比對", "通知"]
                ):
                    print(f"  {line}")
        else:
            print("[ERROR] 找不到提交按鈕")

        browser.close()


def import_results(result_csv, input_json, output_json=None):
    """匯入 TGOS 比對結果，更新 geocoded JSON"""
    # 讀取 TGOS 結果 CSV
    results = {}
    with open(result_csv, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            addr = row.get("Address", "").strip()
            x = row.get("Response_X", "").strip()
            y = row.get("Response_Y", "").strip()
            if addr and x and y:
                try:
                    lng = float(x)
                    lat = float(y)
                    if lng > 0 and lat > 0:
                        results[addr] = {"lat": lat, "lng": lng}
                except ValueError:
                    pass

    print(f"TGOS 結果: {len(results)} 筆有座標")

    # 讀取 mapping (address → item_id)
    mapping_path = Path(result_csv).with_suffix(".mapping.json")
    mapping = {}
    cache_mapping = Path(__file__).parent / "cache" / "tgos_upload.mapping.json"
    for mp in [mapping_path, cache_mapping]:
        if mp.exists():
            with open(mp, "r", encoding="utf-8") as f:
                mapping = json.load(f)
            break

    # 更新 geocoded JSON
    with open(input_json, "r", encoding="utf-8") as f:
        data = json.load(f)

    updated = 0
    for item in data.get("items", []):
        if item.get("geocode_status") == "ok":
            continue

        address = item.get("address") or item.get("location") or ""
        if address in results:
            coord = results[address]
            item["coordinates"] = {
                "lat": coord["lat"],
                "lng": coord["lng"],
                "source": "tgos_batch",
            }
            item["geocode_status"] = "ok"
            updated += 1

    # 更新 meta
    data["meta"]["tgos_batch_updated"] = updated
    data["meta"]["tgos_batch_at"] = datetime.now().isoformat()

    # 儲存
    if output_json is None:
        output_json = input_json
    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # 更新地址快取
    addr_cache_path = Path(__file__).parent / "cache" / "address_cache.json"
    addr_cache = {}
    if addr_cache_path.exists():
        with open(addr_cache_path, "r", encoding="utf-8") as f:
            addr_cache = json.load(f)

    for addr, coord in results.items():
        addr_cache[addr] = {
            "lat": coord["lat"],
            "lng": coord["lng"],
            "cached_at": datetime.now().isoformat(),
        }

    with open(addr_cache_path, "w", encoding="utf-8") as f:
        json.dump(addr_cache, f, ensure_ascii=False, indent=2)

    print(f"更新完成: {updated} 筆房屋物件取得座標")
    print(f"輸出: {output_json}")
    print(f"地址快取更新: {len(results)} 筆")


def main():
    parser = argparse.ArgumentParser(description="TGOS 批次地址比對工具")
    subparsers = parser.add_subparsers(dest="command")

    # export
    export_parser = subparsers.add_parser("export", help="匯出未 geocode 房屋地址為 CSV")
    export_parser.add_argument("-i", "--input", required=True, help="geocoded JSON")
    export_parser.add_argument(
        "-o", "--output", default="scripts/cache/tgos_upload.csv", help="輸出 CSV"
    )

    # upload
    upload_parser = subparsers.add_parser("upload", help="上傳 CSV 到 TGOS 批次比對")
    upload_parser.add_argument("-f", "--file", required=True, help="CSV 檔案路徑")

    # import
    import_parser = subparsers.add_parser("import", help="匯入 TGOS 結果 CSV")
    import_parser.add_argument("-r", "--result", required=True, help="TGOS 結果 CSV")
    import_parser.add_argument("-i", "--input", required=True, help="geocoded JSON")
    import_parser.add_argument("-o", "--output", default=None, help="輸出 JSON (預設覆寫)")

    args = parser.parse_args()

    if args.command == "export":
        export_addresses(args.input, args.output)
    elif args.command == "upload":
        upload_csv(args.file)
    elif args.command == "import":
        import_results(args.result, args.input, args.output)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
