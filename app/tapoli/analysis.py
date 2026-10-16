"""Análisis de la noche: despertar ≠ moverse.

Trabaja sobre las métricas por segundo guardadas en SQLite, así que se puede
reprocesar cualquier noche con otra configuración.
"""
import itertools
import json
import logging
import time
import warnings
from datetime import date, datetime, timedelta

import numpy as np

from . import config

log = logging.getLogger("analisis")


# ----------------------------------------------------------------------------
# Datos
# ----------------------------------------------------------------------------
def load_seconds(con, a, b):
    """Arrays alineados a cada segundo de [a, b). NaN / -1 donde no hay datos."""
    n = b - a
    motion = np.full(n, np.nan)
    out = np.full(n, np.nan)
    sound = np.full(n, np.nan)
    voice = np.full(n, -1, dtype=np.int8)
    rows = con.execute(
        "SELECT ts, motion, motion_out, sound_db, voice FROM seconds WHERE ts>=? AND ts<?", (a, b)
    ).fetchall()
    if rows:
        arr = np.array([(r[0], r[1] if r[1] is not None else np.nan, r[2] if r[2] is not None else np.nan,
                         r[3] if r[3] is not None else np.nan, r[4] if r[4] is not None else -1) for r in rows])
        idx = arr[:, 0].astype(np.int64) - a
        motion[idx] = arr[:, 1]
        out[idx] = arr[:, 2]
        sound[idx] = arr[:, 3]
        voice[idx] = arr[:, 4].astype(np.int8)
    return {"a": a, "motion": motion, "out": out, "sound": sound, "voice": voice}


def minute_features(sec, M, thr):
    """Agrega por minuto los M primeros minutos."""
    n = M * 60
    m = sec["motion"][:n].reshape(M, 60)
    v = sec["voice"][:n].reshape(M, 60)
    with np.errstate(invalid="ignore"):
        active_secs = (m > thr).sum(axis=1)
    n_secs = (~np.isnan(m)).sum(axis=1)
    voice_secs = (v == 1).sum(axis=1)
    mx = np.where(np.isnan(m), 0, m).max(axis=1)
    o = sec["out"][:n].reshape(M, 60)
    out_max = np.where(np.isnan(o), 0, o).max(axis=1)
    return {"active_secs": active_secs, "voice_secs": voice_secs, "n_secs": n_secs,
            "has_data": n_secs >= 30, "still": (n_secs >= 30) & (mx < 0.0005), "out_max": out_max}


def absences(feats, M, cfg):
    """Tramos [ini, fin) con la cuna vacía.

    Un bebé en la cuna nunca pasa mucho tiempo sin NINGÚN cambio en la imagen (medido:
    máx. ~30 min). Cuna vacía = quietud absoluta de ≥ cuna_vacia_min tras ver a un adulto
    (movimiento fuera de la cuna) en los 15 min anteriores, o ≥ cuna_vacia_sin_salida_min
    sin haberlo visto. Termina con el siguiente movimiento en la cuna."""
    n = cfg["noche"]
    L = n.get("cuna_vacia_min", 20)
    L_no_exit = n.get("cuna_vacia_sin_salida_min", 90)
    thr_adult = cfg["movimiento"].get("umbral_adulto", 0.05)
    still, out = feats["still"][:M], feats["out_max"][:M]
    res, m = [], 0
    while m < M:
        if not still[m]:
            m += 1
            continue
        j = m
        while j < M and still[j]:
            j += 1
        exits = [k for k in range(max(0, m - 15), m + 1) if out[k] >= thr_adult]
        if exits and j - m >= L:
            res.append([exits[-1], j])
        elif j - m >= L_no_exit:
            res.append([m, j])
        m = j
    return res


def minute_rows(sec, a, M, thr):
    """Filas para la tabla minutes."""
    f = minute_features(sec, M, thr)
    rows = []
    for i in range(M):
        sl = slice(i * 60, (i + 1) * 60)
        if f["n_secs"][i] == 0:
            continue
        mo, ou, so = sec["motion"][sl], sec["out"][sl], sec["sound"][sl]
        with np.errstate(invalid="ignore"), warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            rows.append((a + i * 60, int(f["n_secs"][i]),
                         _f(np.nanmean(mo)), _f(np.nanmax(mo)), int(f["active_secs"][i]),
                         _f(np.nanmax(ou)), _f(np.nanmean(so)), _f(np.nanmax(so)),
                         int(f["voice_secs"][i])))
    return rows


