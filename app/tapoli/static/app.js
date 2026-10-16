"use strict";
// =====================================================================
// Tapoli · panel
// =====================================================================
const $ = (s, r = document) => r.querySelector(s);
const TZ = "Europe/Madrid";
const SVGNS = "http://www.w3.org/2000/svg";

function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "text") el.textContent = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat(Infinity)) if (c !== null && c !== undefined && c !== false)
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return el;
}
function s(tag, attrs = {}, text) {
  const el = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== null && v !== undefined) el.setAttribute(k, v);
  if (text !== undefined) el.textContent = text;
  return el;
}
function icon(name, cls = "i") {
  // Copia los trazos del símbolo en línea: en WebKit un <use> recién creado se pinta
  // un fotograma tarde y los iconos "parpadean" al cambiar de vista.
  const svg = document.createElementNS(SVGNS, "svg");
  svg.setAttribute("class", cls);
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("viewBox", "0 0 24 24");
  const sym = document.getElementById("i-" + name);
  if (sym) for (const c of sym.children) svg.append(c.cloneNode(true));
  return svg;
}
const VERSION = (document.querySelector('meta[name="tapoli-version"]') || {}).content || "";
const hm = (ts) => ts ? new Date(ts * 1000).toLocaleTimeString("es-ES", { timeZone: TZ, hour: "2-digit", minute: "2-digit" }) : "—";
const hms = (ts) => ts ? new Date(ts * 1000).toLocaleTimeString("es-ES", { timeZone: TZ }) : "—";
const hourOf = (ts) => Number(new Date(ts * 1000).toLocaleString("es-ES", { timeZone: TZ, hour: "2-digit", hour12: false }));
const dayLong = (iso) => new Date(iso + "T12:00:00").toLocaleDateString("es-ES", { weekday: "long", day: "numeric", month: "long" });
const dayShort = (iso) => new Date(iso + "T12:00:00").toLocaleDateString("es-ES", { weekday: "short", day: "numeric", month: "short" }).replace(/\./g, "");
const cap = (t) => t ? t[0].toUpperCase() + t.slice(1) : t;
const durParts = (m) => { m = Math.round(m || 0); return [Math.floor(m / 60), m % 60]; };
const dur = (m) => { if (m === null || m === undefined) return "—"; const [a, b] = durParts(m); return a ? `${a} h ${String(b).padStart(2, "0")}` : `${b} min`; };
const pct = (x) => x === null || x === undefined ? "—" : (x * 100).toLocaleString("es-ES", { maximumFractionDigits: x < 0.1 ? 1 : 0 }) + " %";
const num = (x, d = 1) => x === null || x === undefined ? "—" : Number(x).toLocaleString("es-ES", { maximumFractionDigits: d, minimumFractionDigits: d });
const ago = (t) => { const s2 = Math.max(0, Math.round(Date.now() / 1000 - t)); return s2 < 60 ? `hace ${s2} s` : s2 < 3600 ? `hace ${Math.round(s2 / 60)} min` : `hace ${Math.round(s2 / 3600)} h`; };
const LABEL = { dormida: "Dormida", despierta: "Despierta", falsa: "Falsa alarma", nose: "No lo sé" };
const LABEL_ICON = { dormida: "asleep", despierta: "awake", falsa: "false", nose: "help" };
const myLabel = (c) => c.label || (c.skipped ? "nose" : null);

async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch (e) { }
    throw new Error(msg);
  }
  return r.json();
}
const post = (p, body) => api(p, { method: "POST", body: JSON.stringify(body || {}) });

let STATUS = null;
let NAME = "tu bebé";

// ============================================================ estado
async function refreshStatus() {
  try { STATUS = await api("/api/status"); } catch (e) { STATUS = null; }
  if (STATUS) NAME = STATUS.nombre || NAME;
  if (STATUS && STATUS.version && VERSION && STATUS.version !== VERSION && sessionStorage.getItem("tapoli-reload") !== STATUS.version) {
    sessionStorage.setItem("tapoli-reload", STATUS.version);   // hay versión nueva: recargar (una vez)
    location.reload();
  }
  renderPill();
  const nc = $("#nowcard");
  if (nc && STATUS) {
    const ns = nowState(), orb = nc.querySelector(".orb");
    if (orb.dataset.icon !== ns.icon) { orb.dataset.icon = ns.icon; orb.replaceChildren(icon(ns.icon)); }
    const t = nc.querySelector(".t"), s2 = nc.querySelector(".s");
    if (t.textContent !== ns.t) t.textContent = ns.t;
    if (s2.textContent !== ns.s) s2.textContent = ns.s;
  }
  const badge = $("#badge");
  const n = STATUS ? STATUS.clips_sin_etiquetar : 0;
  badge.hidden = !n;
  badge.textContent = n > 99 ? "99+" : n;
  return STATUS;
}
function camState() {
  if (!STATUS) return { cls: "off", txt: "Sin servidor" };
  const cam = STATUS.camara || {};
  const age = STATUS.ahora_t ? STATUS.server_t - STATUS.ahora_t : 1e9;
  if (cam.estado === "conectada" && age < 60) return { cls: "on pulse", txt: "Cámara activa", ok: true };
  if (cam.estado === "conectando") return { cls: "warn", txt: "Conectando…" };
  return { cls: "off", txt: "Sin cámara", detail: cam.detalle };
}
function nowState() {
  const a = (STATUS && STATUS.ahora) || {};
  const live = STATUS && STATUS.noche_en_curso;
  const c = camState();
  if (!c.ok) return { icon: "alert", t: "Cámara desconectada", s: c.detail || "Reintentando la conexión…" };
  const away = live && (live.wakes || []).find(w => w.kind === "fuera" && w.end >= (STATUS.server_t - 180));
  if (away && !a.active) return { icon: "crib", t: `${cap(NAME)} no está en la cuna`, s: `Cuna vacía desde las ${hm(away.start)}` };
  if (a.voice) return { icon: "wave", t: `Se oye a ${NAME}`, s: "Voz o llanto ahora mismo" };
  if (a.active) return { icon: "awake", t: `${cap(NAME)} se está moviendo`, s: "Movimiento en la cuna" };
  if (live && live.sleep_onset) return { icon: "moon", t: `${cap(NAME)} duerme tranquila`, s: `Dormida desde las ${hm(live.sleep_onset)}${live.n_wakes ? ` · ${live.n_wakes} despertar${live.n_wakes > 1 ? "es" : ""}` : ""}` };
  return { icon: "moon", t: "Todo tranquilo", s: "Sin movimiento en la cuna" };
}

// ============================================================ cabecera
let HEADER = {};
function setHeader(opts) { HEADER = opts || {}; renderHeader(); }
function renderHeader() {
  const top = $("#top");
  const left = HEADER.back ? h("button", { class: "back", onclick: () => { goBack(HEADER.back); } }, icon("back"), HEADER.backLabel || "Atrás") : h("span");
  const slot = $("#pillslot") || h("span", { id: "pillslot" });
  top.replaceChildren(left, slot);
  renderPill();
}
function renderPill() {
  const slot = $("#pillslot");
  if (!slot) return;
  const c = camState();
  let pill = slot.firstChild;
  if (!pill) { pill = h("a", { class: "pill", href: "#directo", "aria-label": "Ver en directo" }, h("span", { class: "dot" }), h("span")); slot.append(pill); }
  pill.firstChild.className = "dot " + c.cls;
  if (pill.lastChild.textContent !== c.txt) pill.lastChild.textContent = c.txt;
}
const SCROLLER = $("#scroller");
const STANDALONE = navigator.standalone === true || matchMedia("(display-mode: standalone)").matches;
// En la app de pantalla de inicio la barra de estado es opaca (la página empieza debajo):
// no hace falta reservar hueco arriba.
if (STANDALONE) document.documentElement.classList.add("standalone");
SCROLLER.addEventListener("scroll", hideTip, { passive: true });

// ============================================================ router
const VIEWS = {};
let cleanup = null;
async function route() {
  const parts = (location.hash.slice(1) || "inicio").split("/");
  const name = parts[0];
  const tab = { noche: "noches", calibrar: "ajustes" }[name] || name;
  document.querySelectorAll(".tabbar a").forEach(a => a.classList.toggle("on", a.dataset.tab === tab));
  if (cleanup) { try { cleanup(); } catch (e) { } cleanup = null; }
  const dlg = $("#sheet");
  if (dlg.open) { dlg.dataset.silent = "1"; dlg.close(); }
  if ($("#dlg").open) $("#dlg").close("cancel");
  const v = $("#view");
  const slow = setTimeout(() => v.classList.add("loading"), 350);  // solo si tarda
  try {
    const fn = VIEWS[name] || VIEWS.inicio;
    const out = await fn(...parts.slice(1));
    const node = out && out.node ? out.node : out;
    if (out && out.cleanup) cleanup = out.cleanup;
    v.style.minHeight = v.offsetHeight + "px";   // evita que la página "encoja" un instante
    v.replaceChildren(node);
    if (!route.keepScroll) SCROLLER.scrollTop = 0;
    requestAnimationFrame(() => requestAnimationFrame(() => { v.style.minHeight = ""; }));
  } catch (e) {
    setHeader({});
    v.replaceChildren(h("div", { class: "card" }, h("b", { text: "No se pudo cargar" }), h("p", { class: "ink2", text: e.message })));
  }
  clearTimeout(slow);
  v.classList.remove("loading");
  route.keepScroll = false;
}
// Navegación propia: en la app de pantalla de inicio de iOS, cada cambio de #hash
// "nativo" se trata como navegación y hace un fundido (parpadeo).
function nav(href) {
  if (href === (location.hash || "#inicio")) { SCROLLER.scrollTo({ top: 0, behavior: "smooth" }); return; }
  history.pushState({ n: ((history.state && history.state.n) || 0) + 1 }, "", href);
  route();
}
function goBack(fallback) {
  if (history.state && history.state.n) history.back(); else nav(fallback);
}
document.addEventListener("click", (e) => {
  const a = e.target.closest && e.target.closest('a[href^="#"]');
  if (!a || e.defaultPrevented || e.metaKey || e.ctrlKey) return;
  e.preventDefault();
  nav(a.getAttribute("href"));
});
window.addEventListener("popstate", route);
const titleBlock = (eyebrow, title, sub) => h("div", { class: "hgroup" },
  eyebrow ? h("div", { class: "eyebrow", text: eyebrow }) : null,
  h("h1", { class: "ltitle", text: title }), sub ? h("div", { class: "subtitle", text: sub }) : null);
const section = (title, link) => h("div", { class: "section" }, h("h2", { text: title }), link || null);

// ============================================================ tooltip y aviso
const tip = $("#tip");
function showTip(x, y, title, rows) {
  tip.replaceChildren(h("div", { class: "t", text: title }),
    ...rows.map(([color, val, name]) => h("div", { class: "r" },
      color ? h("i", { style: `background:${color}` }) : null, h("b", { text: val }), h("span", { class: "muted", text: name }))));
  tip.hidden = false;
  const r = tip.getBoundingClientRect();
  let left = x + 14, top = y - r.height - 14;
  if (left + r.width > innerWidth - 8) left = x - r.width - 14;
  if (top < 8) top = y + 18;
  tip.style.left = Math.max(8, left) + "px";
  tip.style.top = top + "px";
}
function hideTip() { tip.hidden = true; }
// Diálogo propio con la estética de la app
async function ask(title, text, okLabel = "Aceptar", opts = {}) {
  const d = $("#dlg");
  if (d.open) {                                // un diálogo previo se da por cancelado
    const closed = new Promise((r) => d.addEventListener("close", r, { once: true }));
    d.close("cancel");
    await closed;
  }
  return new Promise((resolve) => {
    const done = (v) => d.close(v ? "ok" : "cancel");
    d.addEventListener("close", () => resolve(d.returnValue === "ok"), { once: true });
    d.replaceChildren(h("div", { class: "dlg-in" },
      opts.icon ? h("div", { class: "dlg-ic" + (opts.danger ? " danger" : "") }, icon(opts.icon)) : null,
      h("div", { class: "dlg-t", text: title }),
      text ? h("div", { class: "dlg-s", text }) : null,
      h("div", { class: "dlg-b" },
        opts.noCancel ? null : h("button", { class: "btn", onclick: () => done(false) }, opts.cancelLabel || "Cancelar"),
        h("button", { class: "btn " + (opts.danger ? "danger" : "primary"), onclick: () => done(true) }, okLabel))));
    d.returnValue = "";
    d.showModal();
  });
}
let toastTimer;
function toast(msg, action) {
  const t = $("#toast");
  t.replaceChildren(...[h("span", { text: msg }), action ? h("button", { onclick: () => { t.hidden = true; action.fn(); } }, action.label) : null].filter(Boolean));
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 4500);
}

