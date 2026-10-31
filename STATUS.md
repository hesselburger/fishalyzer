# Fishalyzer – project status

```
Proof of concept   ████████████████████  100%   (v0.1.0 · 2026-10-30)
Deployed to server ░░░░░░░░░░░░░░░░░░░░    0%   (run ./deploy.sh)
Model learning     ░░░░░░░░░░░░░░░░░░░░    0%   (live value: status bar in the app)
```

Live health is in the app's **status bar** (top of every page, tap it for details):
server online · trips · fish · anglers on the water · weather API ✓/⚠ · how much the model has learned.

## Done (v0.1.0)
- [x] SQLite storage: spots, trips, catches, cached weather, settings
- [x] Weather per hour from Open-Meteo (air + sea temp + water level), auto-fetched when a trip is saved
- [x] Sun/moon/solunar computed locally
- [x] Own learning model (catch rate per condition bin, shrunk towards a weak folklore prior)
- [x] 7-day forecast per spot, hourly score, best 3-hour windows with reasons
- [x] Calendar with catches, trips and forecast dots; day view with weather table, edit/delete
- [x] v0.2: log a trip afterwards - one day, one spot, fish count per species + any number of measured "top fish"
- [x] v0.2: burger menu + "＋ Log" header (nothing at the bottom of the screen)
- [x] v0.2: add/edit spots on a map; taps snap to the nearest water (read from the OpenStreetMap tiles)
- [x] Insights: what conditions matter, filter by species and "big fish" min length, prior slider
- [x] CSV export of catches + conditions
- [x] Phone-first PWA (add to home screen), Docker + deploy script
- [x] Running on the home server (Docker), reachable over Tailscale

## Next ideas (not started)
- [ ] Enable `tailscale serve` for HTTPS (needed for GPS + home-screen install)
- [ ] Nightly SQLite backup (cron `sqlite3 data/fishalyzer.db ".backup ..."`)
- [ ] Per-spot model once there's enough data (wind direction matters per inlet)
- [ ] Real water level from DMI (needs free API key) instead of the coarse marine model
- [ ] Photos on catches
- [ ] Simple shared password before moving to a public host (Simply.com)

## Fishing log progress
Aim: ~75 hours fished and ~60 fish before the model is "mostly yours". Log blank trips too!
