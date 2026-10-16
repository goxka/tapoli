"""Una única conexión RTSP (ffmpeg) que alimenta todo:
  · vídeo reducido en gris a N fps  -> tubería para el detector de movimiento
  · audio PCM 8 kHz mono            -> tubería para el analizador de sonido
  · vídeo original (copia) + audio AAC en trozos de 2 s -> búfer circular en RAM (tmpfs)
Si la cámara se cae o rechaza la conexión, reintenta con espera creciente.
"""
import logging
import os
import re
import subprocess
import threading
import time

from . import config

log = logging.getLogger("captura")

BACKOFF = [5, 10, 20, 40, 60, 120, 300]   # de noche el máximo es 60 s (ver max_wait)


def rtsp_url():
    # Con go2rtc, la única conexión a la cámara es la suya y aquí se lee en local
    if os.environ.get("RTSP_SOURCE"):
        return os.environ["RTSP_SOURCE"]
    user = os.environ["CAM_USER"]
    pw = os.environ["CAM_PASS"]
    from urllib.parse import quote
    return "rtsp://{}:{}@{}:{}/{}".format(
        quote(user, safe=""), quote(pw, safe=""),
        os.environ["CAM_IP"], os.environ.get("CAM_PORT", "554"),
        os.environ.get("CAM_STREAM", "stream2"),
    )


def redact(s):
    pw = os.environ.get("CAM_PASS", "")
    if pw:
        s = s.replace(pw, "***")
        from urllib.parse import quote
        s = s.replace(quote(pw, safe=""), "***")
    return s