// ============================================================ línea de tiempo de la noche
// Barra horizontal 19:00→09:00: violeta = dormida, ámbar = despierta.
function timeline(n, opts = {}) {
  const t0 = n.start_ts, t1 = n.end_ts;
  const now = Date.now() / 1000;
  const endData = n.final ? t1 : Math.min(t1, now);
  const P = (t) => Math.max(0, Math.min(100, (t - t0) / (t1 - t0) * 100));
  const H = opts.height || 16;
  const bar = h("div", { style: { position: "relative", height: H + "px", borderRadius: H / 2 + "px", background: "var(--surface-2)", overflow: "hidden" } });
  const seg = (a, b, color, title, extra = {}) => {
    if (b <= a) return;
    const el = h("div", { title, style: Object.assign({ position: "absolute", top: 0, bottom: 0, left: P(a) + "%", width: Math.max(0.6, P(b) - P(a)) + "%", background: color, borderRadius: "3px" }, extra) });
    if (opts.tips) {
      el.addEventListener("pointerenter", (e) => showTip(e.clientX, e.clientY, title, [[null, `${hm(a)} – ${hm(b)}`, dur((b - a) / 60)]]));
      el.addEventListener("pointerleave", hideTip);
    }
    bar.append(el);
  };
  if (n.sleep_onset) {
    const sEnd = n.final ? n.sleep_end : endData;
    seg(n.sleep_onset, sEnd, "var(--sleep)", "Dormida");
  }
  for (const w of (n.wakes || []).filter(w => w.kind === "fuera")) {
    seg(w.start, Math.min(w.end, endData), "var(--surface-3)", "Fuera de la cuna", { boxShadow: "0 0 0 2px var(--surface)" });
  }
  for (const w of (n.wakes || []).filter(w => w.kind !== "fuera")) {
    const pre = w.kind === "antes" || w.kind === "despues";
    seg(w.start, Math.min(w.end, endData), "var(--awake)", w.kind === "final" ? "Despertar de la mañana" : pre ? "Despierta (antes de dormirse)" : "Despierta",
      { opacity: pre ? .55 : 1, boxShadow: "0 0 0 2px var(--surface)" });
  }
  if (!n.final && now < t1) bar.append(h("div", { style: { position: "absolute", top: 0, bottom: 0, left: P(now) + "%", width: "2px", background: "var(--ink-2)" } }));
  if (!opts.ticks) return bar;
  const ticks = h("div", { style: { position: "relative", height: "18px", marginTop: "6px" } });
  for (let t = Math.ceil(t0 / 3600) * 3600; t <= t1; t += 3600) {
    const hr = hourOf(t);
    if (hr % 3 !== 1) continue;
    const p = P(t);
    if (p < 3 || p > 97) continue;
    ticks.append(h("span", { class: "nums", style: { position: "absolute", left: p + "%", transform: "translateX(-50%)", fontSize: "11px", color: "var(--muted)" }, text: String(hr).padStart(2, "0") + "h" }));
  }
  return h("div", { class: "tl" }, bar, ticks);
}
const tlLegend = () => h("div", { class: "legend" },
  h("span", {}, h("i", { style: "background:var(--sleep)" }), "Dormida"),
  h("span", {}, h("i", { style: "background:var(--awake)" }), "Despierta"),
  h("span", {}, h("i", { style: "background:var(--surface-3)" }), "Fuera de la cuna"));

// ============================================================ tarjeta resumen de noche
function heroCard(n, opts = {}) {
  const live = !n.final;
  const [hh, mm] = durParts(n.min_sleep);
  const card = h("div", { class: "card hero" + (opts.link ? " tap" : ""), onclick: opts.link ? () => { nav("#noche/" + n.night); } : null, style: opts.link ? "cursor:pointer" : null },
    h("div", { class: "row between" },
      h("div", { class: "eyebrow", text: opts.eyebrow || cap(dayShort(n.night)) }),
      opts.link ? icon("chev", "i") : null),
    n.sleep_onset ? h("div", { class: "big" }, String(hh), h("small", { text: "h" }), String(mm).padStart(2, "0"), h("small", { text: "min" })) :
      h("div", { class: "big", style: "font-size:28px", text: live ? "Aún despierta" : "Sin datos de sueño" }),
    h("div", { class: "range" }, n.sleep_onset ? [
      icon("moon"), h("b", { text: hm(n.sleep_onset) }), h("span", { class: "muted", text: "→" }),
      n.final ? [icon("awake"), h("b", { text: hm(n.sleep_end) })] : h("span", { text: "durmiendo ahora" })] :
      h("span", { text: live ? "Se mostrará cuando se duerma" : "" })),
    (n.extra && n.extra.away_min) ? h("div", { class: "small muted", style: "margin-top:6px", text: `Además, ${dur(n.extra.away_min)} fuera de la cuna (no cuenta como sueño)` }) : null,
    timeline(n, { ticks: true, tips: true }),
    h("div", { class: "stats" },
      stat(n.sleep_onset ? String(n.n_wakes) : "—", "despertares"),
      stat(n.sleep_onset ? String(n.min_awake) : "—", "min despierta"),
      stat(n.restlessness !== null && n.restlessness !== undefined ? num(n.restlessness, 1) : "—", "inquietud", "/h")));
  return card;
}
const stat = (v, k, unit) => h("div", { class: "stat" }, h("div", { class: "v" }, v, unit && v !== "—" ? h("small", { text: unit }) : null), h("div", { class: "k", text: k }));

// ============================================================ INICIO
VIEWS.inicio = async () => {
  setHeader({ title: "Tapoli" });
  const [st, nights] = await Promise.all([refreshStatus(), api("/api/nights")]);
  const out = h("div");
  const hr = new Date().toLocaleString("es-ES", { timeZone: TZ, hour: "2-digit", hour12: false });
  const greet = hr >= 6 && hr < 14 ? "Buenos días" : hr < 21 && hr >= 14 ? "Buenas tardes" : "Buenas noches";
  out.append(titleBlock(cap(new Date().toLocaleDateString("es-ES", { timeZone: TZ, weekday: "long", day: "numeric", month: "long" })), greet));

  // instalar en el iPhone
  if (navigator.standalone === false && !localStorage.getItem("tapoli-install-hide")) {
    const b = h("div", { class: "card banner" },
      h("div", { class: "ic" }, icon("plus-square")),
      h("div", { class: "grow" }, h("b", { text: "Añádela a tu pantalla de inicio" }),
        h("div", { class: "ink2 small", style: "margin-top:3px" }, "Pulsa ", icon("share", "i"), " Compartir y después «Añadir a pantalla de inicio».")),
      h("button", { class: "x", "aria-label": "Cerrar", onclick: () => { localStorage.setItem("tapoli-install-hide", "1"); b.remove(); } }, icon("x", "i")));
    b.querySelector(".small svg").style.cssText = "width:15px;height:15px;vertical-align:-2px";
    out.append(b);
  }
  if (st && st.aviso_espacio) out.append(h("div", { class: "card banner" }, h("div", { class: "ic warn" }, icon("disk")),
    h("div", {}, h("b", { text: `Los clips ocupan ${num(st.almacenamiento.bytes / 1e9, 1)} GB` }),
      h("div", { class: "ink2 small", text: `Superan el aviso de ${st.aviso_gb} GB. Etiqueta o borra clips, o reduce los días de conservación.` }))));

  const za = st && st.zona_auto;
  if (za && za.estado === "muy_movida" && camState().ok) out.append(h("a", { class: "card banner", href: "#ajustes/zona" },
    h("div", { class: "ic warn" }, icon("crib")),
    h("div", { class: "grow" }, h("b", { text: "La cámara se ha movido" }),
      h("div", { class: "ink2 small", text: "No he podido reajustar la zona de la cuna sola. Toca para dibujarla otra vez." }))));
  // ahora
  const ns = nowState();
  out.append(h("a", { class: "card now", href: "#directo", id: "nowcard" },
    h("div", { class: "orb", "data-icon": ns.icon }, icon(ns.icon)),
    h("div", { class: "grow" }, h("div", { class: "t", text: ns.t }), h("div", { class: "s", text: ns.s })),
    icon("chev", "i")));

  const live = st && st.noche_en_curso;
  const last = (nights || []).find(n => n.final);
  if (live) {
    out.append(section("Esta noche", h("a", { href: "#noche/" + live.night, text: "Detalle" })));
    out.append(heroCard(live, { eyebrow: "En curso" }));
  }
  if (last) {
    out.append(section(live ? "Noche anterior" : "Anoche", h("a", { href: "#noche/" + last.night, text: "Detalle" })));
    out.append(heroCard(last, { eyebrow: cap(dayShort(last.night)) }));
    out.append(tlLegend());
  }
  const pending = st ? st.clips_sin_etiquetar : 0;
  if (pending) out.append(h("a", { class: "card now", href: "#revisar", style: "margin-top:12px" },
    h("div", { class: "orb" }, icon("review")),
    h("div", { class: "grow" }, h("div", { class: "t", text: `${pending} clip${pending > 1 ? "s" : ""} por revisar` }),
      h("div", { class: "s", text: "Tus etiquetas enseñan a Tapoli a distinguir cuándo está despierta" })),
    icon("chev", "i")));

  const done = (nights || []).filter(n => n.final && n.sleep_onset).slice(0, 7);
  if (done.length >= 2) {
    out.append(section("Últimos 7 días", h("a", { href: "#noches", text: "Ver todo" })));
    out.append(h("div", { class: "card" }, weekBars(done)));
  }
  if (!live && !last) out.append(h("div", { class: "card empty" },
    h("div", { class: "done" }, h("div", { class: "orb" }, icon("moon")),
      h("b", { text: "Aún no hay noches analizadas" }),
      h("p", { class: "ink2", text: "El análisis empieza solo a las 19:00. Mientras, revisa la zona de la cuna en Ajustes." }))));
  return out;
};

function weekBars(rows) {
  const data = rows.slice().reverse();
  const avg = data.reduce((a, b) => a + b.min_sleep, 0) / data.length;
  const max = Math.max(...data.map(d => d.min_sleep), 1);
  const bars = h("div", { style: { display: "flex", alignItems: "flex-end", gap: "8px", height: "96px", marginTop: "12px" } },
    data.map(d => h("a", { href: "#noche/" + d.night, style: { flex: 1, display: "flex", flexDirection: "column", alignItems: "center", gap: "6px", height: "100%", justifyContent: "flex-end" } },
      h("div", { title: `${dayShort(d.night)} · ${dur(d.min_sleep)}`, style: { width: "100%", maxWidth: "28px", height: Math.max(4, d.min_sleep / max * 72) + "px", background: "var(--sleep)", borderRadius: "6px 6px 3px 3px" } }),
      h("span", { class: "small muted", text: new Date(d.night + "T12:00:00").toLocaleDateString("es-ES", { weekday: "narrow" }) }))));
  return h("div", {}, h("div", { class: "row between" },
    h("div", {}, h("div", { class: "eyebrow", text: "Media de sueño" }), h("div", { style: "font:700 26px var(--font-d);letter-spacing:-.02em;margin-top:2px", text: dur(avg) })),
    h("div", { style: "text-align:right" }, h("div", { class: "eyebrow", text: "Despertares" }),
      h("div", { style: "font:700 26px var(--font-d);letter-spacing:-.02em;margin-top:2px", text: num(data.reduce((a, b) => a + b.n_wakes, 0) / data.length, 1) }))), bars);
}

// ============================================================ NOCHES
// ---------------------------------------------------------------- NOCHES
const NOTE_TAGS = { dientes: "Dientes", enferma: "Enferma", vacuna: "Vacuna", siesta_larga: "Siesta larga",
  siesta_corta: "Siesta corta", fuera_casa: "Fuera de casa", cena: "Cena distinta", otro: "Otro" };
