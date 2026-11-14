/*
 * Pool Circulation cards — bundled with the pool_circulation integration and
 * loaded automatically on every dashboard.
 *
 *   type: custom:pool-circulation-card        status, temperatures, controls
 *   type: custom:pool-circulation-plan-card   today's price + planned mode per hour
 *
 * Both take `entity:` (the Pool Circulation Mode sensor) — found
 * automatically when omitted — and an optional `title:`.
 */

const CARD_VERSION = "1.11.1";

const MODE = {
  off: { label: "Off", color: "var(--pc-off)" },
  low: { label: "Low", color: "var(--pc-low)" },
  medium: { label: "Medium", color: "var(--pc-medium)" },
  high: { label: "High", color: "var(--pc-high)" },
};

const SAFETY = {
  pool_critical: { icon: "mdi:snowflake-alert", title: "Pool near freezing" },
  frost: { icon: "mdi:snowflake-thermometer", title: "Frost guard" },
  sensor_failsafe: { icon: "mdi:thermometer-off", title: "Outdoor sensor missing" },
};

const FAULT = {
  not_running: { title: "Pump not responding", text: "Switches are on but the feedback sensor shows no running pump" },
  not_stopping: { title: "Pump won't stop", text: "Feedback shows the pump running although it is switched off" },
  wrong_speed: { title: "Pump at wrong speed" },
};

const TIER = {
  algae: "Winter — pool above algae limit, low speed all day",
  rest: "Winter — pool cold enough, pump off",
};

const BASE_CSS = `
  :host {
    --pc-off: var(--disabled-text-color, #9e9e9e);
    --pc-low: #4fc3f7;
    --pc-medium: #1e88e5;
    --pc-high: #7e57c2;
    --pc-summer: #f9a825;
    --pc-winter: #4fa3e0;
    --pc-danger: var(--error-color, #db4437);
    --pc-warn: var(--warning-color, #ffa600);
    --pc-ok: var(--success-color, #43a047);
    --pc-muted: var(--secondary-text-color);
    --pc-track: var(--divider-color, rgba(127,127,127,.25));
  }
  ha-card { padding: 16px; color: var(--primary-text-color); }
  .head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
  .title { font-size: 1.15rem; font-weight: 500; color: var(--primary-text-color); }
  .muted { color: var(--pc-muted); font-size: .85rem; }
  .pill {
    display: inline-flex; align-items: center; gap: 4px; padding: 2px 10px;
    border-radius: 999px; font-size: .8rem; font-weight: 500; white-space: nowrap;
  }
  .pill ha-icon { --mdc-icon-size: 16px; }
  .empty { padding: 8px 0; color: var(--pc-muted); }
`;

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

function fmtTemp(v) {
  return v === null || v === undefined || Number.isNaN(Number(v)) ? "–" : `${Number(v).toFixed(1)}°`;
}

function fmtHours(v) {
  if (v === null || v === undefined) return "–";
  const n = Number(v);
  return Number.isInteger(n) ? `${n}` : n.toFixed(1);
}

// 2 decimals for small values (prices, kWh), fewer for large ones (öre, big costs)
function fmtNum(v) {
  const n = Number(v);
  if (v === null || v === undefined || Number.isNaN(n)) return "–";
  return n.toFixed(Math.abs(n) >= 100 ? 0 : Math.abs(n) >= 10 ? 1 : 2);
}

function findModeEntity(hass) {
  return Object.keys(hass.states).find((id) => {
    const a = hass.states[id].attributes;
    return id.startsWith("sensor.") && Array.isArray(a.plan) && "season" in a && "rule" in a;
  });
}

class PoolCardBase extends HTMLElement {
  setConfig(config) {
    this._config = { ...config };
    this._key = null;
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    if (this._hass) this._update();
  }

  set hass(hass) {
    this._hass = hass;
    this._update();
  }

  get _entityId() {
    return this._config.entity || (this._hass && findModeEntity(this._hass));
  }

  _linked() {
    const st = this._hass.states[this._entityId];
    return (st && st.attributes.entities) || {};
  }

