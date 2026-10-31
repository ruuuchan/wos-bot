"""兵器工場・峡谷の振り分け（計算と画像）。

決め方：
- 軍団ごとに、数値（部隊戦力・総力など）の高い順に並べる
- 優先度の小さい場所から順に、まとめて人を入れる
- 「残り（採取・玉拾い・駐屯）」に最低人数を残せないときは、その優先度の場所は今回は空ける
  （人が少ない日に、全部の施設へ少しずつ散らばらないようにするため）
- 同じ優先度の場所には、強い人を 1→2→2→1… の順に交互に入れて強さを揃える
"""
import io
from itertools import groupby

from PIL import Image, ImageDraw

import render  # フォントと色を共通で使う
from render import f, clean, BG, STRIPE, TXT, SUB, HEAD, DIM, GOLD, W

EVENTS = {
    "foundry": "兵器工場",
    "canyon": "峡谷",
}

DEFAULTS = {
    "foundry": {
        "metric": "部隊戦力",
        "target": 250_000,
        "reserve": {"name": "採取・玉拾い・駐屯", "min": 4},
        "places": [
            {"name": "第1武器試験場", "count": 4, "prio": 1, "points": 74_400},
            {"name": "第2武器試験場", "count": 4, "prio": 1, "points": 74_400},
            {"name": "王城", "count": 3, "prio": 2, "points": 75_600},
            {"name": "第1武器修理工場", "count": 2, "prio": 3, "points": 37_200},
            {"name": "第2武器修理工場", "count": 2, "prio": 3, "points": 37_200},
            {"name": "第3武器修理工場", "count": 2, "prio": 4, "points": 37_200},
            {"name": "第4武器修理工場", "count": 2, "prio": 4, "points": 37_200},
            {"name": "中継所", "count": 1, "prio": 5, "points": 14_880},
            {"name": "スチームボイラー", "count": 1, "prio": 5, "points": 14_880},
            {"name": "兵器倉庫", "count": 1, "prio": 6, "points": 10_080},
            {"name": "傭兵野営地", "count": 1, "prio": 6, "points": 10_080},
        ],
    },
    "canyon": {
        "metric": "総力",
        "target": 0,
        "reserve": {"name": "遊撃・その他", "min": 0},
        "places": [],
    },
}

LEGIONS = ["軍団1", "軍団2"]


def assign(members, places, reserve_min):
    """members: [{name, value}]（同じ軍団の人）。
    戻り値: (taken, skipped, rest)
      taken   = [(place, [member, ...]), ...]  取りに行く場所と入る人（強い順）
      skipped = [place, ...]                   今回は空ける場所
      rest    = [member, ...]                  残り（採取・玉拾いなど）
    """
    pool = sorted(members, key=lambda m: m["value"], reverse=True)
    taken, skipped = [], []
    for _, tier in groupby(sorted(places, key=lambda p: p["prio"]), key=lambda p: p["prio"]):
        tier = list(tier)
        need = sum(p["count"] for p in tier)
        if need == 0:
            continue
        if len(pool) - need < reserve_min:
            skipped.extend(tier)
            continue
        group, pool = pool[:need], pool[need:]
        slots = {p["name"]: [] for p in tier}
        order, forward = list(tier), True
        i = 0
        while i < len(group):
            seq = order if forward else list(reversed(order))
            for p in seq:
                if i >= len(group):
                    break
                if len(slots[p["name"]]) < p["count"]:
                    slots[p["name"]].append(group[i])
                    i += 1
            forward = not forward
        taken.extend((p, slots[p["name"]]) for p in tier)
    taken.sort(key=lambda t: [x["name"] for x in places].index(t[0]["name"]))
    return taken, skipped, pool


def fmt_value(v):
    return f"{v:,}"


def render_legion(event_name, legion, metric, members, places, reserve, target):
    taken, skipped, rest = assign(members, places, reserve.get("min", 0))
    got = sum(p.get("points", 0) for p, _ in taken)
    show_points = any(p.get("points") for p in places)

    # 高さを先に計算
    RH, PH = 44, 58
    h = 200 + (70 if show_points else 0)
    for _, ms in taken:
        h += PH + RH * max(1, len(ms)) + 16
    if skipped:
        h += 60 + 40 * ((len(skipped) + 2) // 3)
    h += PH + RH * ((len(rest) + 1) // 2 if rest else 1) + 16 + 70

    img = Image.new("RGB", (W, h), BG)
    d = ImageDraw.Draw(img)
    d.text((48, 40), f"{event_name} 振り分け　{legion}", font=f(50, True), fill=TXT)
    d.text((48, 110), f"{metric}の高い順 ・ 参戦 {len(members)}人", font=f(26), fill=SUB)
    y = 160
    if show_points:
        ok = target and got >= target
        col = "#1dd1a1" if ok else ("#ffb020" if target else TXT)
        d.rounded_rectangle([24, y, W - 24, y + 56], 10, outline=col, width=2)
        d.text((48, y + 12), "取りに行く施設の合計", font=f(24, True), fill=SUB)
        tgt = f"　／　目標 {target:,}P" if target else ""
        d.text((W - 48, y + 10), f"{got:,}P{tgt}", font=f(28, True), fill=col, anchor="ra")
        y += 80
    y += 10

    for p, ms in taken:
        d.rounded_rectangle([24, y, W - 24, y + PH - 8], 10, fill="#1d3557")
        d.text((48, y + 10), p["name"], font=f(28, True), fill=TXT)
        right = f"{len(ms)}人" + (f"　{p['points']:,}P" if p.get("points") else "")
        d.text((W - 48, y + 12), right, font=f(24, True), fill=HEAD, anchor="ra")
        y += PH
        if not ms:
            d.text((72, y + 8), "（人が足りません）", font=f(24), fill=DIM)
            y += RH
        for i, m in enumerate(ms):
            if i % 2 == 0:
                d.rectangle([24, y, W - 24, y + RH], fill=STRIPE)
            d.text((72, y + 7), clean(m["name"], d, f(26, True), 640), font=f(26, True), fill=TXT)
            d.text((W - 48, y + 8), fmt_value(m["value"]), font=f(24), fill=GOLD, anchor="ra")
            y += RH
        y += 16

    if skipped:
        d.text((48, y + 10), "今回は空ける施設（人数が足りないため）", font=f(24, True), fill=DIM)
        y += 52
        for i, p in enumerate(skipped):
            d.text((48 + (i % 3) * 330, y + (i // 3) * 40), f"・{p['name']}", font=f(22), fill=DIM)
        y += 40 * ((len(skipped) + 2) // 3) + 8

    d.rounded_rectangle([24, y, W - 24, y + PH - 8], 10, fill="#134e3a")
    d.text((48, y + 10), reserve.get("name", "残り"), font=f(28, True), fill=TXT)
    d.text((W - 48, y + 12), f"{len(rest)}人", font=f(24, True), fill="#7ee2b8", anchor="ra")
    y += PH
    if not rest:
        d.text((72, y + 8), "（なし）", font=f(24), fill=DIM)
    for i, m in enumerate(rest):
        x = 48 + (i % 2) * 508
        yy = y + (i // 2) * RH
        d.text((x + 24, yy + 7), clean(m["name"], d, f(24, True), 300), font=f(24, True), fill=TXT)
        d.text((x + 480, yy + 8), fmt_value(m["value"]), font=f(22), fill=GOLD, anchor="ra")
    d.text((48, h - 52), "自動生成", font=f(22), fill="#5f7591")

    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    return buf
