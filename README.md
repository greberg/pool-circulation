# Pool Circulation – Home Assistant Custom Component

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)

A Home Assistant custom component that automatically controls your pool circulation pump, UV lamp, and heat pump based on electricity prices. Runs at high speed during cheap hours, backs off during peak prices, always guarantees the daily minimum circulation target is met, and supports an extra filter mode for on-demand high-RPM filtration.

---

## Features

| Feature | Details |
|---|---|
| **Summer and winter** | Two explicit seasons with their own rules — switch with `switch.pool_winter_mode` or the season toggle on the card |
| **Freeze guards** | Pool water near 3°C or outdoor frost → continuous circulation, regardless of price, timers or manual mode, in any season |
| **Price-optimised** | Summer: cheapest hours at medium speed. Winter: low speed, skipping or choosing hours by price |
| **Restart-safe** | Runtime, season, timers and today's prices are persisted; devices are re-checked every minute and re-applied after a controller reboot |
| **Algae skip** | Below the algae threshold (default 7°C pool water) no filtration hours are needed |
| **Extra filter mode** | High speed for a configurable time, survives HA restarts |
| **Cover burst** | 5 min high speed when the pool cover is opened |
| **Heat pump (summer)** | Kept on, target temperature by price. Winter: never touched |
| **UV lamp control** | On while the pump runs and the cover is not open |
| **Pump cooldown / min on-time** | Prevents rapid on/off cycling |
| **Lovelace cards** | Status card and day-plan card bundled with the integration — no extra install |
| **15-minute prices** | Quarter-hour price lists (Nordpool since Oct 2025) are averaged per hour |
| **HACS compatible** | Install and update via HACS |

---

## How It Works

Every minute (and immediately when a temperature, price window or device changes) the integration decides a mode — `off`, `low`, `medium` or `high` — and brings the switches in line with it. Only switches that differ are commanded, so nothing is spammed.

The sensor `sensor.pool_circulation_reason` always says, in plain words, why the pump is doing what it does.

### Safety guards — both seasons, always first

| Guard | Condition | Pump |
|---|---|---|
| **Pool critical** | Pool water ≤ critical temp (default **4°C** — circulation can fail approaching 3°C) | `medium`, continuously |
| **Frost** | Outdoor ≤ frost threshold (default **2°C**) | `low`, continuously |
| **Sensor failsafe** | Winter and no outdoor temperature reading | `low`, continuously |

Guards ignore price, cooldown, minimum on-time and the automation switch. They release only once the reading is 0.5°C above the threshold, so the pump does not flap. Extra filter and the cover burst still win, since they run faster anyway.

### Summer

1. Pool water below the algae threshold (default 7°C) → `off`, or `low` if outdoor is in the cold buffer zone (frost – 4°C)
2. Cheapest *daily hours* (default 8) → `medium`, heat pump at its best-price target
3. Every other hour → `low` — a warm pool is never left standing
4. Behind on daily hours late in the day → catch up at `medium` (only `low` during peak price)
5. No 24-hour price list → follow the best/peak price binary sensors

### Winter

The heat pump is left completely alone (power it down yourself). Circulation depends on the outdoor and pool temperature:

| Tier | Condition | Pump |
|---|---|---|
| **Cold** | Outdoor between frost threshold and cold buffer (default 2–4°C) | `low` all day except the most expensive hours (default 4), never two skipped hours in a row |
| **Algae** | Outdoor mild, pool ≥ algae threshold (default 7°C) | `low` during the cheapest *winter algae hours* (default 4) |
| **Rest** | Outdoor mild, pool < algae threshold | `low` during the cheapest *winter minimum hours* (default 2) — keeps water moving and the pool sensor reading real water |

If the day's hours have not been met late in the evening, the pump catches up at `low`. Without a price list, cold tier runs except at peak price and the other tiers run during best price.

### Restarts

- Runtime is counted from the time the pump switch is actually on, and saved every 5 minutes. Time while HA was down is not counted.
- Season, automation switch, extra filter end time, cover burst end time, safety guard state and today's price list are stored and restored.
- Nothing is commanded until HA has fully started. Temperature sensors get up to 2 minutes to report before the integration decides without them.
- Every minute the switches are compared with the wanted mode. A pump controller that reboots and comes back off is turned on again as soon as it reappears.
- With automation off, devices are left exactly as you set them — unless a safety guard is active.

---

## Dashboard cards

The integration ships two Lovelace cards and loads them automatically (no HACS frontend install, no resources to add). After updating, reload the browser once.

```yaml
type: custom:pool-circulation-card
```
Season toggle, current mode and RPM, the reason, freeze-guard banner, pool and outdoor temperature on scales with the critical / algae / frost / cold thresholds, today's progress, and buttons for automation and extra filter.

