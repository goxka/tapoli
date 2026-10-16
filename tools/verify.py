#!/usr/bin/env python3
"""Revisión automática tras cada publicación: comprueba en el NAS que todo funciona.

Uso: python3 tools/verify.py [--since EPOCH]
Sale con código 1 si algo falla.
"""
import argparse
import asyncio
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
def _env():
    """Lee .env (PANEL_URL, PORTAINER_URL, PORTAINER_ENDPOINT, PORTAINER_TOKEN_FILE)."""
    out = dict(os.environ)
    try:
        for line in open(os.path.join(ROOT, ".env"), encoding="utf-8"):
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.strip().split("=", 1)
                out.setdefault(k, v)
    except OSError:
        pass
    return out


ENV = _env()
PANEL = ENV.get("PANEL_URL", "http://localhost:8095").rstrip("/")
PORTAINER = f"{ENV.get('PORTAINER_URL', 'http://localhost:9000')}/api/endpoints/{ENV.get('PORTAINER_ENDPOINT', '1')}/docker"
TOKEN_FILE = os.path.expanduser(ENV.get("PORTAINER_TOKEN_FILE", "~/.tapoli-portainer-token"))
VIEWS = ["inicio", "noches", "directo", "revisar", "ajustes", "ajustes/noche", "ajustes/zona",
         "ajustes/umbrales", "ajustes/telegram", "calibrar"]

results = []


VERBOSE = "-v" in sys.argv     # por defecto solo se muestran los fallos (salida corta)


def check(name, ok, detail=""):
    results.append((ok, name, detail))
    if VERBOSE or not ok:
        print(("  ✅ " if ok else "  ❌ ") + name + (f" — {detail}" if detail else ""), flush=True)
    return ok


def get(url, headers=None, timeout=15):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


def get_json(path):
    st, body = get(PANEL + path)
    return st, json.loads(body)


def local_version():
    static = os.path.join(ROOT, "app", "tapoli", "static")
    h = hashlib.sha1()
    for f in sorted(os.listdir(static)):
        p = os.path.join(static, f)
        if os.path.isfile(p):
            with open(p, "rb") as fh:
                h.update(fh.read())
    return h.hexdigest()[:10]


def containers():
    tok = open(TOKEN_FILE).read().strip()
    _, body = get(PORTAINER + "/containers/json?all=1", {"X-API-Key": tok})
    out = {}
    for c in json.loads(body):
        n = c["Names"][0].lstrip("/")
        if n.startswith("tapoli-"):
            _, det = get(PORTAINER + f"/containers/{n}/json", {"X-API-Key": tok})
            d = json.loads(det)
            out[n] = {"state": c["State"], "status": c["Status"], "restarts": d["RestartCount"],
                      "health": (d["State"].get("Health") or {}).get("Status")}
    return out


