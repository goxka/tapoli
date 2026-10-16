"""Panel web (solo red local)."""
import asyncio
import hashlib
import ipaddress
import json
import logging
import os
import re
import shutil
import threading
import time
from datetime import date

from fastapi import Body, FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from websockets.asyncio.client import connect as ws_connect

from . import align, analysis, config, db, notify
from .runner import setup_logging

setup_logging("web")
db.init().close()  # por si el panel arranca antes que el analizador
log = logging.getLogger("web")
app = FastAPI(title="Tapoli", docs_url=None, redoc_url=None)
STATIC = os.path.join(os.path.dirname(__file__), "static")
GO2RTC_WS = os.environ.get("GO2RTC_WS", "ws://127.0.0.1:1984/api/ws?src=tapo")
LABELS = {"dormida", "despierta", "falsa"}   # "nose" = «No lo sé»: revisado sin etiqueta
_job_lock = threading.Lock()
_jobs = {"reprocesar": None}

PRIVATE = [ipaddress.ip_network(n) for n in
           ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "169.254.0.0/16", "::1/128",
            "fc00::/7", "fe80::/10")]


def is_private(host):
    try:
        ip = ipaddress.ip_address(host)
        return any(ip in n for n in PRIVATE)
    except Exception:
        return False


@app.middleware("http")
async def local_only(request: Request, call_next):
    if not is_private(request.client.host):
        return Response("Solo red local", status_code=403)
    resp = await call_next(request)
    path = request.url.path
    if path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    elif path.startswith("/clips/"):
        resp.headers["Cache-Control"] = "no-cache"   # los clips pueden recortarse: revalidar
    elif path.startswith("/static/"):
        # con huella de versión → caché larga; sin ella → revalidar siempre
        versioned = request.query_params.get("v") == ASSET_VERSION
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable" if versioned else "no-cache"
    return resp


_local = threading.local()


class _Con:
    """Conexión reutilizada por hilo: abrir/cerrar SQLite en cada petición obliga a volcar
    el WAL al disco (≈1 s en el btrfs del NAS)."""

    def __init__(self, c):
        self._c = c

    def __getattr__(self, name):
        return getattr(self._c, name)

    def close(self):
        pass


def con():
    c = getattr(_local, "c", None)
    if c is None:
        c = _local.c = _Con(db.connect())
    return c


def night_row_dict(r):
    if not r:
        return None
    d = dict(r)
    d["wakes"] = json.loads(d.pop("wakes_json") or "[]")
    d["extra"] = json.loads(d.pop("extra_json") or "{}")
    return d


def clip_dict(r):
    d = dict(r)
    d["url"] = None if d["deleted"] else "/clips/" + d["path"]
    thumb = d["path"][:-4] + ".jpg"
    d["thumb"] = "/clips/" + thumb if os.path.exists(os.path.join(config.CLIPS_DIR, thumb)) else None
    d["desacuerdo"] = bool(d["label"] and d["algo"] and (
        (d["label"] == "despierta") != (d["algo"] == "despierta")))
    return d


# ------------------------------------------------------------------ estado
@app.get("/api/status")
def status():
    c = con()
    cfg = config.load()
    cam, cam_t = db.kv_get_with_time(c, "camara")
    now_v, now_t = db.kv_get_with_time(c, "ahora")
    store = db.kv_get(c, "almacenamiento", {})
    last = c.execute("SELECT * FROM nights WHERE final=1 ORDER BY night DESC LIMIT 1").fetchone()
    cur_night = config.night_for_ts(cfg, time.time())
    live = None
    if cur_night:
        live = night_row_dict(c.execute("SELECT * FROM nights WHERE night=?", (cur_night.isoformat(),)).fetchone())
    c2 = c
    pending = c.execute("SELECT COUNT(*) FROM clips WHERE review=1 AND label IS NULL AND skipped=0 AND deleted=0").fetchone()[0]
    c.close()
    gb = (store.get("bytes") or 0) / 1e9
    return {
        "nombre": cfg["telegram"].get("nombre", "la bebé"),
        "camara": cam, "camara_t": cam_t,
        "ahora": now_v, "ahora_t": now_t, "server_t": int(time.time()),
        "almacenamiento": store, "aviso_espacio": gb > cfg["almacenamiento"]["aviso_gb"],
        "aviso_gb": cfg["almacenamiento"]["aviso_gb"],
        "ultima_noche": night_row_dict(last), "noche_en_curso": live,
        "clips_sin_etiquetar": pending,
        "telegram": notify.enabled(cfg),
        "telegram_token": notify.has_token(),
        "telegram_chats": [{"id": c["id"], "name": c.get("name")} for c in notify.chats()],
        "directo": bool(cam and cam.get("estado") == "conectada"),
        "version": ASSET_VERSION,
        "zona_auto": db.kv_get(c2, "zona_auto"),
        "snapshot_ts": db.kv_get(c2, "snapshot_ts"),
    }