```yaml
type: custom:pool-circulation-plan-card
```
Today's price per hour as bars, coloured by the planned speed for that hour, with the current hour outlined.

Both find the integration automatically. Add `entity: sensor.pool_circulation_mode` if you have renamed it, and `title:` to change the heading.

---

## Prerequisites

- Home Assistant 2024.7 or newer
- A heat pump integrated as a `climate` entity (e.g. `aqua_temp`)
- Outdoor temperature sensor (required for freeze protection)
- Pool water temperature sensor (optional, for algae skip)
- UV lamp switch entity (optional)
- Pool cover entity (optional, prevents UV lamp from turning on when cover is open)
- Circulation pump wired as:
  - One `switch` entity for on/off
  - Up to three `switch` entities for RPM levels (low / medium / high)
- Electricity price sensors — the integration defaults to the **Trulsibrunn** Nordpool setup:
  - `sensor.trulsibrunn_timpris_aktuell` — current hour price
  - `sensor.trulsibrunn_aktuell_timprisniva` — price level label
  - `binary_sensor.trulsibrunn_basta_prisperiod` — best price window
  - `binary_sensor.trulsibrunn_topprisperiod` — peak price window

Any Nordpool or Tibber-based price integration with equivalent entities works — just override the defaults in the config flow.

---

## Installation via HACS

1. Open HACS → **Integrations** → ⋮ → **Custom repositories**
2. Add `https://github.com/greberg/pool-circulation` as category **Integration**
3. Find **Pool Circulation** in the HACS list and click **Download**
4. Restart Home Assistant
5. Go to **Settings → Devices & Services → Add Integration** → search **Pool Circulation**
6. Fill in the configuration form

---

## Configuration

### Setup (one-time)

| Field | Required | Description |
|---|---|---|
| Heat pump climate entity | ✅ | e.g. `climate.pool_heat_pump` |
| Circulation pump switch | ✅ | Main on/off switch |
| Low RPM switch | — | Optional |
| Low RPM value | — | RPM displayed when low switch is on (default 1000) |
| Medium RPM switch | — | Optional |
| Medium RPM value | — | RPM displayed when medium switch is on (default 2000) |
| High RPM switch | — | Optional |
| High RPM value | — | RPM displayed when high switch is on (default 3000) |
| Current price sensor | — | Default: `sensor.trulsibrunn_timpris_aktuell` |
| Price level sensor | — | Default: `sensor.trulsibrunn_aktuell_timprisniva` |
| Best price binary sensor | — | Default: `binary_sensor.trulsibrunn_basta_prisperiod` |
| Peak price binary sensor | — | Default: `binary_sensor.trulsibrunn_topprisperiod` |
| Daily circulation hours | ✅ | Target hours per day (0–24, default 8) |
| Outdoor temperature sensor | — | Used for freeze protection |
| Pool temperature sensor | — | Used for algae skip |
| Algae growth threshold | — | Default 7°C — no filtration needed below this pool temp |
| Freeze protection threshold | — | Default 2°C — continuous low-speed circulation at or below this outdoor temp |
| Heat pump target (best price) | — | Target temp during best-price hours (default 31°C) |
| Heat pump target (normal) | — | Target temp during normal hours (default 30°C) |
| Pool heating threshold | — | Turn on heat pump if pool temp drops below this, even outside best-price hours (default 29°C) |
| Actual RPM sensor | — | Optional sensor reporting real inverter RPM (e.g. ESPHome). When set, overrides the switch-derived RPM value |
| UV lamp switch | — | Controlled automatically based on circulation state |
| Pool cover entity | — | UV lamp stays off when cover is open |
| Extra filter duration | — | Minutes to run after extra filter is activated (default 60, range 5–480) |
| Pump cooldown | — | Minutes to wait before pump can turn on again after stopping (default 10, 0 = disabled) |
| Minimum on-time | — | Minutes pump must stay on once started (default 10, 0 = disabled) |

### Options (editable after setup)

Go to **Settings → Devices & Services → Pool Circulation → Configure** to adjust:
- Daily circulation hours target
- Price signal entity IDs
- Outdoor and pool temperature sensors
- Algae growth threshold (°C)
- Freeze protection threshold (°C)
- Heat pump target temperatures (best price / normal)
- Pool heating threshold (°C)
- Actual RPM sensor entity
- UV lamp switch and pool cover entity
- Extra filter duration (minutes)
- Cold buffer temperature, critical pool temperature
- Winter skip hours, winter algae hours, winter minimum hours

---

## Entities Created