  _update() {
    if (!this._hass || !this._config) return;
    const st = this._hass.states[this._entityId];
    const linked = this._linked();
    // Re-render only when one of the states we show actually changed
    const key = [st, ...Object.values(linked).map((id) => id && this._hass.states[id])];
    if (this._key && key.length === this._key.length && key.every((s, i) => s === this._key[i])) return;
    this._key = key;
    this.shadowRoot.innerHTML = st ? this._render(st, linked) : this._renderMissing();
    if (st) this._bind(st, linked);
  }

  _renderMissing() {
    return `<style>${BASE_CSS}</style><ha-card><div class="empty">
      Pool Circulation entity not found${this._config.entity ? `: ${esc(this._config.entity)}` : ""}.
      Set <code>entity:</code> to the <b>Pool Circulation Mode</b> sensor.</div></ha-card>`;
  }

  _state(id) {
    return id ? this._hass.states[id] : undefined;
  }

  _call(domain, service, entityId) {
    if (entityId) this._hass.callService(domain, service, { entity_id: entityId });
  }

  _bind() {}
}

/* ------------------------------------------------------------------ */
/* Status card                                                          */
/* ------------------------------------------------------------------ */
class PoolCirculationCard extends PoolCardBase {
  static getStubConfig(hass) {
    return { entity: findModeEntity(hass) || "sensor.pool_circulation_mode" };
  }

  getCardSize() {
    return 6;
  }

  _scale(value, min, max, zones, markers) {
    const span = max - min;
    const pct = (v) => Math.max(0, Math.min(100, ((v - min) / span) * 100));
    let from = min;
    const bands = zones.map(([to, color]) => {
      const left = pct(from);
      const width = pct(Math.min(to, max)) - left;
      from = to;
      return `<div class="band" style="left:${left}%;width:${width}%;background:${color}"></div>`;
    }).join("");
    const ticks = markers.map(([v]) => `<div class="tick" style="left:${pct(v)}%"></div>`).join("");
    const caption = markers.map(([v, label]) => `${esc(label)} ${fmtTemp(v)}`).join(" · ");
    const dot = value === null || value === undefined ? "" :
      `<div class="dot" style="left:${pct(value)}%"></div>`;
    return `<div><div class="scale"><div class="track">${bands}</div>${ticks}${dot}</div>
      <div class="caption">${caption}</div></div>`;
  }