let NOCHES_RANGE = 7;
const avgOf = (arr) => arr.length ? arr.reduce((a, b) => a + b, 0) / arr.length : null;
const sdOf = (arr) => { const m = avgOf(arr); return arr.length > 1 ? Math.sqrt(avgOf(arr.map(x => (x - m) ** 2))) : null; };
const minOfNight = (r, ts) => ts ? (ts - r.start_ts) / 60 : null;          // minutos desde el inicio de la ventana
const clockOf = (r, m) => hm(r.start_ts + m * 60);

function periodStats(rows) {
  const v = (f) => rows.map(f).filter(x => x !== null && x !== undefined && !isNaN(x));
  const on = v(r => minOfNight(r, r.sleep_onset)), up = v(r => r.final ? minOfNight(r, r.sleep_end) : null);
  return {
    n: rows.length, sleep: avgOf(v(r => r.min_sleep)), wakes: avgOf(v(r => r.n_wakes)), awake: avgOf(v(r => r.min_awake)),
    onset: avgOf(on), onsetSd: sdOf(on), up: avgOf(up), upSd: sdOf(up),
    latency: avgOf(v(r => r.extra && r.extra.latency)), restless: avgOf(v(r => r.restlessness)),
  };
}

VIEWS.noches = async () => {
  setHeader({ title: "Noches" });
  const rows = await api("/api/nights");
  const out = h("div", {}, titleBlock(null, "Noches"));
  if (!rows.length) { out.append(h("div", { class: "card empty", text: "Todavía no hay noches." })); return out; }
  const done = rows.filter(r => r.final && r.sleep_onset);
  const body = h("div");
  const seg = h("div", { class: "seg" });
  const render = () => {
    const N = NOCHES_RANGE;
    const cur = N ? done.slice(0, N) : done, prev = N ? done.slice(N, 2 * N) : [];
    seg.replaceChildren(...[[7, "7 noches"], [30, "30 noches"], [0, "Todas"]].map(([k, t]) =>
      h("button", { class: NOCHES_RANGE === k ? "on" : "", onclick: () => { NOCHES_RANGE = k; render(); } }, t)));
    const a = periodStats(cur), b = prev.length >= 3 ? periodStats(prev) : null;
    const ref = cur[0] || rows[0];
    const delta = (k, fmt, better) => {
      if (!b || a[k] === null || b[k] === null) return null;
      const d = a[k] - b[k];
      if (Math.abs(d) < ((k === "wakes" || k === "restless") ? 0.05 : 0.5)) return h("div", { class: "k", text: "igual que antes" });
      const good = better === "up" ? d > 0 : d < 0;
      return h("div", { class: "k", style: { color: good ? "var(--good)" : "var(--ink-2)" }, text: `${d > 0 ? "▲" : "▼"} ${fmt(Math.abs(d))}` });
    };
    const tile = (k, v, sub, d) => h("div", { class: "tile2" }, h("div", { class: "k", text: k }), h("div", { class: "v", text: v }),
      sub ? h("div", { class: "k", text: sub }) : null, d);
    const tiles = cur.length ? h("div", { class: "tiles2" },
      tile("Sueño medio", dur(a.sleep), null, delta("sleep", dur, "up")),
      tile("Despertares", a.wakes !== null ? num(a.wakes, 1) : "—", "por noche", delta("wakes", x => num(x, 1), "down")),
      tile("Se duerme", a.onset !== null ? clockOf(ref, a.onset) : "—", a.onsetSd !== null ? `± ${Math.round(a.onsetSd)} min de una noche a otra` : null),
      tile("Se despierta", a.up !== null ? clockOf(ref, a.up) : "—", a.upSd !== null ? `± ${Math.round(a.upSd)} min de una noche a otra` : null),
      tile("Tarda en dormirse", a.latency !== null ? `${Math.round(a.latency)} min` : "—", "desde que la acostáis", delta("latency", x => `${Math.round(x)} min`, "down")),
      tile("Inquietud", a.restless !== null ? num(a.restless, 1) : "—", "min con movimiento / h", delta("restless", x => num(x, 1), "down")))
      : h("p", { class: "muted", text: "Aún no hay noches completas." });
    body.replaceChildren(
      h("div", { class: "card" }, tiles, b ? h("p", { class: "small muted", style: "margin:12px 2px 0", text: `Flechas: comparado con las ${prev.length} noches anteriores.` }) : null),
      section("Diario de sueño"),
      h("div", { class: "card" }, diary(N ? rows.slice(0, N + (rows[0].final ? 0 : 1)) : rows), tlLegend()),
      section("Tendencias"),
      h("div", { class: "card" },
        h("div", { class: "legend", style: "margin:0 0 6px" },
          h("span", {}, h("i", { style: "background:var(--sleep)" }), "Horas de sueño"),
          h("span", {}, h("i", { class: "line", style: "background:var(--ink-2)" }), "Media de 7 noches"),
          h("span", {}, h("i", { style: "background:var(--awake)" }), "Despertares")),
        historyChart(cur.length ? cur : done)),
      h("div", { class: "card" },
        timeChart(cur, r => r.sleep_onset, "var(--sleep)", "Hora de dormirse"),
        h("div", { style: "height:18px" }),
        timeChart(cur.filter(r => r.final), r => r.sleep_end, "var(--awake)", "Hora de despertarse"),
        h("p", { class: "small muted", style: "margin:10px 2px 0", text: "La franja es su horario habitual (media ± variación). Cuanto más estrecha, más regular." })));
  };
  render();
  out.append(seg, body);
  return out;
};

// Diario: una fila por noche con la misma escala horaria (19→09) → se ven los patrones
function diary(rows) {
  const wrap = h("div", { class: "diary" });
  const ref = rows[0];
  const span = ref.end_ts - ref.start_ts;
  const ticks = h("div", { class: "d-axis" });
  for (let t = Math.ceil(ref.start_ts / 3600) * 3600; t <= ref.end_ts; t += 3600) {
    const hr = hourOf(t);
    if (hr % 3 !== 1) continue;
    const p = (t - ref.start_ts) / span * 100;
    ticks.append(h("span", { style: { left: p + "%" }, text: String(hr).padStart(2, "0") + "h" }));
  }
  wrap.append(h("div", { class: "d-row d-head" }, h("span"), ticks, h("span")));
  let lastWeek = null;
  for (const r of rows) {
    const d = new Date(r.night + "T12:00:00");
    const monday = new Date(d); monday.setDate(d.getDate() - ((d.getDay() + 6) % 7));
    const wk = monday.toISOString().slice(0, 10);
    if (wk !== lastWeek) {
      lastWeek = wk;
      wrap.append(h("div", { class: "d-week", text: "Semana del " + monday.toLocaleDateString("es-ES", { day: "numeric", month: "long" }) }));
    }
    const grid = h("div", { class: "d-bar" }, timeline(r, { height: 14, tips: true }));
    for (let t = Math.ceil(ref.start_ts / 3600) * 3600; t <= ref.end_ts; t += 3600)
      if (hourOf(t) % 3 === 1) grid.append(h("i", { style: { left: ((t - ref.start_ts) / span * 100) + "%" } }));
    const tags = (r.notes && r.notes.tags) || [];
    wrap.append(h("a", { class: "d-row", href: "#noche/" + r.night },
      h("span", { class: "d-date" }, h("b", { text: d.toLocaleDateString("es-ES", { weekday: "short" }).replace(".", "") }), " " + d.getDate()),
      h("div", {}, grid, tags.length || (r.notes && r.notes.text) ? h("div", { class: "d-tags" },
        ...tags.map(t => h("span", { text: NOTE_TAGS[t] })), (!tags.length && r.notes.text) ? h("span", { text: "Nota" }) : null) : null),
      h("span", { class: "d-dur" }, r.sleep_onset ? dur(r.min_sleep) : "—", r.final ? null : h("small", { text: "en curso" }))));
  }
  return wrap;
}

// Hora de dormirse / despertarse por noche, con la franja habitual (media ± desviación)
function timeChart(rows, get, color, title) {
  const wrap = h("div", { class: "chart" });
  const data = rows.filter(r => get(r)).slice().reverse();
  if (data.length < 2) { wrap.append(h("div", { class: "small muted", text: `${title}: hacen falta al menos 2 noches.` })); return wrap; }
  const vals = data.map(r => minOfNight(r, get(r)));
  const m = avgOf(vals), sd = sdOf(vals) || 0;
  const lo = Math.min(...vals, m - sd) - 20, hi = Math.max(...vals, m + sd) + 20;
  const draw = () => {
    const W = Math.max(280, wrap.clientWidth || 320), L = 44, R = 6, top = 22, H = 96, axisH = 18;
    const step = (W - L - R) / data.length;
    const Y = (v) => top + (v - lo) / (hi - lo) * H;           // más tarde = más abajo
    const svg = s("svg", { viewBox: `0 0 ${W} ${top + H + axisH}`, height: top + H + axisH, role: "img", "aria-label": title });
    svg.append(s("text", { x: 0, y: 12, class: "lbl" }, title));
    svg.append(s("rect", { x: L, y: Y(m - sd), width: W - L - R, height: Math.max(2, Y(m + sd) - Y(m - sd)), fill: color, opacity: .12, rx: 4 }));
    svg.append(s("line", { x1: L, x2: W - R, y1: Y(m), y2: Y(m), stroke: color, "stroke-opacity": .5 }));
    for (const v of [lo + 20, m, hi - 20]) svg.append(s("text", { x: L - 6, y: Y(v) + 4, "text-anchor": "end" }, clockOf(data[0], v)));
    let path = "";
    data.forEach((r, i) => { const x = L + step * (i + .5); path += (i ? "L" : "M") + x + "," + Y(vals[i]); });
    svg.append(s("path", { d: path, fill: "none", stroke: color, "stroke-width": 2, "stroke-linejoin": "round", "stroke-opacity": .55 }));
    data.forEach((r, i) => {
      const x = L + step * (i + .5);
      const g = s("g"); g.style.cursor = "pointer";
      g.append(s("rect", { x: x - Math.max(12, step / 2), y: top, width: Math.max(24, step), height: H, fill: "transparent" }));
      g.append(s("circle", { cx: x, cy: Y(vals[i]), r: 4.5, fill: color, stroke: "var(--surface)", "stroke-width": 2 }));
      g.addEventListener("pointerenter", (e) => showTip(e.clientX, e.clientY, cap(dayShort(r.night)), [[color, hm(get(r)), title.toLowerCase()]]));
      g.addEventListener("pointerleave", hideTip);
      g.addEventListener("click", () => { hideTip(); nav("#noche/" + r.night); });
      svg.append(g);
    });
    const every = Math.ceil(data.length / 6);
    data.forEach((r, i) => {
      if ((data.length - 1 - i) % every) return;
      svg.append(s("text", { x: L + step * (i + .5), y: top + H + 15, "text-anchor": "middle" },
        new Date(r.night + "T12:00:00").toLocaleDateString("es-ES", { day: "numeric", month: "numeric" })));
    });
    return svg;
  };
  mountResponsive(wrap, draw);
  return wrap;
}

