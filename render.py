"""集計画像（時間割り・兵舎英雄一覧）を作る。"""
import datetime as dt
import io

from PIL import Image, ImageDraw, ImageFont

import config

W = 1080
BG, STRIPE, LINE = "#0f1b2d", "#15253c", "#4a90e2"
TXT, SUB, HEAD, DIM, GOLD, CHIP = "#ffffff", "#9fb3cc", "#7fa7d9", "#4b5d75", "#ffd166", "#2c3e5a"
ALLY_COLORS = ["#ff9f43", "#54a0ff", "#1dd1a1", "#ff6b9a", "#c08cff", "#feca57", "#48dbfb", "#a3cb38", "#b0bec5", "#f8a5c2"]
WEEK = "月火水木金土日"
_fonts = {}


def f(size, bold=False):
    key = (size, bold)
    if key not in _fonts:
        _fonts[key] = ImageFont.truetype(config.FONT_BOLD if bold else config.FONT_REGULAR, size, index=0)
    return _fonts[key]


def ally_color(name, allies):
    i = allies.index(name) if name in allies else len(allies)
    return ALLY_COLORS[i % len(ALLY_COLORS)]


def clean(name, d, font, width):
    """絵文字など表示できない文字を外し、幅に収まるよう切り詰める。"""
    s = "".join(c for c in name if ord(c) < 0x10000)
    if d.textlength(s, font=font) <= width:
        return s
    while s and d.textlength(s + "…", font=font) > width:
        s = s[:-1]
    return s + "…"


def sp_label(h):
    return f"{h // 24}日{h % 24}時間" if h % 24 else f"{h // 24}日"


def ally_tag(d, x, y, name, allies, max_w=130):
    """同盟名のタグ。長い名前は文字を小さくして収める"""
    size = 22
    while size > 14 and d.textlength(name, font=f(size, True)) > max_w - 16:
        size -= 1
    label = clean(name, d, f(size, True), max_w - 16)
    w = max(95, int(d.textlength(label, font=f(size, True))) + 20)
    d.rounded_rectangle([x, y, x + w, y + 29], 8, fill=ally_color(name, allies))
    d.text((x + w / 2, y + 14), label, font=f(size, True), fill=BG, anchor="mm")


def is_dash(a):
    """「21:00までにログインしてスタートダッシュできる」を選んだ人"""
    return a.get("join") == config.JOIN_OPTIONS[0]


DASH = "#ffb020"


def dash_mark(d, x0, y, h, name_x, name, font, name_w):
    """スタダの人：行の左に金色の帯 ＋ 名前の後ろに「スタダ」バッジ"""
    d.rectangle([x0, y, x0 + 6, y + h], fill=DASH)
    nm = clean(name, d, font, name_w)
    bx = name_x + d.textlength(nm, font=font) + 10
    d.rounded_rectangle([bx, y + 10, bx + 66, y + 36], 7, fill=DASH)
    d.text((bx + 33, y + 23), "スタダ", font=f(17, True), fill=BG, anchor="mm")


def dash_count(d, x, y, apps):
    n = sum(is_dash(a) for a in apps)
    d.rounded_rectangle([x, y, x + 66, y + 28], 7, fill=DASH)
    d.text((x + 33, y + 14), "スタダ", font=f(17, True), fill=BG, anchor="mm")
    d.text((x + 80, y + 1), f"{n}人　／　途中参加 {len(apps) - n}人", font=f(24, True), fill=TXT)


def to_png(img):
    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    return buf


def period_label(start, hours):
    end = start + dt.timedelta(hours=hours) - dt.timedelta(minutes=1)
    fmt = lambda t: f"{t.month}/{t.day}({WEEK[t.weekday()]}){t.hour}:{t.minute:02d}"
    return f"{fmt(start)} 〜 {fmt(end)}"


def render_schedule(apps, slots, left, start, slot_minutes, hours, title, allies):
    rh, top = 46, 262
    lh = (130 + 50 * len(left)) if left else 0
    H = top + rh * len(slots) + 40 + lh + 70
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.text((48, 40), title, font=f(52, True), fill=TXT)
    d.text((48, 110), f"{period_label(start, hours)} ・ 火晶 → 加速の多い順で自動割当", font=f(26), fill=SUB)
    dash_count(d, 48, 148, apps)
    for x, t in [(48, "時間"), (250, "名前"), (560, "同盟"), (700, "火晶"), (860, "加速")]:
        d.text((x, top - 50), t, font=f(24, True), fill=HEAD)
    for i, a in enumerate(slots):
        y = top + i * rh
        t = start + dt.timedelta(minutes=slot_minutes * i)
        if i % 2 == 0:
            d.rectangle([24, y, W - 24, y + rh], fill=STRIPE)
        if i and t.hour == 0 and t.minute == 0:
            d.line([24, y, W - 24, y], fill=LINE, width=3)
        d.text((48, y + 8), f"{t.month}/{t.day} {t:%H:%M}", font=f(24), fill="#c9d6e8")
        if a:
            dash = is_dash(a)
            d.text((250, y + 6), clean(a["name"], d, f(26, True), 210 if dash else 290), font=f(26, True), fill=TXT)
            if dash:
                dash_mark(d, 24, y, rh, 250, a["name"], f(26, True), 210)
            ally_tag(d, 555, y + 9, a["ally"], allies, max_w=135)
            d.text((700, y + 8), f"{a['fc']:,}", font=f(24), fill=GOLD)
            d.text((860, y + 8), sp_label(a["sp_hours"]), font=f(24), fill="#c9d6e8")
        else:
            d.text((250, y + 8), "空き", font=f(24), fill=DIM)
    y = top + len(slots) * rh + 40
    if left:
        d.text((48, y), f"希望枠が埋まっていた人（{len(left)}名）", font=f(30, True), fill="#ff6b6b")
        y += 60
        for a in left:
            tag = "【スタダ】" if is_dash(a) else ""
            d.text((48, y), f"・{tag}{clean(a['name'], d, f(24), 400)}（{a['ally']}／火晶{a['fc']:,}）", font=f(24), fill=DASH if tag else "#c9d6e8")
            y += 50
    d.text((48, H - 55), f"回答 {len(apps)}名 ・ 割当 {sum(1 for s in slots if s)}枠 ・ 自動生成", font=f(22), fill="#5f7591")
    return to_png(img)