  _render(st, linked) {
    const a = st.attributes;
    const selected = MODE[st.state] || MODE.off;
    // Show what the pump really does when the feedback sensor can tell
    const actualKey = a.actual_speed && MODE[a.actual_speed] ? a.actual_speed : null;
    const shownKey = actualKey || st.state;
    const mode = MODE[shownKey] || MODE.off;
    const winter = a.season === "winter";
    const t = a.thresholds || {};
    const safety = a.safety && SAFETY[a.safety];
    const running = (actualKey || st.state) !== "off";
    const automation = this._state(linked.automation);
    const extra = this._state(linked.extra_filter);
    const extraOn = extra && extra.state === "on";
    const target = a.target_hours;
    const done = a.done_hours || 0;
    const pct = target ? Math.min(100, (done / target) * 100) : 100;
    const feedback = a.feedback_value === null || a.feedback_value === undefined ? ""
      : ` · ${Math.round(a.feedback_value)}${a.feedback_unit ? ` ${a.feedback_unit}` : ""}`;
    let pumpState;
    if (actualKey && actualKey !== st.state) {
      const startUp = a.speed_settling && actualKey !== "off" && st.state !== "off";
      pumpState = startUp ? `Start-up · settling to ${selected.label}`
        : st.state === "off" ? "Should be off"
        : actualKey === "off" ? `Starting ${selected.label}…`
        : `Selected ${selected.label}`;
    } else {
      pumpState = a.pump_running ? "Pump running"
        : running ? "Starting…" : "Pump stopped";
    }
    pumpState += feedback;
    const extraLeft = Math.ceil((a.extra_filter_remaining || 0) / 60);

    const poolMax = Math.max(winter ? 16 : 32, (a.pool_temp ?? 0) + 2);
    const poolMin = Math.min(0, (a.pool_temp ?? 0) - 1);
    const outMin = Math.min(-15, (a.outdoor_temp ?? 0) - 2);
    const outMax = Math.max(20, (a.outdoor_temp ?? 0) + 2);

    const poolScale = this._scale(a.pool_temp, poolMin, poolMax, [
      [t.pool_critical, "var(--pc-danger)"],
      [t.algae, "var(--pc-winter)"],
      [poolMax, "var(--pc-ok)"],
    ], [[t.pool_critical, "critical"], [t.algae, "algae"]]);
    // Below "medium" a critical pool runs medium; at or below "frost" the frost guard runs low
    const outMarks = [[t.outdoor_medium, "medium", "var(--pc-warn)"], [t.freeze, "frost", "var(--pc-winter)"]]
      .filter(([v]) => v !== null && v !== undefined).sort((x, y) => x[0] - y[0]);
    const outScale = this._scale(a.outdoor_temp, outMin, outMax,
      [...outMarks.map(([v, , c]) => [v, c]), [outMax, "var(--pc-ok)"]],
      outMarks.map(([v, label]) => [v, label]));

    let today;
    if (target === null || target === undefined) {
      today = `<span class="muted">${winter ? "No daily target — running by temperature" : "No daily target"}</span>`;
    } else {
      today = `<div class="bar"><div style="width:${pct}%"></div></div>
        <span class="muted">${fmtHours(done)} / ${fmtHours(target)} h${a.must_run ? " · catching up" : ""}</span>`;
    }

    // Price now, and the pump's energy and cost today (needs a power feedback sensor)
    const priceUnit = a.price_unit || "";
    const costUnit = priceUnit.split("/")[0].trim();
    const has = (v) => v !== null && v !== undefined;
    // Cost so far today (needs a power feedback sensor), with the current price beside it
    const priceNow = has(a.price)
      ? `now ${fmtNum(a.price)} ${esc(priceUnit)}${a.is_peak_price ? " · peak" : ""}` : "";
    const priceRow = has(a.cost_today) ? `<div class="today"><span class="muted">Cost today</span>
        <span class="val">${fmtNum(a.cost_today)} <span class="muted">${esc(costUnit)}</span></span>
        <span class="muted">${priceNow}</span></div>`
      : has(a.price) ? `<div class="today"><span class="muted">Price</span>
        <span class="val">${fmtNum(a.price)} <span class="muted">${esc(priceUnit)}</span></span>
        <span class="muted">${a.is_peak_price ? "peak hour" : ""}</span></div>` : "";
    // Average price paid per kWh today
    const avg = has(a.energy_today) && a.energy_today > 0 && has(a.cost_today)
      ? `avg ${fmtNum(a.cost_today / a.energy_today)} ${esc(priceUnit)}` : "";
    const energyRow = has(a.energy_today) ? `<div class="today"><span class="muted">Energy today</span>
        <span class="val">${fmtNum(a.energy_today)} <span class="muted">kWh</span></span>
        <span class="muted">${avg}</span></div>` : "";

    const fault = a.pump_fault ? `<div class="banner danger"><ha-icon icon="mdi:pump-off"></ha-icon>
        <div><b>${FAULT[a.pump_fault]?.title || "Pump fault"}</b>
        <div class="muted">${a.pump_fault === "wrong_speed"
          ? `Running ${mode.label} although ${selected.label} is selected`
          : FAULT[a.pump_fault]?.text || ""} — outputs are re-sent every 10 min</div></div></div>` : "";

    const unavailable = (a.unavailable_entities || []).length
      ? `<div class="banner warn"><ha-icon icon="mdi:lan-disconnect"></ha-icon>
          <div><b>Not responding</b><div class="muted">${esc(a.unavailable_entities.join(", "))}</div></div></div>`
      : "";

    return `<style>${BASE_CSS}
      .season { display: inline-flex; border-radius: 999px; background: var(--pc-track); padding: 2px; }
      .season button {
        all: unset; cursor: pointer; display: inline-flex; align-items: center; gap: 4px;
        padding: 4px 10px; border-radius: 999px; font-size: .8rem; color: var(--pc-muted);
      }
      .season button ha-icon { --mdc-icon-size: 16px; }
      .season button.on.summer { background: var(--pc-summer); color: #fff; }
      .season button.on.winter { background: var(--pc-winter); color: #fff; }
      .season button:focus-visible { outline: 2px solid var(--primary-color); }
      .status { display: flex; align-items: center; gap: 14px; margin: 16px 0 6px; }
      .pump {
        width: 52px; height: 52px; border-radius: 50%; display: grid; place-items: center;
        background: color-mix(in srgb, ${mode.color} 18%, transparent); color: ${mode.color}; flex: none;
      }
      .pump ha-icon { --mdc-icon-size: 30px; }
      .pump.spin ha-icon { animation: spin ${shownKey === "high" ? 0.8 : shownKey === "medium" ? 1.4 : 2.4}s linear infinite; }
      @keyframes spin { to { transform: rotate(360deg); } }
      @media (prefers-reduced-motion: reduce) { .pump.spin ha-icon { animation: none; } }
      .mode { font-size: 1.6rem; font-weight: 500; line-height: 1.1; color: var(--primary-text-color); }
      .reason { margin: 4px 0 12px; color: var(--primary-text-color); font-size: .92rem; line-height: 1.35; }
      .banner { color: var(--primary-text-color); display: flex; gap: 10px; align-items: flex-start; padding: 10px 12px; border-radius: 10px; margin: 0 0 12px; }
      .banner.danger { background: color-mix(in srgb, var(--pc-danger) 14%, transparent); }
      .banner.danger ha-icon { color: var(--pc-danger); }
      .banner.warn { background: color-mix(in srgb, var(--pc-warn) 14%, transparent); }
      .banner.warn ha-icon { color: var(--pc-warn); }
      .row { display: grid; grid-template-columns: 92px 52px 1fr; align-items: start; gap: 10px; margin: 14px 0 4px; }
      .row .val { font-weight: 500; font-variant-numeric: tabular-nums; color: var(--primary-text-color); }
      .scale { position: relative; height: 14px; }
      .track { position: absolute; top: 4px; left: 0; right: 0; height: 6px; border-radius: 3px; overflow: hidden; background: var(--pc-track); }
      .band { position: absolute; top: 0; bottom: 0; opacity: .55; }
      .tick { position: absolute; top: 0; height: 14px; border-left: 1px dashed var(--pc-muted); }
      .caption { font-size: .72rem; color: var(--pc-muted); margin-top: 2px; }
      .dot {
        position: absolute; top: 0; width: 14px; height: 14px; margin-left: -7px; border-radius: 50%;
        background: var(--primary-text-color); border: 2px solid var(--card-background-color, #fff); box-sizing: border-box;
      }
      .today { display: grid; grid-template-columns: 92px 1fr auto; align-items: center; gap: 10px; margin-top: 14px; }
      .today .val { font-weight: 500; font-variant-numeric: tabular-nums; color: var(--primary-text-color); }
      .bar { height: 8px; border-radius: 4px; background: var(--pc-track); overflow: hidden; }
      .bar div { height: 100%; background: ${mode.color === MODE.off.color ? "var(--pc-medium)" : mode.color}; border-radius: 4px; }
      .actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 16px; }
      .actions button {
        all: unset; cursor: pointer; display: inline-flex; align-items: center; gap: 6px;
        padding: 8px 12px; border-radius: 10px; font-size: .85rem; color: var(--primary-text-color);
        background: var(--pc-track);
      }
      .actions button.active { background: var(--primary-color); color: var(--text-primary-color, #fff); }
      .actions button:focus-visible { outline: 2px solid var(--primary-color); }
      .actions ha-icon { --mdc-icon-size: 18px; }
      @media (max-width: 420px) { .row, .today { grid-template-columns: 80px 46px 1fr; } .today { grid-template-columns: 80px 1fr auto; } }
    </style>
    <ha-card>
      <div class="head">
        <div class="title">${esc(this._config.title || "Pool")}</div>
        <div class="season" role="group" aria-label="Season">
          <button class="summer ${winter ? "" : "on"}" data-season="summer" aria-pressed="${!winter}">
            <ha-icon icon="mdi:white-balance-sunny"></ha-icon>Summer</button>
          <button class="winter ${winter ? "on" : ""}" data-season="winter" aria-pressed="${winter}">
            <ha-icon icon="mdi:snowflake"></ha-icon>Winter</button>
        </div>
      </div>

      <div class="status">
        <div class="pump ${running ? "spin" : ""}"><ha-icon icon="${running ? "mdi:fan" : "mdi:fan-off"}"></ha-icon></div>
        <div>
          <div class="mode">${mode.label}</div>
          <div class="muted">${pumpState}${a.automation_enabled === false ? " · manual" : ""}</div>
        </div>
      </div>
      <div class="reason">${esc(a.reason)}</div>
      ${safety ? `<div class="banner danger"><ha-icon icon="${safety.icon}"></ha-icon>
        <div><b>${safety.title}</b><div class="muted">Runs regardless of price, timers and manual mode</div></div></div>` : ""}
      ${fault}
      ${unavailable}

      <div class="row"><span class="muted">Pool water</span><span class="val">${fmtTemp(a.pool_temp)}</span>${poolScale}</div>
      <div class="row"><span class="muted">Outdoor</span><span class="val">${fmtTemp(a.outdoor_temp)}</span>${outScale}</div>

      <div class="today"><span class="muted">Today</span>${today}</div>
      ${priceRow}
      ${energyRow}

      <div class="actions">
        ${automation ? `<button data-act="automation" class="${automation.state === "on" ? "active" : ""}">
          <ha-icon icon="mdi:robot"></ha-icon>Automation ${automation.state === "on" ? "on" : "off"}</button>` : ""}
        ${extra ? `<button data-act="extra" class="${extraOn ? "active" : ""}">
          <ha-icon icon="mdi:filter-plus"></ha-icon>${extraOn ? `Extra filter · ${extraLeft} min` : "Extra filter"}</button>` : ""}
        <button data-act="more"><ha-icon icon="mdi:dots-horizontal"></ha-icon>Details</button>
      </div>
    </ha-card>`;
  }