def _f(x):
    x = float(x)
    return None if np.isnan(x) else round(x, 5)


def save_minutes(con, rows):
    con.executemany(
        "INSERT OR REPLACE INTO minutes(ts,n_secs,motion_mean,motion_max,active_secs,out_max,"
        "sound_mean_db,sound_max_db,voice_secs) VALUES(?,?,?,?,?,?,?,?,?)", rows)


# ----------------------------------------------------------------------------
# Algoritmo
# ----------------------------------------------------------------------------
def params_from_cfg(cfg):
    d = cfg["despertar"]
    return {
        "sensibilidad": d["sensibilidad"],
        "K": d["seg_activos_minuto"], "V": d["voz_seg_minuto"],
        "X": d["x_minutos_activos"], "Y": d["y_ventana_min"],
        "Z": d["z_calma_min"], "N": d["n_fusion_min"],
        "voz_dispara": d["voz_dispara"], "voz_sola": d["voz_sola_min"],
    }


def detect_wakes(active, voiced, P):
    """Lista de despertares [(ini, fin)) en minutos.

    Empieza: en la ventana de Y min hay ≥ X minutos activos, o un minuto activo con voz,
    o ≥ voz_sola minutos con voz. Termina tras Z minutos de calma. Se fusionan si
    los separa menos de N minutos.
    """
    X, Y, Z, N = P["X"], P["Y"], P["Z"], P["N"]
    M = len(active)
    wakes = []
    awake = False
    start = last = 0
    calm = 0
    for m in range(M):
        act = active[m]
        voi = voiced[m]
        if not awake:
            lo = max(0, m - Y + 1)
            if not (act or voi):
                continue
            n_act = sum(active[lo:m + 1])
            n_voi = sum(voiced[lo:m + 1])
            if n_act >= X or (P["voz_dispara"] and act and voi) or n_voi >= P["voz_sola"]:
                awake = True
                start = next(i for i in range(lo, m + 1) if active[i] or voiced[i])
                last = m
                calm = 0
        else:
            if act or voi:
                last = m
                calm = 0
            else:
                calm += 1
                if calm >= Z:
                    wakes.append([start, last + 1])
                    awake = False
    if awake:
        wakes.append([start, last + 1])
    return _merge(wakes, N)


def _merge(wakes, N):
    merged = []
    for w in sorted(wakes):
        if merged and w[0] - merged[-1][1] < N:
            merged[-1][1] = max(merged[-1][1], w[1])
        else:
            merged.append(list(w))
    return merged


