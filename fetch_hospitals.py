#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
從衛福部「醫院評鑑資訊專區」抓取全台 22 縣市醫院病床資料，
輸出成 hospitals-data.js 給 index.html 讀取。

用法（Mac 終端機，只需要內建的 python3，不用安裝任何套件）：
    python3 fetch_hospitals.py              # 抓全部 22 縣市
    python3 fetch_hospitals.py 嘉義縣 嘉義市   # 只抓指定縣市

抓完會在同一個資料夾產生 hospitals-data.js，
把它和 index.html 放在同一個資料夾（或一起上傳到你的網站）即可。
"""

import json
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

C01_API = "https://openinfo.mohw.gov.tw/C01/GetPageData"
C02_API = "https://openinfo.mohw.gov.tw/C02/GetPageData"

AREA_CODE_MAP = {
    "台北市": "01", "台中市": "03", "台南市": "05", "高雄市": "07",
    "基隆市": "11", "新竹市": "12", "嘉義市": "22", "新北市": "31",
    "桃園市": "32", "新竹縣": "33", "宜蘭縣": "34", "苗栗縣": "35",
    "彰化縣": "37", "南投縣": "38", "雲林縣": "39", "嘉義縣": "40",
    "屏東縣": "43", "澎湖縣": "44", "花蓮縣": "45", "台東縣": "46",
    "金門縣": "90", "連江縣": "91",
}

COUNTY_RE = re.compile(
    r"(?:嘉義縣|嘉義市|台北市|新北市|桃園市|台中市|台南市|高雄市|基隆市|新竹市|新竹縣|"
    r"苗栗縣|彰化縣|南投縣|雲林縣|屏東縣|宜蘭縣|花蓮縣|台東縣|澎湖縣|金門縣|連江縣)"
    r"([^區鎮鄉市]{1,3}[區鎮鄉市])"
)

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def post_json(url, inner, referer, retries=4):
    """POST 到衛福部 API，失敗自動重試（間隔逐次加長）。"""
    body = json.dumps({
        "data": json.dumps(inner, ensure_ascii=False),
        "device": "Mac",
        "browser": "Chrome",
        "token": "",
    }).encode("utf-8")
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url, data=body, method="POST",
                headers={
                    "Content-Type": "application/json",
                    "Referer": referer,
                    "User-Agent": UA,
                },
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"{url} 失敗：{last}")


def fetch_directory(area_code):
    items, page, total = [], 1, 1
    while page <= total and page <= 10:
        j = post_json(
            C01_API,
            {
                "TYPE": "1", "ZONE_AREA_CODE": area_code, "BAS_NAME": "",
                "BAS_AGENCY_ID": "", "KIND": "", "depList": "",
                "searchmoreList": "", "HA": "", "LIC": "", "ASSESS": "",
                "currentPage": page, "pageSize": "100",
            },
            "https://openinfo.mohw.gov.tw/web/C01",
        )
        if not j.get("success") or not isinstance(j.get("list"), list):
            break
        items.extend(j["list"])
        try:
            total = int(str(j.get("totalPage") or "1"))
        except ValueError:
            total = 1
        page += 1
        time.sleep(0.2)
    return items


def to_int(v):
    try:
        return int(str(v or "0"))
    except ValueError:
        return 0


def fetch_beds(item):
    hid = str(item.get("BAS_AGENCY_ID") or "").strip()
    zone = str(item.get("ZONE_CODE") or "A").strip()
    zone_name = str(item.get("ZONE_NAME") or "本院").strip()
    county = str(item.get("AREA_NAME") or "").strip().replace("臺", "台")

    j = post_json(
        C02_API,
        {"BAS_AGENCY_ID": hid, "ZONE_CODE": zone},
        f"https://openinfo.mohw.gov.tw/web/C02?type=1&basAgencyId={hid}&zoneCode={zone}",
    )
    if not j.get("success") or not j.get("data"):
        raise RuntimeError("C02 回傳異常")

    data = j["data"]
    bas = data.get("magbas") or {}
    beds = data.get("magbed") if isinstance(data.get("magbed"), list) else []
    ha1 = data.get("HA1") or {}

    base_name = str(item.get("BAS_NAME") or bas.get("AGENCY_NAME") or bas.get("BAS_NAME") or "").strip()
    full_name = f"{base_name}（{zone_name}）" if zone != "A" and zone_name and zone_name != "本院" else base_name

    address = str(bas.get("ADDRESS") or bas.get("AGENCY_ADDR") or "").strip()
    m = COUNTY_RE.search(address.replace("臺", "台"))
    district = m.group(1) if m else ""

    lm = re.search(r"(醫學中心|區域醫院|地區醫院)", str(ha1.get("RESULT_NAME") or ""))
    level = lm.group(1) if lm else "地區醫院"

    acute = psych = chronic = general = 0
    icu = dialysis = isolation = er_obs = rcc = baby = special = 0
    for b in beds:
        kind = str(b.get("BED_KIND_NAME") or "").strip()
        name = str(b.get("BED_CODE_NAME") or "").strip()
        n = to_int(b.get("BED_OPEN"))
        if n <= 0 or "病床" not in kind:
            continue
        if kind == "一般病床":
            general += n
            if "精神" in name:
                psych += n
            elif "急性一般" in name:
                acute += n
            else:
                chronic += n
        elif kind == "特殊病床":
            special += n
            if "加護" in name:
                icu += n
            elif "透析" in name or "洗腎" in name:
                dialysis += n
            elif "隔離" in name:
                isolation += n
            elif "急診觀察" in name:
                er_obs += n
            elif "呼吸照護" in name:
                rcc += n
            elif "嬰兒" in name or "新生兒" in name:
                baby += n

    return {
        "id": f"{hid}_{zone}", "code": hid, "zoneCode": zone, "zoneName": zone_name,
        "name": full_name, "county": county, "district": district,
        "address": address, "level": level,
        "acuteGeneral": acute, "psychGeneral": psych, "chronicGeneral": chronic,
        "generalTotal": general, "icu": icu, "dialysis": dialysis,
        "isolation": isolation, "erObs": er_obs, "rcc": rcc, "baby": baby,
        "specialTotal": special, "totalBeds": general + special,
    }


def main():
    counties = [c.replace("臺", "台") for c in sys.argv[1:]] or list(AREA_CODE_MAP)
    unknown = [c for c in counties if c not in AREA_CODE_MAP]
    if unknown:
        sys.exit(f"不認得的縣市：{'、'.join(unknown)}")

    # 1. 名錄
    directory = {}
    for c in counties:
        print(f"[名錄] {c} …", end=" ", flush=True)
        items = fetch_directory(AREA_CODE_MAP[c])
        for it in items:
            directory.setdefault(f"{it.get('BAS_AGENCY_ID')}_{it.get('ZONE_CODE') or 'A'}", it)
        print(f"{len(items)} 筆")

    todo = list(directory.values())
    print(f"\n共 {len(todo)} 個院區，開始抓病床資料…\n")

    # 2. 病床（同時 4 條，並放慢速度避免被擋）
    hospitals, failed = [], []

    def work(it):
        try:
            r = fetch_beds(it)
            time.sleep(0.15)
            return r, None
        except Exception as e:  # noqa: BLE001
            return None, f"{it.get('BAS_AGENCY_ID')} {it.get('BAS_NAME')}: {e}"

    with ThreadPoolExecutor(max_workers=4) as ex:
        for i, (r, err) in enumerate(ex.map(work, todo), 1):
            if r:
                hospitals.append(r)
            else:
                failed.append(err)
            if i % 20 == 0 or i == len(todo):
                print(f"  進度 {i}/{len(todo)}（失敗 {len(failed)}）")

    hospitals.sort(key=lambda h: h["totalBeds"], reverse=True)

    payload = {
        "source": "衛生福利部醫院評鑑資訊專區 (C01+C02)",
        "fetchedAt": datetime.now(timezone.utc).isoformat(),
        "counties": counties,
        "hospitals": hospitals,
    }
    with open("hospitals-data.js", "w", encoding="utf-8") as f:
        f.write("window.HOSPITAL_DATA = ")
        json.dump(payload, f, ensure_ascii=False)
        f.write(";\n")

    print(f"\n完成：{len(hospitals)} 筆已寫入 hospitals-data.js")
    if failed:
        print(f"有 {len(failed)} 筆失敗（再執行一次通常就會補齊）：")
        for e in failed[:20]:
            print("  -", e)


if __name__ == "__main__":
    main()
