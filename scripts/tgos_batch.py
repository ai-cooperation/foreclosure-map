#!/usr/bin/env python3
"""
TGOS 批次地址比對
- export: 從 geocoded JSON 匯出未 geocode 的房屋地址為 CSV
- upload: 上傳 CSV 到 TGOS 批次比對服務 (需登入, CAPTCHA 透過 Telegram 通知)
- import: 匯入 TGOS 結果 CSV，更新 geocoded JSON

用法:
  python3 tgos_batch.py export -i data/geocoded/2026-W06-full-v3.json
  python3 tgos_batch.py upload -f scripts/cache/tgos_upload.csv
  python3 tgos_batch.py import -r tgos_result.csv -i data/geocoded/2026-W06-full-v3.json
"""

import argparse
import csv
import json
import os
import sys
import time
import base64
import re
from datetime import datetime
from pathlib import Path
from io import BytesIO

import requests as http_requests


# === EXPORT ===

def export_addresses(input_path, output_csv):
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

    return str(output_path)


# === UPLOAD (HTTP + Telegram CAPTCHA) ===

TGOS_BASE = "https://www.tgos.tw/TGOS"
TGOS_REQ = f"{TGOS_BASE}/reqcontroller.go"
TGOS_CAPTCHA = f"{TGOS_BASE}/gs.request.go"
TGOS_BATCH_PAGE = f"{TGOS_BASE}/Addr/Compare"

TG_API = "https://api.telegram.org/bot"


def tg_send_photo(bot_token, chat_id, photo_bytes, caption=""):
    """發送圖片到 Telegram"""
    url = f"{TG_API}{bot_token}/sendPhoto"
    files = {"photo": ("captcha.png", BytesIO(photo_bytes), "image/png")}
    data = {"chat_id": chat_id, "caption": caption}
    resp = http_requests.post(url, data=data, files=files, timeout=15)
    return resp.json()


def tg_get_updates(bot_token, offset=None, timeout=60):
    """取得 Telegram 更新 (long polling)"""
    url = f"{TG_API}{bot_token}/getUpdates"
    params = {"timeout": timeout}
    if offset:
        params["offset"] = offset
    resp = http_requests.get(url, params=params, timeout=timeout + 10)
    return resp.json()


def tg_ask_captcha(bot_token, chat_id, captcha_bytes):
    """透過 Telegram 詢問 CAPTCHA，等待用戶回覆"""
    # 先清空舊消息
    updates = tg_get_updates(bot_token, timeout=0)
    offset = None
    if updates.get("ok") and updates.get("result"):
        offset = updates["result"][-1]["update_id"] + 1

    # 發送 CAPTCHA 圖片
    tg_send_photo(bot_token, chat_id, captcha_bytes,
                  caption="TGOS 驗證碼 - 請回覆 4 位數字")
    print("  已發送驗證碼到 Telegram，等待回覆...")

    # 等待回覆 (最多 3 分鐘)
    start = time.time()
    while time.time() - start < 180:
        updates = tg_get_updates(bot_token, offset=offset, timeout=30)
        if updates.get("ok") and updates.get("result"):
            for update in updates["result"]:
                offset = update["update_id"] + 1
                msg = update.get("message", {})
                text = msg.get("text", "").strip()
                if msg.get("chat", {}).get("id") == int(chat_id) and re.match(r"^\d{4}$", text):
                    print(f"  收到驗證碼: {text}")
                    return text

    print("  [TIMEOUT] 3 分鐘內未收到回覆")
    return None


