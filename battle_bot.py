"""兵器工場・峡谷の振り分け（Discord側）。bot.py から setup() で登録する。

メンバー：パネルのボタン → 軍団を選ぶ → 数値（部隊戦力・総力など）を入力
運営　　：/振り分け で軍団ごとの振り分け画像を投稿
          /場所追加 /場所削除 /場所一覧 で施設・人数・優先度を変更
"""
import asyncio
import json
import unicodedata

import discord
from discord import app_commands

import db
import battle_logic as B

EVENT_CHOICES = [app_commands.Choice(name=v, value=k) for k, v in B.EVENTS.items()]


# ---------- 設定の読み書き（サーバーごと） ----------
def cfg(gid, ev):
    raw = db.get_setting(gid, f"battle:{ev}", "")
    base = json.loads(json.dumps(B.DEFAULTS[ev]))  # 初期値のコピー
    if raw:
        base.update(json.loads(raw))
    return base


def save_cfg(gid, ev, c):
    db.set_setting(gid, f"battle:{ev}", json.dumps(c, ensure_ascii=False))


def parse_value(s):
    s = unicodedata.normalize("NFKC", s).replace(",", "").replace(" ", "").strip()
    return int(s) if s.isdigit() else None


# ---------- メンバーの入力 ----------
class ValueModal(discord.ui.Modal):
    def __init__(self, ev, gid, legion, current):
        c = cfg(gid, ev)
        super().__init__(title=f"{B.EVENTS[ev]}　{legion}")
        self.ev, self.gid, self.legion = ev, gid, legion
        self.value = discord.ui.TextInput(label=f"{c['metric']}（名前の横の数値）", placeholder="例：7411",
                                          default=str(current) if current else None, max_length=15)
        self.add_item(self.value)

    async def on_submit(self, interaction: discord.Interaction):
        v = parse_value(self.value.value)
        if v is None:
            return await interaction.response.send_message("数字だけで入れてください（例：7411）", ephemeral=True)
        db.save_entry(self.gid, self.ev, interaction.user.id,
                      {"name": interaction.user.display_name, "legion": self.legion, "value": v})
        c = cfg(self.gid, self.ev)
        await interaction.response.send_message(
            f"✅ {B.EVENTS[self.ev]}：{self.legion}／{c['metric']} {v:,} で受け付けました（もう一度押せば直せます）",
            ephemeral=True)


class LegionView(discord.ui.View):
    def __init__(self, ev, gid, entry):
        super().__init__(timeout=600)
        self.ev, self.gid = ev, gid
        self.entry = entry or {}
        for lg in B.LEGIONS:
            btn = discord.ui.Button(label=lg, style=discord.ButtonStyle.primary)
            btn.callback = self.make_cb(lg)
            self.add_item(btn)
        if entry:
            cancel = discord.ui.Button(label="今回は参加しない（取り消す）", style=discord.ButtonStyle.danger)
            cancel.callback = self.cancel
            self.add_item(cancel)

    def make_cb(self, lg):
        async def cb(interaction: discord.Interaction):
            await interaction.response.send_modal(ValueModal(self.ev, self.gid, lg, self.entry.get("value")))
        return cb

    async def cancel(self, interaction: discord.Interaction):
        db.delete_entry(self.gid, self.ev, interaction.user.id)
        await interaction.response.edit_message(content="🗑️ 取り消しました", view=None)


async def start_entry(interaction: discord.Interaction, ev: str):
    gid = interaction.guild_id
    entry = db.get_entry(gid, ev, interaction.user.id)
    now = f"\n今の登録：{entry['legion']}／{entry['value']:,}" if entry else ""
    await interaction.response.send_message(
        f"**{B.EVENTS[ev]}**　参加する軍団を選んでください{now}", view=LegionView(ev, gid, entry), ephemeral=True)


class BattlePanel(discord.ui.View):
    """チャンネルに置く常設ボタン（イベントごと）"""

    def __init__(self, ev):
        super().__init__(timeout=None)
        btn = discord.ui.Button(label=f"{B.EVENTS[ev]}に登録する／直す", emoji="⚔️",
                                style=discord.ButtonStyle.success, custom_id=f"wos:battle:{ev}")

        async def cb(interaction: discord.Interaction):
            await start_entry(interaction, ev)

        btn.callback = cb
        self.add_item(btn)


# ---------- 運営コマンド ----------
def add_views(bot: discord.Client):
    """ボット起動時に呼ぶ（再起動後も登録ボタンが効くように）"""
    for ev in B.EVENTS:
        bot.add_view(BattlePanel(ev))


