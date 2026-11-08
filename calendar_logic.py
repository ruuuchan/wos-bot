"""TimeTree公開カレンダーの予定を取ってきて、月カレンダーの画像にする。

TimeTreeの公式APIは終了しているため、公開カレンダーのWebページが使っている
内部API（/api/v2/public_calendars/{id}/public_events）を読む。
TimeTree側の仕様が変わると読めなくなることがある。
"""
import calendar as pycal
import datetime as dt
import io
import re
import time
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw

from render import f, clean

JST = ZoneInfo("Asia/Tokyo")
BASE = "https://timetreeapp.com"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
_cache = {}  # (cal_id, year, month) -> (取得時刻, events)
CACHE_SEC = 600


def calendar_id(text: str) -> str | None:
    """「https://timetr.ee/p/a_4297」「https://timetreeapp.com/public_calendars/a_4297」「a_4297」→ a_4297"""
    text = text.strip()
    m = re.search(r"(?:timetr\.ee/p/|public_calendars/)([A-Za-z0-9_\-]+)", text)
    if m:
        return m.group(1)
    return text if re.fullmatch(r"[A-Za-z0-9_\-]+", text) else None


async def fetch_month(cal_id: str, year: int, month: int):
    key = (cal_id, year, month)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_SEC:
        return hit[1]
    import aiohttp  # discord.py に含まれている
    jar = aiohttp.CookieJar()
    async with aiohttp.ClientSession(cookie_jar=jar, headers={"User-Agent": UA},
                                     timeout=aiohttp.ClientTimeout(total=20)) as s:
        async with s.get(f"{BASE}/public_calendars/{cal_id}") as r:
            r.raise_for_status()
            html = await r.text()
        m = re.search(r'<meta\s+name=["\']csrf-token["\']\s+content=["\']([^"\']+)["\']', html)
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Referer": f"{BASE}/public_calendars/{cal_id}",
            "X-TimeTreeA": "web/2.1.0/1.0.0",
        }
        if m:
            headers["X-CSRF-Token"] = m.group(1)
        events, page = [], 1
        while page <= 10:
            url = f"{BASE}/api/v2/public_calendars/{cal_id}/public_events"
            async with s.get(url, params={"year": year, "month": month, "page": page}, headers=headers) as r:
                r.raise_for_status()
                data = await r.json(content_type=None)
            events += data.get("public_events", [])
            paging = data.get("paging") or {}
            if page >= (paging.get("total_pages") or 1):
                break
            page += 1
    out = [parse_event(e) for e in events]
    out = [e for e in out if e]
    _cache[key] = (time.time(), out)
    return out


def parse_event(e):
    # 終日予定はUTCの0時で入っていることが多いので、そのときはUTCのまま日付にする
    if e.get("all_day") and (e.get("start_timezone") in (None, "UTC")):
        tz = ZoneInfo("UTC")
    else:
        try:
            tz = ZoneInfo(e.get("region_timezone") or e.get("start_timezone") or "Asia/Tokyo")
        except Exception:
            tz = JST
    try:
        s = dt.datetime.fromtimestamp(e["start_at"] / 1000, tz)
        en = dt.datetime.fromtimestamp(e["end_at"] / 1000, tz)
    except Exception:
        return None
    sd, ed = s.date(), en.date()
    if e.get("all_day") and ed > sd and en.time() == dt.time(0, 0):
        ed -= dt.timedelta(days=1)  # 終日予定の終わりは「翌日0時」になっていることがある
    if ed < sd:
        ed = sd
    color = None
    for k in ("color", "label_color"):
        v = e.get(k)
        if isinstance(v, str) and re.fullmatch(r"#?[0-9A-Fa-f]{6}", v):
            color = "#" + v.lstrip("#")
    return {"title": e.get("title") or "", "start": sd, "end": ed, "color": color,
            "time": None if e.get("all_day") else s.strftime("%H:%M")}