def run_night(feats, M, P, cfg, a, finished, labels=None):
    """Calcula dormirse/despertar final/despertares nocturnos sobre M minutos.

    labels: [(min_ini, min_fin, etiqueta)] de tus clips etiquetados. Mandan sobre el algoritmo:
      dormida → ese movimiento no cuenta para despertar (sí como inquietud)
      despierta → ese tramo es un despertar
      falsa → ese movimiento se ignora del todo
    Sin labels es la opinión pura del algoritmo (la que se usa para calibrar).
    """
    active = (feats["active_secs"][:M] >= P["K"]).tolist()
    voiced = (feats["voice_secs"][:M] >= P["V"]).tolist()
    has_data = feats["has_data"][:M]
    moving = active[:]                     # para la inquietud
    some = (feats["active_secs"][:M] > 0).tolist()
    forced = []
    for s_min, e_min, lab in labels or []:
        lo, hi = max(0, s_min), min(M, e_min)
        for m in range(lo, hi):
            if lab in ("dormida", "falsa"):
                active[m] = voiced[m] = False
            if lab == "falsa":
                moving[m] = some[m] = False
        if lab == "despierta" and hi > lo:
            forced.append([lo, hi])
    # cuna vacía solo cuenta después de acostarla (antes, que esté vacía es lo normal)
    first_in = next((i for i in range(M) if some[i] or voiced[i]), None)
    away = [x for x in absences(feats, M, cfg) if first_in is not None and x[0] > first_in]
    absent = np.zeros(M, bool)
    for s_min, e_min in away:
        absent[s_min:e_min] = True
    wakes = detect_wakes(active, voiced, P)
    if forced:
        wakes = _merge(wakes + forced, P["N"])

    # presencia: primer minuto con algo de actividad en la cuna
    any_act = np.array(some, dtype=bool) | np.array(voiced, dtype=bool)
    idx = np.flatnonzero(any_act)
    onset = None
    D = cfg["noche"]["dormirse_calma_min"]
    if len(idx):
        calm = (~np.array(active, bool)) & (~np.array(voiced, bool)) & has_data & ~absent
        run = 0
        for t in range(int(idx[0]), M):
            run = run + 1 if calm[t] else 0
            if run >= D:
                onset = t - D + 1
                break

    final_from = _minute_of(cfg, a, cfg["noche"]["despertar_final_desde"])
    final_wake = None
    sleep_end = None
    if onset is not None:
        later = [w for w in wakes if w[0] >= onset]
        if later and later[-1][0] >= final_from:
            final_wake = later[-1]
            sleep_end = final_wake[0]
        else:
            sleep_end = M  # sigue dormida (o fin de la ventana)
        # si al final la cuna se queda vacía, el sueño (en la cuna) acaba al sacarla
        tail = [x for x in away if x[1] >= M and x[0] >= onset]
        if tail and tail[0][0] < sleep_end:
            sleep_end = tail[0][0]
    return {
        "M": M, "active": active, "moving": moving, "voiced": voiced, "has_data": has_data,
        "wakes": wakes, "onset": onset, "sleep_end": sleep_end, "away": away, "absent": absent,
        "bed": int(idx[0]) if len(idx) else None,   # primer movimiento en la cuna (acostarla)
        "final_wake": final_wake, "finished": finished,
    }


