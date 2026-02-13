#!/usr/bin/env python3
"""
資料整合模組
- 讀取 geocoded JSON
- 更新歷史追蹤 (history.json)
- 產出 current.json 和 weeks/YYYY-WNN.json
"""

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path


def load_json(path):
    """載入 JSON 檔案"""
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_json(path, data):
    """儲存 JSON 檔案"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def update_history(items, history, current_week):
    """更新歷史追蹤資料"""
    current_ids = set()

    for item in items:
        item_id = item.get("id")
        if not item_id:
            continue

        current_ids.add(item_id)

        if item_id in history:
            # 既有物件：更新
            history[item_id]["last_seen"] = current_week
            history[item_id]["status"] = "active"

            # 檢查是否有新的拍別
            rounds = history[item_id].get("rounds", [])
            current_round = item.get("auction_round")
            current_price = item.get("min_price")

            if rounds:
                last_round = rounds[-1].get("round")
                if current_round and current_round != last_round:
                    rounds.append({
                        "week": current_week,
                        "round": current_round,
                        "price": current_price,
                    })
            else:
                rounds.append({
                    "week": current_week,
                    "round": current_round,
                    "price": current_price,
                })

            history[item_id]["rounds"] = rounds

        else:
            # 新物件
            history[item_id] = {
                "first_seen": current_week,
                "last_seen": current_week,
                "status": "active",
                "rounds": [{
                    "week": current_week,
                    "round": item.get("auction_round"),
                    "price": item.get("min_price"),
                }],
            }

    # 標記消失的物件
    for item_id in history:
        if item_id not in current_ids and history[item_id]["status"] == "active":
            history[item_id]["status"] = "sold_or_withdrawn"

    return history


def build_current(items, history, meta, current_week):
    """
    建構 current.json
    將歷史資訊嵌入每個物件中
    """
    features = []

    for item in items:
        # 只包含有座標的物件
        if item.get("geocode_status") != "ok":
            continue

        item_id = item.get("id")
        hist = history.get(item_id, {})

        feature = {
            "id": item_id,
            "type": item.get("type", "unknown"),
            "court": item.get("court"),
            "court_code": item.get("court_code"),
            "case_no": item.get("case_no"),
            "auction_date": item.get("auction_date"),
            "auction_round": item.get("auction_round"),
            "location": item.get("location"),
            "county": item.get("county"),
            "district": item.get("district"),
            "min_price": item.get("min_price"),
            "area": item.get("area"),
            "delivery": item.get("delivery", "unknown"),
            "vacant": item.get("vacant", "unknown"),
            "rrange": item.get("rrange"),
            "detail_url": item.get("detail_url"),
            "coordinates": item.get("coordinates"),
            "history": {
                "first_seen": hist.get("first_seen", current_week),
                "rounds": hist.get("rounds", []),
            },
        }

        # 土地物件額外欄位
        if item.get("type") == "land":
            feature["land_section"] = item.get("land_section")
            feature["land_no"] = item.get("land_no")

        # 房屋物件額外欄位
        if item.get("type") == "building":
            feature["address"] = item.get("address")

        features.append(feature)

    # 統計
    land_count = sum(1 for f in features if f["type"] == "land")
    building_count = sum(1 for f in features if f["type"] == "building")

    current = {
        "meta": {
            "generated_at": datetime.now().isoformat(),
            "week": current_week,
            "total_count": len(features),
            "land_count": land_count,
            "building_count": building_count,
            "courts_scraped": meta.get("courts_scraped", 0),
            "geocode_success": meta.get("geocode_success", 0),
            "geocode_failed": meta.get("geocode_failed", 0),
        },
        "features": features,
    }

    return current


def main():
    parser = argparse.ArgumentParser(description="法拍物件資料整合")
    parser.add_argument(
        "--input", "-i",
        required=True,
        help="輸入 geocoded JSON 檔案",
    )
    parser.add_argument(
        "--data-dir",
        default="data",
        help="資料目錄 (預設: data)",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    data_dir = Path(args.data_dir)

    if not input_path.exists():
        print(f"[ERROR] 輸入檔案不存在: {input_path}")
        sys.exit(1)

    # 載入 geocoded 資料
    with open(input_path, "r", encoding="utf-8") as f:
        geocoded_data = json.load(f)

    items = geocoded_data.get("items", [])
    meta = geocoded_data.get("meta", {})
    current_week = meta.get("week", datetime.now().strftime("%Y-W%W"))

    print(f"資料整合啟動")
    print(f"輸入: {input_path} ({len(items)} 筆)")
    print(f"週次: {current_week}")

    # 載入歷史
    history_path = data_dir / "history.json"
    history = load_json(history_path)
    print(f"歷史記錄: {len(history)} 筆物件")

    # 更新歷史
    history = update_history(items, history, current_week)

    # 統計歷史變動
    active = sum(1 for h in history.values() if h["status"] == "active")
    sold = sum(1 for h in history.values() if h["status"] == "sold_or_withdrawn")
    new_this_week = sum(
        1 for h in history.values()
        if h.get("first_seen") == current_week
    )
    print(f"歷史統計: 活躍 {active}, 已拍定/撤回 {sold}, 本週新增 {new_this_week}")

    # 建構 current.json
    current = build_current(items, history, meta, current_week)

    # 儲存
    current_path = data_dir / "current.json"
    week_path = data_dir / "weeks" / f"{current_week}.json"

    save_json(current_path, current)
    save_json(week_path, current)
    save_json(history_path, history)

    print(f"\n輸出:")
    print(f"  {current_path} ({current['meta']['total_count']} 筆)")
    print(f"  {week_path}")
    print(f"  {history_path} ({len(history)} 筆)")
    print(f"\n整合完成!")


if __name__ == "__main__":
    main()
