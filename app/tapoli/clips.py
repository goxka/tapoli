"""Búfer circular en RAM (trozos de 2 s en tmpfs) y grabación de clips por episodio."""
import glob
import logging
import os
import queue
import shutil
import subprocess
import threading
import time
from collections import deque
from datetime import datetime

from . import config

log = logging.getLogger("clips")


class SegmentBuffer:
    def __init__(self, path=None):
        self.path = path or config.BUFFER_DIR

    def segments(self):
        """[(inicio, fin, ruta)] ordenados. El último (en escritura) tiene fin=None."""
        items = []
        for p in glob.glob(os.path.join(self.path, "seg_*.ts")):
            try:
                items.append((int(os.path.basename(p)[4:-3]), p))
            except ValueError:
                continue
        items.sort()
        out = []
        for i, (t, p) in enumerate(items):
            end = items[i + 1][0] if i + 1 < len(items) else None
            out.append((t, end, p))
        return out

    def cleanup(self, keep_from):
        for start, end, p in self.segments():
            if end is not None and end < keep_from:
                try:
                    os.remove(p)
                except OSError:
                    pass

    def clear(self):
        for _, _, p in self.segments():
            try:
                os.remove(p)
            except OSError:
                pass

    def covering(self, a, b):
        """Trozos completos que solapan [a, b] y si ya está todo disponible."""
        segs = self.segments()
        done = [s for s in segs if s[1] is not None]
        sel = [s for s in done if s[0] < b and s[1] > a]
        ready = any(s[0] >= b for s in segs)
        return sel, ready


class EpisodeRecorder:
    """Decide cuándo empieza/acaba un episodio y encarga los clips al ClipWriter."""

    def __init__(self, cfg, writer):
        self.cfg = cfg
        self.writer = writer
        self.recent = deque()
        self.ep = None

    def configure(self, cfg):
        self.cfg = cfg

    def needed_from(self):
        return self.ep["chunk_start"] if self.ep else None

    def feed(self, ts, active, allow_start):
        e = self.cfg["episodios"]
        if self.ep is None:
            self.recent.append((ts, active))
            while self.recent and self.recent[0][0] <= ts - 5:
                self.recent.popleft()
            if allow_start and sum(a for _, a in self.recent) >= e["min_seg_activos"]:
                first = min(t for t, a in self.recent if a)
                self.ep = {"start": first, "last_act": ts,
                           "chunk_start": first - e["pre_s"], "part": 1}
                self.recent.clear()
                log.info("Episodio iniciado %s", _hms(first))
            return
        ep = self.ep
        if active:
            ep["last_act"] = ts
        maxlen = e["max_clip_s"]
        # episodio largo: trocear
        while ts - ep["chunk_start"] >= maxlen:
            self._emit(ep["chunk_start"], ep["chunk_start"] + maxlen, final=False)
        # fin: calma durante calma_fin_s + post_s (si vuelve la actividad durante el
        # post, sigue siendo el mismo episodio y el mismo clip)
        if ts - ep["last_act"] >= e["calma_fin_s"] + e["post_s"]:
            ep["end"] = ep["last_act"] + 1
            clip_end = ep["end"] + e["post_s"]
            while clip_end - ep["chunk_start"] > maxlen:
                self._emit(ep["chunk_start"], ep["chunk_start"] + maxlen, final=False)
            self._emit(ep["chunk_start"], clip_end, final=True)
            log.info("Episodio terminado %s–%s", _hms(ep["start"]), _hms(ep["end"]))
            self.ep = None

    def _emit(self, a, b, final):
        ep = self.ep
        self.writer.submit({
            "ep_start": ep["start"], "ep_end": ep.get("end"),
            "clip_start": a, "clip_end": b, "part": ep["part"], "final": final,
        })
        ep["part"] += 1
        ep["chunk_start"] = b

    def flush(self):
        """Al parar: cerrar el episodio en curso."""
        if self.ep:
            ep = self.ep
            ep["end"] = ep["last_act"] + 1
            self._emit(ep["chunk_start"], min(ep["end"] + self.cfg["episodios"]["post_s"], int(time.time())), True)
            self.ep = None


