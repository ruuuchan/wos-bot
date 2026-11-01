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
        # mode="rally"：施設ごとに「集結主」を決める方式
        "mode": "rally",
        "metric": "部隊戦力",
        "target": 250_000,
        "min_members": 15,  # これより少ないと参戦できない
        "leader_min": 2 / 3,  # 集結主になれるのは、トップの人のこの割合以上の人だけ
        "reserve": {"name": "玉拾い", "min": 4},
        "abandoned": ["王室兵器工場"],
        # side = L（左側・第1試験場側）／R（右側・第2試験場側）。2軍以降は同じ側の施設にしか出さない
        # count = 集結主の人数、need = 必要な強さ（トップの人を1とした割合）、prio = 強い人を回す順
        "places": [
            {"name": "第1武器試験場", "side": "L", "count": 2, "prio": 1, "need": 0, "points": 74_400},
            {"name": "第2武器試験場", "side": "R", "count": 2, "prio": 1, "need": 0, "points": 74_400},
            {"name": "スチームボイラー", "side": "L", "count": 1, "prio": 2, "need": 0.75, "points": 14_880},
            {"name": "兵器倉庫", "side": "L", "count": 1, "prio": 4, "need": 0.6, "leader_min": 0.6, "points": 10_080},
            {"name": "第1武器修理工場", "side": "L", "count": 1, "prio": 3, "need": 0.75, "points": 37_200},
            {"name": "第2武器修理工場", "side": "R", "count": 1, "prio": 3, "need": 0.75, "points": 37_200},
            {"name": "第3武器修理工場", "side": "L", "count": 1, "prio": 3, "need": 0.75, "points": 37_200},
            {"name": "第4武器修理工場", "side": "R", "count": 1, "prio": 3, "need": 0.75, "points": 37_200},
            {"name": "傭兵野営地", "side": "R", "count": 1, "prio": 9, "spare_only": True, "leader_min": 0.6, "points": 10_080},
            {"name": "中継所", "side": "R", "count": 1, "prio": 9, "spare_only": True, "points": 14_880},
            # 採取：集結主になれなかった中で強い人。足りなければ近い施設の人の5軍・6軍
            {"name": "採取・右上", "side": "R", "count": 1, "prio": 50, "points": 28_750, "march": True},
            {"name": "採取・右下", "side": "R", "count": 1, "prio": 50, "points": 28_750, "march": True},
            {"name": "採取・左上", "side": "L", "count": 1, "prio": 50, "points": 28_750, "march": True},
            {"name": "採取・左下", "side": "L", "count": 1, "prio": 50, "points": 28_750, "march": True},
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


MARCH_RATE = {1: 1.0, 2: 0.9, 3: 0.8, 4: 0.7}  # 1軍=100%、2軍=90%、3軍=80%、4軍=70%


def assign_rally(members, places, reserve_min, pos=None, leader_min=2 / 3):
    """集結主方式（兵器工場）。
    1. 集結主になれるのは、トップの人の leader_min（2/3）以上の人だけ
    2. 試験場（prio 1）：強い人の1軍から、第1・第2に均等（1→2→2→1）に集結主を入れる
    3. その他の施設：prio の順に、必要な強さ（need × トップ）を満たす行軍の中で一番強いものを集結主にする。
       まず1軍、いなければ2軍（90%）、3軍（80%）、4軍（70%）の順に探す。誰もいなければ今回は捨てる
    4. 集結主にならなかった人：
       ・強い人から採取（各場所1人ずつ）
       ・一番弱い人たちが玉拾い（最低人数）
       ・残りは「乗り手」：試験場に駐屯して、各施設の集結に乗る（試験場1・2に均等）
    """
    pool = sorted(members, key=lambda m: m["value"], reverse=True)
    if not pool:
        return [], [p for p in places if not p.get("march")], []
    top = pool[0]["value"]
    elig = [m for m in pool if m["value"] >= top * leader_min]
    used = set()           # (人, 何軍目)
    leaders = set()        # 集結主になった人
    slots = {}
    # spare_only：余っている人（採取にも入らず乗り手になる人）の中に集結主になれる人がいれば入れる
    rally_places = [p for p in places if not p.get("march") and not p.get("skip") and not p.get("spare_only")]
    spare_places = [p for p in places if p.get("spare_only")]
    skipped = [p for p in places if p.get("skip")]

    def take(m, k, place):
        used.add((id(m), k))
        leaders.add(id(m))
        slots.setdefault(place["name"], []).append(dict(m, march_no=k, eff=round(m["value"] * MARCH_RATE[k])))

    def next_free(m):
        return next((k for k in MARCH_RATE if (id(m), k) not in used), None)

    # 2. 試験場（いちばん小さい prio）
    first = min((p["prio"] for p in rally_places), default=None)
    labs = [p for p in rally_places if p["prio"] == first]
    need_n = sum(p["count"] for p in labs)
    order, fwd = [], True
    while len(order) < need_n:
        seq = labs if fwd else labs[::-1]
        for p in seq:
            if len(order) < need_n and sum(1 for x in order if x is p) < p["count"]:
                order.append(p)
        fwd = not fwd
    cands = sorted(((m, k) for k in MARCH_RATE for m in elig), key=lambda g: (g[1], -g[0]["value"]))
    def here(m, place):  # 同じ施設に同じ人の行軍は2つ出せない
        return any(x.get("name") == m["name"] for x in slots.get(place["name"], []))

    def homes_of(m):  # その人がすでに集結主になっている施設
        return [n for n, xs in slots.items() for x in xs if x.get("march_no") and x["name"] == m["name"]]

    def d2(a, b):
        if not pos or a not in pos or b not in pos:
            return 0
        (x1, y1), (x2, y2) = pos[a], pos[b]
        return (x1 - x2) ** 2 + (y1 - y2) ** 2

    def near(m, place):  # 2軍以降は、その人の今いる施設から近い順
        hs = homes_of(m)
        return min((d2(h, place["name"]) for h in hs), default=0)

    side = {p["name"]: p.get("side") for p in places}

    def same_side(m, place):  # 2軍以降は、1軍と同じ側（左／右）の施設だけ
        sd = side.get(place["name"])
        return not sd or all(side.get(h) in (None, sd) for h in homes_of(m))

    lab_names_all = {p["name"] for p in labs}
    for p in order:
        # 試験場同士は遠いので、もう片方の試験場にいる人の2軍は使わない
        g = next(((m, k) for m, k in cands
                  if (id(m), k) not in used and next_free(m) == k and not here(m, p)
                  and not (set(homes_of(m)) & (lab_names_all - {p["name"]})) and (k == 1 or same_side(m, p))), None)
        if g:
            take(g[0], g[1], p)
    # 3. その他の施設
    for p in sorted((p for p in rally_places if p["prio"] != first), key=lambda p: p["prio"]):
        line = top * p.get("need", 0)
        elig_p = [m for m in pool if m["value"] >= top * min(leader_min, p.get("leader_min", leader_min))]
        for _ in range(p["count"]):
            g = None
            for k in MARCH_RATE:
                ok = [m for m in elig_p if (id(m), k) not in used and next_free(m) == k
                      and m["value"] * MARCH_RATE[k] >= line and not here(m, p) and (k == 1 or same_side(m, p))]
                if ok:
                    # 1軍は強い人から。2軍以降は、その人がいる施設から近い人を優先（同じくらいなら強い人）
                    g = (min(ok, key=lambda m: (near(m, p) if k > 1 else 0, -m["value"])), k)
                    break
            if g:
                take(g[0], g[1], p)
        if p["name"] not in slots:
            skipped.append(p)
    # 4. 採取と玉拾い
    others = [m for m in pool if id(m) not in leaders]
    n_rest = min(reserve_min, len(others))
    rest = others[len(others) - n_rest:] if n_rest else []
    mid = others[:len(others) - n_rest]
    gathers = [p for p in places if p.get("march")]
    for p, m in zip(gathers, mid):  # 採取は各1人
        slots.setdefault(p["name"], []).append(m)
    riders = mid[len(gathers):]
    for p in sorted(spare_places, key=lambda p: p["prio"]):
        for _ in range(p["count"]):
            g = next((m for m in riders if m["value"] >= top * min(leader_min, p.get("leader_min", leader_min))), None)
            if g:
                riders.remove(g)
                take(g, 1, p)
        if p["name"] not in slots:
            skipped.append(p)
    lab_names = [p["name"] for p in labs if p["name"] in slots]
    for i, m in enumerate(riders):  # 乗り手は試験場1→2→2→1…に駐屯
        if not lab_names:
            break
        r, q = divmod(i, len(lab_names))
        name = lab_names[q] if r % 2 == 0 else lab_names[::-1][q]
        slots[name].append(dict(m, rider=True))
    # 採取が空いたら、近い施設の集結主の5軍・6軍で埋める
    homes = [(m, name) for name, ms in slots.items() for m in ms if m.get("march_no") or m.get("rider")]

    def dist(a, b):
        if not pos or a not in pos or b not in pos:
            return 0
        (x1, y1), (x2, y2) = pos[a], pos[b]
        return (x1 - x2) ** 2 + (y1 - y2) ** 2

    gave = set()
    for p in gathers:
        if not slots.get(p["name"]) and homes:
            sd = p.get("side")
            same = [h for h in homes if not sd or next((q.get("side") for q in places if q["name"] == h[1]), None) in (None, sd)]
            pool_h = [h for h in same if h[0]["name"] not in gave] or same or homes
            m, home = min(pool_h, key=lambda h: dist(p["name"], h[1]))
            gave.add(m["name"])
            base_m = {k: v for k, v in m.items() if k not in ("march_no", "eff")}
            slots[p["name"]] = [dict(base_m, sub="5軍・6軍", home=home)]
    order_names = [x["name"] for x in places]
    taken = [(p, slots[p["name"]]) for p in places if p["name"] in slots]
    taken.sort(key=lambda t: order_names.index(t[0]["name"]))
    return taken, skipped, rest


def plan(members, places, reserve_min, pos=None, mode=None, leader_min=2 / 3):
    if mode == "rally":
        return assign_rally(members, places, reserve_min, pos=pos, leader_min=leader_min)
    return assign(members, places, reserve_min, pos=pos)


def fallback_leaders(members, n=3):
    """試験場が取れなかった時に、王室兵器工場へ集結をかける候補（上位3人）"""
    return [m["name"] for m in sorted(members, key=lambda m: -m["value"])[:n]]


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
                  attack=None, notes=(), time="", min_members=0, mode=None, leader_min=2 / 3):
    taken, skipped, rest = plan(members, places, reserve_for(members, reserve, min_members), pos=pos,
                                mode=mode, leader_min=leader_min)
    got = sum(p.get("points", 0) for p, _ in taken)
    # 2軍・3軍が集結主の施設は「余裕があれば」
    spare = {p["name"] for p, ms in taken if any((m.get("march_no") or 1) > 1 for m in ms)}
    got_main = sum(p.get("points", 0) for p, _ in taken if p["name"] not in spare)
    drop = [p["name"] for p in skipped] + list(abandoned)
    rally = {} if mode == "rally" else rallies(taken, drop, pos)
    fb = fallback_leaders(members) if mode == "rally" and abandoned else []
    strikers = sorted(members, key=lambda m: -m["value"])[:attack["count"]] if attack and attack.get("count") else []
    show_points = any(p.get("points") for p in places)
    short = max(0, min_members - len(members))

    # 高さを先に計算
    RH, PH = 44, 58
    h = 200 + (106 if show_points else 0) + (70 if short else 0)
    for p, ms in taken:
        h += PH + RH * max(1, len(ms)) + 16 + (38 if p.get("note") else 0)
    SIDES = [("L", "◀ 左側（第1試験場側）"), ("R", "右側（第2試験場側） ▶")]
    has_side = any(p.get("side") for p, _ in taken)
    if has_side:
        seq = []
        for sd, title in SIDES + [(None, "")]:
            grp = [(p, ms) for p, ms in taken if p.get("side") == sd]
            if grp:
                if title:
                    seq.append(("hdr", title))
                seq += [("pl", t) for t in grp]
        h += 64 * sum(1 for k, _ in seq if k == "hdr")
    else:
        seq = [("pl", t) for t in taken]
    if drop:
        h += 60 + 40 * len(drop)
    if fb:
        h += 100
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
        ok = target and got_main >= target
        col = "#1dd1a1" if ok else ("#ffb020" if target else TXT)
        bh = 92 if spare else 56
        d.rounded_rectangle([24, y, W - 24, y + bh], 10, outline=col, width=2)
        d.text((48, y + 12), "取りに行く施設の合計", font=f(24, True), fill=SUB)
        tgt = f"　／　目標 {target:,}P" if target else ""
        d.text((W - 48, y + 10), f"{got_main:,}P{tgt}", font=f(28, True), fill=col, anchor="ra")
        if spare:
            d.text((W - 48, y + 52), f"余裕があれば（2・3軍の集結）込みで {got:,}P", font=f(21, True), fill="#ffd166", anchor="ra")
        y += bh + 24
    y += 10

    for kind, item in seq:
        if kind == "hdr":
            d.text((48, y + 14), item, font=f(30, True), fill="#7fa7d9")
            d.line([24, y + 56, W - 24, y + 56], fill="#4a90e2", width=3)
            y += 64
            continue
        p, ms = item
        d.rounded_rectangle([24, y, W - 24, y + PH - 8], 10, fill="#4a3300" if p["name"] in spare else "#1d3557")
        d.text((48, y + 10), p["name"] + ("（余裕があれば）" if p["name"] in spare else ""), font=f(28, True), fill=TXT)
        nm_ = sum(1 for m in ms if not m.get("sub"))
        nk_ = len(ms) - nm_
        lead = any(m.get("march_no") for m in ms)
        nr_ = sum(1 for m in ms if m.get("rider"))
        nm_ -= nr_
        right = ((f"集結主{nm_}人" if lead else f"{nm_}人") if nm_ else "") + (f"＋乗り手{nr_}人" if nr_ else "") + (f"{'＋' if nm_ else ''}行軍{nk_}" if nk_ else "") + (f"　{p['points']:,}P" if p.get("points") else "")
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
            if m.get("march_no"):
                k = m["march_no"]
                sx = 72 + d.textlength(nm, font=f(26, True)) + 12
                d.rounded_rectangle([sx, y + 9, sx + 64, y + 35], 7, fill="#7a4a00" if k > 1 else "#1d3557",
                                    outline="#ffb020" if k > 1 else HEAD, width=1)
                d.text((sx + 32, y + 22), f"{k}軍", font=f(17, True), fill="#ffd166" if k > 1 else HEAD, anchor="mm")
                extra = f"（{int(MARCH_RATE[k] * 100)}%）" if k > 1 else ""
                d.text((W - 48, y + 8), f"{fmt_value(m['eff'])}{extra}", font=f(24), fill=GOLD, anchor="ra")
            elif m.get("rider"):
                sx = 72 + d.textlength(nm, font=f(26, True)) + 12
                d.rounded_rectangle([sx, y + 9, sx + 76, y + 35], 7, fill="#2c3e5a")
                d.text((sx + 38, y + 22), "乗り手", font=f(17, True), fill="#9fb3cc", anchor="mm")
                d.text((W - 48, y + 8), fmt_value(m["value"]), font=f(24), fill=SUB, anchor="ra")
            elif m.get("sub"):
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
        d.text((48, y + 10), "今回は捨てる施設" if mode == "rally" else "今回は捨てる施設 → 余裕があれば強い人の2軍で集結",
               font=f(24, True), fill=SUB)
        y += 52
        for i, name in enumerate(drop):
            d.text((48, y + i * 40), f"・{name}", font=f(22), fill=DIM)
            if name in rally:
                d.text((W - 48, y + i * 40), f"集結：{rally[name]}（2軍）", font=f(22, True), fill="#ffb020", anchor="ra")
        y += 40 * len(drop) + 8
    if fb:
        d.rounded_rectangle([24, y, W - 24, y + 84], 10, outline="#ffb020", width=2)
        d.text((48, y + 10), f"試験場が取れなかった時 → {abandoned[0]}に集結", font=f(24, True), fill="#ffb020")
        d.text((48, y + 46), "集結主：" + "／".join(fb) + "（上位3人の中から）", font=f(22, True), fill=TXT)
        y += 100

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
    "第1武器試験場": (250, 625),
    "第2武器修理工場": (840, 640),
    "第1武器修理工場": (260, 840),
    "第2武器試験場": (812, 890),
    "採取・左下": (330, 1060),
    "採取・右下": (850, 1110),
    "兵器倉庫": (560, 1170),
    "第3武器修理工場": (300, 1330),
    "中継所": (680, 1330),
    "王室兵器工場": (540, 760),
}
MAPS = {"foundry": {"pos": FOUNDRY_MAP, "center": "王室兵器工場"}}
CARD_W = 236


