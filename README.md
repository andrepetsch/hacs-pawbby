# Pawbby Smart Litter Box (HACS)

Standalone [Home Assistant](https://www.home-assistant.io/) custom component,
distributed via [HACS](https://hacs.xyz/), that controls a **Pawbby Smart Cat
Litter Box** directly over your local network (Tuya protocol v3.4) — no cloud,
and **no Pawbby-Reborn server instance required**.

The Tuya DP protocol was reverse-engineered and hardware-confirmed in the
[Pawbby-Reborn](https://github.com/larsjarred9/Pawbby-Reborn) project
(see `VALUES.md` / `DP105_SETTINGS.md` there). This integration is a clean-room
Python port of that protocol layer and is licensed **AGPL-3.0**.

## Features

- Sensors: status, waste bin, litter level, visits today, last visit weight /
  pet / time, last cleaned, deodorizer days left — plus per-cat weight,
  duration, last used and visits today
- Binary sensors: online, cleaning, lid open, bin full/removed, drum removed,
  litter low
- Buttons: clean, flatten, empty, zero scale (tare), cancel clean
- Switches: auto-clean, quiet period, soft clumps mode, auto screen-off,
  screen lock
- Number: auto-clean delay (1–60 min) · Select: litter type
- HA events for every litter-box event (visits, cleans, bin full, …) so you can
  build automations natively — replaces Pawbby-Reborn's webhooks

## Setup

1. Add this repository as a **custom repository** in HACS (category:
   Integration), then install "Pawbby Smart Litter Box".
2. Restart Home Assistant, then add the integration via
   **Settings → Devices & Services → Add Integration → Pawbby**.
3. Enter the Tuya `device id` and `local key` of your litter box (the same
   values the Pawbby-Reborn setup wizard extracts), optionally its IP address,
   and your cats with their profile weights (used to attribute visits).

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements_test.txt
.venv/bin/python -m pytest   # protocol tests against the hardware-confirmed vectors
```

## License

AGPL-3.0 — see [LICENSE](LICENSE).
