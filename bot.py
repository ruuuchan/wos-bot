"""WOS 準備フェーズ申請ボット。

メンバー：パネルのボタン（または /申請）→ 選択式で入力 → 火晶・加速だけ文字入力
運営　　：/集計 で時間割りと兵舎・英雄一覧の画像2枚をまとめて投稿
"""
import asyncio
import json
import datetime as dt
import re
import unicodedata

import discord
from discord import app_commands
from discord.ext import tasks

import config
import db
import render
import battle_bot
from allocate import allocate

WEEK = "月火水木金土日"
JST = dt.timezone(dt.timedelta(hours=9))  # 日本は夏時間なし
FMT = "%Y-%m-%d %H:%M"


# ---------- 共通 ----------
def event(gid):
    start = dt.datetime.strptime(db.get_setting(gid, "start", config.DEFAULT_START), "%Y-%m-%d %H:%M")
    title = db.get_setting(gid, "title", config.DEFAULT_TITLE)
    return start, title


def heroes(gid):
    return json.loads(db.get_setting(gid, "heroes", json.dumps(config.HEROES, ensure_ascii=False)))


def set_heroes(gid, lst):
    db.set_setting(gid, "heroes", json.dumps(lst, ensure_ascii=False))


def alliances(gid):
    return json.loads(db.get_setting(gid, "alliances", json.dumps(config.ALLIANCES, ensure_ascii=False)))


def set_alliances(gid, lst):
    db.set_setting(gid, "alliances", json.dumps(lst, ensure_ascii=False))


def deadline(gid):
    s = db.get_setting(gid, "deadline", "")
    return dt.datetime.strptime(s, FMT) if s else None


def fmt_dt(t):
    return f"{t.month}/{t.day}({WEEK[t.weekday()]}) {t.hour}:{t.minute:02d}"


def parse_dt(s):
    try:
        return dt.datetime.strptime(unicodedata.normalize("NFKC", s).strip(), FMT)
    except ValueError:
        return None