# ---------- 画像 ----------
BG, GRID, TXT, SUB = "#ffffff", "#e6e6e6", "#222222", "#9a9a9a"
RED, BLUE = "#d64541", "#2d5be3"
BIG = ["氷原支配者", "同盟総動員", "最強王国", "SvS", "同盟大作戦", "王城決戦", "大統領"]
COLORS = {"red": "#d64a43", "yellow": "#f2c14e", "brown": "#968177"}


def event_color(ev):
    if ev["color"]:
        return ev["color"]
    t = ev["title"]
    if any(k in t for k in BIG):
        return COLORS["red"]
    if "砦" in t:
        return COLORS["yellow"]
    return COLORS["brown"]


def render_month(events, year, month, title="", today=None):
    weeks = pycal.Calendar(firstweekday=0).monthdatescalendar(year, month)
    return render_weeks(events, weeks, title, today, month=month)


def render_week(events, monday, title="", today=None):
    """1週間分（月〜日）"""
    week = [monday + dt.timedelta(days=i) for i in range(7)]
    return render_weeks(events, [week], title, today, month=None)


def render_weeks(events, weeks, title="", today=None, month=None):
    W = 1170
    CW = W // 7
    LANE, HEAD = 48, 70
    rows = []
    for wk in weeks:
        w0, w1 = wk[0], wk[-1]
        evs = [e for e in events if e["start"] <= w1 and e["end"] >= w0]
        # その週の中での開始日が早い順、同じなら長い予定を上に
        evs.sort(key=lambda e: (max(e["start"], w0), -(min(e["end"], w1) - max(e["start"], w0)).days, e["title"]))
        lanes = []  # 各レーンの「最後に使った日」
        placed = []
        for e in evs:
            a = max(e["start"], w0)
            b = min(e["end"], w1)
            ci, cj = (a - w0).days, (b - w0).days
            for li, last in enumerate(lanes):
                if last < ci:
                    lanes[li] = cj
                    placed.append((li, ci, cj, e))
                    break
            else:
                lanes.append(cj)
                placed.append((len(lanes) - 1, ci, cj, e))
        rows.append((wk, placed, max(1, len(lanes))))

    top = 160 if title else 70
    H = top + sum(HEAD + LANE * n + 16 for _, _, n in rows) + 20
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    if title:
        d.text((24, 26), title, font=f(40, True), fill=TXT)
    for i, wd in enumerate("月火水木金土日"):
        d.text((i * CW + CW // 2, top - 36), wd, font=f(28), fill=SUB, anchor="mm")
    y = top
    today = today or dt.datetime.now(JST).date()
    for wk, placed, n in rows:
        rh = HEAD + LANE * n + 16
        d.line([0, y, W, y], fill=GRID, width=2)
        for i, day in enumerate(wk):
            x = i * CW + CW // 2
            other = month is not None and day.month != month
            col = RED if i == 6 else BLUE if i == 5 else TXT
            if day == today:
                d.rectangle([i * CW, y + 1, (i + 1) * CW, y + rh], fill="#f2f2f2")
                d.ellipse([x - 26, y + 8, x + 26, y + 60], fill=TXT)
                col = "#ffffff"
            elif other:
                col = "#cfd8f5" if i == 5 else "#f2b8b6" if i == 6 else "#bbbbbb"
            label = str(day.day) if month is not None or i else f"{day.month}/{day.day}"
            if month is None and day.day == 1 and i:
                label = f"{day.month}/{day.day}"
            d.text((x, y + 34), label, font=f(32, day == today), fill=col, anchor="mm")
        for li, ci, cj, e in placed:
            x0, x1 = ci * CW + 8, (cj + 1) * CW - 8
            yy = y + HEAD + li * LANE
            c = event_color(e)
            d.rounded_rectangle([x0, yy, x1, yy + LANE - 8], 8, fill=c)
            label = e["title"] if not e["time"] else f"{e['time']} {e['title']}"
            fc = "#ffffff"
            font = f(28, True)
            txt = clean(label, d, font, x1 - x0 - 16)
            d.text(((x0 + x1) / 2, yy + (LANE - 8) / 2), txt, font=font, fill=fc, anchor="mm")
        y += rh
    d.line([0, y, W, y], fill=GRID, width=2)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    return buf
