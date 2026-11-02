# Fishalyzer 🎣

Log your fishing trips and catches, and let the app learn which weather makes *your* fish bite.
Built for a few friends chasing big brackish-water perch around Zealand's fjord inlets.

**Stack:** Python 3.12 · FastAPI · SQLite · plain-JS mobile web app (PWA) · Docker.
Weather: [Open-Meteo](https://open-meteo.com) (free, no key). Sun/moon maths is done locally.

## Run it

### On a Linux server with Docker
Docker + compose plugin must be installed, and your SSH key must be on the server. Put the target in `deploy.env`
next to `deploy.sh` (it's git-ignored):
```bash
SERVER=user@<server-ip>
PORT=8080
```
Then, from this folder on your PC (Git Bash):
```bash
./deploy.sh
```
It copies the code over SSH to `~/fishalyzer` and runs `docker compose up -d --build`.
`SERVER=... PORT=... ./deploy.sh` overrides `deploy.env`. The server's `data/` folder (the database) is never overwritten.

Then open:
- LAN: `http://<server-ip>:<port>`
- Tailscale: `http://<server-name>.<tailnet>.ts.net:<port>`

### Recommended: HTTPS through Tailscale
GPS ("Use my location") and "Add to home screen" need HTTPS. On the server:
```bash
sudo tailscale serve --bg <port>
```
Now everyone on your tailnet can use `https://<server-name>.<tailnet>.ts.net`. On the phone: open it → Share → *Add to Home Screen*.

### Public on your own domain: Cloudflare Tunnel
Needs a domain whose DNS is on Cloudflare. No router ports are opened; Cloudflare handles HTTPS.
1. Cloudflare dashboard → **Zero Trust → Networks → Tunnels → Create a tunnel** (type *Cloudflared*), name it `fishalyzer`.
   Copy the token from the install command (the long string after `--token`).
2. On the server, add to `~/fishalyzer/.env` (never commit this):
   ```bash
   COMPOSE_PROFILES=tunnel
   TUNNEL_TOKEN=<token>
   ```
   then `cd ~/fishalyzer && docker compose up -d`. The tunnel shows as *Healthy* in the dashboard.
3. In the tunnel → **Public hostname → Add**: subdomain `fish`, your domain, service `HTTP` → `fishalyzer:8080`.
4. Create the admin account (see *Accounts* below) **before** turning the tunnel on.

### Locally on Windows
Put an encryption key in `.env` next to `docker-compose.yml` once (any Fernet key; never commit it):
```bash
echo FISHALYZER_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())") >> .env
docker compose up -d --build
```
→ http://localhost:8080

## Accounts
Everyone logs in with a username and a 4-digit PIN and stays logged in on that device.
- **First start:** with no accounts yet, open the app from the home network or Tailscale and the login page
  becomes "Create the admin account". (Never possible through the public Cloudflare address.)
- **Admin page** (☰ → Admin, admins only): create accounts, set a new PIN (also unlocks), delete accounts.
- 5 wrong PINs lock an account for 15 min, doubling up to 24 h; 10 failed logins per device per 15 min.
- From the server shell: `docker exec fishalyzer python -m app.auth add|pin|list ...` (see `app/auth.py`).

## Logbooks, teams and privacy
- Every account has a **personal logbook**: its spots, trips and model are visible to nobody else.
- **Teams** (☰ → Teams): create one to get an invite code like `ABCD-2345`; whoever enters it joins and shares
  that team's spots, trips and model. The owner can make a new code (the old one stops working), rename,
  remove members (they keep a copy of their own trips) or hand the team over.
- **Leaving a team**, you choose: leave your trips with the team, keep a copy, or take them with you.
  Deleting a team first copies every member's own trips into their personal logbook.
- The header shows which logbook is open; tap it to switch. Nothing ever moves between logbooks on its own.
- Spot names, positions and notes and trip notes are **encrypted** in the database with `FISHALYZER_KEY` from the
  server's `.env` (`deploy.sh` creates it on first deploy). A copied database or backup reveals no spots; the server
  operator still could. Weather is cached and fetched on a ~10 km grid, so neither the cache nor Open-Meteo
  pinpoints a spot.
- **Back up the key** (`grep FISHALYZER_KEY ~/fishalyzer/.env`) somewhere other than the database backups.
  Without it the spots can't be read, and a new key does not bring them back.

## How to use it
1. **Spots** (☰ menu) – add your inlets on the map; a tap snaps to the nearest water and fills in its name.
2. **After a trip** – tap **＋ Log** (or a day in the calendar): day, spot, from–to, who fished, fish count per species and any "top fish" you measured.
3. **Log blank trips too.** Hours without fish are what teach the model which conditions are bad.
4. **Insights** shows what's working, e.g. only perch ≥ 35 cm.

## How the prediction works
See the docstring at the top of [app/model.py](app/model.py). In short: each trip is cut into hours; each hour has effort
(hours on the water), catches and conditions sorted into bins (pressure trend, wind, light, moon, sea temp, …).
For each bin: *your* catch rate vs. your average → a multiplier, shrunk towards a mild "folklore" guess
until you have enough hours there (slider on the Insights page). A forecast hour combines its bins' multipliers into a 0-100 score; 50 = an average hour for you.

## Project layout
```
app/main.py      API + serves the frontend
app/model.py     the learning/scoring algorithm
app/weather.py   Open-Meteo fetching + caching
app/astro.py     sunrise/sunset, moon phase, solunar periods
app/db.py        SQLite schema, spot encryption
app/logbooks.py  personal logbooks, teams, invite codes, leaving, v0.4 migration
app/static/      phone web app (index.html, app.js, style.css)
data/            fishalyzer.db (back this up)
STATUS.md        project tracker
tests/           python tests/test_logbooks.py - migration + logbook isolation self-check
```

## Backup
```bash
ssh user@<server-ip> "docker exec fishalyzer python -c \"import sqlite3,datetime; sqlite3.connect('/data/fishalyzer.db').backup(sqlite3.connect(f'/data/backup-{datetime.date.today()}.db'))\""
```