function historyChart(rows) {
  const wrap = h("div", { class: "chart" });
  const data = rows.filter(r => r.final && r.sleep_onset).slice(0, 30).reverse();
  if (!data.length) return h("p", { class: "muted", text: "Aún no hay noches completas." });
  const draw = () => {
    const W = Math.max(280, wrap.clientWidth || 320);
    const L = 26, R = 4, H1 = 130, H2 = 54, gap = 34, top = 18, axisH = 20;
    const y1 = top, y2 = top + H1 + gap, ya = y2 + H2;
    const Ht = ya + axisH;
    const n = data.length, step = (W - L - R) / n, bw = Math.max(3, Math.min(22, step - 4));
    const svg = s("svg", { viewBox: `0 0 ${W} ${Ht}`, height: Ht, role: "img", "aria-label": "Horas de sueño y despertares por noche" });
    const maxH = Math.max(12, Math.ceil(Math.max(...data.map(d => d.min_sleep / 60))));
    const maxW = Math.max(3, Math.max(...data.map(d => d.n_wakes)));
    const grid = (y, H, max, ticks, title) => {
      for (const t of ticks) {
        const yy = y + H - t / max * H;
        svg.append(s("line", { x1: L, x2: W - R, y1: yy, y2: yy, stroke: t ? "var(--grid)" : "var(--axis)" }));
        svg.append(s("text", { x: L - 6, y: yy + 3.5, "text-anchor": "end" }, t));
      }
      svg.append(s("text", { x: L, y: y - 7, class: "lbl" }, title));
    };
    grid(y1, H1, maxH, [0, Math.round(maxH / 2), maxH], "Horas de sueño");
    grid(y2, H2, maxW, [0, maxW], "Despertares");
    const bar = (x, y, H, v, max, color, d) => {
      const hh = Math.max(v > 0 ? 3 : 0, v / max * H);
      const g = s("g"); g.style.cursor = "pointer";
      g.append(s("rect", { x: x - step / 2, y, width: step, height: H, fill: "transparent" }));
      if (hh > 0) g.append(s("path", { d: roundTop(x - bw / 2, y + H - hh, bw, hh, Math.min(4, bw / 2)), fill: color }));
      g.addEventListener("pointerenter", (e) => showTip(e.clientX, e.clientY, cap(dayShort(d.night)), [
        ["var(--sleep)", dur(d.min_sleep), "de sueño"], ["var(--awake)", String(d.n_wakes), "despertares"],
        [null, `${hm(d.sleep_onset)} → ${hm(d.sleep_end)}`, ""]]));
      g.addEventListener("pointerleave", hideTip);
      g.addEventListener("click", () => { hideTip(); nav("#noche/" + d.night); });
      svg.append(g);
    };
    data.forEach((d, i) => {
      const x = L + step * (i + .5);
      bar(x, y1, H1, d.min_sleep / 60, maxH, "var(--sleep)", d);
      bar(x, y2, H2, d.n_wakes, maxW, "var(--awake)", d);
    });
    if (data.length >= 3) {
      let path = "";
      data.forEach((d, i) => {
        const win = data.slice(Math.max(0, i - 6), i + 1);
        const a = win.reduce((p, q) => p + q.min_sleep / 60, 0) / win.length;
        path += (i ? "L" : "M") + (L + step * (i + .5)) + "," + (y1 + H1 - a / maxH * H1);
      });
      svg.append(s("path", { d: path, fill: "none", stroke: "var(--ink-2)", "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
    }
    const every = Math.ceil(n / 6);
    data.forEach((d, i) => {
      if ((n - 1 - i) % every) return;
      svg.append(s("text", { x: L + step * (i + .5), y: ya + 15, "text-anchor": "middle" },
        new Date(d.night + "T12:00:00").toLocaleDateString("es-ES", { day: "numeric", month: "numeric" })));
    });
    return svg;
  };
  mountResponsive(wrap, draw);
  return wrap;
}
function roundTop(x, y, w, hh, r) {
  r = Math.min(r, hh);
  return `M${x},${y + hh}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + hh}Z`;
}
function mountResponsive(wrap, draw) {
  let svg = null;
  const render = () => { const n = draw(); if (svg) svg.replaceWith(n); else wrap.append(n); svg = n; };
  requestAnimationFrame(render);
  let rt; new ResizeObserver(() => { clearTimeout(rt); rt = setTimeout(render, 150); }).observe(wrap);
}

// ============================================================ NOCHE (detalle)
VIEWS.noche = async (night) => {
  const rows = await api("/api/nights");
  night = night || (rows[0] && rows[0].night);
  if (!night) return h("div", { class: "card empty", text: "Todavía no hay noches." });
  const d = await api("/api/night/" + night);
  const n = rows.find(r => r.night === night) || Object.assign({ night }, d.summary || {});
  setHeader({ back: "#noches", backLabel: "Noches", title: cap(dayShort(night)) });
  const idx = rows.findIndex(r => r.night === night);
  const prev = rows[idx + 1], next = rows[idx - 1];
  const out = h("div", {}, titleBlock(n.final ? "Noche" : "Noche en curso", cap(dayLong(night))));
  out.append(heroCard(n, { eyebrow: n.final ? "Sueño total" : "Hasta ahora" }), tlLegend());
  out.append(section("Notas"), notesEditor(night, d.notes));
  out.append(section("Movimiento y sonido"));
  out.append(h("div", { class: "card" }, nightChart(d), nightTable(d)));
  const wakes = (n.wakes || []).filter(w => w.kind === "noche" || w.kind === "final");
  if (wakes.length) {
    out.append(section("Despertares"));
    out.append(h("div", { class: "list" }, wakes.map(w => {
      const c = d.clips.find(c => c.ep_start < w.end && c.ep_end > w.start && c.url);
      return h("button", { class: "row-link", onclick: c ? () => openClipSheet(c, d.clips) : null },
        h("div", { class: "ic", style: "background:var(--awake-band);color:var(--awake)" }, icon(w.kind === "final" ? "awake" : "alert")),
        h("div", { class: "grow" }, h("div", { class: "t", text: `${hm(w.start)} – ${hm(w.end)}` }),
          h("div", { class: "s", text: w.kind === "final" ? "Despertar de la mañana" : `${w.min} min despierta` })),
        c ? h("span", { class: "val", text: "Ver clip" }) : null, c ? icon("chev", "chev") : null);
    })));
  }
  if (d.clips.length) {
    out.append(section(`Clips · ${d.clips.length}`, h("a", { href: "#revisar/" + night, text: "Revisar" })));
    out.append(h("div", { class: "strip" }, d.clips.map(c => h("div", { class: "th", onclick: () => openClipSheet(c, d.clips) },
      h("div", { class: c.desacuerdo ? "dis" : "", style: c.thumb ? { backgroundImage: `url("${c.thumb}")` } : null }),
      h("span", { text: `${hm(c.ep_start)} · ${c.label ? LABEL[c.label] : "sin etiqueta"}` })))));
  }
  out.append(h("div", { class: "row between", style: "margin-top:22px" },
    prev ? h("a", { class: "btn small", href: "#noche/" + prev.night }, icon("back"), cap(dayShort(prev.night))) : h("span"),
    next ? h("a", { class: "btn small", href: "#noche/" + next.night }, cap(dayShort(next.night)), icon("chev")) : h("span")));
  return out;
};

function notesEditor(night, notes) {
  let tags = new Set((notes && notes.tags) || []);
  const state = h("span", { class: "small muted" });
  let t = null;
  const save = () => {
    clearTimeout(t);
    state.textContent = "Guardando…";
    t = setTimeout(async () => {
      try { await post(`/api/night/${night}/notes`, { tags: [...tags], text: ta.value }); state.textContent = "Guardado ✓"; }
      catch (e) { state.textContent = e.message; }
    }, 600);
  };
  const chips = h("div", { class: "row wrap", style: "gap:8px" }, Object.entries(NOTE_TAGS).map(([k, v]) => {
    const c = h("button", { class: "chip" + (tags.has(k) ? " on" : ""), text: v });
    c.onclick = () => { tags.has(k) ? tags.delete(k) : tags.add(k); c.classList.toggle("on", tags.has(k)); save(); };
    return c;
  }));
  const ta = h("textarea", { class: "inp note-ta", rows: 3, placeholder: "Cualquier cosa que pueda influir: horarios, comidas, viajes…" });
  ta.value = (notes && notes.text) || "";
  ta.addEventListener("input", save);
  return h("div", { class: "card" }, chips, ta, h("div", { style: "text-align:right;margin-top:6px;min-height:18px" }, state));
}

// Dos paneles con el mismo eje de tiempo (sin doble eje): movimiento y voz,
// franjas ámbar = despierta, líneas de dormirse/despertar y fila de clips.
function nightChart(data) {
  const wrap = h("div", { class: "chart" });
  const sum = data.summary || {};
  const t0 = data.start, t1 = data.end;
  const mins = data.minutes;
  const byTs = new Map(mins.map(m => [m.ts, m]));
  wrap.append(h("div", { class: "legend", style: "margin:0 0 8px" },
    h("span", {}, h("i", { style: "background:var(--mov)" }), "Movimiento"),
    h("span", {}, h("i", { style: "background:var(--voice)" }), "Voz / llanto"),
    h("span", {}, h("i", { style: "background:var(--awake-band);box-shadow:inset 0 0 0 1px var(--awake)" }), "Despierta"),
    h("span", {}, h("i", { style: "background:var(--surface-3)" }), "Fuera de la cuna"),
    h("span", {}, h("i", { style: "background:var(--ink-2);border-radius:50%" }), "Clip")));
  const draw = () => {
    const W = Math.max(280, wrap.clientWidth || 320);
    const L = 26, R = 4, top = 54, H1 = 110, gap = 26, H2 = 52, stripH = 28, axisH = 20;
    const y1 = top, y2 = top + H1 + gap, ys = y2 + H2 + 8, ya = ys + stripH;
    const Ht = ya + axisH, pw = W - L - R;
    const X = (t) => L + (t - t0) / (t1 - t0) * pw;
    const svg = s("svg", { viewBox: `0 0 ${W} ${Ht}`, height: Ht, role: "img", "aria-label": "Movimiento y voz por minuto durante la noche" });
    for (const w of (sum.wakes || [])) {
      const xa = X(w.start), xb = Math.max(X(w.end), xa + 2);
      const pre = w.kind === "antes" || w.kind === "despues";
      const r = s("rect", { x: xa, y: y1, width: xb - xa, height: ys + stripH - y1 - 4, rx: 4,
        fill: w.kind === "fuera" ? "var(--surface-3)" : "var(--awake-band)", opacity: w.kind === "fuera" ? .7 : pre ? .5 : 1 });
      r.style.cursor = "pointer";
      r.addEventListener("click", () => { const c = data.clips.find(c => c.ep_start < w.end && c.ep_end > w.start && c.url); if (c) openClipSheet(c, data.clips); });
      svg.append(r);
    }
    const panel = (y, H, ticks, title) => {
      for (const t of ticks) {
        const yy = y + H - t / 60 * H;
        svg.append(s("line", { x1: L, x2: W - R, y1: yy, y2: yy, stroke: t ? "var(--grid)" : "var(--axis)" }));
        svg.append(s("text", { x: L - 6, y: yy + 3.5, "text-anchor": "end" }, t));
      }
      svg.append(s("text", { x: L, y: y - 8, class: "lbl" }, title));
    };
    panel(y1, H1, [0, 30, 60], "Movimiento · s por minuto");
    panel(y2, H2, [0, 60], "Voz / llanto · s por minuto");
    const area = (y, H, key, color) => {
      let d = "", line = "", open = false, prevX = null;
      for (const m of mins) {
        const xa = X(m.ts), xb = X(m.ts + 60);
        const yy = y + H - Math.min(60, m[key] || 0) / 60 * H;
        if (!open || xa - prevX > 0.5) {
          if (open) d += `L${prevX},${y + H}Z`;
          d += `M${xa},${y + H}L${xa},${yy}`; line += `M${xa},${yy}`; open = true;
        } else { d += `L${xa},${yy}`; line += `L${xa},${yy}`; }
        d += `L${xb},${yy}`; line += `L${xb},${yy}`; prevX = xb;
      }
      if (open) d += `L${prevX},${y + H}Z`;
      svg.append(s("path", { d, fill: color, "fill-opacity": .22 }));
      svg.append(s("path", { d: line, fill: "none", stroke: color, "stroke-width": 1.5, "stroke-linejoin": "round" }));
    };
    area(y1, H1, "active_secs", "var(--mov)");
    area(y2, H2, "voice_secs", "var(--voice)");
    const yt = y1 + H1 - data.umbral_minuto / 60 * H1;
    svg.append(s("line", { x1: L, x2: W - R, y1: yt, y2: yt, stroke: "var(--mov)", "stroke-opacity": .5 }));
    svg.append(s("text", { x: W - R, y: yt - 4, "text-anchor": "end" }, "minuto activo"));
    const mark = (ts, txt, anchor, ty = 13) => {
      if (!ts) return;
      const x = X(ts);
      svg.append(s("line", { x1: x, x2: x, y1: ty + 4, y2: ys + stripH - 4, stroke: "var(--ink-2)", "stroke-width": 1.5 }));
      svg.append(s("text", { x: x + (anchor === "end" ? -5 : 5), y: ty, "text-anchor": anchor, class: "lbl" }, txt));
    };
    const xOn = sum.sleep_onset ? X(sum.sleep_onset) : 0, xEnd = sum.sleep_end ? X(sum.sleep_end) : W;
    mark(sum.sleep_onset, "Se duerme " + hm(sum.sleep_onset), xOn > W - 130 ? "end" : "start");
    if (sum.final) mark(sum.sleep_end, "Despierta " + hm(sum.sleep_end), xEnd < 130 ? "start" : "end", 29);
    for (const c of data.clips) {
      const x = X(c.ep_start);
      const g = s("g", { tabindex: 0, role: "button", "aria-label": `Clip ${hm(c.ep_start)}` });
      g.style.cursor = "pointer";
      g.append(s("rect", { x: x - 12, y: ys, width: 24, height: 24, fill: "transparent" }));
      g.append(s("circle", { cx: x, cy: ys + 12, r: 5, fill: c.deleted ? "var(--surface)" : "var(--ink-2)", stroke: c.desacuerdo ? "var(--critical)" : c.deleted ? "var(--ink-2)" : "var(--surface)", "stroke-width": 2 }));
      g.addEventListener("pointerenter", (e) => showTip(e.clientX, e.clientY, `Clip ${hms(c.ep_start)}`, [
        [null, c.algo ? (c.algo === "despierta" ? "Despierta" : "Dormida") : "—", "Tapoli"],
        [null, c.label ? LABEL[c.label] : "sin etiqueta", "tú"]]));
      g.addEventListener("pointerleave", hideTip);
      g.addEventListener("click", (e) => { e.stopPropagation(); hideTip(); openClipSheet(c, data.clips); });
      svg.append(g);
    }
    svg.append(s("line", { x1: L, x2: W - R, y1: ya, y2: ya, stroke: "var(--axis)" }));
    for (let t = Math.ceil(t0 / 3600) * 3600; t <= t1; t += 3600) {
      const hr = hourOf(t);
      if (hr % 3 !== 1) continue;
      svg.append(s("text", { x: X(t), y: ya + 15, "text-anchor": "middle" }, String(hr).padStart(2, "0") + "h"));
    }
    const cross = s("line", { y1, y2: y2 + H2, stroke: "var(--ink-2)", visibility: "hidden" });
    const hit = s("rect", { x: L, y: y1, width: pw, height: y2 + H2 - y1, fill: "transparent" });
    const move = (e) => {
      const rect = svg.getBoundingClientRect();
      const px = (e.clientX - rect.left) * (W / rect.width);
      const t = Math.floor((t0 + (px - L) / pw * (t1 - t0)) / 60) * 60;
      if (t < t0 || t >= t1) return;
      cross.setAttribute("x1", X(t + 30)); cross.setAttribute("x2", X(t + 30)); cross.setAttribute("visibility", "visible");
      const m = byTs.get(t);
      const w = (sum.wakes || []).filter(w => t >= w.start && t < w.end).sort((p, q) => (q.kind === "fuera") - (p.kind === "fuera"))[0];
      const state = w && w.kind === "fuera" ? "fuera de la cuna" : !sum.sleep_onset || t < sum.sleep_onset ? "antes de dormirse" : w ? "despierta" : (sum.final && t >= sum.sleep_end) ? "levantada" : "dormida";
      showTip(e.clientX, e.clientY, `${hm(t)} · ${state}`, m ? [
        ["var(--mov)", `${m.active_secs} s`, "con movimiento"],
        ["var(--voice)", `${m.voice_secs} s`, "de voz o llanto"],
        [null, m.sound_max_db !== null ? `${num(m.sound_max_db, 0)} dBFS` : "—", "pico de sonido"]] : [[null, "sin datos", ""]]);
    };
    hit.addEventListener("pointermove", move);
    hit.addEventListener("pointerdown", move);
    hit.addEventListener("pointerleave", () => { cross.setAttribute("visibility", "hidden"); hideTip(); });
    svg.append(hit, cross);
    return svg;
  };
  mountResponsive(wrap, draw);
  return wrap;
}
function nightTable(data) {
  const sum = data.summary || {};
  const rows = (sum.wakes || []).map(w => h("tr", {},
    h("td", { text: { noche: "Despierta", final: "Despertar de la mañana", antes: "Antes de dormirse", despues: "Después", fuera: "Fuera de la cuna" }[w.kind] }),
    h("td", { text: `${hm(w.start)}–${hm(w.end)}` }), h("td", { text: dur(w.min) })));
  return h("details", { class: "table" }, h("summary", { text: "Ver como tabla" }),
    h("div", { class: "scroll-x" }, h("table", {}, h("thead", {}, h("tr", {}, h("th", { text: "Tramo" }), h("th", { text: "Hora" }), h("th", { text: "Duración" }))),
      h("tbody", {}, rows.length ? rows : h("tr", {}, h("td", { colspan: 3, class: "muted", text: "Sin despertares" }))))));
}

// ============================================================ DIRECTO
// ---------------------------------------------------------------- directo
// Cliente WebRTC propio: pinta directamente en el <video> visible. (El reproductor de
// go2rtc espera a un vídeo oculto, que la app de pantalla de inicio de iOS no reproduce.)
function rtcConnect(video, onState) {
  let ws = null, pc = null, closed = false, retryT = null, stream = null;
  const state = (st) => { if (!closed) onState(st); };
  const retry = () => {
    if (closed || retryT) return;
    cleanupConn();
    state("conectando");
    retryT = setTimeout(() => { retryT = null; open(); }, 3000);
  };
  const cleanupConn = () => {
    try { if (pc) pc.close(); } catch (e) { }
    try { if (ws) ws.close(); } catch (e) { }
    pc = null; ws = null;
  };
  const open = () => {
    stream = new MediaStream();
    ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/api/live/ws");
    ws.onopen = async () => {
      pc = new RTCPeerConnection({ iceServers: [] });
      pc.addTransceiver("video", { direction: "recvonly" });
      pc.addTransceiver("audio", { direction: "recvonly" });
      pc.onicecandidate = (ev) => {
        if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: "webrtc/candidate", value: ev.candidate ? ev.candidate.candidate : "" }));
      };
      pc.ontrack = (ev) => {
        if (!stream.getTracks().includes(ev.track)) stream.addTrack(ev.track);
        if (video.srcObject !== stream) video.srcObject = stream;
        video.play().catch(() => { });
      };
      pc.onconnectionstatechange = () => {
        if (!pc) return;
        if (pc.connectionState === "connected") state("rtc");
        else if (["failed", "disconnected", "closed"].includes(pc.connectionState)) retry();
      };
      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);
      ws.send(JSON.stringify({ type: "webrtc/offer", value: offer.sdp }));
    };
    ws.onmessage = (ev) => {
      let msg; try { msg = JSON.parse(ev.data); } catch (e) { return; }
      if (!pc) return;
      if (msg.type === "webrtc/answer") pc.setRemoteDescription({ type: "answer", sdp: msg.value }).catch(retry);
      else if (msg.type === "webrtc/candidate" && msg.value) pc.addIceCandidate({ candidate: msg.value, sdpMid: "0" }).catch(() => { });
      else if (msg.type === "error") retry();
    };
    ws.onclose = () => { if (!pc || pc.connectionState !== "connected") retry(); };
  };
  open();
  return { close: () => { closed = true; clearTimeout(retryT); cleanupConn(); video.srcObject = null; } };
}