### Sensors
| Entity | Description |
|---|---|
| `sensor.pool_circulation_mode` | Current mode: `off` / `low` / `medium` / `high` — attributes include `season`, `rule`, `reason`, `safety`, `winter_tier`, `target_hours`, `done_hours`, `must_run`, `in_cooldown`, `in_min_on`, `extra_filter_active`, temps, price, `scheduled_high_hours`, `scheduled_low_hours`, `unavailable_entities`, and `plan` (today per hour, used by the cards) |
| `sensor.pool_circulation_reason` | Plain-language reason for the current mode, e.g. "Frost: outdoor -3.0°C ≤ 2.0°C — continuous low-speed circulation" |
| `sensor.pool_circulation_rpm` | Current RPM (numeric) — reads actual RPM sensor if configured, otherwise derived from active switch; `0` when pump is off |
| `sensor.pool_heat_pump_mode` | Current HVAC mode of the heat pump: `off` / `cool` / `heat` / `auto` |
| `sensor.pool_heat_pump_current_temperature` | Temperature reading from the heat pump — attributes include target temp and fan mode |
| `sensor.pool_circulation_hours_today` | Hours at medium/high speed today (minute resolution) |
| `sensor.pool_circulation_hours_remaining` | Hours still needed to hit today's target for the current season |
| `sensor.pool_electricity_price` | Current electricity price (SEK/kWh) |
| `sensor.pool_electricity_price_level` | Price level label from your Nordpool integration |
| `sensor.pool_outdoor_temperature` | Outdoor temperature passthrough (hidden by default) |
| `sensor.pool_water_temperature` | Pool water temperature passthrough (hidden by default) |

### Switches
| Entity | Description |
|---|---|
| `switch.pool_circulation_automation` | Enable / disable price-based scheduling |
| `switch.pool_extra_filter` | Activate extra filter mode — forces high RPM for the configured duration, then auto-disables |
| `switch.pool_winter_mode` | Season — on = winter rules (heat pump never touched), off = summer rules |

### Numbers
| Entity | Description |
|---|---|
| `number.pool_circulation_daily_hours` | MEDIUM-RPM target hours per day — the cheapest N hours get full filtration + heat pump (editable in UI) |
| `number.pool_circulation_daily_low_hours` | LOW-RPM target hours per day — next cheapest M hours outside the MEDIUM window (0 = disabled, default 0) |
| `number.pool_extra_filter_duration` | Duration of extra filter mode in minutes (editable in UI, default 60) |
| `number.pool_pump_cooldown` | Minimum minutes between pump off → on (0 = disabled, default 10) |
| `number.pool_pump_minimum_on_time` | Minimum minutes pump must stay on once started (0 = disabled, default 10) |
| `number.pool_heat_pump_target_best_price` | Heat pump target temperature during best-price hours (default 31°C) |
| `number.pool_heat_pump_target_normal` | Heat pump target temperature during normal hours (default 30°C) |
| `number.pool_heating_threshold` | Pool temp below which heat pump activates during any running mode (default 29°C) |
| `number.pool_critical_temperature` | Pool temp at or below which the pump runs continuously at medium (default 4°C) |
| `number.pool_algae_threshold` | Pool temp below which no filtration is needed (default 7°C) |
| `number.pool_frost_threshold` | Outdoor temp at or below which the pump runs continuously at low (default 2°C) |
| `number.pool_outdoor_buffer_temperature` | Cold buffer — upper outdoor temp of the winter cold tier (default 4°C) |
| `number.pool_winter_skip_hours` | Winter, cold tier: priciest hours that may be skipped (default 4) |
| `number.pool_winter_algae_hours` | Winter, pool ≥ algae threshold: hours per day (default 4) |
| `number.pool_winter_minimum_hours` | Winter, cold pool and mild outdoor: hours per day (default 2) |

---

## Events

### `pool_circulation_mode_changed`
Fired whenever the circulation mode changes:
```yaml
previous_mode: medium
mode: high
season: summer
rule: extra_filter
reason: Extra filter running at high speed
hours_run_today: 5.25
daily_target: 8
```

### `pool_circulation_uv_changed`
Fired whenever the UV lamp is turned on or off:
```yaml
uv_on: true
mode: high
cover_open: false
active_rpm: 3000
```

### `pool_circulation_extra_filter_changed`
Fired when extra filter mode is activated or deactivated (including auto-timeout):
```yaml
active: true
duration_minutes: 60
```

Use these events to trigger push notifications without any logic in YAML:

```yaml
- alias: Notify on extra filter done
  trigger:
    - platform: event
      event_type: pool_circulation_extra_filter_changed
      event_data:
        active: false
  action:
    - service: notify.mobile_app_peter_iphone
      data:
        message: "Extra filtration complete!"
```

---

## Credits

Built for a Höllviken pool running an Aqua-Temp heat pump and ESPHome-controlled circulation pump, using the Trulsibrunn Nordpool electricity price integration.

---

## Development

The decision logic lives in `logic.py` and price parsing in `prices.py` — plain Python with no Home Assistant imports. Tests (no HA install needed):

```bash
python3 -m unittest discover -s tests
```
