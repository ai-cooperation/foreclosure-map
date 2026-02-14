#!/usr/bin/env python3
"""
座標轉換模組
- 土地物件: 地號 → twland.ronny.tw API → 經緯度 + 地籍邊界
- 房屋物件: 地址 → TGOS / Google Geocoding → 經緯度
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import requests

# 快取檔案路徑
CACHE_DIR = Path(__file__).parent / "cache"
LAND_CACHE_FILE = CACHE_DIR / "land_cache.json"
ADDRESS_CACHE_FILE = CACHE_DIR / "address_cache.json"

# API 設定
TWLAND_API = "https://twland.ronny.tw/index/search"
TGOS_API = "https://addr.tgos.tw/addrws/v40/QueryAddr.asmx/QueryAddr"
GOOGLE_GEOCODING_API = "https://maps.googleapis.com/maps/api/geocode/json"


def load_cache(cache_file):
    """載入快取"""
    if cache_file.exists():
        with open(cache_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_cache(cache_file, cache_data):
    """儲存快取"""
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(cache_data, f, ensure_ascii=False, indent=2)


def geocode_land(county, district, section, number, land_cache):
    """
    土地地號 → 座標 (twland.ronny.tw)
    回傳: {lat, lng, polygon, source} 或 None
    """
    # 組合查詢 key
    query_key = f"{county},{section},{number}"
    cache_key = query_key

    # 檢查快取
    if cache_key in land_cache:
        cached = land_cache[cache_key]
        return {
            "lat": cached["lat"],
            "lng": cached["lng"],
            "polygon": cached.get("polygon"),
            "source": "twland_cache",
        }

    # 呼叫 twland API
    try:
        resp = requests.get(
            TWLAND_API,
            params={"lands[]": query_key},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("features") and len(data["features"]) > 0:
            feature = data["features"][0]
            props = feature["properties"]
            geometry = feature["geometry"]

            result = {
                "lat": props.get("ycenter"),
                "lng": props.get("xcenter"),
                "polygon": geometry.get("coordinates"),
                "source": "twland",
            }

            # 如果有多筆結果，嘗試匹配鄉鎮
            if len(data["features"]) > 1 and district:
                for feat in data["features"]:
                    if district in feat["properties"].get("鄉鎮", ""):
                        result = {
                            "lat": feat["properties"].get("ycenter"),
                            "lng": feat["properties"].get("xcenter"),
                            "polygon": feat["geometry"].get("coordinates"),
                            "source": "twland",
                        }
                        break

            # 寫入快取
            land_cache[cache_key] = {
                "lat": result["lat"],
                "lng": result["lng"],
                "polygon": result["polygon"],
                "cached_at": datetime.now().isoformat(),
            }

            return result

    except Exception as e:
        print(f"    [WARN] twland API 查詢失敗 ({query_key}): {e}")

    return None


def geocode_address_tgos(address, address_cache):
    """
    地址 → 座標 (TGOS)
    回傳: {lat, lng, source} 或 None
    """
    cache_key = address

    # 檢查快取
    if cache_key in address_cache:
        cached = address_cache[cache_key]
        return {
            "lat": cached["lat"],
            "lng": cached["lng"],
            "source": "tgos_cache",
        }

    app_id = os.environ.get("TGOS_APP_ID")
    api_key = os.environ.get("TGOS_API_KEY")

    if not app_id or not api_key:
        return None

    try:
        resp = requests.get(
            TGOS_API,
            params={
                "oAPPId": app_id,
                "oAPIKey": api_key,
                "oAddress": address,
                "oSRS": "EPSG:4326",
                "oFuzzyType": "2",
                "oFuzzyBuffer": "0",
                "oResultDataType": "json",
                "oIsOnlyFullMatch": "false",
                "oReturnMaxCount": "1",
                "oIsSupportPast": "true",
                "oIsShowCodeBase": "false",
                "oIsLockCounty": "true",
                "oIsLockTown": "false",
                "oIsLockVillage": "false",
                "oIsLockRoadSection": "false",
                "oIsLockLane": "false",
                "oIsLockAlley": "false",
                "oIsLockArea": "false",
                "oIsSameNumber_SubNumber": "true",
                "oCanIgnoreVillage": "true",
                "oCanIgnoreNeighborhood": "true",
            },
            timeout=15,
        )
        resp.raise_for_status()

        # TGOS 回傳 XML 包裹 JSON: <?xml ...><string>JSON</string>
        text = resp.text
        if text.startswith("<?xml"):
            import xml.etree.ElementTree as ET
            root = ET.fromstring(text)
            json_str = root.text
            if not json_str or "錯誤" in json_str or "失敗" in json_str:
                return None
            data = json.loads(json_str)
        else:
            data = resp.json()

        addr_list = data.get("AddressList", [])
        if addr_list and len(addr_list) > 0:
            lat_val = addr_list[0].get("Y")
            lng_val = addr_list[0].get("X")
            if lat_val and lng_val:
                lat_f = float(lat_val)
                lng_f = float(lng_val)
                if lat_f == 0 or lng_f == 0:
                    return None
            else:
                return None
            result = {
                "lat": lat_f,
                "lng": lng_f,
                "source": "tgos",
            }

            # 寫入快取
            address_cache[cache_key] = {
                "lat": result["lat"],
                "lng": result["lng"],
                "cached_at": datetime.now().isoformat(),
            }

            return result

    except Exception as e:
        print(f"    [WARN] TGOS 查詢失敗 ({address}): {e}")

    return None


def geocode_address_google(address, address_cache):
    """
    地址 → 座標 (Google Geocoding API, fallback)
    回傳: {lat, lng, source} 或 None
    """
    api_key = os.environ.get("GOOGLE_GEOCODING_API_KEY")
    if not api_key:
        return None

    cache_key = address

    try:
        resp = requests.get(
            GOOGLE_GEOCODING_API,
            params={
                "address": address,
                "key": api_key,
                "language": "zh-TW",
                "region": "tw",
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("status") == "OK" and data.get("results"):
            loc = data["results"][0]["geometry"]["location"]
            result = {
                "lat": loc["lat"],
                "lng": loc["lng"],
                "source": "google",
            }

            # 寫入快取
            address_cache[cache_key] = {
                "lat": result["lat"],
                "lng": result["lng"],
                "cached_at": datetime.now().isoformat(),
            }

            return result

    except Exception as e:
        print(f"    [WARN] Google Geocoding 查詢失敗 ({address}): {e}")

    return None


def geocode_item(item, land_cache, address_cache):
    """對單一物件進行座標轉換"""
    item_type = item.get("type", "unknown")
    result = None

    if item_type == "land" and item.get("land_section") and item.get("land_no"):
        # 土地: 地號 → twland API
        county = item.get("county", "")
        district = item.get("district", "")
        section = item["land_section"]
        number = item["land_no"]

        result = geocode_land(county, district, section, number, land_cache)

    elif item_type == "building" and item.get("address"):
        # 房屋: 地址 → TGOS → Google (fallback)
        address = item["address"]

        result = geocode_address_tgos(address, address_cache)
        if not result:
            result = geocode_address_google(address, address_cache)

    elif item.get("location"):
        # 嘗試從 location 直接做地址查詢
        location = item["location"]

        # 先嘗試地址查詢
        result = geocode_address_tgos(location, address_cache)
        if not result:
            result = geocode_address_google(location, address_cache)

    if result:
        item["coordinates"] = result
        item["geocode_status"] = "ok"
    else:
        item["geocode_status"] = "failed"

    return item


def main():
    parser = argparse.ArgumentParser(description="法拍物件座標轉換")
    parser.add_argument(
        "--input", "-i",
        required=True,
        help="輸入 JSON 檔案 (scrape.py 的輸出)",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="輸出 JSON 檔案路徑",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="API 呼叫間隔 (秒, 預設: 0.5)",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"[ERROR] 輸入檔案不存在: {input_path}")
        sys.exit(1)

    if args.output:
        output_path = Path(args.output)
    else:
        output_path = Path(str(input_path).replace("/raw/", "/geocoded/"))

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 載入資料
    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    items = data.get("items", [])
    print(f"座標轉換啟動")
    print(f"輸入: {input_path} ({len(items)} 筆)")
    print(f"輸出: {output_path}")

    # 載入快取
    land_cache = load_cache(LAND_CACHE_FILE)
    address_cache = load_cache(ADDRESS_CACHE_FILE)
    print(f"快取: 土地 {len(land_cache)} 筆, 地址 {len(address_cache)} 筆")

    # 逐筆轉換
    success = 0
    failed = 0
    cached = 0

    for i, item in enumerate(items):
        item = geocode_item(item, land_cache, address_cache)

        if item.get("geocode_status") == "ok":
            source = item.get("coordinates", {}).get("source", "")
            if "cache" in source:
                cached += 1
            else:
                success += 1
                time.sleep(args.delay)  # API 呼叫後等待
        else:
            failed += 1

        # 進度
        if (i + 1) % 50 == 0:
            print(f"  進度: {i+1}/{len(items)} (成功: {success}, 快取: {cached}, 失敗: {failed})")

    # 儲存快取
    save_cache(LAND_CACHE_FILE, land_cache)
    save_cache(ADDRESS_CACHE_FILE, address_cache)

    # 更新 meta
    data["meta"]["geocoded_at"] = datetime.now().isoformat()
    data["meta"]["geocode_success"] = success + cached
    data["meta"]["geocode_failed"] = failed
    data["meta"]["geocode_cached"] = cached

    # 統計
    land_count = sum(1 for item in items if item.get("type") == "land")
    building_count = sum(1 for item in items if item.get("type") == "building")
    data["meta"]["land_count"] = land_count
    data["meta"]["building_count"] = building_count

    # 輸出
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\n座標轉換完成!")
    print(f"  成功 (API): {success}")
    print(f"  成功 (快取): {cached}")
    print(f"  失敗: {failed}")
    print(f"  土地: {land_count}, 房屋: {building_count}")
    print(f"  輸出: {output_path}")


if __name__ == "__main__":
    main()