let MsePlayer = null;
async function msePlayerClass() {   // respaldo (unos segundos de retraso)
  if (MsePlayer) return MsePlayer;
  const { VideoRTC } = await import("/static/video-rtc.js?v=" + VERSION);
  class TapoliMse extends VideoRTC {
    oninit() { super.oninit(); this.video.controls = false; this.video.muted = true; this.video.style.objectFit = "cover"; }
  }
  if (!customElements.get("tapoli-mse")) customElements.define("tapoli-mse", TapoliMse);
  return (MsePlayer = TapoliMse);
}

VIEWS.directo = async () => {
  setHeader({ title: "Directo" });
  await refreshStatus();
  const out = h("div", {}, titleBlock(null, "Directo", `${cap(NAME)} · cuna`));
  let video = h("video", { playsinline: true, autoplay: true, muted: true });
  video.muted = true;
  const screen = h("div", { style: "position:absolute;inset:0" }, video);
  const modeBadge = h("span", { class: "badge", text: "conectando…" });
  let mode = "conectando", mse = null;
  const muteBtn = h("button", { class: "iconbtn", "aria-label": "Activar sonido" }, icon("mute"));
  const setMuteIcon = () => { muteBtn.replaceChildren(icon(video.muted ? "mute" : "sound")); muteBtn.setAttribute("aria-label", video.muted ? "Activar sonido" : "Silenciar"); };
  muteBtn.onclick = () => { video.muted = !video.muted; video.play().catch(() => { }); setMuteIcon(); };
  const fsBtn = h("button", { class: "iconbtn", "aria-label": "Pantalla completa", onclick: () => {
    if (video.webkitEnterFullscreen) video.webkitEnterFullscreen(); else if (video.requestFullscreen) video.requestFullscreen();
  } }, icon("expand"));
  const offWhen = h("div", { class: "off-s" });
  const off = h("div", { class: "cam-off", hidden: true },
    h("div", { class: "off-ic" }, icon("camoff")),
    h("div", { class: "off-t", text: "Cámara apagada" }), offWhen,
    h("div", { class: "off-s", text: "Se conectará sola en cuanto la enciendas." }));
  const player = h("div", { class: "player" }, screen,
    h("div", { class: "over live-only" }, h("span", { class: "badge live" }, "EN DIRECTO"), modeBadge), off,
    h("div", { class: "ctrls live-only" }, muteBtn, fsBtn));
  out.append(player);

  // Vigilante: "tiempo real" solo si llegan fotogramas nuevos. Si se congela (p. ej. al volver
  // a la app tras bloquear el iPhone), se reconecta.
  let lastFrame = 0, lastT = -1;
  const onFrame = () => { lastFrame = performance.now(); if (video.requestVideoFrameCallback) video.requestVideoFrameCallback(onFrame); };
  if (video.requestVideoFrameCallback) video.requestVideoFrameCallback(onFrame);
  let rtc = rtcConnect(video, (st) => { mode = st; if (st === "rtc") lastFrame = performance.now(); });
  const reconnect = () => {
    if (mse) return;
    rtc.close(); mode = "conectando"; lastFrame = performance.now();
    rtc = rtcConnect(video, (st) => { mode = st; if (st === "rtc") lastFrame = performance.now(); });
  };
  const watchdog = setInterval(() => {
    if (!video.requestVideoFrameCallback && video.currentTime !== lastT) { lastT = video.currentTime; lastFrame = performance.now(); }
    if (mode === "rtc" && performance.now() - lastFrame > 4000) reconnect();
  }, 1000);
  const onVis = () => { if (document.visibilityState === "visible") reconnect(); else if (!mse) { rtc.close(); mode = "conectando"; } };
  document.addEventListener("visibilitychange", onVis);
  window.addEventListener("pageshow", onVis);
  const fallback = setTimeout(async () => {     // WebRTC no conecta: usar MSE
    if (mode === "rtc") return;
    rtc.close();
    await msePlayerClass();
    mse = document.createElement("tapoli-mse");
    mse.mode = "mse";
    mse.style.cssText = "display:block;width:100%;height:100%";
    screen.replaceChildren(mse);
    mse.src = "/api/live/ws";
    video = mse.video;
    mode = "mse";
  }, 8000);

  // indicadores en vivo
  const movBar = h("div", { style: { width: "0%", background: "var(--mov)" } });
  const sndBar = h("div", { style: { width: "0%", background: "var(--voice)" } });
  const movV = h("span", { class: "nums ink2 small" }), sndV = h("span", { class: "nums ink2 small" });
  const stateT = h("div", { class: "t" }), stateS = h("div", { class: "s" }), orb = h("div", { class: "orb" });
  out.append(section("Ahora"));
  out.append(h("div", { class: "card" },
    h("div", { class: "now" }, orb, h("div", { class: "grow" }, stateT, stateS)),
    h("div", { style: "margin-top:18px;display:grid;gap:14px" },
      h("div", {}, h("div", { class: "row between small", style: "margin-bottom:6px" }, h("span", { class: "ink2", text: "Movimiento en la cuna" }), movV), h("div", { class: "meter" }, movBar)),
      h("div", {}, h("div", { class: "row between small", style: "margin-bottom:6px" }, h("span", { class: "ink2", text: "Sonido" }), sndV), h("div", { class: "meter" }, sndBar)))));
  let lastIcon = null;
  const tick = async () => {
    await refreshStatus();
    const ns = nowState();
    if (lastIcon !== ns.icon) { orb.replaceChildren(icon(ns.icon)); lastIcon = ns.icon; }
    stateT.textContent = ns.t; stateS.textContent = ns.s;
    const isOn = !!camState().ok;
    off.hidden = isOn;
    player.classList.toggle("is-off", !isOn);
    if (!isOn && STATUS && STATUS.ahora_t) {
      const d = new Date(STATUS.ahora_t * 1000), today = new Date().toDateString() === d.toDateString();
      offWhen.textContent = `Última imagen ${today ? "hoy" : d.toLocaleDateString("es-ES", { weekday: "long" })} a las ${hm(STATUS.ahora_t)}`;
    }
    if (!isOn) {          // cámara apagada: no mostrar el último valor que quedó
      movBar.style.width = sndBar.style.width = "0%";
      movV.textContent = sndV.textContent = "—";
    } else {
      const a = (STATUS && STATUS.ahora) || {};
      const m = a.motion || 0, db = a.sound ?? -72;
      movBar.style.width = Math.min(100, m / 0.08 * 100) + "%";
      movV.textContent = pct(m) + " de la cuna";
      sndBar.style.width = Math.max(0, Math.min(100, (db + 72) / 52 * 100)) + "%";
      sndV.textContent = a.voice ? "voz o llanto" : db <= -70 ? "silencio" : num(db, 0) + " dBFS";
    }
    modeBadge.textContent = liveLabel();
    setMuteIcon();
  };
  tick();
  const iv = setInterval(tick, 2500);
  function liveLabel() {
    if (mode === "rtc") return performance.now() - lastFrame < 2500 ? "tiempo real" : "reconectando…";
    return mode === "mse" ? "modo lento" : "conectando…";
  }
  const fast = setInterval(() => { modeBadge.textContent = liveLabel(); }, 500);
  return {
    node: out, cleanup: () => {
      clearInterval(iv); clearInterval(fast); clearInterval(watchdog); clearTimeout(fallback);
      document.removeEventListener("visibilitychange", onVis);
      window.removeEventListener("pageshow", onVis);
      rtc.close();
      if (mse) { try { mse.ondisconnect(); } catch (e) { } mse.remove(); }
    }
  };
};

