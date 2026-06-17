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

# 縣市邊界框 (min_lng, min_lat, max_lng, max_lat) — 用於驗證地理編碼落點是否在宣告縣市內。
# 目的: fail-closed 擋掉跨縣市模糊比對錯置 (例: 屏東地址被 NLSC 配到新竹竹北泰和路)。
# 範圍取寬鬆邊界 (含山區/離島),只攔多度數級別的明顯錯置,不誤殺縣界附近的合法點。
COUNTY_BBOX = {
    "臺北市": (121.45, 24.95, 121.67, 25.21),
    "新北市": (121.27, 24.67, 122.01, 25.30),
    "基隆市": (121.65, 25.07, 121.82, 25.20),
    "桃園市": (120.96, 24.59, 121.46, 25.12),
    "新竹縣": (120.92, 24.40, 121.43, 24.90),
    "新竹市": (120.85, 24.74, 121.03, 24.86),
    "苗栗縣": (120.66, 24.30, 121.27, 24.70),
    "臺中市": (120.43, 23.99, 121.46, 24.43),
    "彰化縣": (120.30, 23.80, 120.75, 24.18),
    "南投縣": (120.55, 23.45, 121.45, 24.22),
    "雲林縣": (120.10, 23.50, 120.70, 23.86),
    "嘉義縣": (120.10, 23.20, 120.95, 23.65),
    "嘉義市": (120.40, 23.44, 120.50, 23.52),
    "臺南市": (120.02, 22.88, 120.66, 23.43),
    "高雄市": (120.13, 22.46, 121.06, 23.47),
    "屏東縣": (120.32, 21.90, 120.90, 22.95),
    "宜蘭縣": (121.30, 24.30, 122.00, 24.99),
    "花蓮縣": (121.06, 23.10, 121.70, 24.38),
    "臺東縣": (120.70, 21.90, 121.70, 23.40),
    "澎湖縣": (119.30, 23.18, 119.74, 23.80),
    "金門縣": (118.14, 24.38, 118.55, 24.56),
    "連江縣": (119.88, 25.93, 120.51, 26.40),
}

# 邊界框緩衝 (度) — 吸收縣界地段/門牌的座標精度誤差,避免誤殺縣界附近的合法點。
# 校準依據: 對 live 資料實測,合法的縣界地段 (新豐鄉/竹南/苑裡等) 出界 < 0.05deg,
# 真錯置 (跨區模糊比對) 出界 >= 0.20deg,兩者間有明顯空隙,0.1deg 可乾淨分離。
COUNTY_MARGIN_DEG = 0.1


def _normalize_county(county):
    """正規化縣市名 (台/臺 統一為臺),供 COUNTY_BBOX 查表"""
    if not county:
        return ""
    return county.strip().replace("台", "臺")


def in_county(lat, lng, county):
    """
    驗證座標是否落在宣告縣市的邊界框內。
    回傳 True 表示通過 (或縣市未知無法驗證時放行,不誤殺);
    回傳 False 表示明顯落在別的縣市,應拒絕。
    """
    bbox = COUNTY_BBOX.get(_normalize_county(county))
    if bbox is None:
        return True  # 未知縣市無法驗證,放行
    try:
        lat_f = float(lat)
        lng_f = float(lng)
    except (TypeError, ValueError):
        return False
    min_lng, min_lat, max_lng, max_lat = bbox
    m = COUNTY_MARGIN_DEG
    return (min_lng - m <= lng_f <= max_lng + m) and (min_lat - m <= lat_f <= max_lat + m)


def county_center(county):
    """回傳縣市中心點 (lng, lat) 供 NLSC 查詢偏好,未知縣市回傳 None"""
    bbox = COUNTY_BBOX.get(_normalize_county(county))
    if bbox is None:
        return None
    min_lng, min_lat, max_lng, max_lat = bbox
    return ((min_lng + max_lng) / 2, (min_lat + max_lat) / 2)


def _parse_nlsc_location(item):
    """從 NLSC ITEM 解析 (lat, lng),無效回傳 None"""
    location_el = item.find("LOCATION")
    if location_el is None or not location_el.text:
        return None
    try:
        lon, lat = location_el.text.split(",")
        lat_f = float(lat)
        lng_f = float(lon)
    except (ValueError, AttributeError):
        return None
    if lat_f <= 0 or lng_f <= 0:
        return None
    return lat_f, lng_f