def _minute_of(cfg, a, hm):
    """Minuto (desde el inicio de la ventana) en que es la hora hm."""
    t = config.parse_hm(hm)
    start = datetime.fromtimestamp(a, config.TZ)
    target = start.replace(hour=t.hour, minute=t.minute, second=0)
    if target < start:
        target += timedelta(days=1)
    return int((target.timestamp() - a) // 60)


def classify(res, s_min, e_min):
    """Clasificación de un episodio [s_min, e_min) → ('despierta'|'dormida', motivo)."""
    for x in res.get("away", []):
        if s_min >= x[0] and e_min <= x[1] + 1:
            return "despierta", "fuera de la cuna"
    for w in res["wakes"]:
        if s_min < w[1] and e_min > w[0]:
            if res["final_wake"] is w:
                return "despierta", "despertar de la mañana"
            return "despierta", "dentro de un despertar"
    if res["onset"] is None:
        return "despierta", "aún no se ha dormido"
    if e_min <= res["onset"]:
        return "despierta", "antes de dormirse"
    if res["sleep_end"] is not None and s_min >= res["sleep_end"] and res["final_wake"]:
        return "despierta", "después del despertar de la mañana"
    return "dormida", "movimiento breve durante el sueño"


def summarize(res, a, cfg):
    """Métricas de la noche a partir del resultado de run_night."""
    onset, end = res["onset"], res["sleep_end"]
    out = {"onset": None, "end": None, "n_wakes": 0, "min_awake": 0, "min_sleep": 0,
           "restlessness": None, "efficiency": None, "wakes": [], "nodata_min": 0}
    allw = []
    for w in res["wakes"]:
        kind = "noche"
        if res["final_wake"] is w:
            kind = "final"
        elif onset is None or w[1] <= onset:
            kind = "antes"
        elif end is not None and w[0] >= end:
            kind = "despues"
        allw.append({"start": a + w[0] * 60, "end": a + w[1] * 60, "min": w[1] - w[0], "kind": kind})
    for x in res.get("away", []):
        allw.append({"start": a + x[0] * 60, "end": a + x[1] * 60, "min": x[1] - x[0], "kind": "fuera"})
    allw.sort(key=lambda w: w["start"])
    out["wakes"] = allw
    if onset is None:
        return out
    hd = res["has_data"]
    act = np.array(res.get("moving", res["active"]), bool)
    in_wake = np.zeros(res["M"], bool)
    night_w = [w for w in res["wakes"] if w[0] < end and w[1] > onset and w is not res["final_wake"]]
    for w in night_w:
        in_wake[max(w[0], onset):min(w[1], end)] = True
    period = slice(onset, end)
    absent = res.get("absent", np.zeros(res["M"], bool))
    in_wake &= ~absent
    away = int(absent[period].sum())
    nodata = int((~hd[period] & ~in_wake[period] & ~absent[period]).sum())
    awake = int(in_wake[period].sum())
    sleep = max(0, (end - onset) - awake - nodata - away)
    restless = int((act[period] & ~in_wake[period] & hd[period] & ~absent[period]).sum())
    out.update({
        "onset": a + onset * 60, "end": a + end * 60,
        "n_wakes": len(night_w), "min_awake": awake, "min_sleep": sleep,
        "restlessness": round(restless / (sleep / 60), 2) if sleep >= 30 else None,
        "efficiency": round(sleep / max(1, (end - onset) - nodata - away), 3),
        "nodata_min": nodata, "away_min": away,
        "bed": a + res["bed"] * 60 if res.get("bed") is not None else None,
        "latency": onset - res["bed"] if res.get("bed") is not None else None,
    })
    return out


# ----------------------------------------------------------------------------
# Noches
# ----------------------------------------------------------------------------
def analyze_night(con, cfg, night_date, now=None, config_id=None, write=True):
    now = int(now or time.time())
    a, b = config.night_window(cfg, night_date)
    if now <= a:
        return None
    finished = now >= b
    M = (min(now, b) - a) // 60
    if M <= 0:
        return None
    sec = load_seconds(con, a, a + M * 60)
    if np.isnan(sec["motion"]).all():
        return None
    P = params_from_cfg(cfg)
    thr = config.motion_threshold(cfg)
    feats = minute_features(sec, M, thr)
    algo = run_night(feats, M, P, cfg, a, finished)           # opinión del algoritmo
    labels = [(*_clip_minutes(r["ep_start"], r["ep_end"], a), r["label"]) for r in con.execute(
        "SELECT ep_start, ep_end, label FROM clips WHERE night=? AND label IS NOT NULL",
        (night_date.isoformat(),)).fetchall()]
    res = run_night(feats, M, P, cfg, a, finished, labels) if labels else algo  # con tus etiquetas
    summ = summarize(res, a, cfg)
    if write:
        _write_night(con, night_date, a, b, summ, finished, config_id)
        _classify_clips(con, night_date, a, algo, P)
    return summ


def _write_night(con, night_date, a, b, s, finished, config_id):
    con.execute(
        "INSERT INTO nights(night,start_ts,end_ts,sleep_onset,sleep_end,n_wakes,min_awake,min_sleep,"
        "restlessness,efficiency,wakes_json,extra_json,final,config_id,updated) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(night) DO UPDATE SET "
        "start_ts=excluded.start_ts,end_ts=excluded.end_ts,sleep_onset=excluded.sleep_onset,"
        "sleep_end=excluded.sleep_end,n_wakes=excluded.n_wakes,min_awake=excluded.min_awake,"
        "min_sleep=excluded.min_sleep,restlessness=excluded.restlessness,efficiency=excluded.efficiency,"
        "wakes_json=excluded.wakes_json,extra_json=excluded.extra_json,final=excluded.final,"
        "config_id=excluded.config_id,updated=excluded.updated",
        (night_date.isoformat(), a, b, s["onset"], s["end"], s["n_wakes"], s["min_awake"],
         s["min_sleep"], s["restlessness"], s["efficiency"], json.dumps(s["wakes"]),
         json.dumps({"nodata_min": s["nodata_min"], "away_min": s.get("away_min", 0),
                    "bed": s.get("bed"), "latency": s.get("latency")}), 1 if finished else 0, config_id,
         int(time.time())))
    con.commit()


OBVIOUS = ("antes de dormirse", "aún no se ha dormido", "después del despertar de la mañana", "fuera de la cuna")


def _classify_clips(con, night_date, a, res, P=None):
    """Opinión del algoritmo por clip y si merece revisión (solo los dudosos)."""
    rows = con.execute("SELECT id, ep_start, ep_end, motion_max, active_secs, voice_secs FROM clips WHERE night=?",
                       (night_date.isoformat(),)).fetchall()
    active = res["active"]
    best_in_wake = {}
    out = []
    for r in rows:
        s_min, e_min = _clip_minutes(r["ep_start"], r["ep_end"], a)
        cls, why = classify(res, s_min, e_min)
        review = 0
        if why in OBVIOUS:
            review = 0
        elif cls == "despierta":
            # un clip por despertar: el de más movimiento, para confirmarlo
            w = next((tuple(w) for w in res["wakes"] if s_min < w[1] and e_min > w[0]), None)
            score = (r["active_secs"] or 0) + 5 * (r["voice_secs"] or 0)
            if w is not None and (w not in best_in_wake or score > best_in_wake[w][1]):
                best_in_wake[w] = (r["id"], score)
        else:
            # dormida, pero dudosa: con voz, movimiento largo, o casi dispara un despertar
            near = False
            if P:
                for m in range(max(0, s_min), min(len(active), e_min)):
                    lo = max(0, m - P["Y"] + 1)
                    if sum(active[lo:m + 1]) >= max(1, P["X"] - 1) and active[m]:
                        near = True
                        break
            review = 1 if ((r["voice_secs"] or 0) >= 2 or (r["active_secs"] or 0) >= 20 or near) else 0
        out.append([r["id"], cls, why, review, r["ep_start"], (r["active_secs"] or 0) + 5 * (r["voice_secs"] or 0)])
    # dudosos muy seguidos (menos de 5 min entre sí) son la misma duda: pedir solo el más claro
    doubtful = sorted([o for o in out if o[1] == "dormida" and o[3]], key=lambda o: o[4])
    group = []
    for o in doubtful + [None]:
        if group and (o is None or o[4] - group[-1][4] >= 300):
            keep = max(group, key=lambda g: g[5])
            for g in group:
                g[3] = 1 if g is keep else 0
            group = []
        if o is not None:
            group.append(o)
    chosen = {v[0] for v in best_in_wake.values()}
    for cid, cls, why, review, _, _ in out:
        if cls == "despierta" and why not in OBVIOUS:
            review = 1 if cid in chosen else 0
        con.execute("UPDATE clips SET algo=?, algo_reason=?, review=? WHERE id=?", (cls, why, review, cid))
    con.commit()


def _clip_minutes(ep_start, ep_end, a):
    s_min = int((ep_start - a) // 60)
    e_min = int((max(ep_end, ep_start + 1) - 1 - a) // 60) + 1
    return s_min, e_min


def nights_with_data(con, cfg):
    r = con.execute("SELECT MIN(ts), MAX(ts) FROM seconds").fetchone()
    if not r or r[0] is None:
        return []
    first = config.night_for_ts(cfg, r[0]) or datetime.fromtimestamp(r[0], config.TZ).date()
    last = config.night_for_ts(cfg, r[1]) or datetime.fromtimestamp(r[1], config.TZ).date()
    out = []
    d = first
    while d <= last:
        out.append(d)
        d += timedelta(days=1)
    return out


def reprocess_all(con, cfg, config_id=None, progress=None):
    """Recalcula minutos, noches y clasificaciones con la configuración dada."""
    nights = nights_with_data(con, cfg)
    thr = config.motion_threshold(cfg)
    for i, d in enumerate(nights):
        a, b = config.night_window(cfg, d)
        end = min(b, int(time.time()))
        M = (end - a) // 60
        if M > 0:
            sec = load_seconds(con, a, a + M * 60)
            save_minutes(con, minute_rows(sec, a, M, thr))
            con.commit()
        analyze_night(con, cfg, d, config_id=config_id)
        if progress:
            progress((i + 1) / max(1, len(nights)))
    return len(nights)


# ----------------------------------------------------------------------------
# Calibración
# ----------------------------------------------------------------------------
GRID = {
    "sensibilidad": [3, 4, 5, 6, 7],
    "X": [2, 3, 4, 5],
    "Y": [3, 5, 8, 10],
    "Z": [3, 5, 8],
    "N": [5, 10, 15],
}
KEYMAP = {"sensibilidad": "despertar.sensibilidad", "X": "despertar.x_minutos_activos",
          "Y": "despertar.y_ventana_min", "Z": "despertar.z_calma_min", "N": "despertar.n_fusion_min"}


def _score(tp, fp, fn, tn):
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    n = tp + fp + fn + tn
    return {"precision": round(prec, 3), "sensibilidad": round(rec, 3), "f1": round(f1, 3),
            "exactitud": round((tp + tn) / n, 3) if n else 0.0,
            "tp": tp, "fp": fp, "fn": fn, "tn": tn, "n": n}


def calibrate(con, cfg, progress=None):
    labeled = con.execute(
        "SELECT id, night, ep_start, ep_end, label FROM clips WHERE label IS NOT NULL").fetchall()
    if not labeled:
        return {"error": "Todavía no hay clips etiquetados."}
    by_night = {}
    for r in labeled:
        by_night.setdefault(r["night"], []).append(r)
    now = int(time.time())
    nights = []
    for nd, clips in by_night.items():
        d = date.fromisoformat(nd)
        a, b = config.night_window(cfg, d)
        M = (min(now, b) - a) // 60
        if M <= 0:
            continue
        sec = load_seconds(con, a, a + M * 60)
        if np.isnan(sec["motion"]).all():
            continue
        truth = [(_clip_minutes(c["ep_start"], c["ep_end"], a), c["label"] == "despierta") for c in clips]
        nights.append((a, M, now >= b, sec, truth))
    if not nights:
        return {"error": "Los clips etiquetados no tienen datos por segundo guardados."}

    base = params_from_cfg(cfg)
    current = {"sensibilidad": base["sensibilidad"], "X": base["X"], "Y": base["Y"], "Z": base["Z"], "N": base["N"]}
    combos = [c for c in itertools.product(*GRID.values()) if c[1] <= c[2]]
    cur_tuple = tuple(current[k] for k in GRID)
    if cur_tuple not in combos:
        combos.append(cur_tuple)
    results = []
    feats_cache = {}
    for i, combo in enumerate(combos):
        p = dict(zip(GRID.keys(), combo))
        P = dict(base, **p)
        if p["sensibilidad"] not in feats_cache:
            thr = config.motion_threshold(cfg, p["sensibilidad"])
            feats_cache[p["sensibilidad"]] = [minute_features(n[3], n[1], thr) for n in nights]
        tp = fp = fn = tn = 0
        for (a, M, fin, _sec, truth), feats in zip(nights, feats_cache[p["sensibilidad"]]):
            res = run_night(feats, M, P, cfg, a, fin)
            for (s_min, e_min), is_awake in truth:
                pred = classify(res, s_min, e_min)[0] == "despierta"
                if pred and is_awake:
                    tp += 1
                elif pred:
                    fp += 1
                elif is_awake:
                    fn += 1
                else:
                    tn += 1
        sc = _score(tp, fp, fn, tn)
        dist = sum(abs(p[k] - current[k]) / (max(GRID[k]) - min(GRID[k])) for k in GRID)
        results.append({"params": p, "metrics": sc, "dist": round(dist, 3)})
        if progress and i % 20 == 0:
            progress(i / len(combos))
    results.sort(key=lambda r: (-r["metrics"]["f1"], -r["metrics"]["exactitud"], r["dist"]))
    cur = next(r for r in results if tuple(r["params"][k] for k in GRID) == cur_tuple)
    n_pos = sum(t for n in nights for _, t in n[4])
    n_lab = sum(len(n[4]) for n in nights)
    avisos = []
    if n_lab < 15:
        avisos.append(f"Solo hay {n_lab} clips etiquetados: la propuesta es orientativa (mejor ≥ 30).")
    if n_pos < 3:
        avisos.append(f"Solo {n_pos} clips etiquetados como 'Despierta': hacen falta más para medir bien.")
    return {
        "best": results[0], "current": cur, "top": results[:8],
        "n_labels": n_lab, "n_awake": n_pos, "n_nights": len(nights),
        "n_combos": len(combos), "warnings": avisos,
        "keymap": KEYMAP,
    }
