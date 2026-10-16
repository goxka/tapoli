"""Resumen matutino por Telegram (opcional)."""
import json
import logging
import os
import urllib.parse
import urllib.request
from datetime import datetime

from . import config

log = logging.getLogger("telegram")


CHATS_FILE = os.path.join(config.DATA_DIR, "telegram.json")


def chats():
    """Chats vinculados desde la app (data/telegram.json) + el de .env si lo hay."""
    out = []
    try:
        with open(CHATS_FILE) as f:
            out = json.load(f).get("chats", [])
    except (OSError, ValueError):
        pass
    env = os.environ.get("TELEGRAM_CHAT_ID")
    if env and all(str(c["id"]) != env for c in out):
        out.append({"id": env, "name": "configurado en .env"})
    return out


def save_chats(lst):
    tmp = CHATS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"chats": lst}, f)
    os.replace(tmp, CHATS_FILE)


def has_token():
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN"))


def enabled(cfg):
    return bool(cfg.get("telegram", {}).get("activado", True) and has_token() and chats())


def api(method, **params):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    data = urllib.parse.urlencode(params).encode() if params else None
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/{method}", data=data)
    with urllib.request.urlopen(req, timeout=20) as r:
        body = json.loads(r.read())
    if not body.get("ok"):
        raise RuntimeError(body.get("description", body))
    return body["result"]


def send(text, chat_id=None):
    targets = [chat_id] if chat_id else [c["id"] for c in chats()]
    if not targets:
        raise RuntimeError("no hay ningún chat vinculado")
    for c in targets:
        api("sendMessage", chat_id=c, text=text, disable_web_page_preview="true")


def hm(ts):
    return datetime.fromtimestamp(ts, config.TZ).strftime("%H:%M") if ts else "—"


def dur(minutes):
    if minutes is None:
        return "—"
    h, m = divmod(int(minutes), 60)
    return f"{h} h {m:02d} min" if h else f"{m} min"


def night_message(cfg, night_row):
    name = cfg.get("telegram", {}).get("nombre", "la bebé")
    d = datetime.fromisoformat(night_row["night"])
    wakes = [w for w in json.loads(night_row["wakes_json"] or "[]") if w["kind"] == "noche"]
    extra = json.loads(night_row["extra_json"] or "{}")
    lines = [f"🌙 Noche del {d.strftime('%d/%m')} — {name}" + ("" if night_row["final"] else " (en curso)")]
    if not night_row["sleep_onset"]:
        lines.append("No se ha detectado cuándo se durmió (¿cámara apagada o cuna vacía?).")
    else:
        lines += [
            f"😴 Se durmió: {hm(night_row['sleep_onset'])}" + (
                f" (acostada {hm(extra['bed'])}, tardó {extra['latency']} min)" if extra.get("bed") and extra.get("latency") is not None else ""),
            f"☀️ Se despertó: {hm(night_row['sleep_end'])}" if night_row["final"] or night_row["sleep_end"] and
            any(w["kind"] == "final" for w in json.loads(night_row["wakes_json"] or "[]")) else "☀️ Aún no se ha despertado",
            f"🔔 Despertares: {night_row['n_wakes']} ({dur(night_row['min_awake'])} despierta)",
            f"💤 Sueño: {dur(night_row['min_sleep'])}",
        ]
        if night_row["restlessness"] is not None:
            lines.append(f"〰️ Inquietud: {night_row['restlessness']:.1f} min con movimiento por hora")
        for w in wakes:
            lines.append(f"   · {hm(w['start'])}–{hm(w['end'])} ({w['min']} min)")
        for w in [w for w in json.loads(night_row["wakes_json"] or "[]") if w["kind"] == "fuera"]:
            lines.append(f"🛏️ Fuera de la cuna: {hm(w['start'])}–{hm(w['end'])} ({dur(w['min'])})")
    url = os.environ.get("PANEL_URL")
    if url:
        lines.append(f"\nRevisar: {url}")
    return "\n".join(lines)
