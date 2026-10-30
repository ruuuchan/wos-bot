"""時間枠の自動割り振り（1枠1人）。

決め方：
1. 火晶が多い順（同じなら加速が多い順）に1人ずつ見ていく
2. その人を、希望した枠のどこかに入れられるか試す。
   空きがなくても、すでに入っている人を「その人の別の希望枠」へずらせば入れられるなら、ずらして入れる
   （すでに入った人が外れることはない＝上位の人は必ず優先される）
3. どうしても入れられない人だけ「希望枠が埋まっていた人」になる

単純に早い者勝ちで入れるより、あぶれる人が少なくなる。
"""
import datetime as dt


def allocate(apps, start: dt.datetime, slot_minutes: int, hours: int):
    n = hours * 60 // slot_minutes
    order = sorted(apps, key=lambda a: (a["fc"], a["sp_hours"]), reverse=True)
    wishes = []
    for a in order:
        w = set()
        for iso in a["hours"]:  # 各値 = 希望した枠の開始時刻
            k = int((dt.datetime.fromisoformat(iso) - start).total_seconds() // 60) // slot_minutes
            if 0 <= k < n:
                w.add(k)
        wishes.append(sorted(w))

    owner = [None] * n  # 枠 → 何番目の人が入っているか

    def place(p, seen):
        for k in wishes[p]:
            if k in seen:
                continue
            seen.add(k)
            if owner[k] is None or place(owner[k], seen):
                owner[k] = p
                return True
        return False

    left = []
    for p, a in enumerate(order):
        if not place(p, set()):
            left.append(a)
    slots = [order[p] if p is not None else None for p in owner]
    return slots, left
