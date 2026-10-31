"""兵器工場・峡谷の振り分け（計算と画像）。

決め方：
1. どこを取りに行くか：優先度（prio）の小さい場所から順に、まとめて取りに行く。
   「残り（玉拾いなど）」に最低人数を残せないときは、その優先度の場所は今回は空ける
   （人が少ない日に、全部の施設へ少しずつ散らばらないようにするため）
2. 誰を入れるか：取りに行く場所に、強さ順（strength）の小さい場所から強い人を入れる。
   例：採取は早めに取りに行く（優先度が高い）が、入るのは施設の人の次に強い人（強さ順は後ろ）
3. 強さ順が同じ場所には、強い人を 1→2→2→1… の順に交互に入れて強さを揃える
4. 残った人（一番弱い人たち）は「玉拾い」など
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
        "reserve": {"name": "玉拾い", "min": 0},
        # prio = 取りに行く順（小さいほど先）、strength = 強い人を入れる順（小さいほど強い人）
        "places": [
            {"name": "第1武器試験場", "count": 4, "prio": 1, "strength": 1, "points": 74_400},
            {"name": "第2武器試験場", "count": 4, "prio": 1, "strength": 1, "points": 74_400},
            {"name": "王城", "count": 3, "prio": 2, "strength": 2, "points": 75_600},
            {"name": "採取・右上", "count": 1, "prio": 3, "strength": 7, "points": 28_750, "note": "＋施設メンバーの5軍・6軍も採取へ"},
            {"name": "採取・右下", "count": 1, "prio": 3, "strength": 7, "points": 28_750},
            {"name": "採取・左上", "count": 1, "prio": 3, "strength": 7, "points": 28_750},
            {"name": "採取・左下", "count": 1, "prio": 3, "strength": 7, "points": 28_750},
            {"name": "第1武器修理工場", "count": 2, "prio": 4, "strength": 3, "points": 37_200},
            {"name": "第2武器修理工場", "count": 2, "prio": 4, "strength": 3, "points": 37_200},
            {"name": "第3武器修理工場", "count": 2, "prio": 5, "strength": 4, "points": 37_200},
            {"name": "第4武器修理工場", "count": 2, "prio": 5, "strength": 4, "points": 37_200},
            {"name": "中継所", "count": 1, "prio": 6, "strength": 5, "points": 14_880},
            {"name": "スチームボイラー", "count": 1, "prio": 6, "strength": 5, "points": 14_880},
            {"name": "兵器倉庫", "count": 1, "prio": 7, "strength": 6, "points": 10_080},
            {"name": "傭兵野営地", "count": 1, "prio": 7, "strength": 6, "points": 10_080},
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


def strength(p):
    return p.get("strength", p["prio"])


def assign(members, places, reserve_min):
    """members: [{name, value}]（同じ軍団の人）。
    戻り値: (taken, skipped, rest)
      taken   = [(place, [member, ...]), ...]  取りに行く場所と入る人（強い順）
      skipped = [place, ...]                   今回は空ける場所
      rest    = [member, ...]                  残り（玉拾いなど）
    """
    pool = sorted(members, key=lambda m: m["value"], reverse=True)
    # 1. どこを取りに行くか
    chosen, skipped, left = [], [], len(pool)
    for _, tier in groupby(sorted(places, key=lambda p: p["prio"]), key=lambda p: p["prio"]):
        tier = list(tier)
        need = sum(p["count"] for p in tier)
        if need == 0:
            continue
        if left - need < reserve_min:
            skipped.extend(tier)
            continue
        chosen.extend(tier)
        left -= need
    # 2. 誰を入れるか（強さ順）
    slots = {p["name"]: [] for p in chosen}
    for _, tier in groupby(sorted(chosen, key=strength), key=strength):
        tier = list(tier)
        need = sum(p["count"] for p in tier)
        group, pool = pool[:need], pool[need:]
        forward, i = True, 0
        while i < len(group):
            for p in (tier if forward else tier[::-1]):
                if i >= len(group):
                    break
                if len(slots[p["name"]]) < p["count"]:
                    slots[p["name"]].append(group[i])
                    i += 1
            forward = not forward
    order = [x["name"] for x in places]
    taken = sorted(((p, slots[p["name"]]) for p in chosen), key=lambda t: order.index(t[0]["name"]))
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
    for p, ms in taken:
        h += PH + RH * max(1, len(ms)) + 16 + (38 if p.get("note") else 0)
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
        if p.get("note"):
            d.text((72, y + 6), p["note"], font=f(22), fill="#7ee2b8")
            y += 38
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
