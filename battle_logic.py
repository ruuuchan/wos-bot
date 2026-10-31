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
        "min_members": 15,  # これより少ないと参戦できない
        "reserve": {"name": "玉拾い", "min": 4},
        # 最初から取りに行かない場所（余裕があれば、強い人の2軍で集結をかける）
        "abandoned": ["王室兵器工場"],
        # prio = 取りに行く順（小さいほど先）、strength = 強い人を入れる順（小さいほど強い人）
        "places": [
            {"name": "第1武器試験場", "count": 4, "prio": 1, "strength": 1, "points": 74_400},
            {"name": "第2武器試験場", "count": 4, "prio": 1, "strength": 1, "points": 74_400},
            # 王城は捨てて、その分をスチームボイラーと兵器倉庫へ
            {"name": "スチームボイラー", "count": 2, "prio": 2, "strength": 2, "points": 14_880},
            {"name": "兵器倉庫", "count": 1, "prio": 2, "strength": 2, "points": 10_080},
            # march=True：人が足りないときは、施設メンバーの5軍・6軍（余った行軍）で埋める
            {"name": "採取・右上", "count": 1, "prio": 3, "strength": 7, "points": 28_750, "march": True},
            {"name": "採取・右下", "count": 1, "prio": 3, "strength": 7, "points": 28_750, "march": True},
            {"name": "採取・左上", "count": 1, "prio": 3, "strength": 7, "points": 28_750, "march": True},
            {"name": "採取・左下", "count": 1, "prio": 3, "strength": 7, "points": 28_750, "march": True},
            {"name": "第1武器修理工場", "count": 2, "prio": 4, "strength": 3, "points": 37_200},
            {"name": "第2武器修理工場", "count": 2, "prio": 4, "strength": 3, "points": 37_200},
            {"name": "第3武器修理工場", "count": 2, "prio": 5, "strength": 4, "points": 37_200},
            {"name": "第4武器修理工場", "count": 2, "prio": 5, "strength": 4, "points": 37_200},
            {"name": "中継所", "count": 1, "prio": 6, "strength": 5, "points": 14_880},
            {"name": "傭兵野営地", "count": 1, "prio": 7, "strength": 6, "points": 10_080},
        ],
    },
    "canyon": {
        "metric": "総力",
        "target": 0,
        "reserve": {"name": "遊撃・その他", "min": 0},
        # fill=True：人数を決めず、来た人を強さが偏らないよう均等に分ける
        "places": [
            {"name": "A　30,31方面", "count": 0, "prio": 1, "strength": 1, "fill": True},
            {"name": "B　18,27方面", "count": 0, "prio": 1, "strength": 1, "fill": True},
            {"name": "C　28,19方面", "count": 0, "prio": 1, "strength": 1, "fill": True},
        ],
        "attack": {"count": 5, "title": "真ん中を攻める時",
                   "note": "この人たちの1軍のみ真ん中へ送る。その他のメンバーは持ち場の状況を見つつ、相手の真ん中へのルートを断ちにいく"},
        "notes": ["真ん中を守る時：10〜15人の駐屯を維持できるように、誰でもいいので送る"],
    },
}

# 参加する時間帯（メンバーには「軍団1・2」より時間のほうがわかりやすいため、時間で分ける）
LEGIONS = ["21:00", "23:00"]


def strength(p):
    return p.get("strength", p["prio"])


def reserve_for(members, reserve, min_members=0):
    """玉拾いなどの最低人数。参戦できる人数（15人）に届かないときは残さず、全員を施設・採取に回す"""
    return reserve.get("min", 0) if len(members) >= min_members else 0


