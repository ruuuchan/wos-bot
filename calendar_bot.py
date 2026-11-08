"""TimeTreeのイベントカレンダーをDiscordで見る。bot.py から setup() で登録する。

/カレンダー            … 今月（または来月）のカレンダー画像を出す（誰でも使える）
/今週の予定            … 今週（または来週）の予定を画像で出す（誰でも使える）
/カレンダー設定        … 【運営】読み込むTimeTree公開カレンダーのURLを変える
/週間カレンダー自動投稿 … 【運営】毎週決まった曜日・時刻に、その週の予定をこのチャンネルへ自動投稿
/週間カレンダー停止    … 【運営】自動投稿を止める
/月間カレンダー自動投稿 … 【運営】毎月1日に、その月のカレンダーをこのチャンネルへ自動投稿
/月間カレンダー停止    … 【運営】毎月の自動投稿を止める
"""
import asyncio
import datetime as dt
import json

from discord.ext import tasks

import discord
from discord import app_commands

import db
import calendar_logic as C

OTHER_CAL = "b_4297"  # 「もう一つのカレンダー」（/カレンダー 種類:もう一つ で表示）
DEFAULT_CAL = "a_4297"  # 複数のカレンダーはカンマ区切り（予定は1枚にまとめて表示）
COMMANDS = ["カレンダー", "今週の予定", "カレンダー設定", "週間カレンダー自動投稿", "週間カレンダー停止",
            "月間カレンダー自動投稿", "月間カレンダー停止"]


def cal_of(gid):
    return db.get_setting(gid, "timetree", DEFAULT_CAL)


def link_view(cal_id):
    v = discord.ui.View()
    ids = [c for c in cal_id.split(",") if c][:5]
    for i, c in enumerate(ids, 1):
        label = "TimeTreeで開く" if len(ids) == 1 else "TimeTreeで開く" + "①②③④⑤"[i - 1]
        v.add_item(discord.ui.Button(label=label, url=f"https://timetreeapp.com/public_calendars/{c}"))
    return v


WEEKDAYS = "月火水木金土日"


def targets(gid, kind=None):
    """出すカレンダーの一覧（省略すると両方＝2枚）"""
    main = cal_of(gid)
    if kind == "main":
        return [main]
    if kind == "other":
        return [OTHER_CAL]
    return [main] + ([OTHER_CAL] if OTHER_CAL not in main.split(",") else [])


def tag(cal_id, many):
    """2枚出すときに見分けるための印"""
    if not many:
        return ""
    return "　②" if cal_id == OTHER_CAL else "　①"


async def images(ids, make, name):
    """カレンダーごとに画像を作る（読めなかった分は飛ばす）"""
    files = []
    for i, cid in enumerate(ids, 1):
        try:
            img = await make(cid, tag(cid, len(ids) > 1))
            files.append(discord.File(img, f"{name}_{i}.png"))
        except Exception as e:
            print(f"カレンダー取得エラー {cid}: {e!r}")
    return files


async def events_around(cal_id, days):
    """指定した日付を含む月（と前後の月）の予定をまとめて読む"""
    months = sorted({(d.year, d.month) for d in days})
    y0, m0 = months[0]
    y1, m1 = months[-1]
    want = {(y0, m0 - 1) if m0 > 1 else (y0 - 1, 12), *months, (y1, m1 + 1) if m1 < 12 else (y1 + 1, 1)}
    evs, errors = [], 0
    ids = [c for c in cal_id.split(",") if c]
    for cid in ids:
        for yy, mm in sorted(want):
            try:
                evs += await C.fetch_month(cid, yy, mm)
            except Exception as e:
                print(f"カレンダー取得エラー {cid} {yy}/{mm}: {e!r}")
                errors += 1
    if errors and not evs:
        raise RuntimeError("どのカレンダーも読み込めませんでした")
    seen, uniq = set(), []
    for e in evs:
        k = (e["title"], e["start"], e["end"])
        if k not in seen:
            seen.add(k)
            uniq.append(e)
    return uniq


async def month_image(cal_id, y, m, today, mark=""):
    evs = await events_around(cal_id, [dt.date(y, m, 1)])
    return await asyncio.to_thread(C.render_month, evs, y, m, f"イベントカレンダー　{y}年{m}月{mark}", today)


async def week_image(cal_id, monday, today, mark=""):
    days = [monday + dt.timedelta(days=i) for i in range(7)]
    evs = await events_around(cal_id, days)
    sunday = days[-1]
    which = "今週" if monday <= today <= sunday else "来週" if monday > today else "先週"
    title = f"{which}の予定　{monday.month}/{monday.day}（月）〜{sunday.month}/{sunday.day}（日）{mark}"
    return await asyncio.to_thread(C.render_week, evs, monday, title, today)


_client = None