def _cache_and_return(address_cache, cache_key, lat_f, lng_f):
    """寫入地址快取並回傳結果"""
    address_cache[cache_key] = {
        "lat": lat_f,
        "lng": lng_f,
        "cached_at": datetime.now().isoformat(),
    }
    return {"lat": lat_f, "lng": lng_f, "source": "nlsc"}


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


def geocode_address_nlsc(address, address_cache, county=""):
    """
    地址 → 座標 (NLSC 國土測繪中心, 免費免 key, 門牌等級)
    county: 宣告縣市,用於 (1) 查詢中心點偏好 (2) 落點驗證,過濾跨縣市模糊比對。
    回傳: {lat, lng, source} 或 None
    """
    cache_key = address

    # 檢查快取 — 須通過縣市驗證才採用。
    # 自我修復: 舊版留下的跨縣市錯置快取 (例: 屏東地址被配到竹北) 會被視為未命中而重查。
    if cache_key in address_cache:
        cached = address_cache[cache_key]
        if in_county(cached["lat"], cached["lng"], county):
            return {
                "lat": cached["lat"],
                "lng": cached["lng"],
                "source": "nlsc_cache",
            }
        print(f"    [FIX] 快取錯置重查 ({address}): 舊座標不在 {county}")

    try:
        # 雙重 URL encode (NLSC API 要求)
        encoded = urllib.parse.quote(urllib.parse.quote(address))
        # 查詢中心點用宣告縣市中心,避免模糊比對被寫死的預設中心 (北部) 拉偏
        center = county_center(county) or (121.0, 24.0)
        data = f"word={encoded}&feedback=XML&center={center[0]:.6f},{center[1]:.6f}"

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

        # 第一輪: 只取門牌結果 (最精確),且須落在宣告縣市內
        for item in root.findall("ITEM"):
            remark_el = item.find("REMARK")
            remark = remark_el.text if remark_el is not None else ""
            if "門牌" not in remark:
                continue

            coords = _parse_nlsc_location(item)
            if coords is None:
                continue
            lat_f, lng_f = coords
            if not in_county(lat_f, lng_f, county):
                continue

            return _cache_and_return(address_cache, cache_key, lat_f, lng_f)

        # 第二輪 fallback: 接受任何有座標的結果,但仍須通過縣市驗證閘。
        # 這裡是錯置的主因 — 沒門牌時 NLSC 會回傳同名道路的模糊比對 (泰和路@竹北),
        # 縣市閘確保只收落在宣告縣市內的結果,寧可查無也不錯置。
        for item in root.findall("ITEM"):
            coords = _parse_nlsc_location(item)
            if coords is None:
                continue
            lat_f, lng_f = coords
            if not in_county(lat_f, lng_f, county):
                continue

            return _cache_and_return(address_cache, cache_key, lat_f, lng_f)

    except Exception as e:
        print(f"    [WARN] NLSC 查詢失敗 ({address}): {e}")

    return None


def geocode_item(item, land_cache, address_cache):
    """對單一物件進行座標轉換"""
    # 如果已有司法院 API 提供的座標，跳過
    if item.get("geocode_status") == "ok" and item.get("coordinates"):
        return item

    item_type = item.get("type", "unknown")
    county = item.get("county", "")
    result = None

    if item_type == "land" and item.get("land_section") and item.get("land_no"):
        # 土地: 地號 → twland API
        district = item.get("district", "")
        section = item["land_section"]
        number = item["land_no"]

        result = geocode_land(county, district, section, number, land_cache)

    elif item_type == "building" and item.get("address"):
        # 房屋: 地址 → NLSC (免費免 key)
        result = geocode_address_nlsc(item["address"], address_cache, county)

    elif item.get("location"):
        # 嘗試從 location 直接做地址查詢
        result = geocode_address_nlsc(item["location"], address_cache, county)

    # Fail-closed 縣市驗證閘 — 涵蓋所有來源 (twland / nlsc / 快取)。
    # 落點不在宣告縣市 → 視為失敗,寧可不顯示也不錯置到別的縣市。
    if result and not in_county(result.get("lat"), result.get("lng"), county):
        print(
            f"    [REJECT] 落點不在 {county} "
            f"({result.get('lat')},{result.get('lng')}): "
            f"{item.get('location') or item.get('address')}"
        )
        result = None

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