def tgos_login(session, account, password, bot_token=None, chat_id=None):
    """登入 TGOS (處理 CAPTCHA)"""
    # 1. 載入批次比對頁面取得 session cookies 和 tokens
    print("  載入 TGOS 頁面...")
    resp = session.get(TGOS_BATCH_PAGE, timeout=30)
    resp.raise_for_status()

    # 提取 tokens
    html = resp.text
    token_match = re.search(r'gs-request-token\s+value="([^"]+)"', html)
    gs_token = token_match.group(1) if token_match else ""

    verify_match = re.search(r'__RequestVerificationToken[^>]*value="([^"]+)"', html)
    verify_token = verify_match.group(1) if verify_match else ""

    if not gs_token:
        print("  [WARN] 未找到 gs-request-token")

    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        print(f"  登入嘗試 {attempt}/{max_attempts}...")

        # 2. 取得 CAPTCHA
        captcha_resp = session.post(TGOS_CAPTCHA, data={
            "method": "GeCaptcha",
            "Type": "number",
            "Key": "GS_CAPTCHA_LOGON",
            "ValidateLength": "4",
            "FontWidth": "85",
            "FontHeight": "36",
        }, headers={"gs-request-token": gs_token}, timeout=15)

        if captcha_resp.status_code != 200:
            print(f"  [ERROR] CAPTCHA 取得失敗: {captcha_resp.text[:100]}")
            continue

        # 回傳格式: data:image/jpeg;base64,/9j/4AAQ...
        captcha_text = captcha_resp.text
        if "," in captcha_text:
            captcha_b64 = captcha_text.split(",", 1)[1]
        else:
            captcha_b64 = captcha_text

        try:
            captcha_bytes = base64.b64decode(captcha_b64)
        except Exception:
            print(f"  [ERROR] 無法解碼驗證碼圖片")
            continue

        # 3. 透過 Telegram 詢問驗證碼
        if bot_token and chat_id:
            captcha_code = tg_ask_captcha(bot_token, chat_id, captcha_bytes)
        else:
            # 儲存到檔案讓用戶手動輸入
            captcha_path = Path(__file__).parent / "cache" / "captcha.png"
            captcha_path.parent.mkdir(parents=True, exist_ok=True)
            with open(captcha_path, "wb") as f:
                f.write(captcha_bytes)
            print(f"  驗證碼圖片已存到: {captcha_path}")
            print(f"  請輸入 4 位驗證碼:")
            try:
                captcha_code = input("  > ").strip()
            except EOFError:
                captcha_code = None

        if not captcha_code:
            print(f"  [ERROR] 未取得驗證碼")
            continue

        # 4. 登入
        login_data = {
            "method": "logonout.Logon",
            "account": account,
            "password": password,
            "captcha": captcha_code,
        }
        if verify_token:
            login_data["__RequestVerificationToken"] = verify_token

        headers = {}
        if gs_token:
            headers["gs-request-token"] = gs_token

        login_resp = session.post(TGOS_REQ, data=login_data, headers=headers, timeout=15)

        try:
            result = login_resp.json()
        except Exception:
            result = {"Message": login_resp.text[:200]}

        if result.get("Success") or result.get("success"):
            print(f"  登入成功!")
            return True
        else:
            msg = result.get("Message", result.get("message", str(result)))
            print(f"  登入失敗: {msg}")
            if "驗證碼" in str(msg):
                continue  # 驗證碼錯誤，重試
            else:
                return False  # 帳密錯誤，不重試

    print(f"  [ERROR] 登入失敗 ({max_attempts} 次嘗試)")
    return False


