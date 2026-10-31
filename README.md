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

### Locally on Windows
```bash
docker compose up -d --build
```
→ http://localhost:8080

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
app/db.py        SQLite schema
app/static/      phone web app (index.html, app.js, style.css)
data/            fishalyzer.db (back this up)
STATUS.md        project tracker
```

## Backup
```bash
ssh user@<server-ip> "docker exec fishalyzer python -c \"import sqlite3,datetime; sqlite3.connect('/data/fishalyzer.db').backup(sqlite3.connect(f'/data/backup-{datetime.date.today()}.db'))\""
```
