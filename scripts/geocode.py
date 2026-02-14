#!/usr/bin/env python3
"""
座標轉換模組
- 土地物件: 地號 → twland.ronny.tw API → 經緯度 + 地籍邊界
- 房屋物件: 地址 → NLSC (免費免 key) → 經緯度
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
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
NLSC_API = "https://api.nlsc.gov.tw/MapSearch/QuerySearch"


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


def geocode_address_nlsc(address, address_cache):
    """
    地址 → 座標 (NLSC 國土測繪中心, 免費免 key, 門牌等級)
    回傳: {lat, lng, source} 或 None
    """
    cache_key = address

    # 檢查快取
    if cache_key in address_cache:
        cached = address_cache[cache_key]
        return {
            "lat": cached["lat"],
            "lng": cached["lng"],
            "source": "nlsc_cache",
        }

    try:
        # 雙重 URL encode (NLSC API 要求)
        encoded = urllib.parse.quote(urllib.parse.quote(address))
        data = f"word={encoded}&feedback=XML&center=121.000000,24.000000"

        req = urllib.request.Request(
            NLSC_API,
            data=data.encode("utf-8"),
            method="POST",
        )
        req.add_header("User-Agent", "Mozilla/5.0")
        req.add_header("Content-Type", "application/x-www-form-urlencoded; charset=UTF-8")
        req.add_header("Referer", "https://maps.nlsc.gov.tw/")

        with urllib.request.urlopen(req, timeout=15) as resp:
            xml_str = resp.read().decode("utf-8")

        root = ET.fromstring(xml_str)
        for item in root.findall("ITEM"):
            remark_el = item.find("REMARK")
            remark = remark_el.text if remark_el is not None else ""

            # 只取門牌結果 (最精確)
            if "門牌" not in remark:
                continue

            location = item.find("LOCATION").text
            lon, lat = location.split(",")
            lat_f = float(lat)
            lng_f = float(lon)

            if lat_f <= 0 or lng_f <= 0:
                continue

            result = {
                "lat": lat_f,
                "lng": lng_f,
                "source": "nlsc",
            }

            # 寫入快取
            address_cache[cache_key] = {
                "lat": result["lat"],
                "lng": result["lng"],
                "cached_at": datetime.now().isoformat(),
            }

            return result

        # 如果沒有門牌結果，接受任何有座標的結果
        root = ET.fromstring(xml_str)
        for item in root.findall("ITEM"):
            location_el = item.find("LOCATION")
            if location_el is None or not location_el.text:
                continue
            location = location_el.text
            lon, lat = location.split(",")
            lat_f = float(lat)
            lng_f = float(lon)

            if lat_f <= 0 or lng_f <= 0:
                continue

            result = {
                "lat": lat_f,
                "lng": lng_f,
                "source": "nlsc",
            }

            address_cache[cache_key] = {
                "lat": result["lat"],
                "lng": result["lng"],
                "cached_at": datetime.now().isoformat(),
            }

            return result

    except Exception as e:
        print(f"    [WARN] NLSC 查詢失敗 ({address}): {e}")

    return None


def geocode_item(item, land_cache, address_cache):
    """對單一物件進行座標轉換"""
    # 如果已有司法院 API 提供的座標，跳過
    if item.get("geocode_status") == "ok" and item.get("coordinates"):
        return item

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
        # 房屋: 地址 → NLSC (免費免 key)
        address = item["address"]
        result = geocode_address_nlsc(address, address_cache)

    elif item.get("location"):
        # 嘗試從 location 直接做地址查詢
        location = item["location"]
        result = geocode_address_nlsc(location, address_cache)

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
        default=0.3,
        help="API 呼叫間隔 (秒, 預設: 0.3)",
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

    # 統計已有座標 (司法院 API 提供)
    pre_geocoded = sum(1 for item in items if item.get("geocode_status") == "ok" and item.get("coordinates"))
    print(f"已有座標 (司法院 API): {pre_geocoded} 筆")

    # 逐筆轉換
    success = 0
    failed = 0
    cached = 0
    skipped = 0

    for i, item in enumerate(items):
        # 已有座標的跳過
        if item.get("geocode_status") == "ok" and item.get("coordinates"):
            skipped += 1
            if (i + 1) % 200 == 0:
                print(f"  進度: {i+1}/{len(items)} (API: {success}, 快取: {cached}, 跳過: {skipped}, 失敗: {failed})")
            continue

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
            print(f"  進度: {i+1}/{len(items)} (API: {success}, 快取: {cached}, 跳過: {skipped}, 失敗: {failed})")

    # 儲存快取
    save_cache(LAND_CACHE_FILE, land_cache)
    save_cache(ADDRESS_CACHE_FILE, address_cache)

    # 更新 meta
    data["meta"]["geocoded_at"] = datetime.now().isoformat()
    data["meta"]["geocode_success"] = success + cached + skipped
    data["meta"]["geocode_failed"] = failed
    data["meta"]["geocode_cached"] = cached
    data["meta"]["geocode_pre"] = skipped

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
    print(f"  跳過 (已有): {skipped}")
    print(f"  失敗: {failed}")
    print(f"  土地: {land_count}, 房屋: {building_count}")
    print(f"  輸出: {output_path}")


if __name__ == "__main__":
    main()