async def browser_checks(status):
    from playwright.async_api import async_playwright
    nights = get_json("/api/nights")[1]
    views = VIEWS + ([f"noche/{nights[0]['night']}"] if nights else [])
    cam_on = (status.get("camara") or {}).get("estado") == "conectada"
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path="/usr/bin/chromium",
                                    args=["--no-sandbox", "--autoplay-policy=no-user-gesture-required"])
        for scheme in ("dark", "light"):
            ctx = await b.new_context(viewport={"width": 390, "height": 844}, color_scheme=scheme,
                                      is_mobile=True, has_touch=True)
            pg = await ctx.new_page()
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
            await pg.goto(PANEL + "/")
            await pg.wait_for_timeout(1200)
            bad = []
            for v in views:
                errs.clear()
                await pg.evaluate(f"nav('#{v}')")
                await pg.wait_for_timeout(1300)
                h1 = await pg.evaluate("(document.querySelector('main h1')||{}).textContent||''")
                tab = await pg.evaluate("(()=>{const r=document.querySelector('.tabbar').getBoundingClientRect();return [r.top,r.bottom,innerHeight]})()")
                # errores esperables con la cámara apagada (no hay imagen ni directo)
                real = [e for e in errs if cam_on or not re.search(r"snapshot|WebSocket|404|live/ws", e)]
                if not h1 or real or tab[1] > tab[2] or tab[0] < tab[2] * 0.7:
                    bad.append(f"{v}: h1='{h1[:20]}' errores={real[:2]} barra={tab}")
            check(f"Todas las pantallas cargan sin errores ({scheme}, {len(views)} vistas)", not bad, "; ".join(bad)[:400])
            if scheme == "dark" and cam_on:
                await pg.evaluate("nav('#directo')")
                ok = False
                for _ in range(24):
                    await pg.wait_for_timeout(500)
                    badge = await pg.evaluate("(document.querySelector('.player .over .badge:nth-child(2)')||{}).textContent")
                    if badge == "tiempo real":
                        ok = True
                        break
                ct = "(document.querySelector('.player video')||{currentTime:0}).currentTime"
                t1 = await pg.evaluate(ct)
                await pg.wait_for_timeout(1500)
                t2 = await pg.evaluate(ct)
                check("Directo en tiempo real (WebRTC) y el vídeo avanza", ok and t2 > t1, f"indicador={badge} avance={t2 - t1:.1f}s")
            await ctx.close()
        await b.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", type=int, default=int(time.time()) - 300)
    ap.add_argument("-v", action="store_true", help="mostrar también lo que va bien")
    args = ap.parse_args()

    cs = containers()
    for n in ("tapoli-go2rtc", "tapoli-analizador", "tapoli-web"):
        c = cs.get(n)
        check(f"Contenedor {n} en marcha", bool(c and c["state"] == "running"),
              f"{c['status']} · reinicios {c['restarts']}" if c else "no existe")
    if cs.get("tapoli-web"):
        h = cs["tapoli-web"]["health"]
        check("Panel sano (healthcheck)", h in ("healthy", "starting"), str(h))

    try:
        st, status = get_json("/api/status")
        check("API /api/status responde", st == 200)
    except Exception as e:
        check("API /api/status responde", False, str(e))
        return finish()
    check("El NAS sirve la versión recién publicada", status.get("version") == local_version(),
          f"servida {status.get('version')} · local {local_version()}")
    for path in ("/api/nights", "/api/config", "/api/calibrate", "/api/reprocess", "/manifest.webmanifest", "/"):
        try:
            code, _ = get(PANEL + path)
            check(f"GET {path}", code == 200)
        except Exception as e:
            check(f"GET {path}", False, str(e))
    nights = get_json("/api/nights")[1]
    if nights:
        code, _ = get(PANEL + f"/api/night/{nights[0]['night']}")
        check(f"GET /api/night/{nights[0]['night']}", code == 200)

    cam = (status.get("camara") or {}).get("estado")
    db = sqlite3.connect(os.path.join(ROOT, "data", "tapoli.db"))
    if cam == "conectada":
        last = db.execute("SELECT MAX(ts) FROM seconds").fetchone()[0] or 0
        check("El analizador está guardando datos", time.time() - last < 90, f"último dato hace {int(time.time() - last)} s")
    else:
        check("Cámara apagada: el analizador reintenta (correcto)", cam in ("desconectada", "conectando"), str(cam))
    check("Base de datos íntegra", db.execute("PRAGMA quick_check").fetchone()[0] == "ok")

    for log in ("analizador", "web"):
        p = os.path.join(ROOT, "data", "logs", f"{log}.log")
        bad = []
        if os.path.exists(p):
            for line in open(p, encoding="utf-8", errors="replace"):
                m = re.match(r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)", line)
                if m and time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")) >= args.since \
                        and (" ERROR " in line or "Traceback" in line):
                    bad.append(line.strip()[:160])
        check(f"Sin errores en el log del {log} desde la publicación", not bad, " | ".join(bad[:2]))

    asyncio.run(browser_checks(status))
    return finish()


def finish():
    fails = [r for r in results if not r[0]]
    print(f"\n{'✅ TODO CORRECTO' if not fails else '❌ HAY FALLOS'}: {len(results) - len(fails)}/{len(results)} comprobaciones superadas")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