def _read_exact(f, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = f.read(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return bytes(buf)


def restart_go2rtc():
    import urllib.request
    try:
        urllib.request.urlopen(urllib.request.Request(
            os.environ.get("GO2RTC_API", "http://127.0.0.1:1984") + "/api/restart", method="POST"), timeout=10)
        log.warning("Vídeo congelado: go2rtc reiniciado para reconectar con la cámara")
    except Exception as e:
        log.warning("No se pudo reiniciar go2rtc: %s", e)


class Capture:
    """Lanza ffmpeg y entrega fotogramas/audio mediante callbacks (llamados desde hilos lectores)."""

    def __init__(self, cfg, on_frame, on_audio, on_state):
        self.cfg = cfg
        self.on_frame = on_frame
        self.on_audio = on_audio
        self.on_state = on_state
        self.proc = None
        self.stop = threading.Event()
        self.last_frame = 0.0
        self.restart_requested = threading.Event()
        self.stalls = 0
        os.makedirs(config.BUFFER_DIR, exist_ok=True)

    def _cmd(self, fd_v, fd_a):
        v = self.cfg["video"]
        seg = os.path.join(config.BUFFER_DIR, "seg_%s.ts")
        return [
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "warning",
            "-rtsp_transport", "tcp", "-timeout", "10000000",
            # la Tapo manda marcas de tiempo irregulares: usar el reloj al recibir
            "-fflags", "+genpts+discardcorrupt", "-use_wallclock_as_timestamps", "1",
            "-avoid_negative_ts", "make_zero",
            "-i", rtsp_url(),
            # 1) análisis de movimiento
            "-map", "0:v:0", "-an",
            "-vf", f"fps={v['fps']},scale={v['ancho']}:{v['alto']}:flags=area,format=gray",
            "-f", "rawvideo", f"pipe:{fd_v}",
            # 2) análisis de sonido
            "-map", "0:a:0", "-vn", "-af", "aresample=async=1", "-ac", "1", "-ar", "8000",
            "-f", "s16le", f"pipe:{fd_a}",
            # 3) búfer circular para los clips (sin recodificar el vídeo)
            "-map", "0:v:0", "-map", "0:a:0", "-c:v", "copy",
            "-af", "aresample=async=1", "-c:a", "aac", "-ar", "16000", "-ac", "1", "-b:a", "32k",
            "-f", "segment", "-segment_time", "2", "-segment_format", "mpegts",
            "-strftime", "1", seg,
        ]

    def run_forever(self):
        attempt = 0
        while not self.stop.is_set():
            started = time.time()
            self.on_state("conectando", None)
            ok, err = self._run_once()
            ran = time.time() - started
            if self.stop.is_set():
                break
            if ran > 120:
                attempt = 0  # funcionó un rato: reiniciar la espera
            # cámara "congelada" (conexión abierta pero sin vídeo) dos veces seguidas:
            # forzar a go2rtc a reconectar con la cámara (como "recargar" en la app de Tapo)
            self.stalls = self.stalls + 1 if err and "sin vídeo" in err else 0
            if self.stalls >= 2 and os.environ.get("RTSP_SOURCE"):
                self.stalls = 0
                restart_go2rtc()
            delay = min(BACKOFF[min(attempt, len(BACKOFF) - 1)], self.max_wait())
            attempt += 1
            msg = err or "conexión cerrada"
            log.warning("Cámara desconectada (%s). Reintento en %d s", msg, delay)
            self.on_state("desconectada", f"{msg} · reintento en {delay} s")
            self.stop.wait(delay)

    def max_wait(self):
        """De noche (o 30 min antes) reintentar cada minuto: si la encendéis al acostarla,
        hay que verlo enseguida. De día, cada 5 min basta."""
        now = time.time()
        if config.night_for_ts(self.cfg, now) or config.night_for_ts(self.cfg, now + 1800):
            return 60
        return 300

    def _run_once(self):
        rv, wv = os.pipe()
        ra, wa = os.pipe()
        cmd = self._cmd(wv, wa)
        log.info("Abriendo RTSP: %s", redact(rtsp_url()))
        try:
            self.proc = subprocess.Popen(
                cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE, pass_fds=(wv, wa),
            )
        except OSError as e:
            for fd in (rv, wv, ra, wa):
                os.close(fd)
            return False, str(e)
        os.close(wv)
        os.close(wa)
        self.last_frame = time.time()
        errors = []
        v = self.cfg["video"]
        frame_size = v["ancho"] * v["alto"]
        connected = threading.Event()

        def read_video():
            with os.fdopen(rv, "rb", buffering=0) as f:
                while True:
                    data = _read_exact(f, frame_size)
                    if data is None:
                        return
                    self.last_frame = time.time()
                    if not connected.is_set():
                        connected.set()
                        self.on_state("conectada", None)
                    try:
                        self.on_frame(self.last_frame, data)
                    except Exception:
                        log.exception("Error procesando fotograma")

        def read_audio():
            with os.fdopen(ra, "rb", buffering=0) as f:
                while True:
                    data = f.read(4000)  # 250 ms
                    if not data:
                        return
                    try:
                        self.on_audio(time.time(), data)
                    except Exception:
                        log.exception("Error procesando audio")

        def read_err():
            for line in self.proc.stderr:
                line = redact(line.decode("utf-8", "replace").strip())
                if not line:
                    continue
                errors.append(line)
                del errors[:-5]
                if any(x in line for x in ("Non-monotonous", "Past duration", "Timestamps are unset", "non monotonically")):
                    continue
                log.info("ffmpeg: %s", line)

        threads = [threading.Thread(target=t, daemon=True) for t in (read_video, read_audio, read_err)]
        for t in threads:
            t.start()

        # vigilante: si no llegan fotogramas en 30 s, cortar y reconectar
        while self.proc.poll() is None:
            if self.stop.wait(2):
                break
            if time.time() - self.last_frame > 30:
                errors.append("sin vídeo durante 30 s")
                break
            if self.restart_requested.is_set():
                self.restart_requested.clear()
                errors.append("reinicio por cambio de configuración")
                break
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        for t in threads:
            t.join(5)
        err = errors[-1] if errors else None
        if err:
            err = re.sub(r"^\S*rtsp://\S+:\s+", "", err)
            m = re.search(r"(\d{3} [A-Za-z ]+)", err)
            if m and ("401" in err or "454" in err or "503" in err):
                err = m.group(1).strip()
        return connected.is_set(), err

    def shutdown(self):
        self.stop.set()
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
