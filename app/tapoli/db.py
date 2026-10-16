"""Esquema SQLite y utilidades. Lo comparten el analizador y el panel."""
import json
import os
import sqlite3
import time

from . import config

DB_PATH = os.path.join(config.DATA_DIR, "tapoli.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS seconds (
    ts INTEGER PRIMARY KEY,         -- epoch (s)
    motion REAL,                    -- fracción de la cuna con cambio (máx. del segundo)
    motion_out REAL,                -- ídem fuera de la cuna
    sound_db REAL,                  -- nivel RMS (dBFS)
    voice_db REAL,                  -- energía en banda de voz (dB)
    voice INTEGER,                  -- 1 = voz/llanto detectado
    flags INTEGER DEFAULT 0         -- 1 = cambio global de luz ignorado
);
CREATE TABLE IF NOT EXISTS minutes (
    ts INTEGER PRIMARY KEY,         -- inicio del minuto (epoch)
    n_secs INTEGER,
    motion_mean REAL, motion_max REAL,
    active_secs INTEGER,            -- segundos por encima del umbral de movimiento
    out_max REAL,
    sound_mean_db REAL, sound_max_db REAL,
    voice_secs INTEGER
);
CREATE TABLE IF NOT EXISTS nights (
    night TEXT PRIMARY KEY,         -- AAAA-MM-DD (tarde en que empieza)
    start_ts INTEGER, end_ts INTEGER,
    sleep_onset INTEGER, sleep_end INTEGER,
    n_wakes INTEGER, min_awake INTEGER, min_sleep INTEGER,
    restlessness REAL, efficiency REAL,
    wakes_json TEXT, extra_json TEXT,
    final INTEGER DEFAULT 0,
    config_id INTEGER,
    telegram_sent INTEGER DEFAULT 0,
    updated INTEGER
);
CREATE TABLE IF NOT EXISTS clips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    night TEXT,
    path TEXT,                      -- relativo a la carpeta clips
    start_ts REAL, end_ts REAL,     -- lo que abarca el vídeo
    ep_start INTEGER, ep_end INTEGER,  -- el episodio de movimiento/sonido
    part INTEGER DEFAULT 1, parts INTEGER DEFAULT 1,
    motion_max REAL, motion_mean REAL, active_secs INTEGER,
    out_max REAL,
    sound_max_db REAL, voice_secs INTEGER,
    algo TEXT,                      -- dormida | despierta
    algo_reason TEXT,
    label TEXT,                     -- dormida | despierta | falsa
    labeled_at INTEGER,
    size_bytes INTEGER,
    deleted INTEGER DEFAULT 0,
    created INTEGER
);
CREATE INDEX IF NOT EXISTS clips_night ON clips(night);
CREATE INDEX IF NOT EXISTS clips_start ON clips(start_ts);
CREATE TABLE IF NOT EXISTS config_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created INTEGER,
    source TEXT,                    -- inicial | calibracion | manual | restaurar
    note TEXT,
    values_json TEXT,
    metrics_json TEXT
);
CREATE TABLE IF NOT EXISTS calibration_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created INTEGER, finished INTEGER,
    status TEXT,                    -- corriendo | ok | error
    progress REAL,
    result_json TEXT
);
CREATE TABLE IF NOT EXISTS night_notes (
    night TEXT PRIMARY KEY,
    tags TEXT,                      -- ["dientes", "enferma", ...]
    text TEXT,
    updated INTEGER
);
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated INTEGER
);
"""


def connect():
    os.makedirs(config.DATA_DIR, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA busy_timeout=30000")
    return con


def init():
    con = connect()
    con.executescript(SCHEMA)
    cols = {r[1] for r in con.execute("PRAGMA table_info(clips)")}
    if "skipped" not in cols:  # «No lo sé»: revisado, pero sin etiqueta
        con.execute("ALTER TABLE clips ADD COLUMN skipped INTEGER DEFAULT 0")
    if "review" not in cols:   # 1 = hay dudas de si estaba despierta: pedir revisión
        con.execute("ALTER TABLE clips ADD COLUMN review INTEGER DEFAULT 1")
    con.commit()
    return con


def kv_set(con, key, value):
    con.execute(
        "INSERT INTO kv(key,value,updated) VALUES(?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated=excluded.updated",
        (key, json.dumps(value), int(time.time())),
    )


def kv_get(con, key, default=None):
    r = con.execute("SELECT value, updated FROM kv WHERE key=?", (key,)).fetchone()
    if not r:
        return default
    return json.loads(r["value"])


def kv_get_with_time(con, key):
    r = con.execute("SELECT value, updated FROM kv WHERE key=?", (key,)).fetchone()
    if not r:
        return None, None
    return json.loads(r["value"]), r["updated"]
