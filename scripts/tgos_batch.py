#!/usr/bin/env python3
"""
TGOS 批次地址比對工具
- export: 匯出未 geocode 的房屋地址為 CSV，發送到 Telegram
- import: 匯入 TGOS 結果 CSV，更新座標
- notify: 發送 Telegram 提醒上傳

流程 (每週):
  1. run.sh 執行 export → CSV + Telegram 通知
  2. 你到 TGOS 網站手動上傳 CSV (已用 Google 登入)
  3. 1-2 天後收到 TGOS email → 下載結果 CSV
  4. 執行 import → 更新座標 → 重新 build + push
"""

import argparse
import csv
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import requests


TG_API = "https://api.telegram.org/bot"
TGOS_BATCH_URL = "https://www.tgos.tw/TGOS/Addr/Compare"


def tg_send_message(text):
    """發送 Telegram 訊息"""
    bot_token = os.environ.get("TG_BOT_TOKEN", "")
    chat_id = os.environ.get("TG_CHAT_ID", "")
    if not bot_token or not chat_id:
        print("  [SKIP] TG_BOT_TOKEN/TG_CHAT_ID 未設定")
        return
    url = f"{TG_API}{bot_token}/sendMessage"
    requests.post(url, data={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}, timeout=15)


def tg_send_file(file_path, caption=""):
    """發送檔案到 Telegram"""
    bot_token = os.environ.get("TG_BOT_TOKEN", "")
    chat_id = os.environ.get("TG_CHAT_ID", "")
    if not bot_token or not chat_id:
        print("  [SKIP] TG_BOT_TOKEN/TG_CHAT_ID 未設定")
        return
    url = f"{TG_API}{bot_token}/sendDocument"
    with open(file_path, "rb") as f:
        requests.post(url, data={"chat_id": chat_id, "caption": caption},
                      files={"document": (os.path.basename(file_path), f)}, timeout=30)


# === EXPORT ===

def export_addresses(input_path, output_csv, notify=True):
    """匯出未 geocode 的房屋地址為 TGOS CSV 格式"""
    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    items = data.get("items", [])
    rows = []

    for item in items:
        if item.get("type") != "building":
            continue
        if item.get("geocode_status") == "ok":
            continue

        address = item.get("address") or item.get("location") or ""
        if not address:
            continue

        item_id = item.get("id", "")
        rows.append({"id": item_id, "address": address})

    # 去重
    seen_addrs = {}
    unique_rows = []
    for row in rows:
        if row["address"] not in seen_addrs:
            seen_addrs[row["address"]] = row["id"]
            unique_rows.append(row)

    if not unique_rows:
        print("無需 TGOS 比對的地址 (全部已有座標)")
        return None

    # 寫入 CSV (BOM UTF-8, TGOS 格式)
    output_path = Path(output_csv)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "Address", "Response_Address", "Response_X", "Response_Y"])
        for i, row in enumerate(unique_rows, 1):
            writer.writerow([i, row["address"], "", "", ""])

    # 另存 ID 對應表
    mapping_path = output_path.with_suffix(".mapping.json")
    with open(mapping_path, "w", encoding="utf-8") as f:
        json.dump(seen_addrs, f, ensure_ascii=False, indent=2)

    print(f"匯出完成:")
    print(f"  CSV: {output_path} ({len(unique_rows)} 筆不重複地址)")
    print(f"  對應表: {mapping_path}")
    print(f"  (原始 {len(rows)} 筆房屋物件, 去重後 {len(unique_rows)} 筆)")

    # 發送 Telegram 通知 + CSV 檔案
    if notify:
        week = data.get("meta", {}).get("week", "")
        api_key = os.environ.get("TGOS_BATCH_API_KEY", "")
        key_hint = api_key[:8] + "..." if api_key else "(未設定)"

        msg = (
            f"<b>法拍地圖 - TGOS 批次地址比對</b>\n\n"
            f"週次: {week}\n"
            f"待比對: {len(unique_rows)} 筆房屋地址\n\n"
            f"<b>請手動上傳:</b>\n"
            f"1. 下載附件 CSV\n"
            f"2. 開啟 TGOS: {TGOS_BATCH_URL}\n"
            f"3. APIKEY: <code>{key_hint}</code>\n"
            f"4. 坐標系統: EPSG:4326\n"
            f"5. 上傳 CSV → 進行批次比對\n\n"
            f"比對完成後 email 通知，下載結果 CSV 放到:\n"
            f"<code>scp result.csv acmacmini2:~/foreclosure-map/scripts/cache/tgos_result.csv</code>"
        )
        tg_send_message(msg)
        tg_send_file(str(output_path), caption=f"TGOS 批次比對 CSV ({len(unique_rows)} 筆)")
        print("  已發送 Telegram 通知 + CSV 檔案")

    return str(output_path)


# === IMPORT ===

def import_results(result_csv, input_json, output_json=None, rebuild=False):
    """匯入 TGOS 比對結果，更新 geocoded JSON"""
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

    data["meta"]["tgos_batch_updated"] = updated
    data["meta"]["tgos_batch_at"] = datetime.now().isoformat()

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

    # 重建 current.json + push
    if rebuild:
        import subprocess
        print("\n重建 current.json...")
        subprocess.run([
            "python3", "scripts/build.py", "--input", str(output_json)
        ], cwd=str(Path(__file__).parent.parent))

        week = data.get("meta", {}).get("week", "")
        subprocess.run(["git", "add", "data/"], cwd=str(Path(__file__).parent.parent))
        subprocess.run([
            "git", "commit", "-m", f"tgos batch geocode update {week}"
        ], cwd=str(Path(__file__).parent.parent))
        subprocess.run(["git", "push", "origin", "main"], cwd=str(Path(__file__).parent.parent))
        print("已推送到 GitHub")

        tg_send_message(f"TGOS 批次比對匯入完成\n更新: {updated} 筆房屋座標\n地圖已更新")


# === MAIN ===

def main():
    parser = argparse.ArgumentParser(description="TGOS 批次地址比對工具")
    subparsers = parser.add_subparsers(dest="command")

    export_p = subparsers.add_parser("export", help="匯出 CSV + Telegram 通知")
    export_p.add_argument("-i", "--input", required=True, help="geocoded JSON")
    export_p.add_argument("-o", "--output", default="scripts/cache/tgos_upload.csv")
    export_p.add_argument("--no-notify", action="store_true", help="不發送 Telegram")

    import_p = subparsers.add_parser("import", help="匯入 TGOS 結果 CSV")
    import_p.add_argument("-r", "--result", required=True, help="TGOS 結果 CSV")
    import_p.add_argument("-i", "--input", required=True, help="geocoded JSON")
    import_p.add_argument("-o", "--output", default=None, help="輸出 JSON")
    import_p.add_argument("--rebuild", action="store_true", help="重建 current.json + push")

    args = parser.parse_args()

    if args.command == "export":
        export_addresses(args.input, args.output, notify=not args.no_notify)
    elif args.command == "import":
        import_results(args.result, args.input, args.output, rebuild=args.rebuild)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
