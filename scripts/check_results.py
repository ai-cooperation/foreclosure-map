#!/usr/bin/env python3
import json, sys

with open(sys.argv[1]) as f:
    data = json.load(f)

ok = [i for i in data["items"] if i.get("geocode_status") == "ok"]
fail = [i for i in data["items"] if i.get("geocode_status") != "ok"]

print(f"=== 成功 {len(ok)} 筆 ===")
for i in ok:
    c = i["coordinates"]
    print(f"  {i['type']:10s} {i['location'][:40]:40s} -> ({c['lat']:.4f}, {c['lng']:.4f}) [{c['source']}]")

print(f"\n=== 失敗 {len(fail)} 筆 ===")
for i in fail:
    sec = i.get("land_section", "")
    no = i.get("land_no", "")
    addr = i.get("address", "")
    print(f"  {i['type']:10s} {i['location'][:40]:40s} | sec={sec} no={no} addr={addr}")