@tasks.loop(minutes=1)
async def weekly_post():
    """毎週の自動投稿（設定した曜日・時刻に1回だけ）"""
    now = dt.datetime.now(C.JST)
    for gid, raw in db.guilds_with("cal_weekly"):
        try:
            cfg = json.loads(raw)
        except Exception:
            continue
        if not cfg.get("on") or now.weekday() != cfg["weekday"] or now.hour != cfg["hour"]:
            continue
        stamp = now.strftime("%Y-%m-%d")
        if cfg.get("last") == stamp:
            continue
        cfg["last"] = stamp
        db.set_setting(gid, "cal_weekly", json.dumps(cfg))
        try:
            ch = _client.get_channel(cfg["channel"]) or await _client.fetch_channel(cfg["channel"])
            today = now.date()
            monday = today - dt.timedelta(days=today.weekday())
            if today.weekday() >= 5:  # 土日に投稿するときは来週の予定
                monday += dt.timedelta(weeks=1)
            ids = targets(gid)
            files = await images(ids, lambda c, mk: week_image(c, monday, today, mk), "week")
            if files:
                await ch.send(content="📅 **" + ("来週" if monday > today else "今週") + "の予定**", files=files,
                              view=link_view(",".join(ids)))
        except Exception as e:
            print(f"週間カレンダー自動投稿に失敗 guild={gid}: {e!r}")


    # 毎月1日の自動投稿
    for gid, raw in db.guilds_with("cal_monthly"):
        try:
            cfg = json.loads(raw)
        except Exception:
            continue
        if not cfg.get("on") or now.day != 1 or now.hour != cfg["hour"]:
            continue
        stamp = now.strftime("%Y-%m")
        if cfg.get("last") == stamp:
            continue
        cfg["last"] = stamp
        db.set_setting(gid, "cal_monthly", json.dumps(cfg))
        try:
            ch = _client.get_channel(cfg["channel"]) or await _client.fetch_channel(cfg["channel"])
            ids = targets(gid)
            files = await images(ids, lambda c, mk: month_image(c, now.year, now.month, now.date(), mk), "month")
            if files:
                await ch.send(content=f"🗓️ **{now.month}月のスケジュール**", files=files,
                              view=link_view(",".join(ids)))
        except Exception as e:
            print(f"月間カレンダー自動投稿に失敗 guild={gid}: {e!r}")


@weekly_post.before_loop
async def _wait():
    await _client.wait_until_ready()


def start(client):
    """ボット起動時に呼ぶ"""
    global _client
    _client = client
    if not weekly_post.is_running():
        weekly_post.start()