// ============================================================ REVISAR
let REV = { mode: "pendientes" };
function verdictTags(c) {
  const tags = [];
  if (c.algo) tags.push(h("span", { class: "tag " + (c.algo === "despierta" ? "awake" : "sleep") },
    icon(c.algo === "despierta" ? "awake" : "asleep"), c.algo === "despierta" ? "Tapoli cree: despierta" : "Tapoli cree: dormida"));
  if (myLabel(c)) tags.push(h("span", { class: "tag" }, icon(LABEL_ICON[myLabel(c)]), "Tú: " + LABEL[myLabel(c)]));
  if (c.desacuerdo) tags.push(h("span", { class: "tag bad" }, icon("alert"), "No coincide"));
  return tags;
}
function clipDetails(c) {
  const bits = [c.algo_reason, `movimiento máx. ${pct(c.motion_max)}`, `${c.active_secs ?? 0} s moviéndose`,
    c.voice_secs ? `${c.voice_secs} s de voz` : "sin voz", c.out_max > 0.02 ? "movimiento fuera de la cuna (¿un adulto?)" : null];
  return h("p", { class: "small muted", style: "margin:6px 2px 0", text: bits.filter(Boolean).join(" · ") });
}
function clipPlayer(c, autoplay) {
  if (!c.url) return h("div", { class: "player" }, c.thumb ? h("img", { src: c.thumb, alt: "" }) : null,
    h("div", { class: "over" }, h("span", { class: "badge", text: "Vídeo borrado por la limpieza" })));
  const v = h("video", { src: c.url, controls: true, playsinline: true, preload: autoplay ? "auto" : "none", poster: c.thumb || null });
  if (autoplay) setTimeout(() => v.play().catch(() => { }), 50);
  return h("div", { class: "player" }, v,
    h("div", { class: "over" }, h("span", { class: "badge nums", text: `${hms(c.ep_start)} · ${Math.round(c.end_ts - c.start_ts)} s` + (c.parts > 1 ? ` · parte ${c.part}/${c.parts}` : "") })));
}
function labelPanel(c, onLabel) {
  const cur = myLabel(c);
  const send = async (lab, btn) => {
    const prevLab = myLabel(c);
    const newLab = cur === lab ? null : lab;
    btn.disabled = true;
    try {
      const r = await post(`/api/clips/${c.id}/label`, { label: newLab });
      Object.assign(c, r);
      refreshStatus();
      onLabel(c, prevLab);
    } catch (err) { toast(err.message); btn.disabled = false; }
  };
  const mk = (lab) => h("button", { class: `lbl-btn ${lab}${cur === lab ? " sel" : ""}`, "aria-pressed": cur === lab ? "true" : "false",
    onclick: (e) => send(lab, e.currentTarget) }, icon(LABEL_ICON[lab]), LABEL[lab]);
  return h("div", { class: "label-panel" },
    h("div", { class: "ask" }, `¿Cómo estaba ${NAME}?`, h("span", { class: "ask-sub", text: "«Dormida» también si se mueve sin despertarse" })),
    h("div", { class: "labels" }, mk("dormida"), mk("despierta"), mk("falsa")),
    h("button", { class: "nose-btn" + (cur === "nose" ? " sel" : ""), onclick: (e) => send("nose", e.currentTarget) }, icon("help"), "No lo sé · saltar"));
}
async function undoLabel(c, prevLab, after) {
  const r = await post(`/api/clips/${c.id}/label`, { label: prevLab || null });   // prevLab puede ser "nose"
  Object.assign(c, r); refreshStatus(); after();
}

function openClipSheet(c, list) {
  const dlg = $("#sheet");
  const render = (autoplay) => {
    dlg.replaceChildren(h("div", { class: "grab" }), h("div", { class: "inner" },
      h("div", { class: "row between", style: "margin:4px 2px 12px" },
        h("b", { style: "font-size:17px", text: `Clip de las ${hm(c.ep_start)}` }),
        h("button", { class: "btn small", onclick: () => dlg.close() }, "Cerrar")),
      clipPlayer(c, autoplay),
      labelPanel(c, (cc, prevLab) => {
        render(false);
        toast(myLabel(cc) ? `Guardado: ${LABEL[myLabel(cc)]}` : "Etiqueta quitada", { label: "Deshacer", fn: () => undoLabel(cc, prevLab, () => render(false)) });
      }),
      h("div", { class: "verdict" }, verdictTags(c)), clipDetails(c)));
  };
  render(true);
  dlg.addEventListener("close", () => {
    dlg.querySelectorAll("video").forEach(v => v.pause());
    if (dlg.dataset.silent) { delete dlg.dataset.silent; return; }
    route.keepScroll = true; route();
  }, { once: true });
  dlg.showModal();
  dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });
}

VIEWS.revisar = async (night) => {
  setHeader({ title: "Revisar" });
  const rows = await api("/api/nights");
  const withClips = rows.filter(r => r.clips > 0);
  const out = h("div", {}, titleBlock(null, "Revisar"));
  if (!withClips.length) {
    out.append(h("div", { class: "card done" }, h("div", { class: "orb" }, icon("review")), h("b", { text: "Todavía no hay clips" }),
      h("p", { class: "ink2", text: "Cada vez que se mueva por la noche se guardará un clip corto para revisarlo aquí." })));
    return out;
  }
  night = night || (withClips.find(r => r.pendientes > 0) || withClips[0]).night;
  const d = await api("/api/night/" + night);
  out.append(h("div", { class: "chips" }, withClips.slice(0, 21).map(r => {
    const pend = r.pendientes || 0;
    return h("a", { class: "chip" + (r.night === night ? " on" : ""), href: "#revisar/" + r.night }, cap(dayShort(r.night)), pend > 0 ? h("b", { text: pend }) : null);
  })));
  const seg = h("div", { class: "seg" });
  const body = h("div");
  const modes = { pendientes: "Dudosos", todos: "Todos", desacuerdos: "No coinciden" };
  const renderSeg = () => seg.replaceChildren(...Object.entries(modes).map(([k, v]) => h("button", { class: REV.mode === k ? "on" : "", onclick: () => { REV.mode = k; renderSeg(); renderBody(); } }, v)));
  const clips = d.clips;
  const renderBody = () => {
    if (REV.mode === "pendientes") {
      // solo los dudosos: el resto de movimientos cuentan como inquietud sin preguntarte
      const pend = clips.filter(c => c.review && !c.label && !c.skipped && c.url);
      const total = clips.filter(c => c.review && c.url).length;
      const doneN = total - pend.length;
      if (!pend.length) {
        const lab = clips.filter(c => c.label).length;
        body.replaceChildren(h("div", { class: "card done" }, h("div", { class: "orb" }, icon("check")),
          h("b", { style: "font-size:18px", text: "No hay dudas pendientes" }),
          h("p", { class: "ink2", text: `Solo te pregunto por los clips en los que no está claro si estaba despierta. El resto de movimientos (${clips.filter(c => !c.review).length} esta noche) cuentan como inquietud y los tienes en «Todos». ${lab} etiquetados en total.` }),
          h("a", { class: "btn", href: "#calibrar" }, icon("target"), "Ir a calibración")));
        return;
      }
      const c = pend[0];
      body.replaceChildren(
        h("div", { class: "row between small", style: "margin:0 2px 6px" }, h("span", { class: "ink2", text: `${doneN + 1} de ${total}` }), h("span", { class: "muted", text: `${pend.length} por revisar` })),
        h("div", { class: "progress-line" }, h("div", { style: { width: (doneN / Math.max(1, total) * 100) + "%" } })),
        clipPlayer(c, false),
        labelPanel(c, (cc, prevLab) => {
          toast(myLabel(cc) ? `Guardado: ${LABEL[myLabel(cc)]}` : "Etiqueta quitada", { label: "Deshacer", fn: () => undoLabel(cc, prevLab, renderBody) });
          renderBody();
        }),
        h("div", { class: "verdict" }, verdictTags(c)), clipDetails(c));
    } else {
      const items = clips.filter(c => REV.mode === "todos" || c.desacuerdo);
      if (!items.length) { body.replaceChildren(h("div", { class: "card empty", text: REV.mode === "todos" ? "No hay clips esta noche." : "Ningún desacuerdo. Tapoli y tú pensáis lo mismo." })); return; }
      body.replaceChildren(h("div", { class: "list" }, items.map(c => h("div", { class: "clip-row" + (c.desacuerdo ? " dis" : ""), onclick: () => openClipSheet(c, clips) },
        h("div", { class: "th", style: c.thumb ? { backgroundImage: `url("${c.thumb}")` } : null }, h("span", { text: `${Math.round(c.end_ts - c.start_ts)} s` })),
        h("div", { class: "grow" }, h("div", { class: "t", text: hms(c.ep_start) }),
          h("div", { class: "row wrap", style: "gap:6px;margin-top:5px" }, verdictTags(c).slice(0, 2))),
        icon("chev", "i")))));
    }
  };
  renderSeg(); renderBody();
  out.append(seg, body);
  return out;
};