def slot_options(start):
    """希望時間帯の選択肢（SLOT_MINUTES ごと）。値は枠の開始時刻"""
    step = dt.timedelta(minutes=config.SLOT_MINUTES)
    opts = []
    for k in range(config.HOURS * 60 // config.SLOT_MINUTES):
        t = start + step * k
        e = t + step
        opts.append((f"{t.month}/{t.day}({WEEK[t.weekday()]}) {t.hour}:{t.minute:02d}〜{e.hour}:{e.minute:02d}", t.isoformat()))
    return opts


def slot_groups(start):
    """Discordのプルダウンは25個までなので、選択肢を均等に分ける（24時間・30分枠なら24個×2）"""
    opts = slot_options(start)
    n = -(-len(opts) // 25)
    size = -(-len(opts) // n)
    return [opts[i:i + size] for i in range(0, len(opts), size)]


def parse_fc(s):
    s = unicodedata.normalize("NFKC", s).replace(",", "").strip()
    return int(s) if s.isdigit() else None


def parse_sp(s):
    """「52日13時間」「52日」「52」→ 時間に変換"""
    s = unicodedata.normalize("NFKC", s).replace(" ", "")
    d = re.search(r"(\d+)日", s)
    h = re.search(r"(\d+)時", s)
    if d or h:
        return int(d.group(1) if d else 0) * 24 + int(h.group(1) if h else 0)
    return int(s) * 24 if s.isdigit() else None


def hours_label(hours):
    """選んだ枠を、続いている所はまとめて「10/5(月) 12:00〜14:00」のように表示"""
    ts = sorted(dt.datetime.fromisoformat(h) for h in hours)
    if not ts:
        return "なし"
    step = dt.timedelta(minutes=config.SLOT_MINUTES)
    spans, s0, prev = [], ts[0], ts[0]
    for t in ts[1:] + [None]:
        if t is not None and t - prev == step:
            prev = t
            continue
        e = prev + step
        spans.append(f"{s0.month}/{s0.day}({WEEK[s0.weekday()]}) {s0.hour}:{s0.minute:02d}〜{e.hour}:{e.minute:02d}")
        if t is not None:
            s0 = prev = t
    return "、".join(spans)


def summary_text(a):
    hrs = hours_label(a.get("hours", []))
    heroes = "、".join(a["heroes"]) or "なし"
    b = a["barracks"]
    return (
        f"同盟：{a['ally']}　参加：{a['join']}\n"
        f"火晶：{a['fc']:,}　加速：{render.sp_label(a['sp_hours'])}\n"
        f"希望時間帯：{hrs}\n"
        f"兵舎：盾{b[0]}／槍{b[1]}／弓{b[2]}\n"
        f"英雄：{heroes}"
    )


def make_select(placeholder, options, chosen, on_pick, row, min_v=1, max_v=1):
    sel = discord.ui.Select(
        placeholder=placeholder, min_values=min_v, max_values=max_v, row=row,
        options=[discord.SelectOption(label=lab, value=val, default=val in chosen) for lab, val in options],
    )

    async def cb(interaction: discord.Interaction):
        on_pick(sel.values)
        await interaction.response.defer()

    sel.callback = cb
    return sel


# ---------- 入力ステップ ----------
STEP1_TEXT = "**申請 1/3**　同盟・参加時間・希望時間帯を選んでください\n希望時間帯は30分ごとです。《できるだけ多く》選んでください（前半・後半どちらも複数選択できます）"
STEP2_TEXT = (
    "**申請 2/3**　兵舎レベルと英雄を選んでください\n"
    "⚠️ 準備フェーズ中に強化予定がある場合は **強化後** のレベルで\n"
    "英雄は遠征第1スキル（右上）が Lv5 の英雄をすべて。いなければ空のままでOK"
)


class Step1(discord.ui.View):
    def __init__(self, draft, gid):
        super().__init__(timeout=900)
        self.draft, self.gid = draft, gid
        start, _ = event(gid)
        lst = lambda xs: [(x, x) for x in xs]
        self.add_item(make_select("所属同盟", lst(alliances(gid)[:25]), [draft.get("ally")],
                                  lambda v: draft.update(ally=v[0]), row=0))
        self.add_item(make_select("参加可能時間", lst(config.JOIN_OPTIONS), [draft.get("join")],
                                  lambda v: draft.update(join=v[0]), row=1))
        groups = slot_groups(start)
        for gi, opts in enumerate(groups):
            mine = {v for _, v in opts}
            first, last = opts[0][0].split(" ")[0] + " " + opts[0][0].split(" ")[1].split("〜")[0], opts[-1][0].split("〜")[1]
            def pick(v, mine=mine):
                draft["hours"] = sorted([h for h in draft.get("hours", []) if h not in mine] + list(v))
            self.add_item(make_select(f"希望時間帯 {first}〜{last}（複数選択可）", opts, draft.get("hours", []),
                                      pick, row=2 + gi, min_v=0, max_v=len(opts)))

    @discord.ui.button(label="次へ", style=discord.ButtonStyle.primary, row=4)
    async def next(self, interaction: discord.Interaction, _):
        missing = [n for k, n in [("ally", "所属同盟"), ("join", "参加可能時間"), ("hours", "希望時間帯")] if not self.draft.get(k)]
        if missing:
            return await interaction.response.send_message(f"「{'・'.join(missing)}」を選んでください", ephemeral=True)
        await interaction.response.edit_message(content=STEP2_TEXT, view=Step2(self.draft, self.gid))


class Step2(discord.ui.View):
    def __init__(self, draft, gid):
        super().__init__(timeout=900)
        self.draft, self.gid = draft, gid
        b = draft.setdefault("barracks", [None, None, None])
        lv = [(x, x) for x in config.BARRACKS_LEVELS]
        for i, name in enumerate(["盾兵舎", "槍兵舎", "弓兵舎"]):
            self.add_item(make_select(name, lv, [b[i]], lambda v, i=i: b.__setitem__(i, v[0]), row=i))
        hl = heroes(gid)[:25]
        draft["heroes"] = [h for h in draft.get("heroes", []) if h in hl]
        self.add_item(make_select("遠征第1スキルLv5の英雄（複数選択）", [(h, h) for h in hl],
                                  draft["heroes"], lambda v: draft.update(heroes=list(v)),
                                  row=3, min_v=0, max_v=len(hl)))

    @discord.ui.button(label="戻る", style=discord.ButtonStyle.secondary, row=4)
    async def back(self, interaction: discord.Interaction, _):
        await interaction.response.edit_message(content=STEP1_TEXT, view=Step1(self.draft, self.gid))

    @discord.ui.button(label="次へ：火晶・加速を入力", style=discord.ButtonStyle.primary, row=4)
    async def next(self, interaction: discord.Interaction, _):
        if None in self.draft["barracks"]:
            return await interaction.response.send_message("盾・槍・弓の兵舎レベルを選んでください", ephemeral=True)
        self.draft.setdefault("heroes", [])
        await interaction.response.send_modal(NumModal(self.draft, self.gid))


class NumModal(discord.ui.Modal):
    def __init__(self, draft, gid):
        super().__init__(title="申請 3/3　火晶・加速")
        self.draft, self.gid = draft, gid
        sp_default = render.sp_label(draft["sp_hours"]) if "sp_hours" in draft else None
        self.fc = discord.ui.TextInput(label="① 火晶の保有数", placeholder="例：2500",
                                       default=str(draft["fc"]) if "fc" in draft else None, max_length=10)
        self.sp = discord.ui.TextInput(label="② 一般加速＋建築加速の合計日数",
                                       placeholder="例：52日13時間（バッグ→右上のグラフ→加速で確認）",
                                       default=sp_default, max_length=20)
        self.add_item(self.fc)
        self.add_item(self.sp)

    async def on_submit(self, interaction: discord.Interaction):
        if await closed(interaction):
            return
        fc, sp = parse_fc(self.fc.value), parse_sp(self.sp.value)
        if fc is None or sp is None:
            return await interaction.response.send_message(
                "数字が読み取れませんでした。火晶は「2500」、加速は「52日13時間」の形で入れ直してください（もう一度「次へ」を押せます）",
                ephemeral=True)
        if fc > config.MAX_FC or sp > config.MAX_SP_DAYS * 24:
            return await interaction.response.send_message(
                f"数字が大きすぎます（火晶 {fc:,}／加速 {render.sp_label(sp)}）。打ち間違いがないか確認して、もう一度「次へ」から入れ直してください\n"
                f"上限：火晶 {config.MAX_FC:,}、加速 {config.MAX_SP_DAYS:,}日",
                ephemeral=True)
        self.draft.update(fc=fc, sp_hours=sp, name=interaction.user.display_name, user_id=interaction.user.id)
        db.save_answer(self.gid, interaction.user.id, self.draft)
        await interaction.response.send_message(
            f"✅ 受け付けました。変更するときはもう一度申請すれば上書きされます。\n\n{summary_text(self.draft)}",
            ephemeral=True)


async def closed(interaction: discord.Interaction):
    """締め切り後は申請を受け付けない（サーバー管理権限のある運営は代理入力のため通す）"""
    dl = deadline(interaction.guild_id)
    if not dl or dt.datetime.now(JST).replace(tzinfo=None) < dl:
        return False
    perms = getattr(interaction.user, "guild_permissions", None)
    if perms and perms.manage_guild:
        return False
    await interaction.response.send_message(
        f"⏰ 申請は締め切りました（{fmt_dt(dl)}）。変更したいときは運営に連絡してください", ephemeral=True)
    return True


async def start_apply(interaction: discord.Interaction):
    if await closed(interaction):
        return
    gid = interaction.guild_id
    start, _ = event(gid)
    draft = db.get_answer(gid, interaction.user.id) or {}
    valid = {v for _, v in slot_options(start)}
    draft["hours"] = [h for h in draft.get("hours", []) if h in valid]  # 期間が変わっていたら希望時間はリセット
    await interaction.response.send_message(STEP1_TEXT, view=Step1(draft, gid), ephemeral=True)


class Panel(discord.ui.View):
    """チャンネルに置いておく常設ボタン（ボット再起動後も有効）"""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="申請する／回答を直す", style=discord.ButtonStyle.success, emoji="📝", custom_id="wos:apply")
    async def apply(self, interaction: discord.Interaction, _):
        await start_apply(interaction)


class Confirm(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.button(label="火晶・加速・希望時間だけ消す", style=discord.ButtonStyle.primary)
    async def numbers(self, interaction: discord.Interaction, _):
        db.clear_numbers(interaction.guild_id)
        db.set_setting(interaction.guild_id, "posted", "0")
        await interaction.response.edit_message(
            content="🔄 火晶・加速・希望時間帯をリセットしました。同盟・兵舎・英雄は残っているので、次の申請では最初から入っています",
            view=None)

    @discord.ui.button(label="全部消す", style=discord.ButtonStyle.danger)
    async def everything(self, interaction: discord.Interaction, _):
        db.clear_answers(interaction.guild_id)
        db.set_setting(interaction.guild_id, "posted", "0")
        await interaction.response.edit_message(content="🗑️ 回答をすべて削除しました", view=None)


async def build_summary(gid):
    """集計画像2枚と本文を作る。回答がなければ None"""
    apps = [a for a in db.all_answers(gid) if "fc" in a]
    if not apps:
        return None
    start, title = event(gid)
    hl = heroes(gid)
    al = alliances(gid)

    def build():
        slots, left = allocate(apps, start, config.SLOT_MINUTES, config.HOURS)
        return (render.render_schedule(apps, slots, left, start, config.SLOT_MINUTES, config.HOURS, title, al),
                render.render_roster(apps, hl, al))

    img1, img2 = await asyncio.to_thread(build)
    files = [discord.File(img1, "1_jikanwari.png"), discord.File(img2, "2_hensei.png")]
    return f"📋 **{title}**（回答 {len(apps)}名）", files


# ---------- ボット本体 ----------
class Bot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        self.add_view(Panel())
        battle_bot.add_views(self)
        auto_post.start()
        if config.GUILD_IDS:
            for gid in config.GUILD_IDS:
                g = discord.Object(id=gid)
                self.tree.copy_global_to(guild=g)
                try:
                    await self.tree.sync(guild=g)
                except discord.HTTPException as e:  # まだ招待されていないサーバーなど
                    print(f"コマンド登録に失敗 guild={gid}: {e!r}")
        else:
            await self.tree.sync()

    async def on_guild_join(self, guild: discord.Guild):
        """許可済みのサーバーに後から招待されたら、すぐコマンドを使えるようにする"""
        if guild.id in config.GUILD_IDS:
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)


bot = Bot()
tree = bot.tree
admin = app_commands.default_permissions(manage_guild=True)
battle_bot.setup(tree, admin)  # 兵器工場・峡谷の振り分け


@tree.command(name="申請", description="準備フェーズの申請（何度でも直せます）")
@app_commands.guild_only()
async def cmd_apply(interaction: discord.Interaction):
    await start_apply(interaction)


@tree.command(name="自分の回答", description="自分の申請内容を確認")
@app_commands.guild_only()
async def cmd_mine(interaction: discord.Interaction):
    a = db.get_answer(interaction.guild_id, interaction.user.id)
    if a and "fc" in a:
        msg = summary_text(a)
    elif a:
        msg = "今回の申請はまだです（同盟・兵舎・英雄は前回の内容が残っています）"
    else:
        msg = "まだ申請していません"
    await interaction.response.send_message(msg, ephemeral=True)


@tree.command(name="申請パネル", description="【運営】このチャンネルに申請ボタンを設置")
@app_commands.guild_only()
@admin
async def cmd_panel(interaction: discord.Interaction):
    gid = interaction.guild_id
    start, title = event(gid)
    db.set_setting(gid, "channel", str(interaction.channel_id))
    dl = deadline(gid)
    dl_text = f"締め切り：{fmt_dt(dl)}（時間になったら結果を自動で投稿します）\n" if dl else ""
    embed = discord.Embed(
        title=f"📝 {title}",
        description=(f"対象：{render.period_label(start, config.HOURS)}\n{dl_text}\n"
                     "下のボタンから申請してください。名前の入力は不要です。\n"
                     "⚠️ 強化予定がある人は **強化後** のレベルで回答してください\n"
                     "内容を変えたいときは、もう一度押せば上書きされます"),
        color=0x4A90E2)
    await interaction.channel.send(embed=embed, view=Panel())
    await interaction.response.send_message("設置しました", ephemeral=True)


@tree.command(name="集計", description="【運営】時間割りと兵舎・英雄一覧の画像を投稿")
@app_commands.guild_only()
@admin
async def cmd_summary(interaction: discord.Interaction):
    if not [a for a in db.all_answers(interaction.guild_id) if "fc" in a]:
        return await interaction.response.send_message("まだ回答がありません", ephemeral=True)
    await interaction.response.defer(thinking=True)
    content, files = await build_summary(interaction.guild_id)
    await interaction.followup.send(content=content, files=files)


@tree.command(name="イベント設定", description="【運営】対象期間・締め切り・タイトルを設定")
@app_commands.describe(開始="対象期間の開始 例：2026-11-02 09:00（日本時間）",
                       締め切り="この時刻に結果を自動投稿 例：2026-11-01 21:00（省略可）",
                       タイトル="画像の見出し（省略可）")
@app_commands.guild_only()
@admin
async def cmd_setting(interaction: discord.Interaction, 開始: str, 締め切り: str | None = None, タイトル: str | None = None):
    gid = interaction.guild_id
    t = parse_dt(開始)
    d = parse_dt(締め切り) if 締め切り else None
    if not t or (締め切り and not d):
        return await interaction.response.send_message("日時は「2026-11-02 09:00」の形で入れてください", ephemeral=True)
    db.set_setting(gid, "start", t.strftime(FMT))
    if d:
        db.set_setting(gid, "deadline", d.strftime(FMT))
        db.set_setting(gid, "posted", "0")
        db.set_setting(gid, "channel", str(interaction.channel_id))
    if タイトル:
        db.set_setting(gid, "title", タイトル)
    _, title = event(gid)
    dl = deadline(gid)
    msg = f"設定しました：{title}\n対象：{render.period_label(t, config.HOURS)}"
    if dl:
        msg += f"\n締め切り：{fmt_dt(dl)} → このチャンネルに自動投稿します"
    await interaction.response.send_message(msg, ephemeral=True)


@tree.command(name="同盟追加", description="【運営】同盟の選択肢を追加")
@app_commands.describe(名前="追加する同盟名", 位置="何番目に入れるか（省略すると最後）")
@app_commands.guild_only()
@admin
async def cmd_ally_add(interaction: discord.Interaction, 名前: str, 位置: app_commands.Range[int, 1, 25] | None = None):
    gid, name = interaction.guild_id, 名前.strip()
    al = alliances(gid)
    if name in al:
        return await interaction.response.send_message(f"{name} はもう入っています", ephemeral=True)
    if len(al) >= 25:
        return await interaction.response.send_message("同盟は25個までです。先に /同盟削除 で減らしてください", ephemeral=True)
    al.insert((位置 - 1) if 位置 else len(al), name)
    set_alliances(gid, al)
    await interaction.response.send_message(f"✅ {name} を追加しました\n" + "、".join(al), ephemeral=True)


async def ally_autocomplete(interaction: discord.Interaction, current: str):
    return [app_commands.Choice(name=a, value=a) for a in alliances(interaction.guild_id) if current.lower() in a.lower()][:25]


@tree.command(name="同盟削除", description="【運営】同盟の選択肢を削除")
@app_commands.describe(名前="削除する同盟")
@app_commands.autocomplete(名前=ally_autocomplete)
@app_commands.guild_only()
@admin
async def cmd_ally_del(interaction: discord.Interaction, 名前: str):
    gid = interaction.guild_id
    al = alliances(gid)
    if 名前 not in al:
        return await interaction.response.send_message(f"{名前} は見つかりません", ephemeral=True)
    al.remove(名前)
    set_alliances(gid, al)
    await interaction.response.send_message(f"🗑️ {名前} を削除しました\n" + "、".join(al), ephemeral=True)


@tree.command(name="選択肢一覧", description="【運営】今の同盟・英雄の選択肢を確認")
@app_commands.guild_only()
@admin
async def cmd_options(interaction: discord.Interaction):
    gid = interaction.guild_id
    await interaction.response.send_message(
        f"**同盟**（{len(alliances(gid))}）\n" + "、".join(alliances(gid)) +
        f"\n\n**英雄**（{len(heroes(gid))}）\n" + "、".join(heroes(gid)), ephemeral=True)


@tree.command(name="英雄追加", description="【運営】英雄の選択肢を追加")
@app_commands.describe(名前="追加する英雄の名前")
@app_commands.guild_only()
@admin
async def cmd_hero_add(interaction: discord.Interaction, 名前: str):
    gid, name = interaction.guild_id, 名前.strip()
    hl = heroes(gid)
    if name in hl:
        return await interaction.response.send_message(f"{name} はもう入っています", ephemeral=True)
    if len(hl) >= 25:
        return await interaction.response.send_message("英雄は25人までです。先に /英雄削除 で減らしてください", ephemeral=True)
    set_heroes(gid, hl + [name])
    await interaction.response.send_message(f"✅ {name} を追加しました（全{len(hl) + 1}人）\n" + "、".join(hl + [name]), ephemeral=True)


async def hero_autocomplete(interaction: discord.Interaction, current: str):
    return [app_commands.Choice(name=h, value=h) for h in heroes(interaction.guild_id) if current in h][:25]


@tree.command(name="英雄削除", description="【運営】英雄の選択肢を削除")
@app_commands.describe(名前="削除する英雄")
@app_commands.autocomplete(名前=hero_autocomplete)
@app_commands.guild_only()
@admin
async def cmd_hero_del(interaction: discord.Interaction, 名前: str):
    gid = interaction.guild_id
    hl = heroes(gid)
    if 名前 not in hl:
        return await interaction.response.send_message(f"{名前} は見つかりません", ephemeral=True)
    hl.remove(名前)
    set_heroes(gid, hl)
    await interaction.response.send_message(f"🗑️ {名前} を削除しました\n" + "、".join(hl), ephemeral=True)


@tree.command(name="リセット", description="【運営】次のイベント用に回答をリセット")
@app_commands.guild_only()
@admin
async def cmd_reset(interaction: discord.Interaction):
    await interaction.response.send_message("どちらをリセットしますか？\n・**火晶・加速・希望時間だけ**：同盟・兵舎・英雄は残ります（おすすめ）\n・**全部**：全員の回答を完全に削除", view=Confirm(), ephemeral=True)


@tasks.loop(seconds=30)
async def auto_post():
    """締め切り時刻になったら、そのチャンネルに集計画像を1回だけ投稿"""
    now = dt.datetime.now(JST).replace(tzinfo=None)
    for gid, value in db.guilds_with("deadline"):
        if not value or db.get_setting(gid, "posted", "0") == "1":
            continue
        if dt.datetime.strptime(value, FMT) > now:
            continue
        db.set_setting(gid, "posted", "1")
        try:
            channel = bot.get_channel(int(db.get_setting(gid, "channel", "0"))) \
                or await bot.fetch_channel(int(db.get_setting(gid, "channel", "0")))
            res = await build_summary(gid)
            if res:
                content, files = res
                await channel.send(content="⏰ 締め切りました\n" + content, files=files)
            else:
                await channel.send("⏰ 締め切りました。回答はありませんでした")
        except Exception as e:  # 投稿できなくてもループは止めない
            print(f"auto_post failed guild={gid}: {e!r}")


@auto_post.before_loop
async def _wait_ready():
    await bot.wait_until_ready()


if __name__ == "__main__":
    if not config.TOKEN:
        raise SystemExit("環境変数 DISCORD_TOKEN が設定されていません")
    bot.run(config.TOKEN)