# ------------------------------------------------------------------ noches
@app.get("/api/nights")
def nights():
    c = con()
    rows = [night_row_dict(r) for r in c.execute("SELECT * FROM nights ORDER BY night DESC LIMIT 120")]
    counts = {r[0]: (r[1], r[2], r[3]) for r in c.execute(
        "SELECT night, COUNT(*), SUM(label IS NOT NULL OR skipped=1), "
        "SUM(review=1 AND label IS NULL AND skipped=0 AND deleted=0) FROM clips GROUP BY night")}
    c.close()
    notes = notes_map(c)
    for r in rows:
        r["clips"], r["etiquetados"], r["pendientes"] = counts.get(r["night"], (0, 0, 0))
        r["notes"] = notes.get(r["night"])
    return rows


NOTE_TAGS = {"dientes", "enferma", "vacuna", "siesta_larga", "siesta_corta", "fuera_casa", "cena", "otro"}


def notes_map(c):
    out = {}
    for r in c.execute("SELECT night, tags, text FROM night_notes"):
        out[r["night"]] = {"tags": json.loads(r["tags"] or "[]"), "text": r["text"] or ""}
    return out


@app.post("/api/night/{night}/notes")
def set_notes(night: str, payload: dict = Body(...)):
    date.fromisoformat(night)
    tags = [t for t in payload.get("tags", []) if t in NOTE_TAGS]
    text = str(payload.get("text", ""))[:1000].strip()
    c = con()
    if not tags and not text:
        c.execute("DELETE FROM night_notes WHERE night=?", (night,))
    else:
        c.execute("INSERT INTO night_notes(night,tags,text,updated) VALUES(?,?,?,?) ON CONFLICT(night) DO UPDATE SET "
                  "tags=excluded.tags, text=excluded.text, updated=excluded.updated",
                  (night, json.dumps(tags), text, int(time.time())))
    c.commit()
    return {"tags": tags, "text": text}


@app.get("/api/night/{night}")
def night(night: str):
    try:
        d = date.fromisoformat(night)
    except ValueError:
        raise HTTPException(400, "fecha inválida")
    cfg = config.load()
    a, b = config.night_window(cfg, d)
    c = con()
    row = night_row_dict(c.execute("SELECT * FROM nights WHERE night=?", (night,)).fetchone())
    mins = [dict(r) for r in c.execute(
        "SELECT ts, active_secs, voice_secs, motion_max, sound_max_db, out_max, n_secs FROM minutes "
        "WHERE ts>=? AND ts<? ORDER BY ts", (a, b))]
    clips = [clip_dict(r) for r in c.execute("SELECT * FROM clips WHERE night=? ORDER BY start_ts", (night,))]
    c.close()
    nr = con().execute("SELECT tags, text FROM night_notes WHERE night=?", (night,)).fetchone()
    notes = {"tags": json.loads(nr["tags"] or "[]"), "text": nr["text"] or ""} if nr else None
    return {"night": night, "start": a, "end": b, "summary": row, "minutes": mins, "clips": clips, "notes": notes,
            "umbral_minuto": cfg["despertar"]["seg_activos_minuto"],
            "umbral_voz": cfg["despertar"]["voz_seg_minuto"]}


# ------------------------------------------------------------------ clips
@app.post("/api/clips/{clip_id}/label")
def label(clip_id: int, payload: dict = Body(...)):
    lab = payload.get("label")
    if lab is not None and lab not in LABELS and lab != "nose":
        raise HTTPException(400, "etiqueta no válida")
    c = con()
    skipped = 1 if lab == "nose" else 0
    if lab == "nose":
        lab = None
    c.execute("UPDATE clips SET label=?, labeled_at=?, skipped=? WHERE id=?",
              (lab, int(time.time()) if lab else None, skipped, clip_id))
    c.commit()
    r = c.execute("SELECT * FROM clips WHERE id=?", (clip_id,)).fetchone()
    if r:  # tus etiquetas mandan en el resumen de la noche: recalcularla ya
        try:
            cid = c.execute("SELECT MAX(id) FROM config_history").fetchone()[0]
            analysis.analyze_night(c, config.load(), date.fromisoformat(r["night"]), config_id=cid)
            r = c.execute("SELECT * FROM clips WHERE id=?", (clip_id,)).fetchone()
        except Exception:
            log.exception("No se pudo recalcular la noche tras etiquetar")
    c.close()
    if not r:
        raise HTTPException(404)
    return clip_dict(r)


