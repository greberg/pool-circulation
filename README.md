# Pool Circulation – Home Assistant Custom Component

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)

A Home Assistant custom component that automatically controls your pool circulation pump and heat pump based on electricity prices. Runs at high speed during cheap hours, backs off during peak prices, and always guarantees the daily minimum circulation target is met.

---

## Features

| Feature | Details |
|---|---|
| **Price-based scheduling** | Runs at high RPM + heat pump during best-price windows; stops during peak prices |
| **Daily hours guarantee** | Must-run override kicks in when the daily target can't be met any other way |
| **Algae skip** | Skips circulation when all temperature sensors are below the algae growth threshold |
| **Freeze protection** | Forces low-speed circulation when outdoor temp drops to freeze threshold — overrides everything |
| **Heat pump control** | Turns heat pump on/off via any `climate` entity |
| **3-speed RPM control** | Maps low / medium / high RPM to individual switches |
| **Persisted state** | Daily hours counter survives HA restarts; resets at midnight |
| **Automation switch** | Pause the scheduler without changing any other config |
| **Editable daily target** | Adjust hours/day from the HA UI without reconfiguring |
| **HACS compatible** | Install and update via HACS |

---

## How It Works

Every hour at HH:00, the coordinator evaluates price signals and sets one of four modes:

| Mode | Condition | Circulation | Heat pump |
|---|---|---|---|
| `low` | **Freeze protection** — outdoor temp ≤ freeze threshold | Low RPM ON | OFF |
| `high` | Best-price period active | High RPM switch ON | ON |
| `medium` | Normal price, hours still needed | Medium RPM switch ON | OFF |
| `off` | Peak price, daily target met, or **algae skip** (all temps below algae threshold) | All switches OFF | OFF |

**Priority order (highest → lowest):**
1. **Freeze protection** — outdoor temp ≤ freeze threshold (default 2°C) → forces `low`, ignores everything else
2. **Automation switch off** → holds current mode
3. **Algae skip** — all configured temp sensors below algae threshold (default 8°C) → `off`
4. **Price logic** — peak → `off`, best → `high`, normal → `medium` if hours still needed
5. **Must-run override** — hours needed ≥ hours left today → forces `medium` regardless of price

---

## Prerequisites

- Home Assistant 2023.1 or newer
- A heat pump integrated as a `climate` entity (e.g. `aqua_temp`)
- Outdoor temperature sensor (recommended for algae skip and freeze protection)
- Pool water temperature sensor (optional, strengthens algae skip)
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
| Medium RPM switch | — | Optional |
| High RPM switch | — | Optional |
| Current price sensor | — | Default: `sensor.trulsibrunn_timpris_aktuell` |
| Price level sensor | — | Default: `sensor.trulsibrunn_aktuell_timprisniva` |
| Best price binary sensor | — | Default: `binary_sensor.trulsibrunn_basta_prisperiod` |
| Peak price binary sensor | — | Default: `binary_sensor.trulsibrunn_topprisperiod` |
| Daily circulation hours | ✅ | Target hours per day (0–24, default 8) |
| Outdoor temperature sensor | — | Used for algae skip and freeze protection |
| Pool temperature sensor | — | Used for algae skip |
| Algae growth threshold | — | Default 8°C — skip circulation below this |
| Freeze protection threshold | — | Default 2°C — force low-speed circulation below this |

### Options (editable after setup)

Go to **Settings → Devices & Services → Pool Circulation → Configure** to adjust:
- Daily circulation hours target
- Price signal entity IDs
- Best / peak price binary sensors
- Outdoor and pool temperature sensors
- Algae growth threshold (°C)
- Freeze protection threshold (°C)

---

## Entities Created

### Sensors
| Entity | Description |
|---|---|
| `sensor.pool_circulation_mode` | Current mode: `off` / `low` / `medium` / `high` — attributes include `too_cold`, `freeze_risk`, temps, price |
| `sensor.pool_circulation_hours_today` | Hours the pump has run today |
| `sensor.pool_circulation_hours_remaining` | Hours still needed to hit today's target |
| `sensor.pool_electricity_price` | Current electricity price (SEK/kWh) |
| `sensor.pool_electricity_price_level` | Price level label from your Nordpool integration |
| `sensor.pool_outdoor_temperature` | Outdoor temperature passthrough (hidden by default) |
| `sensor.pool_water_temperature` | Pool water temperature passthrough (hidden by default) |

### Switch
| Entity | Description |
|---|---|
| `switch.pool_circulation_automation` | Enable / disable price-based scheduling |

### Number
| Entity | Description |
|---|---|
| `number.pool_circulation_daily_hours` | Target circulation hours per day (editable in UI) |

---

## Events

The coordinator fires `pool_circulation_mode_changed` whenever the mode changes, with data:

```yaml
previous_mode: medium
mode: high
hours_run_today: 5
daily_target: 8
```

Use this to trigger push notifications or log to Google Sheets.

---

## Credits

Built for a Höllviken pool running an Aqua-Temp heat pump and ESPHome-controlled circulation pump, using the Trulsibrunn Nordpool electricity price integration.
