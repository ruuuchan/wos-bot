import json
import sqlite3
import datetime as dt

import config
import os

os.makedirs(os.path.dirname(os.path.abspath(config.DB_PATH)), exist_ok=True)
_conn = sqlite3.connect(config.DB_PATH)
_conn.execute("""CREATE TABLE IF NOT EXISTS answers(
    guild_id INTEGER, user_id INTEGER, data TEXT, updated_at TEXT,
    PRIMARY KEY(guild_id, user_id))""")
_conn.execute("""CREATE TABLE IF NOT EXISTS settings(
    guild_id INTEGER, key TEXT, value TEXT, PRIMARY KEY(guild_id, key))""")
_conn.commit()


def save_answer(guild_id: int, user_id: int, data: dict):
    _conn.execute(
        "INSERT OR REPLACE INTO answers VALUES(?,?,?,?)",
        (guild_id, user_id, json.dumps(data, ensure_ascii=False), dt.datetime.now().isoformat()),
    )
    _conn.commit()


def get_answer(guild_id: int, user_id: int):
    row = _conn.execute("SELECT data FROM answers WHERE guild_id=? AND user_id=?", (guild_id, user_id)).fetchone()
    return json.loads(row[0]) if row else None


def all_answers(guild_id: int):
    rows = _conn.execute("SELECT data FROM answers WHERE guild_id=?", (guild_id,)).fetchall()
    return [json.loads(r[0]) for r in rows]


def clear_answers(guild_id: int):
    _conn.execute("DELETE FROM answers WHERE guild_id=?", (guild_id,))
    _conn.commit()


def get_setting(guild_id: int, key: str, default: str):
    row = _conn.execute("SELECT value FROM settings WHERE guild_id=? AND key=?", (guild_id, key)).fetchone()
    return row[0] if row else default


def set_setting(guild_id: int, key: str, value: str):
    _conn.execute("INSERT OR REPLACE INTO settings VALUES(?,?,?)", (guild_id, key, value))
    _conn.commit()


def clear_numbers(guild_id: int):
    """火晶・加速・希望時間帯だけ消す（同盟・兵舎・英雄は次回の初期値として残す）"""
    for (uid, data) in _conn.execute("SELECT user_id, data FROM answers WHERE guild_id=?", (guild_id,)).fetchall():
        a = json.loads(data)
        for k in ("fc", "sp_hours", "hours"):
            a.pop(k, None)
        _conn.execute("UPDATE answers SET data=? WHERE guild_id=? AND user_id=?",
                      (json.dumps(a, ensure_ascii=False), guild_id, uid))
    _conn.commit()


def guilds_with(key: str):
    return _conn.execute("SELECT guild_id, value FROM settings WHERE key=?", (key,)).fetchall()