# ------------------------------------------------------------------ configuración
@app.get("/api/config")
def get_config():
    cfg = config.load()
    c = con()
    hist = [dict(r) for r in c.execute("SELECT * FROM config_history ORDER BY id DESC LIMIT 50")]
    c.close()
    for h in hist:
        h["values"] = json.loads(h.pop("values_json"))
        h["metrics"] = json.loads(h.pop("metrics_json") or "null")
    return {"config": cfg, "actual": config.calib_values(cfg), "historial": hist,
            "umbral_movimiento": config.motion_threshold(cfg)}


def _apply_values(values, source, note, metrics=None):
    changes = {}
    for k, v in values.items():
        s, key = k.split(".", 1)
        if (s, key) not in config.CALIB_KEYS:
            raise HTTPException(400, f"clave no permitida: {k}")
        changes[(s, key)] = int(v) if float(v).is_integer() else float(v)
    config.set_values(changes)
    cfg = config.load()
    c = con()
    cur = c.execute("INSERT INTO config_history(created,source,note,values_json,metrics_json) VALUES(?,?,?,?,?)",
                    (int(time.time()), source, note, json.dumps(config.calib_values(cfg)),
                     json.dumps(metrics) if metrics else None))
    c.commit()
    c.close()
    log.info("Configuración aplicada (%s): %s", source, values)
    return cur.lastrowid


@app.post("/api/config/apply")
def apply_config(payload: dict = Body(...)):
    cid = _apply_values(payload["values"], payload.get("source", "calibracion"),
                        payload.get("note", ""), payload.get("metrics"))
    return {"ok": True, "id": cid}


@app.post("/api/config/restore/{hid}")
def restore(hid: int):
    c = con()
    r = c.execute("SELECT values_json FROM config_history WHERE id=?", (hid,)).fetchone()
    c.close()
    if not r:
        raise HTTPException(404)
    cid = _apply_values(json.loads(r[0]), "restaurar", f"vuelta a la configuración #{hid}")
    return {"ok": True, "id": cid}


@app.post("/api/roi")
def set_roi(payload: dict = Body(...)):
    poly = payload.get("poly")
    if not isinstance(poly, list) or len(poly) < 3:
        raise HTTPException(400, "el polígono necesita al menos 3 puntos")
    poly = [[round(min(1, max(0, float(x))), 3), round(min(1, max(0, float(y))), 3)] for x, y in poly]
    config.set_values({("movimiento", "zona_cuna"): poly})
    # la imagen actual pasa a ser la referencia para el reajuste automático
    snap = os.path.join(config.DATA_DIR, "snapshot.jpg")
    ref_ok = False
    img = align.load_gray(snap) if os.path.exists(snap) else None
    if img is not None and align.sharpness(img) >= 3.5:
        shutil.copyfile(snap, os.path.join(config.DATA_DIR, "zona_ref.jpg"))
        with open(os.path.join(config.DATA_DIR, "zona_ref.json"), "w") as f:
            json.dump({"poly": poly, "ts": int(time.time())}, f)
        ref_ok = True
    c = con()
    db.kv_set(c, "zona_auto", {"ts": int(time.time()), "estado": "manual", "aplicado": False})
    c.commit()
    log.info("Zona de la cuna actualizada (referencia guardada: %s): %s", ref_ok, poly)
    return {"ok": True, "poly": poly, "referencia": ref_ok}


@app.post("/api/config/noche")
def set_night(payload: dict = Body(...)):
    def hhmm(v):
        if not isinstance(v, str) or not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", v):
            raise HTTPException(400, f"hora no válida: {v}")
        return v
    changes = {
        ("noche", "inicio"): hhmm(payload["inicio"]),
        ("noche", "fin"): hhmm(payload["fin"]),
        ("noche", "despertar_final_desde"): hhmm(payload["despertar_final_desde"]),
    }
    calma = int(payload["dormirse_calma_min"])
    if not 5 <= calma <= 60:
        raise HTTPException(400, "la calma para dormirse debe estar entre 5 y 60 min")
    changes[("noche", "dormirse_calma_min")] = calma
    config.set_values(changes)
    log.info("Ajustes de la noche cambiados: %s", payload)
    return reprocess()


