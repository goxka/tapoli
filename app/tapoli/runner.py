"""Proceso del analizador: captura, métricas por segundo/minuto, clips, noches, limpieza y Telegram."""
import json
import logging
import logging.handlers
import os
import shutil
import signal
import subprocess
import threading
import time
from collections import deque
from datetime import date, datetime, timedelta

import numpy as np

from . import align, analysis, config, db, notify
from .audio import AudioAnalyzer
from .capture import Capture
from .clips import ClipWriter, EpisodeRecorder, SegmentBuffer, make_thumb
from .motion import MotionDetector

log = logging.getLogger("analizador")


def setup_logging(name):
    os.makedirs(os.path.join(config.DATA_DIR, "logs"), exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    fh = logging.handlers.RotatingFileHandler(
        os.path.join(config.DATA_DIR, "logs", f"{name}.log"), maxBytes=5_000_000, backupCount=3)
    fh.setFormatter(fmt)
    root.handlers = [sh, fh]


def record_config_if_changed(con, cfg, source):
    vals = config.calib_values(cfg)
    last = con.execute("SELECT values_json FROM config_history ORDER BY id DESC LIMIT 1").fetchone()
    if last and json.loads(last[0]) == vals:
        return con.execute("SELECT MAX(id) FROM config_history").fetchone()[0]
    cur = con.execute("INSERT INTO config_history(created,source,note,values_json) VALUES(?,?,?,?)",
                      (int(time.time()), source, "", json.dumps(vals)))
    con.commit()
    return cur.lastrowid


class Analyzer:
    def __init__(self):
        self.con = db.init()
        self.cfg = config.load()
        self.cfg_mtime = config.mtime()
        self.config_id = record_config_if_changed(
            self.con, self.cfg, "inicial" if not self._has_history() else "manual")
        self.motion = MotionDetector(self.cfg)
        self.audio = AudioAnalyzer(self.cfg)
        self.lock = threading.Lock()
        self.pending = {}          # segundo -> métricas parciales
        self.audio_buf = bytearray()
        self.history = deque(maxlen=1800)  # últimos 30 min de filas por segundo (para los clips)
        self.batch = []
        self.buffer = SegmentBuffer()
        self.buffer.clear()
        self.writer = ClipWriter(self.buffer, self.on_clip)
        self.recorder = EpisodeRecorder(self.cfg, self.writer)
        self.capture = Capture(self.cfg, self.on_frame, self.on_audio, self.on_state)
        self.stop = threading.Event()
        self.last_minute = None
        self.last_maint = 0
        self.last_snapshot = 0
        self.thr = config.motion_threshold(self.cfg)
        self.prev_s = None
        self.align_due = time.time() + 60
        self.align_tries = 0

    def _has_history(self):
        return self.con.execute("SELECT COUNT(*) FROM config_history").fetchone()[0] > 0

    # ---------------------------------------------------------------- entradas
    def on_state(self, state, detail):
        db_con = db.connect()
        db.kv_set(db_con, "camara", {"estado": state, "detalle": detail, "desde": int(time.time())})
        db_con.commit()
        db_con.close()
        if state == "conectada":
            log.info("Cámara conectada")
            # al (re)conectar: foto nueva y comprobar si la cámara se ha movido
            self.last_snapshot = 0
            self.align_due = time.time() + 30
            self.align_tries = 0

    def on_frame(self, ts, data):
        r = self.motion.process(data)
        if r is None:
            return
        roi, out, glob = r
        s = int(ts)
        with self.lock:
            p = self.pending.setdefault(s, {})
            p["motion"] = max(p.get("motion", 0.0), roi)
            p["out"] = max(p.get("out", 0.0), out)
            if glob:
                p["flags"] = 1

    def on_audio(self, ts, data):
        self.audio_buf += data
        while len(self.audio_buf) >= 16000:
            chunk = bytes(self.audio_buf[:16000])
            del self.audio_buf[:16000]
            level, band, voice = self.audio.process_second(np.frombuffer(chunk, np.int16))
            s = int(ts)
            with self.lock:
                p = self.pending.setdefault(s, {})
                if "sound" in p:  # dos bloques en el mismo segundo: el siguiente
                    p = self.pending.setdefault(s + 1, {})
                p["sound"] = level
                p["band"] = band
                p["voice"] = int(voice)

    # ---------------------------------------------------------------- clips
    def on_clip(self, job, rel, start, end, size):
        rows = [h for h in list(self.history) if start <= h[0] < end]
        ep_end = job["ep_end"] or job["clip_end"]
        motion = [r[1] for r in rows if r[1] is not None]
        night = config.night_for_ts(self.cfg, job["ep_start"]) or \
            datetime.fromtimestamp(job["ep_start"], config.TZ).date()
        con = db.connect()
        con.execute(
            "INSERT INTO clips(night,path,start_ts,end_ts,ep_start,ep_end,part,parts,motion_max,motion_mean,"
            "active_secs,out_max,sound_max_db,voice_secs,algo,algo_reason,size_bytes,created) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (night.isoformat(), rel, start, end, max(job["ep_start"], start), min(ep_end, end), job["part"], job["part"],
             max(motion) if motion else None, float(np.mean(motion)) if motion else None,
             sum(1 for m in motion if m > self.thr),
             max((r[2] for r in rows if r[2] is not None), default=None),
             max((r[3] for r in rows if r[3] is not None), default=None),
             sum(1 for r in rows if r[5] == 1),
             None, "pendiente de analizar", size, int(time.time())))
        if job["final"] and job["part"] > 1:
            con.execute("UPDATE clips SET parts=? WHERE night=? AND ep_start>=? AND ep_start<?",
                        (job["part"], night.isoformat(), job["ep_start"] - 1, ep_end + 1))
        con.commit()
        try:
            analysis.analyze_night(con, self.cfg, night, config_id=self.config_id)
        except Exception:
            log.exception("Error clasificando clip")
        con.close()

    # ---------------------------------------------------------------- bucle
    def tick(self):
        now = time.time()
        cutoff = int(now) - 2
        with self.lock:
            ready = sorted(s for s in self.pending if s < cutoff)
            items = [(s, self.pending.pop(s)) for s in ready]
        for s, p in items:
            if self.prev_s and s - self.prev_s > 60:
                self.recorder.flush()  # hueco largo (cámara caída): cerrar el episodio abierto
            self.prev_s = s
            row = (s, p.get("motion"), p.get("out"), p.get("sound"), p.get("band"), p.get("voice"), p.get("flags", 0))
            self.batch.append(row)
            self.history.append(row)
            active = (row[1] is not None and row[1] > self.thr) or row[5] == 1
            in_night = config.night_for_ts(self.cfg, s) is not None
            allow = in_night or not self.cfg["episodios"]["solo_de_noche"]
            self.recorder.feed(s, active, allow)
            self.live = {"ts": s, "motion": row[1], "voice": row[5], "sound": row[3], "active": bool(active)}

        # búfer circular: conservar lo necesario
        keep = int(now) - 20
        for t in (self.recorder.needed_from(), self.writer.needed_from()):
            if t is not None:
                keep = min(keep, t - 4)
        self.buffer.cleanup(keep)

        if self.batch and (len(self.batch) >= 10 or now - getattr(self, "_last_flush", 0) > 10):
            self.flush()

    def flush(self):
        self.con.executemany(
            "INSERT OR REPLACE INTO seconds(ts,motion,motion_out,sound_db,voice_db,voice,flags) VALUES(?,?,?,?,?,?,?)",
            self.batch)
        if getattr(self, "live", None):
            db.kv_set(self.con, "ahora", self.live)
        self.con.commit()
        self.batch = []
        self._last_flush = time.time()

    def every_minute(self):
        now = int(time.time())
        minute = now // 60 * 60
        if self.last_minute == minute:
            return
        prev = self.last_minute
        self.last_minute = minute
        self.flush_safe()
        # minutos cerrados
        start = prev if prev else minute - 60
        sec = analysis.load_seconds(self.con, start, minute)
        rows = analysis.minute_rows(sec, start, (minute - start) // 60, self.thr)
        if rows:
            analysis.save_minutes(self.con, rows)
            self.con.commit()
        self.reload_config()
        # noche en curso / recién terminada
        for d in self._recent_nights(now):
            row = self.con.execute("SELECT final, telegram_sent FROM nights WHERE night=?", (d.isoformat(),)).fetchone()
            if row and row["final"] and row["telegram_sent"]:
                continue
            a, b = config.night_window(self.cfg, d)
            if now < a:
                continue
            summ = analysis.analyze_night(self.con, self.cfg, d, config_id=self.config_id)
            if now >= b and summ is not None:
                self.morning_summary(d, b)
        if now - self.last_maint > 3600:
            self.last_maint = now
            try:
                self.maintenance()
            except Exception:
                log.exception("Error en mantenimiento")
        if now - self.last_snapshot > 300 and self.snapshot():
            self.last_snapshot = now
        if self.align_due and now >= self.align_due:
            self.auto_align()

    def flush_safe(self):
        if self.batch:
            self.flush()

    def _recent_nights(self, now):
        today = datetime.fromtimestamp(now, config.TZ).date()
        return [today - timedelta(days=1), today]

    def morning_summary(self, d, end_ts):
        row = self.con.execute("SELECT * FROM nights WHERE night=?", (d.isoformat(),)).fetchone()
        if not row or row["telegram_sent"]:
            return
        if not notify.enabled(self.cfg) or time.time() - end_ts > 3 * 3600:
            self.con.execute("UPDATE nights SET telegram_sent=2 WHERE night=?", (d.isoformat(),))
            self.con.commit()
            return
        try:
            notify.send(notify.night_message(self.cfg, row))
            self.con.execute("UPDATE nights SET telegram_sent=1 WHERE night=?", (d.isoformat(),))
            self.con.commit()
            log.info("Resumen de la noche %s enviado por Telegram", d)
        except Exception as e:
            log.warning("No se pudo enviar el resumen por Telegram: %s", e)

    def reload_config(self):
        m = config.mtime()
        if m == self.cfg_mtime:
            return
        try:
            new = config.load()
        except Exception as e:
            log.error("config.yaml no es válido, sigo con la anterior: %s", e)
            self.cfg_mtime = m
            return
        old = self.cfg
        self.cfg, self.cfg_mtime = new, m
        self.motion.configure(new)
        self.audio.configure(new)
        self.recorder.configure(new)
        self.thr = config.motion_threshold(new)
        self.config_id = record_config_if_changed(self.con, new, "manual")
        if new["video"] != old["video"]:
            self.motion = MotionDetector(new)
            self.capture.cfg = new
            self.capture.restart_requested.set()
        log.info("Configuración recargada (umbral de movimiento %.4f)", self.thr)

    def snapshot(self):
        segs = [s for s in self.buffer.segments() if s[1] is not None]
        if not segs:
            return False
        out = os.path.join(config.DATA_DIR, "snapshot.jpg")
        tmp = out + ".tmp.jpg"
        subprocess.run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-i", segs[-1][2],
                        "-frames:v", "1", "-q:v", "4", tmp], timeout=30)
        if not os.path.exists(tmp):
            return False
        img = align.load_gray(tmp)
        if img is None or align.sharpness(img) < 3.5:
            # cámara tapada, moviéndose o desenfocada: no guardar esa foto
            os.remove(tmp)
            return False
        os.replace(tmp, out)
        db.kv_set(self.con, "snapshot_ts", int(time.time()))
        self.con.commit()
        return True

    def auto_align(self):
        """Si la cámara se ha movido respecto a la imagen de referencia, mover la zona."""
        ref_jpg = os.path.join(config.DATA_DIR, "zona_ref.jpg")
        ref_meta = os.path.join(config.DATA_DIR, "zona_ref.json")
        snap = os.path.join(config.DATA_DIR, "snapshot.jpg")
        now = int(time.time())
        self.align_due = now + 1800          # siguiente comprobación en 30 min
        if not (os.path.exists(ref_jpg) and os.path.exists(ref_meta)):
            return
        self.align_tries = getattr(self, "align_tries", 0) + 1
        fresh = self.snapshot()
        cur = align.load_gray(snap) if fresh else None
        if cur is None:
            if self.align_tries < 10:
                self.align_due = now + 60    # aún no hay buena imagen: reintentar en 1 min
            return
        with open(ref_meta) as f:
            meta = json.load(f)
        ref = align.load_gray(ref_jpg)
        dx, dy, conf = align.estimate_shift(ref, cur, self.cfg["movimiento"].get("zonas_excluidas"))
        info = {"ts": now, "dx": round(dx, 4), "dy": round(dy, 4), "conf": round(conf, 1), "aplicado": False}
        if conf < 25:
            info["estado"] = "dudosa"        # no se reconoce bien la escena: no tocar
        elif max(abs(dx), abs(dy)) > 0.25:
            info["estado"] = "muy_movida"    # demasiado: mejor que la revises tú
        else:
            info["estado"] = "ok"
            new = align.shift_poly(meta["poly"], dx, dy) if max(abs(dx), abs(dy)) >= 0.01 else meta["poly"]
            if new != self.cfg["movimiento"]["zona_cuna"]:
                config.set_values({("movimiento", "zona_cuna"): new})
                info["aplicado"] = True
                log.info("Cámara movida %+.1f%% / %+.1f%%: zona de la cuna reajustada", dx * 100, dy * 100)
        if info["estado"] != "ok":
            log.warning("Comprobación de la zona: %s (confianza %.0f)", info["estado"], conf)
        db.kv_set(self.con, "zona_auto", info)
        self.con.commit()

    def maintenance(self):
        st = self.cfg["almacenamiento"]
        now = int(time.time())
        removed = 0
        rules = [("label IS NULL", st["dias_clips_sin_etiquetar"])]
        if st.get("dias_clips_etiquetados"):
            rules.append(("label IS NOT NULL", st["dias_clips_etiquetados"]))
        for cond, days in rules:
            if not days:
                continue
            for r in self.con.execute(f"SELECT id, path FROM clips WHERE deleted=0 AND {cond} AND start_ts<?",
                                      (now - days * 86400,)).fetchall():
                try:
                    os.remove(os.path.join(config.CLIPS_DIR, r["path"]))
                except FileNotFoundError:
                    pass
                self.con.execute("UPDATE clips SET deleted=1 WHERE id=?", (r["id"],))
                removed += 1
        # miniaturas que falten (clips antiguos)
        for r in self.con.execute("SELECT path, start_ts, end_ts FROM clips WHERE deleted=0").fetchall():
            mp4 = os.path.join(config.CLIPS_DIR, r["path"])
            if os.path.exists(mp4) and not os.path.exists(mp4[:-4] + ".jpg"):
                make_thumb(mp4, (r["end_ts"] - r["start_ts"]) / 2)
        for dpath in os.listdir(config.CLIPS_DIR):
            full = os.path.join(config.CLIPS_DIR, dpath)
            if os.path.isdir(full) and not os.listdir(full):
                os.rmdir(full)
        # métricas por segundo: conservar las noches con etiquetas
        keep_days = st.get("dias_datos_por_segundo", 120)
        limit = now - keep_days * 86400
        keep = []
        for (nd,) in self.con.execute("SELECT DISTINCT night FROM clips WHERE label IS NOT NULL"):
            keep.append(config.night_window(self.cfg, date.fromisoformat(nd)))
        q = "DELETE FROM seconds WHERE ts<?" + "".join(" AND NOT (ts>=? AND ts<?)" for _ in keep)
        args = [limit] + [x for k in keep for x in k]
        self.con.execute(q, args)
        self.con.commit()
        total = 0
        n = 0
        for root, _, files in os.walk(config.CLIPS_DIR):
            for f in files:
                total += os.path.getsize(os.path.join(root, f))
                n += 1
        du = shutil.disk_usage(config.CLIPS_DIR)
        db.kv_set(self.con, "almacenamiento", {"bytes": total, "clips": n, "libre": du.free,
                                               "db_bytes": os.path.getsize(db.DB_PATH)})
        self.con.commit()
        if removed:
            log.info("Limpieza: %d clips borrados", removed)

    # ---------------------------------------------------------------- arranque
    def run(self):
        log.info("Tapoli analizador arrancando (umbral de movimiento %.4f)", self.thr)
        self.writer.start()
        threading.Thread(target=self.capture.run_forever, daemon=True, name="captura").start()
        signal.signal(signal.SIGTERM, lambda *_: self.stop.set())
        signal.signal(signal.SIGINT, lambda *_: self.stop.set())
        self.maintenance()
        self.last_maint = time.time()
        while not self.stop.wait(1):
            try:
                self.tick()
                self.every_minute()
            except Exception:
                log.exception("Error en el bucle principal")
        log.info("Parando…")
        self.recorder.flush()
        self.capture.shutdown()
        self.flush_safe()
        t0 = time.time()
        while self.writer.needed_from() is not None and time.time() - t0 < 20:
            time.sleep(0.5)


def main():
    setup_logging("analizador")
    Analyzer().run()


if __name__ == "__main__":
    main()
