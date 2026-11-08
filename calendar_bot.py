"""TimeTreeのイベントカレンダーをDiscordで見る。bot.py から setup() で登録する。

/カレンダー        … 今月（または来月）のカレンダー画像を出す（誰でも使える）
/カレンダー設定    … 【運営】読み込むTimeTree公開カレンダーのURLを変える
"""
import asyncio
import datetime as dt

import discord
from discord import app_commands

import db
import calendar_logic as C

DEFAULT_CAL = "a_4297"


def cal_of(gid):
    return db.get_setting(gid, "timetree", DEFAULT_CAL)


def link_view(cal_id):
    v = discord.ui.View()
    v.add_item(discord.ui.Button(label="TimeTreeで開く", url=f"https://timetreeapp.com/public_calendars/{cal_id}"))
    return v


def setup(tree: app_commands.CommandTree, admin):
    @tree.command(name="カレンダー", description="ホワサバのイベントカレンダーを表示")
    @app_commands.choices(月=[app_commands.Choice(name="今月", value=0), app_commands.Choice(name="来月", value=1)])
    @app_commands.describe(月="今月か来月", 全員に見せる="はい＝チャンネルのみんなに見える形で出す（省略すると自分だけに表示）")
    @app_commands.guild_only()
    async def cmd_calendar(interaction: discord.Interaction, 月: app_commands.Choice[int] | None = None,
                           全員に見せる: bool = False):
        await interaction.response.defer(thinking=True, ephemeral=not 全員に見せる)
        cal_id = cal_of(interaction.guild_id)
        today = dt.datetime.now(C.JST).date()
        y, m = today.year, today.month + (月.value if 月 else 0)
        if m > 12:
            y, m = y + 1, m - 12
        try:
            # 月をまたぐ予定も出すため、前後の月も読む
            evs = []
            for yy, mm in ((y, m - 1) if m > 1 else (y - 1, 12), (y, m), (y, m + 1) if m < 12 else (y + 1, 1)):
                evs += await C.fetch_month(cal_id, yy, mm)
        except Exception as e:
            print(f"カレンダー取得エラー: {e!r}")
            return await interaction.followup.send(
                "TimeTreeからカレンダーを読み込めませんでした。少し待ってからもう一度試すか、下のボタンから直接見てください。",
                view=link_view(cal_id), ephemeral=not 全員に見せる)
        seen, uniq = set(), []
        for e in evs:
            k = (e["title"], e["start"], e["end"])
            if k not in seen:
                seen.add(k)
                uniq.append(e)
        img = await asyncio.to_thread(C.render_month, uniq, y, m, f"イベントカレンダー　{y}年{m}月", today)
        await interaction.followup.send(file=discord.File(img, f"calendar_{y}{m:02d}.png"),
                                        view=link_view(cal_id), ephemeral=not 全員に見せる)

    @tree.command(name="カレンダー設定", description="【運営】表示するTimeTree公開カレンダーのURLを変える")
    @app_commands.describe(url="TimeTree公開カレンダーのURL（例：https://timetr.ee/p/a_4297）")
    @app_commands.guild_only()
    @admin
    async def cmd_calendar_setting(interaction: discord.Interaction, url: str):
        cid = C.calendar_id(url)
        if not cid:
            return await interaction.response.send_message("URLが読み取れませんでした", ephemeral=True)
        db.set_setting(interaction.guild_id, "timetree", cid)
        await interaction.response.send_message(f"✅ カレンダーを {cid} に設定しました", ephemeral=True)
