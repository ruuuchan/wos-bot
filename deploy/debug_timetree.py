"""TimeTreeから返ってくる予定データを確認する（調査用）。
使い方：/opt/wosbot/venv/bin/python deploy/debug_timetree.py a_4297 2026 11
"""
import asyncio
import json
import re
import sys

import aiohttp

BASE = "https://timetreeapp.com"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


async def main(cid, y, m):
    async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(), headers={"User-Agent": UA}) as s:
        async with s.get(f"{BASE}/public_calendars/{cid}") as r:
            html = await r.text()
        tok = re.search(r'<meta\s+name=["\']csrf-token["\']\s+content=["\']([^"\']+)["\']', html)
        h = {"Accept": "application/json", "Referer": f"{BASE}/public_calendars/{cid}", "X-TimeTreeA": "web/2.1.0/1.0.0"}
        if tok:
            h["X-CSRF-Token"] = tok.group(1)
        async with s.get(f"{BASE}/api/v2/public_calendars/{cid}", headers=h) as r:
            cal = await r.json(content_type=None)
        pc = cal.get("public_calendar", cal)
        print("カレンダーの項目名:", sorted(pc.keys()) if isinstance(pc, dict) else type(pc))
        for k, v in (pc.items() if isinstance(pc, dict) else []):
            if any(w in k for w in ("label", "color", "categor", "theme")):
                print("  ", k, "=", json.dumps(v, ensure_ascii=False)[:600])
        async with s.get(f"{BASE}/api/v2/public_calendars/{cid}/public_events",
                         params={"year": y, "month": m}, headers=h) as r:
            print("HTTP", r.status)
            data = await r.json(content_type=None)
    evs = data.get("public_events", [])
    print("paging:", data.get("paging"), " 件数:", len(evs))
    keys = sorted({k for e in evs for k in e})
    print("項目名:", keys)
    import datetime as dt
    f = lambda x: dt.datetime.fromtimestamp(x / 1000, dt.timezone.utc).strftime("%m/%d(%a) %H:%M")
    for e in evs:
        col = e.get("color")
        print(f"{e.get('title')}｜終日={e.get('all_day')}｜tz={e.get('start_timezone')}｜"
              f"{f(e['start_at'])} → {f(e['end_at'])} (UTC)｜色={('#%06x' % col) if isinstance(col, int) else col}｜"
              f"{e.get('recurrences')}")


asyncio.run(main(sys.argv[1], int(sys.argv[2]), int(sys.argv[3])))