# ------------------------------------------------------------------ calibración
def _calib_worker(run_id):
    c = con()
    try:
        cfg = config.load()

        def prog(p):
            c.execute("UPDATE calibration_runs SET progress=? WHERE id=?", (round(p, 3), run_id))
            c.commit()
        res = analysis.calibrate(c, cfg, prog)
        c.execute("UPDATE calibration_runs SET status=?, progress=1, finished=?, result_json=? WHERE id=?",
                  ("error" if "error" in res else "ok", int(time.time()), json.dumps(res), run_id))
        c.commit()
    except Exception as e:
        log.exception("Error en la calibración")
        c.execute("UPDATE calibration_runs SET status='error', finished=?, result_json=? WHERE id=?",
                  (int(time.time()), json.dumps({"error": str(e)}), run_id))
        c.commit()
    finally:
        c.close()


@app.post("/api/calibrate")
def start_calibration():
    c = con()
    running = c.execute("SELECT id FROM calibration_runs WHERE status='corriendo' AND created>?",
                        (int(time.time()) - 1800,)).fetchone()
    if running:
        c.close()
        return {"id": running[0]}
    cur = c.execute("INSERT INTO calibration_runs(created,status,progress) VALUES(?,?,0)",
                    (int(time.time()), "corriendo"))
    c.commit()
    rid = cur.lastrowid
    c.close()
    threading.Thread(target=_calib_worker, args=(rid,), daemon=True).start()
    return {"id": rid}


@app.get("/api/calibrate")
def calibration_status():
    c = con()
    r = c.execute("SELECT * FROM calibration_runs ORDER BY id DESC LIMIT 1").fetchone()
    lab = {row[0]: row[1] for row in c.execute(
        "SELECT label, COUNT(*) FROM clips WHERE label IS NOT NULL GROUP BY label")}
    c.close()
    out = {"etiquetas": lab, "run": None}
    if r:
        d = dict(r)
        d["result"] = json.loads(d.pop("result_json") or "null")
        out["run"] = d
    return out


# ------------------------------------------------------------------ reprocesar
def _reprocess_worker():
    c = con()
    try:
        cfg = config.load()
        cid = c.execute("SELECT MAX(id) FROM config_history").fetchone()[0]

        def prog(p):
            _jobs["reprocesar"]["progress"] = p
        n = analysis.reprocess_all(c, cfg, config_id=cid, progress=prog)
        _jobs["reprocesar"].update(status="ok", nights=n, finished=int(time.time()))
        log.info("Reprocesadas %d noches", n)
    except Exception as e:
        log.exception("Error reprocesando")
        _jobs["reprocesar"].update(status="error", error=str(e))
    finally:
        c.close()


@app.post("/api/reprocess")
def reprocess():
    with _job_lock:
        j = _jobs["reprocesar"]
        if j and j["status"] == "corriendo":
            return j
        _jobs["reprocesar"] = {"status": "corriendo", "progress": 0, "started": int(time.time())}
    threading.Thread(target=_reprocess_worker, daemon=True).start()
    return _jobs["reprocesar"]


@app.get("/api/reprocess")
def reprocess_status():
    return _jobs["reprocesar"] or {"status": "nunca"}


# ------------------------------------------------------------------ varios
_link = {"code": None, "until": 0, "offset": 0}


@app.post("/api/telegram/link")
def telegram_link_start():
    """Empieza a vincular un móvil: devuelve un enlace al bot con un código de un solo uso."""
    if not notify.has_token():
        raise HTTPException(400, "Falta TELEGRAM_BOT_TOKEN")
    try:
        me = notify.api("getMe")
        upd = notify.api("getUpdates", timeout=0)
        _link["offset"] = (upd[-1]["update_id"] + 1) if upd else 0
    except Exception as e:
        raise HTTPException(502, f"Telegram no responde: {e}")
    import secrets
    _link["code"] = secrets.token_hex(4)
    _link["until"] = time.time() + 300
    return {"url": f"https://t.me/{me['username']}?start={_link['code']}",
            "app_url": f"tg://resolve?domain={me['username']}&start={_link['code']}",   # abre Telegram directamente
            "bot": me["username"]}


@app.get("/api/telegram/link")
def telegram_link_poll():
    """Comprueba si ya han pulsado «Iniciar» en el bot con el código."""
    if not _link["code"] or time.time() > _link["until"]:
        return {"estado": "caducado"}
    try:
        upd = notify.api("getUpdates", offset=_link["offset"], timeout=0)
    except Exception as e:
        return {"estado": "esperando", "error": str(e)}
    for u in upd:
        _link["offset"] = u["update_id"] + 1
        m = u.get("message") or {}
        if (m.get("text") or "").strip() == f"/start {_link['code']}":
            ch = m["chat"]
            name = " ".join(x for x in (ch.get("first_name"), ch.get("last_name")) if x) or ch.get("username") or str(ch["id"])
            lst = [c for c in notify.chats() if str(c["id"]) != str(ch["id"]) and c.get("name") != "configurado en .env"]
            lst.append({"id": ch["id"], "name": name})
            notify.save_chats(lst)
            _link["code"] = None
            cfg = config.load()
            nombre = cfg.get("telegram", {}).get("nombre", "la bebé")
            notify.send(f"✅ ¡Listo, {ch.get('first_name') or name}! Cada mañana te mandaré aquí el resumen de la noche de {nombre}.", ch["id"])
            log.info("Telegram vinculado: %s", name)
            return {"estado": "ok", "name": name}
    return {"estado": "esperando"}