def setup(tree: app_commands.CommandTree, admin):
    @tree.command(name="カレンダー", description="ホワサバのイベントカレンダーを表示")
    @app_commands.choices(月=[app_commands.Choice(name="今月", value=0), app_commands.Choice(name="来月", value=1)])
    @app_commands.describe(月="今月か来月", 種類="どのカレンダーを出すか（省略すると両方の2枚）", 全員に見せる="はい＝チャンネルのみんなに見える形で出す（省略すると自分だけに表示）")
    @app_commands.choices(種類=[app_commands.Choice(name="両方（2枚）", value="both"),
                               app_commands.Choice(name="①だけ（a_4297）", value="main"),
                               app_commands.Choice(name="②だけ（b_4297）", value="other")])
    @app_commands.guild_only()
    async def cmd_calendar(interaction: discord.Interaction, 月: app_commands.Choice[int] | None = None,
                           種類: app_commands.Choice[str] | None = None, 全員に見せる: bool = False):
        await interaction.response.defer(thinking=True, ephemeral=not 全員に見せる)
        ids = targets(interaction.guild_id, 種類.value if 種類 else None)
        cal_id = ",".join(ids)
        today = dt.datetime.now(C.JST).date()
        y, m = today.year, today.month + (月.value if 月 else 0)
        if m > 12:
            y, m = y + 1, m - 12
        files = await images(ids, lambda c, mk: month_image(c, y, m, today, mk), f"calendar_{y}{m:02d}")
        if not files:
            return await interaction.followup.send(
                "TimeTreeからカレンダーを読み込めませんでした。少し待ってからもう一度試すか、下のボタンから直接見てください。",
                view=link_view(cal_id), ephemeral=not 全員に見せる)
        await interaction.followup.send(files=files,
                                        view=link_view(cal_id), ephemeral=not 全員に見せる)

    @tree.command(name="カレンダー設定", description="【運営】表示するTimeTree公開カレンダーのURLを変える")
    @app_commands.describe(url="TimeTree公開カレンダーのURL。複数ならスペースかカンマで区切る（例：https://timetr.ee/p/a_4297 https://timetr.ee/p/b_4297）")
    @app_commands.guild_only()
    @admin
    async def cmd_calendar_setting(interaction: discord.Interaction, url: str):
        import re
        ids = [C.calendar_id(u) for u in re.split(r"[\s,、]+", url.strip()) if u]
        ids = [i for i in ids if i]
        if not ids:
            return await interaction.response.send_message("URLが読み取れませんでした", ephemeral=True)
        db.set_setting(interaction.guild_id, "timetree", ",".join(dict.fromkeys(ids)))
        await interaction.response.send_message(f"✅ カレンダーを {'、'.join(dict.fromkeys(ids))} に設定しました（予定は1枚にまとめて表示）",
                                                ephemeral=True)

    @tree.command(name="今週の予定", description="今週（または来週）のイベント予定を表示")
    @app_commands.choices(週=[app_commands.Choice(name="今週", value=0), app_commands.Choice(name="来週", value=1)])
    @app_commands.describe(週="今週か来週", 種類="どのカレンダーを出すか（省略すると両方の2枚）", 全員に見せる="はい＝チャンネルのみんなに見える形で出す（省略すると自分だけに表示）")
    @app_commands.choices(種類=[app_commands.Choice(name="両方（2枚）", value="both"),
                               app_commands.Choice(name="①だけ（a_4297）", value="main"),
                               app_commands.Choice(name="②だけ（b_4297）", value="other")])
    @app_commands.guild_only()
    async def cmd_week(interaction: discord.Interaction, 週: app_commands.Choice[int] | None = None,
                       種類: app_commands.Choice[str] | None = None, 全員に見せる: bool = False):
        await interaction.response.defer(thinking=True, ephemeral=not 全員に見せる)
        ids = targets(interaction.guild_id, 種類.value if 種類 else None)
        cal_id = ",".join(ids)
        today = dt.datetime.now(C.JST).date()
        monday = today - dt.timedelta(days=today.weekday()) + dt.timedelta(weeks=週.value if 週 else 0)
        files = await images(ids, lambda c, mk: week_image(c, monday, today, mk), "week")
        if not files:
            return await interaction.followup.send("TimeTreeからカレンダーを読み込めませんでした。下のボタンから直接見てください。",
                                                   view=link_view(cal_id), ephemeral=not 全員に見せる)
        await interaction.followup.send(files=files, view=link_view(cal_id),
                                        ephemeral=not 全員に見せる)

    @tree.command(name="週間カレンダー自動投稿", description="【運営】毎週、その週の予定をこのチャンネルに自動投稿")
    @app_commands.choices(曜日=[app_commands.Choice(name=f"{w}曜日", value=i) for i, w in enumerate(WEEKDAYS)])
    @app_commands.describe(曜日="投稿する曜日（省略すると月曜日）", 時刻="投稿する時刻（0〜23時、省略すると9時）")
    @app_commands.guild_only()
    @admin
    async def cmd_weekly(interaction: discord.Interaction, 曜日: app_commands.Choice[int] | None = None,
                         時刻: app_commands.Range[int, 0, 23] = 9):
        wd = 曜日.value if 曜日 else 0
        cfg = {"on": True, "channel": interaction.channel_id, "weekday": wd, "hour": 時刻, "last": ""}
        db.set_setting(interaction.guild_id, "cal_weekly", json.dumps(cfg))
        await interaction.response.send_message(
            f"✅ 毎週{WEEKDAYS[wd]}曜日の{時刻}時に、このチャンネルへ" + ("来週" if wd >= 5 else "今週") +
            "の予定を自動投稿します\n止めるときは /週間カレンダー停止",
            ephemeral=True)

    @tree.command(name="週間カレンダー停止", description="【運営】週間カレンダーの自動投稿を止める")
    @app_commands.guild_only()
    @admin
    async def cmd_weekly_stop(interaction: discord.Interaction):
        raw = db.get_setting(interaction.guild_id, "cal_weekly", "")
        if raw:
            cfg = json.loads(raw)
            cfg["on"] = False
            db.set_setting(interaction.guild_id, "cal_weekly", json.dumps(cfg))
        await interaction.response.send_message("⏹️ 週間カレンダーの自動投稿を止めました", ephemeral=True)

    @tree.command(name="月間カレンダー自動投稿", description="【運営】毎月1日に、その月のカレンダーをこのチャンネルに自動投稿")
    @app_commands.describe(時刻="投稿する時刻（0〜23時、省略すると9時）")
    @app_commands.guild_only()
    @admin
    async def cmd_monthly(interaction: discord.Interaction, 時刻: app_commands.Range[int, 0, 23] = 9):
        cfg = {"on": True, "channel": interaction.channel_id, "hour": 時刻, "last": ""}
        db.set_setting(interaction.guild_id, "cal_monthly", json.dumps(cfg))
        await interaction.response.send_message(
            f"✅ 毎月1日の{時刻}時に、このチャンネルへその月のカレンダーを自動投稿します\n止めるときは /月間カレンダー停止",
            ephemeral=True)

    @tree.command(name="月間カレンダー停止", description="【運営】毎月のカレンダー自動投稿を止める")
    @app_commands.guild_only()
    @admin
    async def cmd_monthly_stop(interaction: discord.Interaction):
        raw = db.get_setting(interaction.guild_id, "cal_monthly", "")
        if raw:
            cfg = json.loads(raw)
            cfg["on"] = False
            db.set_setting(interaction.guild_id, "cal_monthly", json.dumps(cfg))
        await interaction.response.send_message("⏹️ 毎月のカレンダー自動投稿を止めました", ephemeral=True)