def render_roster(apps, heroes, allies):
    lv = lambda v: config.BARRACKS_LEVELS.index(v) if v in config.BARRACKS_LEVELS else -1
    hi = lv(config.HIGHLIGHT_FROM)
    order = {a: i for i, a in enumerate(allies)}
    # スタダ → 途中参加、その中で同盟順、さらに兵舎レベル（盾＋槍＋弓）の高い順
    mem = sorted(apps, key=lambda a: (not is_dash(a), order.get(a["ally"], 99), -sum(lv(b) for b in a["barracks"])))
    groups = [(True, "スタダ"), (False, "途中参加")]
    groups = [(g, name, [a for a in mem if is_dash(a) == g]) for g, name in groups]
    groups = [x for x in groups if x[2]]
    GH = 70  # 区切り見出しの高さ

    img = Image.new("RGB", (W, 10), BG)
    d = ImageDraw.Draw(img)
    # 英雄の保有人数チップの段数を先に計算
    chips, x, y = [], 48, 245
    for h in heroes:
        lab = f"{h} {sum(h in a['heroes'] for a in apps)}"
        tw = d.textlength(lab, font=f(22))
        if x + tw + 24 > W - 30:
            x, y = 48, y + 52
        chips.append((x, y, tw, lab))
        x += tw + 36
    rh, top = 96, y + 160
    H = top + rh * len(mem) + GH * len(groups) + 70

    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.text((48, 40), "兵舎・英雄 一覧", font=f(52, True), fill=TXT)
    d.text((48, 110), "スタダ → 途中参加、同盟順・兵舎レベルの高い順 ／ 英雄は遠征スキルLv5", font=f(26), fill=SUB)
    dash_count(d, 48, 150, apps)
    d.text((48, 200), "英雄の保有人数", font=f(26, True), fill=HEAD)
    for x, y, tw, lab in chips:
        d.rounded_rectangle([x, y, x + tw + 24, y + 40], 10, fill=CHIP)
        d.text((x + 12, y + 5), lab, font=f(22), fill="#e0e8f3")
    for x, t in [(48, "名前"), (330, "同盟"), (460, "盾"), (600, "槍"), (740, "弓")]:
        d.text((x, top - 50), t, font=f(24, True), fill=HEAD)
    y = top
    for g, gname, members in groups:
        # 区切り見出し
        if g:
            d.rounded_rectangle([24, y + 14, 120, y + 50], 8, fill=DASH)
            d.text((72, y + 32), gname, font=f(22, True), fill=BG, anchor="mm")
            d.text((136, y + 18), f"{len(members)}人", font=f(24, True), fill=TXT)
        else:
            d.rounded_rectangle([24, y + 14, 150, y + 50], 8, outline=SUB, width=2)
            d.text((87, y + 32), gname, font=f(22, True), fill=SUB, anchor="mm")
            d.text((166, y + 18), f"{len(members)}人", font=f(24, True), fill=SUB)
        y += GH
        prev = None
        for i, a in enumerate(members):
            if i % 2 == 0:
                d.rectangle([24, y, W - 24, y + rh], fill=STRIPE)
            if prev is not None and prev != a["ally"]:
                d.line([24, y, W - 24, y], fill=LINE, width=3)
            prev = a["ally"]
            draw_member(d, a, y, rh, heroes, allies, lv, hi)
            y += rh
    d.text((48, H - 55), f"回答 {len(apps)}名 ・ 自動生成", font=f(22), fill="#5f7591")
    return to_png(img)


def draw_member(d, a, y, rh, heroes, allies, lv, hi):
    """1人分の行（名前・スタダ・同盟・兵舎・英雄）"""
    dash = is_dash(a)
    d.text((48, y + 8), clean(a["name"], d, f(26, True), 180 if dash else 260), font=f(26, True), fill=TXT)
    if dash:
        dash_mark(d, 24, y, rh, 48, a["name"], f(26, True), 180)
    ally_tag(d, 325, y + 11, a["ally"], allies, max_w=118)
    for k, b in enumerate(a["barracks"]):
        strong = hi >= 0 and lv(b) >= hi
        d.text((460 + 140 * k, y + 8), b, font=f(26, strong), fill=GOLD if strong else "#c9d6e8")
    x = 48
    if not a["heroes"]:
        d.text((x, y + 54), "英雄なし", font=f(20), fill=DIM)
    mine = [h for h in heroes if h in a["heroes"]] + [h for h in a["heroes"] if h not in heroes]
    for n, h in enumerate(mine):
        tw = d.textlength(h, font=f(19))
        if x + tw + 18 > W - 90:
            d.text((x, y + 53), f"+{len(mine) - n}", font=f(19), fill=SUB)
            break
        d.rounded_rectangle([x, y + 52, x + tw + 18, y + 82], 7, fill=CHIP)
        d.text((x + 9, y + 53), h, font=f(19), fill="#e0e8f3")
        x += tw + 26