@app.post("/api/telegram/unlink")
def telegram_unlink(payload: dict = Body(...)):
    cid = str(payload.get("id"))
    notify.save_chats([c for c in notify.chats() if str(c["id"]) != cid and c.get("name") != "configurado en .env"])
    return {"ok": True}


@app.post("/api/telegram/test")
def telegram_test():
    cfg = config.load()
    if not notify.enabled(cfg):
        raise HTTPException(400, "Telegram no está configurado: vincula un móvil en Ajustes → Resumen por Telegram")
    c = con()
    # la última noche (aunque aún no haya terminado): el mismo mensaje que llegaría por la mañana
    r = c.execute("SELECT * FROM nights ORDER BY night DESC LIMIT 1").fetchone()
    c.close()
    text = notify.night_message(cfg, r) if r else "Tapoli: Telegram funciona ✅ (aún no hay ninguna noche para resumir)"
    try:
        notify.send(text)
    except Exception as e:
        raise HTTPException(502, f"Telegram respondió con error: {e}")
    return {"ok": True}


@app.get("/api/zona_ref.jpg")
def zona_ref():
    p = os.path.join(config.DATA_DIR, "zona_ref.jpg")
    if not os.path.exists(p):
        raise HTTPException(404)
    return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/api/zona_ref")
def zona_ref_meta():
    p = os.path.join(config.DATA_DIR, "zona_ref.json")
    if not os.path.exists(p):
        return {}
    with open(p) as f:
        return json.load(f)


@app.get("/api/snapshot.jpg")
def snapshot():
    p = os.path.join(config.DATA_DIR, "snapshot.jpg")
    if not os.path.exists(p):
        raise HTTPException(404, "Aún no hay imagen (se genera cada 5 min)")
    return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.websocket("/api/live/ws")
async def live_ws(ws: WebSocket):
    """Puente hacia go2rtc (que solo escucha en 127.0.0.1): señalización WebRTC y MSE."""
    if not is_private(ws.client.host):
        await ws.close(code=1008)
        return
    await ws.accept()
    try:
        async with ws_connect(GO2RTC_WS, max_size=None, open_timeout=5, ping_interval=None) as up:
            async def from_client():
                while True:
                    m = await ws.receive()
                    if m["type"] == "websocket.disconnect":
                        return
                    if m.get("text") is not None:
                        await up.send(m["text"])
                    elif m.get("bytes") is not None:
                        await up.send(m["bytes"])

            async def from_go2rtc():
                async for m in up:
                    if isinstance(m, bytes):
                        await ws.send_bytes(m)
                    else:
                        await ws.send_text(m)

            tasks = [asyncio.create_task(from_client()), asyncio.create_task(from_go2rtc())]
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)   # el móvil cerró: no es un error
    except Exception as e:
        log.info("Directo cerrado: %s", e)
    finally:
        try:
            await ws.close()
        except Exception:
            pass


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(os.path.join(STATIC, "manifest.webmanifest"), media_type="application/manifest+json")


def _asset_version():
    """Huella de los archivos del panel: cambia en cada despliegue."""
    hsh = hashlib.sha1()
    for f in sorted(os.listdir(STATIC)):
        p = os.path.join(STATIC, f)
        if os.path.isfile(p):
            with open(p, "rb") as fh:
                hsh.update(fh.read())
    return hsh.hexdigest()[:10]


ASSET_VERSION = _asset_version()


@app.get("/")
def index():
    with open(os.path.join(STATIC, "index.html"), encoding="utf-8") as f:
        html = f.read()
    html = re.sub(r"\?v=\w+", "?v=" + ASSET_VERSION, html)
    html = html.replace("<head>", f'<head>\n<meta name="tapoli-version" content="{ASSET_VERSION}">', 1)
    return Response(html, media_type="text/html", headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


app.mount("/static", StaticFiles(directory=STATIC), name="static")
os.makedirs(config.CLIPS_DIR, exist_ok=True)
app.mount("/clips", StaticFiles(directory=config.CLIPS_DIR), name="clips")
