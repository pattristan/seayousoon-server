# Sea You Soon — Crew Deck + Pairing API

Small self-run backend for the **Sea You Soon** app. It does two jobs:

1. **Crew Deck** (server-rendered HTML crew website — "Crew Portal" is AIDA's
   own existing system, so we don't use that name) — a seafarer creates a
   **username + PIN** account with their ship & contract, generates short
   **pairing codes** to share with family, and can **revoke** any follower.

   There is deliberately **no crew ID** anywhere: it can't be verified (no
   roster access) and is semi-public on board, so treating it as an identifier
   only created an impersonation target. Identity lives in the sharing
   channel — a code is trusted because the person you know sent it themselves.
   The location data itself is public anyway (any ship tracker shows it).
2. **Pairing API** — the family iOS app calls `POST /pairing-codes/redeem` to
   turn a code into the crew member's name/ship/dates.

The itinerary feed stays a static file elsewhere; this server only handles
pairing, links, and consent, so it stays tiny (single SQLite file).

## Run locally

```bash
cd seayousoon-server
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000 — create an account (username + PIN), then generate a code.

The iOS app talks to it by pointing `Pairing.service` at
`RemotePairingService(baseURL: URL(string: "http://localhost:8000")!)`.

## Data

Everything lives in `data.db` (SQLite) next to the app. Back it up by copying
that one file. Override the path with `SEAYOUSOON_DB=/path/to.db`.

Set a real session secret in production: `SEAYOUSOON_SECRET=<random-string>`.

## Deploy (later)

Shared hosting (e.g. IONOS webspace) generally can't run a persistent Python
process — use a small VPS. Run behind HTTPS (Let's Encrypt), e.g.

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000   # behind an nginx TLS proxy
```

Pairing codes and personal data move over the wire, so HTTPS is required.

## Endpoints

| Method | Path                    | Purpose                              |
|--------|-------------------------|--------------------------------------|
| GET    | `/`                     | Crew login                           |
| GET/POST | `/register`           | Crew registration                    |
| POST   | `/login` / `/logout`    | Session auth                         |
| GET    | `/dashboard`            | Generate codes, see/revoke followers |
| POST   | `/generate-code`        | Issue a new single-use code          |
| POST   | `/revoke`               | Revoke a follower link               |
| POST   | `/pairing-codes/redeem` | **App:** redeem a code → crew profile |
