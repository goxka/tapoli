"""Carga y modificación de config.yaml conservando los comentarios."""
import json
import os
import re
import threading
from datetime import datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

import yaml

TZ = ZoneInfo(os.environ.get("TZ", "Europe/Madrid"))
CONFIG_PATH = os.environ.get("TAPOLI_CONFIG", "/config/config.yaml")
DATA_DIR = os.environ.get("TAPOLI_DATA", "/data")
CLIPS_DIR = os.environ.get("TAPOLI_CLIPS", "/clips")
BUFFER_DIR = os.environ.get("TAPOLI_BUFFER", "/buffer")

# Parámetros que entran en la calibración (sección, clave)
CALIB_KEYS = [
    ("despertar", "sensibilidad"),
    ("despertar", "x_minutos_activos"),
    ("despertar", "y_ventana_min"),
    ("despertar", "z_calma_min"),
    ("despertar", "n_fusion_min"),
]

_lock = threading.Lock()


DEFAULT_PATH = os.path.join(os.path.dirname(__file__), "config.default.yaml")


def load():
    if not os.path.exists(CONFIG_PATH):          # primera vez: copiar la configuración por defecto
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(DEFAULT_PATH, encoding="utf-8") as src, open(CONFIG_PATH, "w", encoding="utf-8") as dst:
            dst.write(src.read())
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def mtime():
    try:
        return os.path.getmtime(CONFIG_PATH)
    except OSError:
        return 0


def _fmt(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, float):
        return repr(round(v, 6))
    if isinstance(v, (list, tuple)):
        return json.dumps(v)
    return str(v)


def set_values(changes):
    """changes: {(seccion, clave): valor}. Reescribe solo esas líneas."""
    with _lock:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            lines = f.read().split("\n")
        section = None
        pending = dict(changes)
        for i, line in enumerate(lines):
            m = re.match(r"^([A-Za-z_]\w*):\s*(#.*)?$", line)
            if m:
                section = m.group(1)
                continue
            m = re.match(r"^(\s+)([A-Za-z_]\w*):\s*(.*?)(\s+#.*)?$", line)
            if not m or (section, m.group(2)) not in pending:
                continue
            indent, key, _old, comment = m.groups()
            new = f"{indent}{key}: {_fmt(pending.pop((section, key)))}"
            if comment:
                col = len(line) - len(comment.lstrip())
                new += " " * max(1, col - len(new)) + comment.lstrip()
            lines[i] = new
        if pending:
            raise KeyError(f"Claves no encontradas en config.yaml: {list(pending)}")
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        # validar antes de sustituir
        with open(tmp, encoding="utf-8") as f:
            yaml.safe_load(f)
        os.replace(tmp, CONFIG_PATH)


def calib_values(cfg):
    return {f"{s}.{k}": cfg[s][k] for s, k in CALIB_KEYS}


def motion_threshold(cfg, sensibilidad=None):
    s = cfg["despertar"]["sensibilidad"] if sensibilidad is None else sensibilidad
    return cfg["movimiento"]["umbral_base"] * 1.5 ** (5 - float(s))


def parse_hm(s):
    h, m = str(s).split(":")
    return dtime(int(h), int(m))


def night_window(cfg, night_date):
    """Devuelve (inicio_ts, fin_ts) epoch de la noche que empieza la tarde de night_date."""
    start_t = parse_hm(cfg["noche"]["inicio"])
    end_t = parse_hm(cfg["noche"]["fin"])
    start = datetime.combine(night_date, start_t, TZ)
    end_day = night_date + timedelta(days=1) if end_t <= start_t else night_date
    end = datetime.combine(end_day, end_t, TZ)
    return int(start.timestamp()), int(end.timestamp())


def night_for_ts(cfg, ts):
    """Fecha (date) de la noche que contiene ts, o None si ts está fuera de la ventana."""
    dt = datetime.fromtimestamp(ts, TZ)
    for d in (dt.date(), dt.date() - timedelta(days=1)):
        a, b = night_window(cfg, d)
        if a <= ts < b:
            return d
    return None


def env_bool(name, default=False):
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "si", "sí")