def render_map(ev, event_name, legion, metric, members, places, reserve, min_members=0, mode=None, leader_min=2 / 3):
    pos = MAPS[ev]["pos"]
    taken, skipped, rest = plan(members, places, reserve_for(members, reserve, min_members), pos=pos,
                                mode=mode, leader_min=leader_min)
    tk = {p["name"]: ms for p, ms in taken}
    sk = {p["name"] for p in skipped}
    center = MAPS[ev]["center"]
    rally = {} if mode == "rally" else rallies(taken, [p["name"] for p in skipped] + [center], pos)
    fb = fallback_leaders(members) if mode == "rally" else []
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
    # 左側・右側の境目
    if any(p.get("side") for p in places):
        for y0_, y1_ in ((190, 622), (908, 1430)):
            for yy_ in range(y0_, y1_, 24):
                d.line([540, yy_, 540, min(yy_ + 12, y1_)], fill="#3a4d6b", width=2)
        d.text((120, 190), "◀ 左側", font=f(22, True), fill="#7fa7d9")
        d.text((W - 120, 190), "右側 ▶", font=f(22, True), fill="#7fa7d9", anchor="ra")
    # 中央
    d.ellipse([400, 625, 680, 905], outline="#4b5d75", width=3, fill="#1f2b40")
    d.text((540, 730), MAPS[ev]["center"], font=f(22, True), fill=DIM, anchor="mm")
    d.text((540, 764), "今回は捨てる", font=f(18), fill=DIM, anchor="mm")
    if center in rally:
        d.text((540, 806), "余裕があれば集結", font=f(16), fill="#ffb020", anchor="mm")
        d.text((540, 830), clean(rally[center], d, f(18, True), 170) + "（2軍）", font=f(18, True), fill="#ffb020", anchor="mm")
    if fb:
        d.text((540, 796), "試験場が取れなかった時", font=f(16, True), fill="#ffb020", anchor="mm")
        for i, nm in enumerate(fb):
            d.text((540, 822 + 22 * i), clean(nm, d, f(17, True), 200), font=f(17, True), fill="#ffb020", anchor="mm")

    for name, (cx, cy) in pos.items():
        if name in tk:
            ms = tk[name]
            gather = name.startswith("採取")
            spare_here = any((m.get("march_no") or 1) > 1 for m in ms)
            head = "#134e3a" if gather else ("#4a3300" if spare_here else "#1d3557")
            edge = "#1dd1a1" if gather else ("#ffb020" if spare_here else "#54a0ff")
            lines = []  # (高さ, 描く関数用データ)
            if spare_here:
                lines.append((26, ("spare", None)))
            for m in ms:
                if not m.get("rider"):
                    lines.append((32, ("m", m)))
            riders = [m for m in ms if m.get("rider")]
            if riders:
                lines.append((26, ("rhead", len(riders))))
                for k in range(0, len(riders), 2):
                    lines.append((26, ("rpair", riders[k:k + 2])))
        elif name in sk:
            head, edge, lines = "#1f2b40", "#4b5d75", None
        else:
            continue
        h = 46 + (sum(a for a, _ in lines) if lines else (3 if name in rally else 1) * 32) + 10
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
        yy = y0 + 46
        for lh, (kind, m) in lines:
            c = yy + lh // 2
            if kind == "spare":
                d.text((cx, c), "余裕があれば", font=f(17, True), fill="#ffb020", anchor="mm")
            elif kind == "rhead":
                d.text((cx, c), f"乗り手 {m}人", font=f(16, True), fill=SUB, anchor="mm")
            elif kind == "rpair":
                txt = "　".join(clean(r["name"], d, f(17), (CARD_W - 30) // 2) for r in m)
                d.text((cx, c), txt, font=f(17), fill="#c9d6e8", anchor="mm")
            elif m.get("march_no"):
                k = m["march_no"]
                label = "★" + clean(m["name"], d, f(20, True), CARD_W - 90) + (f"（{k}軍）" if k > 1 else "")
                d.text((cx, c), label, font=f(20, True), fill="#ffd166" if k > 1 else TXT, anchor="mm")
            elif m.get("sub"):
                label = clean(m["name"], d, f(20, True), CARD_W - 100) + "（5・6軍）"
                d.text((cx, c), label, font=f(19, True), fill="#7ee2b8", anchor="mm")
            else:
                d.text((cx, c), clean(m["name"], d, f(21, True), CARD_W - 20), font=f(21, True), fill=TXT, anchor="mm")
            yy += lh

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
