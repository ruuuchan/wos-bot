"""設定ファイル。サーバーに合わせてここを書き換えてください。"""
import os

# 所属同盟の初期値（Discordの /同盟追加 /同盟削除 で変更できます）
ALLIANCES = ["RAV", "GaL", "WSS", "RNE", "YEK", "ARK", "MOF", "lgo", "その他の同盟"]

# 兵舎レベルの選択肢（最大25個まで）
# 低い順に並べる（FC5からはT11版も）
BARRACKS_LEVELS = [f"FC{i}" for i in range(1, 5)] + [x for i in range(5, 11) for x in (f"FC{i}", f"FC{i}T11")]
# これ以上のレベルは編成表で黄色く強調する
HIGHLIGHT_FROM = "FC7"

# 打ち間違い防止の上限（これを超える数字は受け付けない）
MAX_FC = 100_000        # 火晶
MAX_SP_DAYS = 5_000     # 加速の合計日数

# 遠征第1スキルの英雄
HEROES = ["ジェシー", "ソユン", "リンセツ", "ルムボーガン", "パトリック", "ジンマン", "フレンダー", "ローガン"]

# 参加可能時間の選択肢
JOIN_OPTIONS = ["21:00までにログインしてスタートダッシュできる", "途中参加"]

# イベントの初期設定（/イベント設定 で変更できます）
DEFAULT_START = "2026-10-05 09:00"   # 対象期間の開始（日本時間）
DEFAULT_TITLE = "準備フェーズ 枠割り振り"
SLOT_MINUTES = 30                     # 1枠の長さ
HOURS = 24                            # 対象期間の長さ（時間）

# 実行環境
TOKEN = os.environ.get("DISCORD_TOKEN", "")
# コマンドを使えるようにするサーバーのID。複数ならカンマ区切り（例：111,222）
GUILD_IDS = [int(x) for x in os.environ.get("GUILD_ID", "").replace(" ", "").split(",") if x.isdigit()]
DB_PATH = os.environ.get("DB_PATH", "data.db")
FONT_REGULAR = os.environ.get("FONT_REGULAR", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
FONT_BOLD = os.environ.get("FONT_BOLD", "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc")
