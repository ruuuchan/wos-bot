"""時間枠の自動割り振り。火晶 → 加速の多い順に、希望した中で一番早い空き枠を割り当てる。"""
import datetime as dt


def allocate(apps, start: dt.datetime, slot_minutes: int, hours: int):
    n = hours * 60 // slot_minutes
    slots = [None] * n
    left = []
    for a in sorted(apps, key=lambda a: (a["fc"], a["sp_hours"]), reverse=True):
        wish = set()
        for iso in a["hours"]:  # 各値 = 希望した枠の開始時刻
            off = int((dt.datetime.fromisoformat(iso) - start).total_seconds() // 60)
            k = off // slot_minutes
            if 0 <= k < n:
                wish.add(k)
        free = [k for k in sorted(wish) if slots[k] is None]
        if free:
            slots[free[0]] = a
        else:
            left.append(a)
    return slots, left
