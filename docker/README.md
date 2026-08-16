# Running on a NAS with Docker Compose

Two containers share one image: `web` (the decision UI, port 8000) and
`worker` (an always-on `bym tick` loop that polls approved sources and
reforecasts). They share a database on purpose — round scheduling only
takes effect if both point at the same one, which `docker-compose.yml`
already does.

Nothing here needs a shell inside the container. Every step below is a
browser action.

## First boot

```bash
docker compose up -d --build
```

This creates `./data`, `./rounds`, `./config`, `./secrets` and
`./results` next to `docker-compose.yml` on first run (bind mounts, not
Docker volumes — visible and backupable through the NAS's own file
manager). `web` fetches its historical corpus from football-data.co.uk
automatically the first time it starts; that can take a few seconds
before port 8000 answers.

Open `http://<nas-address>:8000`. A brand-new deployment has no round
file yet, so the first thing you see is **"Set up your first Super 6
round"** — add the six fixtures and the UK deadline, submit, and the
dashboard loads normally from then on.

## Turning monitoring on

Confirming a round already schedules polling jobs, but `worker` needs at
least an Odds API key to do anything with them. In the app: **Settings →
API keys**. Paste a key, click Save — it's written to `./secrets` on the
host (never displayed again, only whether it's configured) and the
`worker` container picks it up on its next cycle, usually within a
minute. No restart needed.

The other three keys (API-Football, Tavily, football-data.org) are
optional — each unlocks one intelligence layer (confirmed lineups, team
news, fixture/result feed) but the app works without them.

## Everyday operation

- `docker compose logs -f worker` — tick cycles, skipped cleanly before
  a key is configured, active once one is.
- `docker compose logs -f web` — request errors, the same messages
  `bym serve` prints locally.
- `docker compose restart web` — picks up config changes that need a
  fresh process (a `.env`-supplied key, for instance); Settings-saved
  keys already apply live without this.
- Everything under `./data`, `./rounds`, `./config`, `./secrets` and
  `./results` is the entire state of the deployment. Back those five
  directories up and a fresh `docker compose up -d` on new hardware
  picks up exactly where it left off.