def assign(members, places, reserve_min, pos=None):
    """members: [{name, value}]（同じ時間帯の人）。
    戻り値: (taken, skipped, rest)
      taken   = [(place, [member, ...]), ...]  取りに行く場所と入る人（強い順）
      skipped = [place, ...]                   今回は空ける場所
      rest    = [member, ...]                  残り（玉拾いなど）
    """
    pool = sorted(members, key=lambda m: m["value"], reverse=True)
    # 1. どこを取りに行くか
    chosen, skipped, left = [], [], len(pool)
    march_fill = []  # 人を置かず、施設メンバーの5軍・6軍で埋める場所
    for _, tier in groupby(sorted(places, key=lambda p: p["prio"]), key=lambda p: p["prio"]):
        tier = list(tier)
        if all(p.get("fill") for p in tier):
            # 均等に分ける場所：残った人を全部、強さが偏らないように分ける
            avail = max(0, left - reserve_min)
            for i, p in enumerate(tier):
                chosen.append(dict(p, count=avail // len(tier) + (1 if i < avail % len(tier) else 0)))
            left -= avail
            continue
        need = sum(p["count"] for p in tier)
        if need == 0:
            continue
        if all(p.get("march") for p in tier):
            # 行軍で埋められる場所：置ける人数だけ専任を置き、足りない分は5軍・6軍で埋める
            room = max(0, left - reserve_min)
            for p in tier:
                k = min(p["count"], room)
                room -= k
                left -= k
                if k:
                    chosen.append(dict(p, count=k))
                if k < p["count"]:
                    march_fill.append((p, p["count"] - k))
            continue
        if left - need < reserve_min:
            if not chosen:
                # 一番大事な場所（試験場など）は空けない。人が足りなければ人数を減らして均等に入れる
                avail = max(0, left - reserve_min) or min(left, len(tier))
                for i, p in enumerate(tier):
                    k = min(p["count"], avail // len(tier) + (1 if i < avail % len(tier) else 0))
                    chosen.append(dict(p, count=k))
                    left -= k
                continue
            skipped.extend(tier)
            continue
        chosen.extend(tier)
        left -= need
    # 2. 誰を入れるか（強さ順）
    slots = {p["name"]: [] for p in chosen}
    for p, _ in march_fill:
        slots.setdefault(p["name"], [])
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
    # 3. 足りない採取などは、施設に入った人の5軍・6軍で埋める
    #    地図の位置がわかるときは、その採取場所に一番近い施設の人から出す（同じ人が重ならないように）
    if march_fill:
        givers = [(m, p["name"]) for p in chosen if not p.get("march") for m in slots[p["name"]]]
        used = set()

        def dist(a, b):
            if not pos or a not in pos or b not in pos:
                return 0
            (x1, y1), (x2, y2) = pos[a], pos[b]
            return (x1 - x2) ** 2 + (y1 - y2) ** 2

        for p, n in march_fill:
            for _ in range(n):
                cands = [g for g in givers if id(g[0]) not in used] or givers
                if not cands:
                    break
                m, home = min(cands, key=lambda g: (dist(p["name"], g[1]), -g[0]["value"]))
                used.add(id(m))
                slots[p["name"]].append(dict(m, sub="5軍・6軍", home=home))
    order = [x["name"] for x in places]
    base = {p["name"]: p for p in places}
    names = {p["name"] for p in chosen} | {p["name"] for p, _ in march_fill}
    taken = sorted(((base[n], slots[n]) for n in names), key=lambda t: order.index(t[0]["name"]))
    return taken, skipped, pool


def rallies(taken, targets, pos=None):
    """今回捨てる場所に、余裕があれば集結をかける人（強い人の2軍）を割り当てる。
    強い人 = 一番強い人を入れる場所（試験場など）のメンバー。その場所に近い人から、同じ人が重ならないように選ぶ"""
    homes = [(p, ms) for p, ms in taken if not p.get("march")]
    if not homes or not targets:
        return {}
    top = min(strength(p) for p, _ in homes)
    cands = sorted(((m, p["name"]) for p, ms in homes if strength(p) == top for m in ms if not m.get("sub")),
                   key=lambda g: -g[0]["value"])
    if not cands:
        return {}

    def dist(a, b):
        if not pos or a not in pos or b not in pos:
            return 0
        (x1, y1), (x2, y2) = pos[a], pos[b]
        return (x1 - x2) ** 2 + (y1 - y2) ** 2

    out, used = {}, set()
    for t in targets:
        pool = [g for g in cands if id(g[0]) not in used] or cands
        m, home = min(pool, key=lambda g: (dist(t, g[1]), -g[0]["value"]))
        used.add(id(m))
        out[t] = m["name"]
    return out


def wrap(d, text, font, width):
    """幅に合わせて折り返す（行頭に「、。」が来ないように）"""
    lines, cur = [], ""
    for ch in text:
        if d.textlength(cur + ch, font=font) > width and cur:
            if ch in "、。）」":
                cur += ch
                continue
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    return lines


def fmt_value(v):
    return f"{v:,}"


def render_legion(event_name, legion, metric, members, places, reserve, target, pos=None, abandoned=(),
                  attack=None, notes=(), time="", min_members=0):
    taken, skipped, rest = assign(members, places, reserve_for(members, reserve, min_members), pos=pos)
    got = sum(p.get("points", 0) for p, _ in taken)
    drop = [p["name"] for p in skipped] + list(abandoned)
    rally = rallies(taken, drop, pos)
    strikers = sorted(members, key=lambda m: -m["value"])[:attack["count"]] if attack and attack.get("count") else []
    show_points = any(p.get("points") for p in places)
    short = max(0, min_members - len(members))

    # 高さを先に計算
    RH, PH = 44, 58
    h = 200 + (70 if show_points else 0) + (70 if short else 0)
    for p, ms in taken:
        h += PH + RH * max(1, len(ms)) + 16 + (38 if p.get("note") else 0)
    if drop:
        h += 60 + 40 * len(drop)
    h += 70
    _d = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    note_lines = wrap(_d, attack.get("note", ""), f(21), W - 140) if strikers else []
    if strikers:
        h += PH + 34 * len(note_lines) + 8 + RH * ((len(strikers) + 1) // 2) + 16
    show_rest = bool(rest) or reserve_for(members, reserve, min_members) > 0
    if show_rest:
        h += PH + RH * ((len(rest) + 1) // 2 if rest else 1) + 16
    h += 44 * len(notes) + (16 if notes else 0)

    img = Image.new("RGB", (W, h), BG)
    d = ImageDraw.Draw(img)
    d.text((48, 40), f"{event_name} 振り分け　{legion}の部", font=f(50, True), fill=TXT)
    d.text((48, 110), f"{metric}の高い順 ・ 参戦 {len(members)}人", font=f(26), fill=SUB)
    y = 160
    if short:
        d.rounded_rectangle([24, y, W - 24, y + 56], 10, fill="#5a1e2a")
        d.text((48, y + 12), f"⚠ {min_members}人に届いていません（あと{short}人）。このままだと参戦できません",
               font=f(24, True), fill="#ff9fb0")
        y += 70
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
        nm_ = sum(1 for m in ms if not m.get("sub"))
        nk_ = len(ms) - nm_
        right = (f"{nm_}人" if nm_ else "") + (f"{'＋' if nm_ else ''}行軍{nk_}" if nk_ else "") + (f"　{p['points']:,}P" if p.get("points") else "")
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
            nm = clean(m["name"], d, f(26, True), 520)
            d.text((72, y + 7), nm, font=f(26, True), fill=TXT)
            if m.get("sub"):
                sx = 72 + d.textlength(nm, font=f(26, True)) + 12
                d.rounded_rectangle([sx, y + 9, sx + 110, y + 35], 7, fill="#2c3e5a")
                d.text((sx + 55, y + 22), m["sub"], font=f(17, True), fill="#7ee2b8", anchor="mm")
                d.text((W - 48, y + 8), f"{m.get('home', '')}から", font=f(22), fill=SUB, anchor="ra")
            else:
                if any(m is st for st in strikers):
                    sx = 72 + d.textlength(nm, font=f(26, True)) + 12
                    d.rounded_rectangle([sx, y + 9, sx + 60, y + 35], 7, fill="#5a1e2a")
                    d.text((sx + 30, y + 22), "攻め", font=f(17, True), fill="#ff9fb0", anchor="mm")
                d.text((W - 48, y + 8), fmt_value(m["value"]), font=f(24), fill=GOLD, anchor="ra")
            y += RH
        y += 16

    if drop:
        d.text((48, y + 10), "今回は捨てる施設 → 余裕があれば強い人の2軍で集結", font=f(24, True), fill=SUB)
        y += 52
        for i, name in enumerate(drop):
            d.text((48, y + i * 40), f"・{name}", font=f(22), fill=DIM)
            if name in rally:
                d.text((W - 48, y + i * 40), f"集結：{rally[name]}（2軍）", font=f(22, True), fill="#ffb020", anchor="ra")
        y += 40 * len(drop) + 8

    if strikers:
        d.rounded_rectangle([24, y, W - 24, y + PH - 8], 10, fill="#5a1e2a")
        d.text((48, y + 10), attack.get("title", "攻め"), font=f(28, True), fill=TXT)
        d.text((W - 48, y + 12), f"{len(strikers)}人", font=f(24, True), fill="#ff9fb0", anchor="ra")
        y += PH
        for k, ln in enumerate(note_lines):
            d.text((72, y + 4 + 34 * k), ln, font=f(21), fill="#ff9fb0")
        y += 34 * len(note_lines) + 8
        for i, m in enumerate(strikers):
            x = 48 + (i % 2) * 508
            yy = y + (i // 2) * RH
            d.text((x + 24, yy + 7), clean(m["name"], d, f(24, True), 300), font=f(24, True), fill=TXT)
            d.text((x + 480, yy + 8), fmt_value(m["value"]), font=f(22), fill=GOLD, anchor="ra")
        y += RH * ((len(strikers) + 1) // 2) + 16
    for n in notes:
        d.text((48, y + 6), f"・{n}", font=f(22, True), fill=HEAD)
        y += 44
    if notes:
        y += 16

    if show_rest:
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


# ---------- 地図版（兵器工場） ----------
# 地図上の位置（画像の座標）。名前が一致する場所だけ地図に描く
FOUNDRY_MAP = {
    "スチームボイラー": (330, 250),
    "第4武器修理工場": (690, 250),
    "傭兵野営地": (520, 415),
    "採取・左上": (250, 440),
    "採取・右上": (800, 440),
    "第1武器試験場": (250, 600),
    "第2武器修理工場": (840, 640),
    "第1武器修理工場": (260, 840),
    "第2武器試験場": (790, 880),
    "採取・左下": (330, 1060),
    "採取・右下": (850, 1110),
    "兵器倉庫": (560, 1170),
    "第3武器修理工場": (300, 1330),
    "中継所": (680, 1330),
    "王室兵器工場": (540, 760),
}
MAPS = {"foundry": {"pos": FOUNDRY_MAP, "center": "王室兵器工場"}}
CARD_W = 236


def render_map(ev, event_name, legion, metric, members, places, reserve, min_members=0):
    pos = MAPS[ev]["pos"]
    taken, skipped, rest = assign(members, places, reserve_for(members, reserve, min_members), pos=pos)
    tk = {p["name"]: ms for p, ms in taken}
    sk = {p["name"] for p in skipped}
    center = MAPS[ev]["center"]
    rally = rallies(taken, [p["name"] for p in skipped] + [center], pos)
    H = 1580 + 46 * ((len(rest) + 2) // 3)
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.text((48, 40), f"{event_name} 配置図　{legion}の部", font=f(50, True), fill=TXT)
    d.text((48, 110), f"{metric}の高い順 ・ 参戦 {len(members)}人", font=f(26), fill=SUB)

    # 地面とセーフゾーン
    d.rounded_rectangle([60, 180, W - 60, 1440], 60, fill="#1a2436", outline="#2c3e5a", width=3)
    for cx, col, lab in [(70, "#54a0ff", "Safe zone"), (W - 70, "#ff6b6b", "Safe zone")]:
        d.polygon([(cx, 740), (cx + 44, 784), (cx, 828), (cx - 44, 784)], outline=col, width=4)
        d.text((cx, 850), lab, font=f(20, True), fill=col, anchor="ma")
    # 中央
    d.ellipse([415, 640, 665, 890], outline="#4b5d75", width=3, fill="#1f2b40")
    d.text((540, 730), MAPS[ev]["center"], font=f(22, True), fill=DIM, anchor="mm")
    d.text((540, 764), "今回は捨てる", font=f(18), fill=DIM, anchor="mm")
    if center in rally:
        d.text((540, 806), "余裕があれば集結", font=f(16), fill="#ffb020", anchor="mm")
        d.text((540, 830), clean(rally[center], d, f(18, True), 170) + "（2軍）", font=f(18, True), fill="#ffb020", anchor="mm")

    for name, (cx, cy) in pos.items():
        if name in tk:
            ms = tk[name]
            gather = name.startswith("採取")
            head = "#134e3a" if gather else "#1d3557"
            edge = "#1dd1a1" if gather else "#54a0ff"
            lines = ms
        elif name in sk:
            head, edge, lines = "#1f2b40", "#4b5d75", None
        else:
            continue
        n = len(lines) if lines else (3 if name in rally else 1)
        h = 46 + 32 * n + 10
        x0, y0 = cx - CARD_W // 2, cy - h // 2
        d.rounded_rectangle([x0, y0, x0 + CARD_W, y0 + h], 12, fill="#101a2b", outline=edge, width=3)
        d.rounded_rectangle([x0, y0, x0 + CARD_W, y0 + 44], 12, fill=head)
        d.text((cx, y0 + 22), clean(name, d, f(21, True), CARD_W - 16), font=f(21, True),
               fill=TXT if lines is not None else DIM, anchor="mm")
        if lines is None:
            d.text((cx, y0 + 46 + 16), "今回は捨てる", font=f(19), fill=DIM, anchor="mm")
            if name in rally:
                d.text((cx, y0 + 46 + 48), "余裕があれば集結", font=f(17), fill="#ffb020", anchor="mm")
                d.text((cx, y0 + 46 + 80), clean(rally[name], d, f(19, True), CARD_W - 80) + "（2軍）",
                       font=f(19, True), fill="#ffb020", anchor="mm")
            continue
        for i, m in enumerate(lines):
            yy = y0 + 46 + 32 * i + 16
            if m.get("sub"):
                label = clean(m["name"], d, f(20, True), CARD_W - 100) + "（5・6軍）"
                d.text((cx, yy), label, font=f(19, True), fill="#7ee2b8", anchor="mm")
            else:
                d.text((cx, yy), clean(m["name"], d, f(21, True), CARD_W - 20), font=f(21, True), fill=TXT, anchor="mm")

    # 地図にない場所（あとから追加した場所など）
    extra = [(p, ms) for p, ms in taken if p["name"] not in pos]
    y = 1470
    if extra:
        d.text((48, y), "地図外：" + "　".join(f"{p['name']}（{'、'.join(m['name'] for m in ms)}）" for p, ms in extra)[:80],
               font=f(20), fill=SUB)
        y += 34
    # 玉拾い
    d.rounded_rectangle([24, y, W - 24, y + 48], 10, fill="#134e3a")
    d.text((48, y + 8), f"{reserve.get('name', '残り')}　{len(rest)}人", font=f(26, True), fill=TXT)
    y += 58
    for i, m in enumerate(rest):
        d.text((48 + (i % 3) * 340, y + (i // 3) * 46), clean(m["name"], d, f(23, True), 320), font=f(23, True), fill=TXT)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    return buf