// ============================================================ AJUSTES
VIEWS.ajustes = async (sub) => {
  if (sub === "calibrar") return VIEWS.calibrar();
  if (sub === "zona") return zonaView();
  if (sub === "umbrales") return umbralesView();
  if (sub === "noche") return nocheView();
  if (sub === "telegram") return telegramView();
  setHeader({ title: "Ajustes" });
  const [cf, cal] = await Promise.all([api("/api/config"), api("/api/calibrate"), refreshStatus()]);
  const nLab = Object.values(cal.etiquetas || {}).reduce((a, b) => a + b, 0);
  const st = (STATUS && STATUS.almacenamiento) || {};
  const gb = (st.bytes || 0) / 1e9;
  const out = h("div", {}, titleBlock(null, "Ajustes"));
  const link = (href, ic, t, sub2, val) => h("a", { class: "row-link", href },
    h("div", { class: "ic" }, icon(ic)), h("div", { class: "grow" }, h("div", { class: "t", text: t }), sub2 ? h("div", { class: "s", text: sub2 }) : null),
    val ? h("span", { class: "val", text: val }) : null, icon("chev", "chev"));
  out.append(section("Análisis"));
  out.append(h("div", { class: "list" },
    link("#calibrar", "target", "Calibración", "Ajusta los umbrales con tus etiquetas", `${nLab} etiquetas`),
    link("#ajustes/zona", "crib", "Zona de la cuna", "Solo cuenta el movimiento dentro"),
    link("#ajustes/noche", "moon", "Horario de la noche", `${cf.config.noche.inicio}–${cf.config.noche.fin} · dormida tras ${cf.config.noche.dormirse_calma_min} min · mañana desde ${cf.config.noche.despertar_final_desde}`),
    link("#ajustes/umbrales", "sliders", "Umbrales activos", `Sensibilidad ${cf.config.despertar.sensibilidad} · X ${cf.config.despertar.x_minutos_activos} · Y ${cf.config.despertar.y_ventana_min} · Z ${cf.config.despertar.z_calma_min} · N ${cf.config.despertar.n_fusion_min}`)));
  out.append(section("Sistema"));
  const chats = (STATUS && STATUS.telegram_chats) || [];
  out.append(h("div", { class: "list" },
    h("div", { class: "row-link", style: "cursor:default" }, h("div", { class: "ic" }, icon("disk")),
      h("div", { class: "grow" }, h("div", { class: "row between" }, h("div", { class: "t", text: "Clips guardados" }), h("span", { class: "val", text: `${num(gb, 2)} GB` })),
        h("div", { class: "meter", style: "margin:8px 0 6px" }, h("div", { style: { width: Math.min(100, gb / (STATUS ? STATUS.aviso_gb : 5) * 100) + "%", background: gb > (STATUS ? STATUS.aviso_gb : 5) ? "var(--critical)" : "var(--accent)" } })),
        h("div", { class: "s", text: `${st.clips ?? 0} archivos · aviso a ${STATUS ? STATUS.aviso_gb : 5} GB · sin etiquetar se borran a los ${cf.config.almacenamiento.dias_clips_sin_etiquetar} días` }))),
    h("a", { class: "row-link", href: "#ajustes/telegram" }, h("div", { class: "ic" }, icon("send")),
      h("div", { class: "grow" }, h("div", { class: "t", text: "Resumen por Telegram" }),
        h("div", { class: "s", text: chats.length ? `Cada mañana a ${chats.map(c => c.name).join(" y ")}` : "Sin vincular · toca para configurarlo" })),
      icon("chev", "chev"))));
  return out;
};
async function umbralesView() {
  setHeader({ back: "#ajustes", backLabel: "Ajustes", title: "Umbrales" });
  const cf = await api("/api/config");
  const d = cf.config.despertar, n = cf.config.noche;
  const row = (k, v, hint) => h("div", { class: "row-link", style: "cursor:default;align-items:flex-start" },
    h("div", { class: "grow" }, h("div", { class: "t", text: k }), hint ? h("div", { class: "s", text: hint }) : null), h("span", { class: "val", text: v }));
  return h("div", {}, titleBlock(null, "Umbrales activos", "Se cambian desde Calibración o en config/config.yaml"),
    section("Movimiento"),
    h("div", { class: "list" },
      row("Sensibilidad", String(d.sensibilidad), `Un segundo cuenta como movimiento si cambia más del ${pct(cf.umbral_movimiento)} de la cuna`),
      row("Minuto activo", `≥ ${d.seg_activos_minuto} s`, "Segundos con movimiento para que un minuto cuente como activo"),
      row("Minuto con voz", `≥ ${d.voz_seg_minuto} s`, "Segundos de voz o llanto")),
    section("Despertar"),
    h("div", { class: "list" },
      row("X · minutos activos", String(d.x_minutos_activos), `Despierta si hay ${d.x_minutos_activos} minutos activos…`),
      row("Y · ventana", `${d.y_ventana_min} min`, `…dentro de ${d.y_ventana_min} minutos (o movimiento + voz)`),
      row("Z · calma para terminar", `${d.z_calma_min} min`, "Minutos seguidos de calma que cierran el despertar"),
      row("N · fusionar", `< ${d.n_fusion_min} min`, "Despertares más juntos se cuentan como uno")));
}

async function telegramView() {
  setHeader({ back: "#ajustes", backLabel: "Ajustes", title: "Telegram" });
  await refreshStatus();
  const cf = await api("/api/config");
  const chats = (STATUS && STATUS.telegram_chats) || [];
  const out = h("div", {}, titleBlock(null, "Resumen por Telegram",
    `Cuando termina la noche (a las ${cf.config.noche.fin}) Tapoli manda a cada móvil vinculado un resumen de cómo ha dormido ${NAME}.`));
  if (!STATUS || !STATUS.telegram_token) { out.append(h("div", { class: "card", text: "Falta el token del bot en la configuración." })); return out; }
  if (chats.length) out.append(section("Móviles vinculados"), h("div", { class: "list" }, chats.map(c =>
    h("div", { class: "row-link", style: "cursor:default" }, h("div", { class: "ic" }, icon("send")),
      h("div", { class: "grow" }, h("div", { class: "t", text: c.name })),
      c.name === "configurado en .env" ? null : h("button", { class: "btn small", onclick: async () => {
        if (!await ask(`¿Dejar de enviar el resumen a ${c.name}?`, "Ese móvil dejará de recibir el resumen de cada mañana. Podrás volver a vincularlo cuando quieras.", "Quitar", { icon: "send", danger: true })) return;
        await post("/api/telegram/unlink", { id: c.id }); route();
      } }, "Quitar")))));
  const status = h("p", { class: "small ink2", style: "margin:10px 4px 0;text-align:center" });
  const linkBtn = h("button", { class: "btn primary block", style: "margin-top:16px" }, icon("plus-square"), chats.length ? "Añadir otro móvil" : "Vincular mi móvil");
  let poll = null;
  linkBtn.onclick = async () => {
    linkBtn.disabled = true;
    try {
      const r = await post("/api/telegram/link");
      const a = h("div", {},
        h("a", { class: "btn block", href: r.app_url, style: "margin-top:10px" }, icon("send"), `Abrir @${r.bot} en Telegram`),
        h("a", { class: "small", href: r.url, target: "_blank", rel: "noopener", style: "display:block;text-align:center;margin-top:8px;color:var(--accent)" },
          "¿No se abre? Usa este enlace"));
      linkBtn.after(a);
      status.textContent = "Abre Telegram, pulsa «Iniciar» y vuelve aquí…";
      const t0 = Date.now();
      poll = setInterval(async () => {
        const st = await api("/api/telegram/link");
        if (st.estado === "ok") { clearInterval(poll); toast(`Vinculado: ${st.name}`); route(); }
        else if (st.estado === "caducado" || Date.now() - t0 > 300000) { clearInterval(poll); status.textContent = "Se ha caducado. Vuelve a pulsar el botón."; linkBtn.disabled = false; a.remove(); }
      }, 2000);
    } catch (e) { toast(e.message); linkBtn.disabled = false; }
  };
  out.append(linkBtn, status);
  if (chats.length) out.append(h("button", { class: "btn block", style: "margin-top:10px", onclick: async (e) => {
    e.currentTarget.disabled = true;
    try { await post("/api/telegram/test"); toast("Enviado: mira Telegram"); } catch (err) { toast(err.message); }
    e.currentTarget.disabled = false;
  } }, "Enviar el resumen de la última noche"));
  return { node: out, cleanup: () => clearInterval(poll) };
}

async function nocheView() {
  setHeader({ back: "#ajustes", backLabel: "Ajustes", title: "Horario de la noche" });
  const cf = await api("/api/config");
  const n = cf.config.noche;
  const field = (label, hint, input) => h("label", { class: "row-link", style: "cursor:default" },
    h("div", { class: "grow" }, h("div", { class: "t", text: label }), h("div", { class: "s", text: hint })), input);
  const time = (v) => h("input", { type: "time", value: v, class: "inp", step: 300 });
  const ini = time(n.inicio), fin = time(n.fin), man = time(n.despertar_final_desde);
  let calma = Number(n.dormirse_calma_min);
  const calmaV = h("span", { class: "nums", style: "min-width:58px;text-align:center;font-weight:650", text: `${calma} min` });
  const step = (d) => { calma = Math.max(5, Math.min(60, calma + d)); calmaV.textContent = `${calma} min`; };
  const stepper = h("div", { class: "stepper" }, h("button", { onclick: (e) => { e.preventDefault(); step(-5); } }, "−"), calmaV,
    h("button", { onclick: (e) => { e.preventDefault(); step(5); } }, "+"));
  const save = h("button", { class: "btn primary block", style: "margin-top:16px" }, icon("check"), "Guardar y recalcular");
  save.onclick = async () => {
    save.disabled = true;
    try {
      await post("/api/config/noche", { inicio: ini.value, fin: fin.value, despertar_final_desde: man.value, dormirse_calma_min: calma });
      toast("Guardado · recalculando las noches");
    } catch (e) { toast(e.message); }
    save.disabled = false;
  };
  return h("div", {}, titleBlock(null, "Horario de la noche", "Cambia cómo se mide cada noche. Al guardar se recalculan todas."),
    h("div", { class: "list" },
      field("Empieza", "Desde esta hora se analiza la noche", ini),
      field("Termina", "Hora de cierre y resumen de la noche", fin),
      field("Despertar de la mañana", "El último despertar desde esta hora no cuenta como nocturno", man),
      field("Para considerarla dormida", "Minutos seguidos de calma tras acostarla", stepper)),
    save);
}