def upload_csv(csv_path):
    """上傳 CSV 到 TGOS 批次比對服務"""
    # 環境變數
    batch_api_key = os.environ.get("TGOS_BATCH_API_KEY", "")
    tgos_account = os.environ.get("TGOS_ACCOUNT", "")
    tgos_password = os.environ.get("TGOS_PASSWORD", "")
    bot_token = os.environ.get("TG_BOT_TOKEN", "")
    chat_id = os.environ.get("TG_CHAT_ID", "")

    if not batch_api_key:
        print("[ERROR] 未設定 TGOS_BATCH_API_KEY")
        sys.exit(1)
    if not tgos_account or not tgos_password:
        print("[ERROR] 未設定 TGOS_ACCOUNT / TGOS_PASSWORD")
        sys.exit(1)

    csv_path = os.path.abspath(csv_path)
    if not os.path.exists(csv_path):
        print(f"[ERROR] CSV 不存在: {csv_path}")
        sys.exit(1)

    with open(csv_path, "r", encoding="utf-8-sig") as f:
        line_count = sum(1 for _ in f) - 1
    print(f"上傳 CSV: {csv_path} ({line_count} 筆)")

    if line_count > 10000:
        print(f"[WARN] 超過每日上限 10,000 筆")

    # 建立 session
    session = http_requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    })

    # 登入
    print("Step 1: 登入 TGOS...")
    if not tgos_login(session, tgos_account, tgos_password, bot_token, chat_id):
        print("[ERROR] TGOS 登入失敗")
        sys.exit(1)

    # 重新載入頁面取得登入後的 token
    print("Step 2: 載入批次比對頁面...")
    resp = session.get(TGOS_BATCH_PAGE, timeout=30)
    html = resp.text

    # 確認已登入 (頁面應有 logoutform)
    if "logoutform" not in html and "登出" not in html:
        print("[WARN] 可能未成功登入 (頁面未顯示登出)")

    # 提取 token
    token_match = re.search(r'gs-request-token\s+value="([^"]+)"', html)
    gs_token = token_match.group(1) if token_match else ""

    # 上傳
    print("Step 3: 上傳 CSV 並提交批次比對...")
    with open(csv_path, "rb") as f:
        files = {"addrCsvFile": (os.path.basename(csv_path), f, "text/csv")}
        form_data = {
            "method": "addr.ExecuteBathQuery",
            "apikey": batch_api_key,
            "crs": "EPSG:4326",
            "compareRule": "fuzzy",
            "fuzzyType": "2",
            "fuzzyBuffer": "0",
            "fuzzyIgnoreVillage": "true",
            "fuzzyIgnoreNeighborhood": "true",
            "fuzzyReturnMaxCount": "1",
            "fuzzySupportHistory": "true",
        }

        headers = {}
        if gs_token:
            headers["gs-request-token"] = gs_token

        upload_resp = session.post(TGOS_REQ, data=form_data, files=files,
                                   headers=headers, timeout=60)

    try:
        result = upload_resp.json()
    except Exception:
        result = {"Message": upload_resp.text[:500]}

    success = result.get("Success") or result.get("success")
    message = result.get("Message") or result.get("message") or str(result)

    if success:
        print(f"\n上傳成功! {message}")
        print(f"TGOS 會在 1-2 天內寄送比對結果到 email")
    else:
        print(f"\n上傳結果: {message}")

    return success


# === IMPORT ===

def import_results(result_csv, input_json, output_json=None):
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

    # 讀取 mapping
    mapping_path = Path(result_csv).with_suffix(".mapping.json")
    cache_mapping = Path(__file__).parent / "cache" / "tgos_upload.mapping.json"
    for mp in [mapping_path, cache_mapping]:
        if mp.exists():
            with open(mp, "r", encoding="utf-8") as f:
                json.load(f)  # validate
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


# === MAIN ===

def main():
    parser = argparse.ArgumentParser(description="TGOS 批次地址比對工具")
    subparsers = parser.add_subparsers(dest="command")

    export_p = subparsers.add_parser("export", help="匯出未 geocode 房屋地址為 CSV")
    export_p.add_argument("-i", "--input", required=True, help="geocoded JSON")
    export_p.add_argument("-o", "--output", default="scripts/cache/tgos_upload.csv", help="輸出 CSV")

    upload_p = subparsers.add_parser("upload", help="上傳 CSV 到 TGOS 批次比對")
    upload_p.add_argument("-f", "--file", required=True, help="CSV 檔案路徑")

    import_p = subparsers.add_parser("import", help="匯入 TGOS 結果 CSV")
    import_p.add_argument("-r", "--result", required=True, help="TGOS 結果 CSV")
    import_p.add_argument("-i", "--input", required=True, help="geocoded JSON")
    import_p.add_argument("-o", "--output", default=None, help="輸出 JSON (預設覆寫)")

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