def make_thumb(mp4, at=8):
    """Miniatura JPG junto al clip (portada en el panel)."""
    jpg = mp4[:-4] + ".jpg"
    try:
        subprocess.run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-ss", str(max(0, int(at))),
                        "-i", mp4, "-frames:v", "1", "-vf", "scale=640:-2", "-q:v", "5", jpg],
                       capture_output=True, timeout=60)
    except Exception:
        log.warning("No se pudo crear la miniatura de %s", mp4)
    return os.path.exists(jpg)


def _hms(ts):
    return datetime.fromtimestamp(ts, config.TZ).strftime("%H:%M:%S")


class ClipWriter(threading.Thread):
    """Hilo que espera a tener los trozos necesarios y los une en un MP4 (H.264 + AAC, sin recodificar vídeo)."""

    def __init__(self, buffer, on_clip):
        super().__init__(daemon=True, name="clips")
        self.buffer = buffer
        self.on_clip = on_clip
        self.q = queue.Queue()
        self.pending = []
        self.lock = threading.Lock()

    def submit(self, job):
        with self.lock:
            self.pending.append(job)
        self.q.put(job)

    def needed_from(self):
        with self.lock:
            return min((j["clip_start"] for j in self.pending), default=None)

    def run(self):
        while True:
            job = self.q.get()
            try:
                self._process(job)
            except Exception:
                log.exception("Error guardando clip")
            finally:
                with self.lock:
                    if job in self.pending:
                        self.pending.remove(job)

    def _process(self, job):
        deadline = time.time() + 90
        while True:
            segs, ready = self.buffer.covering(job["clip_start"], job["clip_end"])
            if ready or time.time() > deadline:
                break
            time.sleep(1)
        if not segs:
            log.warning("No hay vídeo en el búfer para %s", _hms(job["clip_start"]))
            return
        seg_start, seg_end = segs[0][0], segs[-1][1]
        # recortar a lo pedido (los trozos son de ~2 s); sin recodificar, el inicio
        # puede adelantarse hasta el fotograma clave anterior
        offset = max(0, job["clip_start"] - seg_start)
        start = seg_start + offset
        end = min(seg_end, job["clip_end"])
        day = datetime.fromtimestamp(start, config.TZ)
        folder = os.path.join(config.CLIPS_DIR, day.strftime("%Y-%m-%d"))
        os.makedirs(folder, exist_ok=True)
        name = day.strftime("%H%M%S") + (f"_p{job['part']}" if job["part"] > 1 or not job["final"] else "") + ".mp4"
        out = os.path.join(folder, name)
        tmp = os.path.join(config.BUFFER_DIR, "clip_tmp.mp4")
        cmd = ["ffmpeg", "-nostdin", "-y", "-loglevel", "error",
               "-ss", str(offset), "-i", "concat:" + "|".join(p for _, _, p in segs),
               "-t", str(max(1, end - start)),
               "-map", "0", "-c", "copy", "-bsf:a", "aac_adtstoasc",
               "-movflags", "+faststart", tmp]
        r = subprocess.run(cmd, capture_output=True, timeout=120)
        if r.returncode != 0 or not os.path.exists(tmp):
            log.error("ffmpeg no pudo crear el clip: %s", r.stderr.decode()[-300:])
            return
        shutil.move(tmp, out)
        make_thumb(out, min(max(0, job["ep_start"] - start + 1), max(0, end - start - 1)))
        rel = os.path.relpath(out, config.CLIPS_DIR)
        log.info("Clip guardado %s (%d s, %.1f MB)", rel, end - start, os.path.getsize(out) / 1e6)
        self.on_clip(job, rel, start, end, os.path.getsize(out))