async function zonaView() {
  setHeader({ back: "#ajustes", backLabel: "Ajustes", title: "Zona de la cuna" });
  const [cf, ref] = await Promise.all([api("/api/config"), api("/api/zona_ref"), refreshStatus()]);
  const cfg = cf.config;
  const live = camState().ok;   // con la cámara apagada: imagen de referencia, solo lectura
  let poly = (live || !ref.poly ? cfg.movimiento.zona_cuna : ref.poly).map(p => [...p]);
  let VH = 562.5;
  const img = h("img", { alt: "Imagen de la cámara", src: (live ? "/api/snapshot.jpg?t=" : "/api/zona_ref.jpg?t=") + Date.now() });
  const ov = s("svg", { viewBox: `0 0 1000 ${VH}`, preserveAspectRatio: "none" });
  const roi = h("div", { class: "roi" }, img, ov);
  const msg = h("span", { class: "small ink2" });
  const draw = () => {
    ov.replaceChildren();
    for (const r of cfg.movimiento.zonas_excluidas || [])
      ov.append(s("rect", { x: r[0] * 1000, y: r[1] * VH, width: (r[2] - r[0]) * 1000, height: (r[3] - r[1]) * VH, fill: "rgba(0,0,0,.55)" }));
    if (poly.length) ov.append(s("polygon", { points: poly.map(p => `${p[0] * 1000},${p[1] * VH}`).join(" "),
      fill: "rgba(144,133,233,.22)", stroke: "#b3a9ff", "stroke-width": 3, "stroke-linejoin": "round", "vector-effect": "non-scaling-stroke" }));
    poly.forEach(p => ov.append(s("circle", { cx: p[0] * 1000, cy: p[1] * VH, r: 9, fill: "#b3a9ff", stroke: "#fff", "stroke-width": 2, "vector-effect": "non-scaling-stroke" })));
  };
  img.addEventListener("load", () => { VH = 1000 * img.naturalHeight / img.naturalWidth; ov.setAttribute("viewBox", `0 0 1000 ${VH}`); draw(); });
  img.addEventListener("error", () => roi.replaceChildren(h("p", { class: "empty", text: "Aún no hay imagen de la cámara." })));
  roi.addEventListener("pointerdown", (e) => {
    if (!live) { toast("Enciende la cámara para dibujar la zona"); return; }
    const r = roi.getBoundingClientRect();
    poly.push([+((e.clientX - r.left) / r.width).toFixed(3), +((e.clientY - r.top) / r.height).toFixed(3)]);
    draw(); msg.textContent = `${poly.length} puntos · sin guardar`;
  });
  draw();
  await refreshStatus();
  const za = STATUS && STATUS.zona_auto;
  const zaTxt = !za ? "Al dibujar la zona se guarda la imagen de referencia." :
    za.estado === "manual" ? "Dibujada a mano. Si movéis la cámara, se reajustará sola al conectarse." :
    za.estado === "ok" ? (za.aplicado ? `Reajustada sola ${hm(za.ts)}: la cámara se había movido ${num(Math.abs(za.dx) * 100, 0)} % en horizontal y ${num(Math.abs(za.dy) * 100, 0)} % en vertical.` :
      `Comprobada ${hm(za.ts)}: la cámara está en su sitio.`) :
    za.estado === "muy_movida" ? `Comprobada ${hm(za.ts)}: la cámara se ha movido demasiado para reajustarla sola. Dibuja la zona otra vez.` :
    `Comprobada ${hm(za.ts)}: no se ha podido comparar bien con la referencia (¿luz muy distinta?). Revisa que la zona encaje.`;
  const snapTxt = !live ? "Cámara apagada: se muestra la imagen de referencia. Enciéndela para dibujar la zona." :
    STATUS && STATUS.snapshot_ts ? `Imagen de las ${hm(STATUS.snapshot_ts)}` : "";
  return h("div", {}, titleBlock(null, "Zona de la cuna", "Solo cuenta el movimiento dentro del polígono. Las zonas oscuras (reloj y logo) se ignoran siempre."),
    h("div", { class: "card banner", style: "margin-bottom:12px" },
      h("div", { class: "ic" + (za && (za.estado === "muy_movida" || za.estado === "dudosa") ? " warn" : "") }, icon("target")),
      h("div", { class: "small" }, h("b", { text: "Reajuste automático" }), h("div", { class: "ink2", style: "margin-top:2px", text: zaTxt }))),
    roi, snapTxt ? h("div", { class: "small muted", style: "margin:6px 4px 0", text: snapTxt }) : null,
    h("p", { class: "small muted", style: "margin:10px 4px", text: "Pulsa «Empezar de nuevo» y toca las esquinas del interior de la cuna en orden." }),
    h("div", { class: "row wrap", style: "gap:8px" },
      h("button", { class: "btn small", disabled: !live, onclick: () => { poly = []; draw(); msg.textContent = "Toca las esquinas"; } }, "Empezar de nuevo"),
      h("button", { class: "btn small", disabled: !live, onclick: () => { poly.pop(); draw(); } }, "Deshacer punto")),
    h("button", { class: "btn primary block", style: "margin-top:14px", disabled: !live, onclick: async () => {
      try { await post("/api/roi", { poly }); toast("Zona guardada · se aplica en menos de 1 minuto"); msg.textContent = ""; } catch (e) { toast(e.message); }
    } }, icon("check"), "Guardar zona"),
    h("div", { style: "margin-top:8px;text-align:center" }, msg));
}

// ---------------------------------------------------------------- calibración
const PNAMES = { sensibilidad: "Sensibilidad", X: "X · min activos", Y: "Y · ventana", Z: "Z · calma", N: "N · fusionar" };
VIEWS.calibrar = async () => {
  setHeader({ back: "#ajustes", backLabel: "Ajustes", title: "Calibración" });
  const [st, cf] = await Promise.all([api("/api/calibrate"), api("/api/config")]);
  const lab = st.etiquetas || {};
  const total = Object.values(lab).reduce((a, b) => a + b, 0);
  const out = h("div", {}, titleBlock(null, "Calibración", "Prueba cientos de combinaciones de umbrales con tus etiquetas y te propone la que más acierta. Nada cambia sin tu confirmación."));
  const tile = (ic, color, n, k) => h("div", { class: "stat", style: "padding-top:0" },
    h("div", { class: "row", style: `gap:6px;color:${color}` }, icon(ic)), h("div", { class: "v", style: "margin-top:6px", text: String(n) }), h("div", { class: "k", text: k }));
  out.append(h("div", { class: "card" },
    h("div", { class: "stats", style: "border-top:0;margin-top:0" },
      tile("asleep", "var(--sleep)", lab.dormida || 0, "Dormida"), tile("awake", "var(--awake)", lab.despierta || 0, "Despierta"), tile("false", "var(--muted)", lab.falsa || 0, "Falsa alarma")),
    total < 30 ? h("div", { style: "margin-top:16px" }, h("div", { class: "row between small", style: "margin-bottom:6px" }, h("span", { class: "ink2", text: "Para una propuesta fiable" }), h("span", { class: "muted nums", text: `${total} / 30` })),
      h("div", { class: "meter" }, h("div", { style: { width: Math.min(100, total / 30 * 100) + "%", background: "var(--accent)" } }))) : null));

  const startBtn = h("button", { class: "btn primary block", disabled: !total }, icon("target"), "Buscar mejores umbrales");
  const resBody = h("div");
  out.append(startBtn, resBody);
  startBtn.onclick = async () => { startBtn.disabled = true; await post("/api/calibrate"); poll(); };
  const showRun = (run) => {
    if (!run) { resBody.replaceChildren(); return; }
    if (run.status === "corriendo") {
      resBody.replaceChildren(h("div", { class: "card", style: "margin-top:12px" }, h("div", { class: "ink2 small", text: "Calculando…" }),
        h("div", { class: "meter", style: "margin-top:8px" }, h("div", { style: { width: (run.progress * 100) + "%", background: "var(--accent)" } }))));
      return;
    }
    startBtn.disabled = !total;
    const r = run.result || {};
    if (r.error) { resBody.replaceChildren(h("div", { class: "card", style: "margin-top:12px", text: r.error })); return; }
    const same = JSON.stringify(r.best.params) === JSON.stringify(r.current.params);
    const cmp = h("div", { class: "metric-cmp" }, h("span", { class: "h" }), h("span", { class: "h n", text: "Actual" }), h("span", { class: "h n", text: "Propuesta" }));
    for (const k of Object.keys(PNAMES)) cmp.append(h("span", { text: PNAMES[k] }), h("span", { class: "n", text: r.current.params[k] }),
      h("span", { class: "n" + (r.best.params[k] !== r.current.params[k] ? " best" : ""), text: r.best.params[k] }));
    const mrow = (k, name) => [h("span", { text: name }), h("span", { class: "n", text: pct(r.current.metrics[k]) }),
      h("span", { class: "n" + (r.best.metrics[k] > r.current.metrics[k] ? " best" : ""), text: pct(r.best.metrics[k]) })];
    const cmp2 = h("div", { class: "metric-cmp", style: "margin-top:14px;padding-top:14px;border-top:1px solid var(--hair)" },
      mrow("precision", "Precisión"), mrow("sensibilidad", "Detecta"), mrow("f1", "F1"), mrow("exactitud", "Aciertos"));
    const applyBtn = h("button", { class: "btn primary block", style: "margin-top:16px", disabled: same }, same ? "Tu configuración ya es la mejor" : "Aplicar propuesta");
    applyBtn.onclick = async () => {
      const values = {}; for (const k of Object.keys(PNAMES)) values[r.keymap[k]] = r.best.params[k];
      if (!await ask("¿Aplicar los nuevos umbrales?", "La configuración actual queda guardada en el historial para poder volver a ella.", "Aplicar", { icon: "target" })) return;
      await post("/api/config/apply", { values, source: "calibracion", note: `F1 ${pct(r.best.metrics.f1)} con ${r.n_labels} etiquetas`, metrics: r.best.metrics });
      if (await ask("Umbrales aplicados", "¿Recalcular ahora las noches pasadas con los umbrales nuevos?", "Recalcular", { icon: "check", cancelLabel: "Más tarde" })) await post("/api/reprocess");
      route();
    };
    resBody.replaceChildren(section("Resultado"),
      ...(r.warnings || []).map(w => h("div", { class: "card banner" }, h("div", { class: "ic warn" }, icon("alert")), h("div", { class: "small", text: w }))),
      h("div", { class: "card" }, cmp, cmp2, applyBtn,
        h("p", { class: "small muted", style: "margin:12px 0 0", text: `Precisión: de las veces que dice «despierta», cuántas lo estaba. Detecta: de las veces que estaba despierta, cuántas lo ve. ${r.n_labels} clips de ${r.n_nights} noches · ${r.n_combos} combinaciones.` })));
  };
  let timer;
  const poll = async () => {
    const s2 = await api("/api/calibrate");
    showRun(s2.run);
    clearTimeout(timer);
    if (s2.run && s2.run.status === "corriendo" && location.hash.startsWith("#calibrar")) timer = setTimeout(poll, 1500);
  };
  showRun(st.run);
  if (st.run && st.run.status === "corriendo") { startBtn.disabled = true; poll(); }

  // reprocesar
  const rpBody = h("span", { class: "val" });
  const rpBtn = h("button", { class: "row-link" }, h("div", { class: "ic" }, icon("nights")),
    h("div", { class: "grow" }, h("div", { class: "t", text: "Recalcular todas las noches" }), h("div", { class: "s", text: "Con los umbrales activos" })), rpBody);
  const rpPoll = async () => {
    const j = await api("/api/reprocess");
    if (j.status === "corriendo") { rpBody.textContent = Math.round((j.progress || 0) * 100) + " %"; if (location.hash.startsWith("#calibrar")) setTimeout(rpPoll, 1500); }
    else rpBody.textContent = j.status === "ok" ? `${j.nights} noches ✓` : j.status === "error" ? "error" : "";
  };
  rpBtn.onclick = async () => { await post("/api/reprocess"); rpPoll(); };
  rpPoll();
  out.append(section("Herramientas"), h("div", { class: "list" }, rpBtn));

  out.append(section("Historial"));
  out.append(h("div", { class: "list" }, cf.historial.map((c, i) => h("div", { class: "row-link", style: "cursor:default" },
    h("div", { class: "grow" },
      h("div", { class: "t nums", text: ["despertar.sensibilidad", "despertar.x_minutos_activos", "despertar.y_ventana_min", "despertar.z_calma_min", "despertar.n_fusion_min"].map((k, j) => `${["S", "X", "Y", "Z", "N"][j]} ${c.values[k]}`).join(" · ") }),
      h("div", { class: "s", text: `${new Date(c.created * 1000).toLocaleString("es-ES", { timeZone: TZ, dateStyle: "short", timeStyle: "short" })} · ${c.source}${c.note ? " · " + c.note : ""}` })),
    i === 0 ? h("span", { class: "tag sleep" }, icon("check"), "Activa") :
      h("button", { class: "btn small", onclick: async () => { if (await ask("¿Volver a esta configuración?", "Se aplicarán esos umbrales; la actual queda en el historial.", "Volver", { icon: "target" })) { await post(`/api/config/restore/${c.id}`); toast("Configuración restaurada"); route(); } } }, "Volver")))));
  return out;
};

// ============================================================ arranque
refreshStatus().then(route);
// Refresco: el estado cada 20 s (sin redibujar); Inicio solo se redibuja si hay datos nuevos
const nightSig = (n) => n ? [n.night, n.final, n.sleep_onset, n.sleep_end, n.n_wakes, n.min_awake, Math.floor((n.min_sleep || 0) / 10)] : null;
const dataSig = () => STATUS ? JSON.stringify([nightSig(STATUS.noche_en_curso), nightSig(STATUS.ultima_noche), STATUS.clips_sin_etiquetar]) : "";
let lastSig = null;
setInterval(async () => {
  if (location.hash.startsWith("#directo") || document.visibilityState !== "visible") return;
  await refreshStatus();
  const hsh = location.hash || "#inicio";
  const sig = dataSig();
  if (lastSig === null) { lastSig = sig; return; }
  if (sig !== lastSig && (hsh.startsWith("#inicio") || hsh === "#") && !$("#sheet").open && tip.hidden) {
    lastSig = sig; route.keepScroll = true; route();
  }
  lastSig = sig;
}, 20000);
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") refreshStatus(); });