  _bind(st, linked) {
    const root = this.shadowRoot;
    root.querySelectorAll("[data-season]").forEach((b) => b.addEventListener("click", () => {
      const winter = b.dataset.season === "winter";
      if (winter === (st.attributes.season === "winter")) return;
      this._call("switch", winter ? "turn_on" : "turn_off", linked.winter_mode);
    }));
    root.querySelector('[data-act="automation"]')?.addEventListener("click", () =>
      this._call("switch", "toggle", linked.automation));
    root.querySelector('[data-act="extra"]')?.addEventListener("click", () =>
      this._call("switch", "toggle", linked.extra_filter));
    root.querySelector('[data-act="more"]')?.addEventListener("click", () => {
      this.dispatchEvent(new CustomEvent("hass-more-info", {
        detail: { entityId: this._entityId }, bubbles: true, composed: true,
      }));
    });
  }
}

/* ------------------------------------------------------------------ */
/* Plan card                                                            */
/* ------------------------------------------------------------------ */
class PoolCirculationPlanCard extends PoolCardBase {
  static getStubConfig(hass) {
    return { entity: findModeEntity(hass) || "sensor.pool_circulation_mode" };
  }

  getCardSize() {
    return 4;
  }

  _render(st) {
    const a = st.attributes;
    const plan = a.plan || [];
    const hour = new Date().getHours();
    const winter = a.season === "winter";
    const prices = plan.map((p) => p.price).filter((p) => p !== null && p !== undefined);
    const hasPrices = prices.length > 0;
    const hasPlan = plan.some((p) => p.mode);
    const max = hasPrices ? Math.max(...prices) : 1;
    const min = hasPrices ? Math.min(0, ...prices) : 0;

    const W = 480, H = 150, top = 8, bottom = 22, gap = 2;
    const bw = W / 24;
    const y = (v) => top + (H - top - bottom) * (1 - (v - min) / (max - min || 1));

    const bars = plan.map((p) => {
      const m = MODE[p.mode] || null;
      const x = p.hour * bw + gap / 2;
      const h0 = hasPrices && p.price !== null ? y(p.price) : top + (H - top - bottom) * 0.35;
      const height = Math.max(3, H - bottom - h0);
      const fill = m ? m.color : "var(--pc-track)";
      const opacity = p.hour < hour ? 0.4 : 1;
      const title = `${String(p.hour).padStart(2, "0")}:00 · ${m ? m.label : "unknown"}` +
        (p.price !== null && p.price !== undefined ? ` · ${p.price.toFixed(2)}` : "");
      return `<rect x="${x}" y="${h0}" width="${bw - gap}" height="${height}" rx="2"
        fill="${fill}" opacity="${opacity}"><title>${esc(title)}</title></rect>`;
    }).join("");

    const now = `<rect x="${hour * bw}" y="${top - 4}" width="${bw}" height="${H - bottom - top + 4}"
      fill="none" stroke="var(--primary-text-color)" stroke-width="1.5" rx="3"/>`;
    const labels = [0, 3, 6, 9, 12, 15, 18, 21].map((h) =>
      `<text x="${h * bw + bw / 2}" y="${H - 6}" text-anchor="middle">${String(h).padStart(2, "0")}</text>`).join("");

    const counts = {};
    plan.forEach((p) => { if (p.mode) counts[p.mode] = (counts[p.mode] || 0) + 1; });
    const legend = ["high", "medium", "low", "off"].filter((m) => counts[m]).map((m) =>
      `<span class="lg"><i style="background:${MODE[m].color}"></i>${MODE[m].label} ${counts[m]} h</span>`).join("");

    const subtitle = a.safety ? (SAFETY[a.safety] || {}).title || a.safety
      : winter ? TIER[a.winter_tier] || "Winter" : "Summer — cheapest hours at medium speed";

    return `<style>${BASE_CSS}
      svg { width: 100%; height: auto; display: block; margin-top: 12px; overflow: visible; }
      svg text { font-size: 11px; fill: var(--pc-muted); }
      .legend { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 8px; font-size: .8rem; color: var(--pc-muted); }
      .lg { display: inline-flex; align-items: center; gap: 6px; }
      .lg i { width: 10px; height: 10px; border-radius: 2px; display: inline-block; }
    </style>
    <ha-card>
      <div class="head">
        <div class="title">${esc(this._config.title || "Today's plan")}</div>
        <span class="pill" style="background:${winter ? "var(--pc-winter)" : "var(--pc-summer)"};color:#fff">
          <ha-icon icon="${winter ? "mdi:snowflake" : "mdi:white-balance-sunny"}"></ha-icon>${winter ? "Winter" : "Summer"}</span>
      </div>
      <div class="muted">${esc(subtitle)}</div>
      ${hasPlan ? `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Planned pump speed per hour today">${bars}${now}${labels}</svg>
        <div class="legend">${legend}${hasPrices ? "" : "<span>No price list for today</span>"}</div>`
        : `<div class="empty">No plan yet — waiting for today's prices. Meanwhile the pump runs until today's hours are done.</div>`}
    </ha-card>`;
  }
}

if (!customElements.get("pool-circulation-card")) {
  customElements.define("pool-circulation-card", PoolCirculationCard);
}
if (!customElements.get("pool-circulation-plan-card")) {
  customElements.define("pool-circulation-plan-card", PoolCirculationPlanCard);
}

window.customCards = window.customCards || [];
for (const card of [
  { type: "pool-circulation-card", name: "Pool Circulation", description: "Season, pump status, temperatures and controls." },
  { type: "pool-circulation-plan-card", name: "Pool Circulation Plan", description: "Today's electricity price and planned pump speed per hour." },
]) {
  if (!window.customCards.some((c) => c.type === card.type)) window.customCards.push({ ...card, preview: true });
}

console.info(`%c POOL-CIRCULATION-CARD %c ${CARD_VERSION} `,
  "background:#1e88e5;color:#fff;border-radius:3px 0 0 3px", "background:#4fc3f7;color:#000;border-radius:0 3px 3px 0");