def setup(tree: app_commands.CommandTree, admin):

    async def place_autocomplete(interaction: discord.Interaction, current: str):
        ev = getattr(interaction.namespace, "イベント", None) or "foundry"
        names = [p["name"] for p in cfg(interaction.guild_id, ev)["places"]]
        return [app_commands.Choice(name=n, value=n) for n in names if current in n][:25]

    @tree.command(name="振り分けパネル", description="【運営】兵器工場・峡谷の登録ボタンをこのチャンネルに置く")
    @app_commands.choices(イベント=EVENT_CHOICES)
    @app_commands.guild_only()
    @admin
    async def cmd_panel(interaction: discord.Interaction, イベント: app_commands.Choice[str]):
        ev = イベント.value
        c = cfg(interaction.guild_id, ev)
        embed = discord.Embed(
            title=f"⚔️ {B.EVENTS[ev]} 振り分け登録",
            description=(f"下のボタンから **参加する軍団** と **{c['metric']}**（名前の横の数値）を登録してください。\n"
                         "名前の入力は不要です。数値が変わったら、もう一度押せば上書きされます。\n"
                         "参加しなくなったときは、ボタンから取り消せます。"),
            color=0xE67E22)
        await interaction.channel.send(embed=embed, view=BattlePanel(ev))
        await interaction.response.send_message("設置しました", ephemeral=True)

    @tree.command(name="振り分け", description="【運営】軍団ごとの振り分け画像を投稿")
    @app_commands.choices(イベント=EVENT_CHOICES)
    @app_commands.guild_only()
    @admin
    async def cmd_assign(interaction: discord.Interaction, イベント: app_commands.Choice[str]):
        ev, gid = イベント.value, interaction.guild_id
        c = cfg(gid, ev)
        entries = db.all_entries(gid, ev)
        if not entries:
            return await interaction.response.send_message("まだ登録がありません", ephemeral=True)
        if not c["places"]:
            return await interaction.response.send_message("場所が登録されていません。先に /場所追加 で登録してください", ephemeral=True)
        await interaction.response.defer(thinking=True)

        def build():
            files = []
            for i, lg in enumerate(B.LEGIONS, 1):
                ms = [e for e in entries if e["legion"] == lg]
                if ms:
                    pos = B.MAPS.get(ev, {}).get("pos")
                    img = B.render_legion(B.EVENTS[ev], lg, c["metric"], ms, c["places"], c["reserve"], c["target"], pos=pos)
                    files.append(discord.File(img, f"{ev}_legion{i}_list.png"))
                    if ev in B.MAPS:
                        files.append(discord.File(B.render_map(ev, B.EVENTS[ev], lg, c["metric"], ms, c["places"], c["reserve"]),
                                                  f"{ev}_legion{i}_map.png"))
            return files

        files = await asyncio.to_thread(build)
        counts = "、".join(f"{lg} {sum(e['legion'] == lg for e in entries)}人" for lg in B.LEGIONS)
        await interaction.followup.send(content=f"⚔️ **{B.EVENTS[ev]} 振り分け**（{counts}）", files=files)

    @tree.command(name="場所追加", description="【運営】施設・場所を追加（同じ名前なら上書き）")
    @app_commands.choices(イベント=EVENT_CHOICES)
    @app_commands.describe(名前="施設・場所の名前", 人数="入れる人数",
                           優先="取りに行く順。小さいほど先に取りに行き、人が足りないと大きい数字から空ける",
                           強さ順="強い人を入れる順。小さいほど強い人（省略すると優先と同じ。同じ数字は交互に入れる）",
                           ポイント="占領ポイント（省略可）", メモ="画像に出す一言（例：＋5軍・6軍も採取へ）")
    @app_commands.guild_only()
    @admin
    async def cmd_place_add(interaction: discord.Interaction, イベント: app_commands.Choice[str], 名前: str,
                            人数: app_commands.Range[int, 0, 30], 優先: app_commands.Range[int, 1, 99],
                            強さ順: app_commands.Range[int, 1, 99] | None = None,
                            ポイント: app_commands.Range[int, 0, 10_000_000] | None = None, メモ: str | None = None):
        ev, gid = イベント.value, interaction.guild_id
        c = cfg(gid, ev)
        name = 名前.strip()
        idx = next((i for i, p in enumerate(c["places"]) if p["name"] == name), None)
        old = c["places"][idx] if idx is not None else {}
        new = {"name": name, "count": 人数, "prio": 優先,
               "strength": 強さ順 if 強さ順 is not None else old.get("strength", 優先),
               "points": ポイント if ポイント is not None else old.get("points", 0)}
        note = メモ if メモ is not None else old.get("note")
        if note:
            new["note"] = note
        if idx is None:
            c["places"].append(new)
            msg = "追加しました"
        else:
            c["places"][idx] = new
            msg = "上書きしました"
        save_cfg(gid, ev, c)
        await interaction.response.send_message(f"✅ {name} を{msg}\n\n{places_text(c)}", ephemeral=True)

    @tree.command(name="場所削除", description="【運営】施設・場所を削除")
    @app_commands.choices(イベント=EVENT_CHOICES)
    @app_commands.autocomplete(名前=place_autocomplete)
    @app_commands.guild_only()
    @admin
    async def cmd_place_del(interaction: discord.Interaction, イベント: app_commands.Choice[str], 名前: str):
        ev, gid = イベント.value, interaction.guild_id
        c = cfg(gid, ev)
        before = len(c["places"])
        c["places"] = [p for p in c["places"] if p["name"] != 名前]
        if len(c["places"]) == before:
            return await interaction.response.send_message(f"{名前} は見つかりません", ephemeral=True)
        save_cfg(gid, ev, c)
        await interaction.response.send_message(f"🗑️ {名前} を削除しました\n\n{places_text(c)}", ephemeral=True)

    @tree.command(name="場所一覧", description="【運営】施設・人数・優先度の一覧と設定を確認")
    @app_commands.choices(イベント=EVENT_CHOICES)
    @app_commands.guild_only()
    @admin
    async def cmd_place_list(interaction: discord.Interaction, イベント: app_commands.Choice[str]):
        c = cfg(interaction.guild_id, イベント.value)
        await interaction.response.send_message(f"**{B.EVENTS[イベント.value]}**\n{places_text(c)}", ephemeral=True)

    @tree.command(name="振り分け設定", description="【運営】数値の名前・目標ポイント・残りの最低人数などを変更")
    @app_commands.choices(イベント=EVENT_CHOICES)
    @app_commands.describe(数値名="例：部隊戦力、総力", 目標="目標ポイント（0で表示しない）",
                           最低人数="どこにも入れず残しておく最低人数（玉拾いなど）", 残りの名前="例：玉拾い")
    @app_commands.guild_only()
    @admin
    async def cmd_battle_setting(interaction: discord.Interaction, イベント: app_commands.Choice[str],
                                 数値名: str | None = None, 目標: app_commands.Range[int, 0, 100_000_000] | None = None,
                                 最低人数: app_commands.Range[int, 0, 30] | None = None, 残りの名前: str | None = None):
        ev, gid = イベント.value, interaction.guild_id
        c = cfg(gid, ev)
        if 数値名:
            c["metric"] = 数値名.strip()
        if 目標 is not None:
            c["target"] = 目標
        if 最低人数 is not None:
            c["reserve"]["min"] = 最低人数
        if 残りの名前:
            c["reserve"]["name"] = 残りの名前.strip()
        save_cfg(gid, ev, c)
        await interaction.response.send_message(f"✅ 設定しました\n\n{places_text(c)}", ephemeral=True)

    @tree.command(name="振り分けリセット", description="【運営】登録された数値を全部消す（次回用）")
    @app_commands.choices(イベント=EVENT_CHOICES)
    @app_commands.guild_only()
    @admin
    async def cmd_battle_reset(interaction: discord.Interaction, イベント: app_commands.Choice[str]):
        db.clear_entries(interaction.guild_id, イベント.value)
        await interaction.response.send_message(f"🗑️ {B.EVENTS[イベント.value]} の登録を全部消しました", ephemeral=True)

    @tree.command(name="振り分け登録状況", description="【運営】誰が登録したかを確認")
    @app_commands.choices(イベント=EVENT_CHOICES)
    @app_commands.guild_only()
    @admin
    async def cmd_battle_status(interaction: discord.Interaction, イベント: app_commands.Choice[str]):
        es = db.all_entries(interaction.guild_id, イベント.value)
        if not es:
            return await interaction.response.send_message("まだ登録がありません", ephemeral=True)
        lines = []
        for lg in B.LEGIONS:
            ms = sorted((e for e in es if e["legion"] == lg), key=lambda e: -e["value"])
            lines.append(f"**{lg}**（{len(ms)}人）\n" + ("\n".join(f"{e['name']}　{e['value']:,}" for e in ms) or "なし"))
        await interaction.response.send_message("\n\n".join(lines)[:1990], ephemeral=True)


def places_text(c):
    lines = [f"数値：{c['metric']}　目標：{c['target']:,}P　残り「{c['reserve']['name']}」最低 {c['reserve']['min']}人",
             "（優先＝取りに行く順、強さ順＝強い人を入れる順。どちらも小さいほど先）"]
    for p in sorted(c["places"], key=lambda p: p["prio"]):
        pt = f"　{p['points']:,}P" if p.get("points") else ""
        lines.append(f"優先{p['prio']}・強さ順{p.get('strength', p['prio'])}　{p['name']}　{p['count']}人{pt}")
    if not c["places"]:
        lines.append("（場所がまだありません。/場所追加 で登録してください）")
    return "\n".join(lines)
