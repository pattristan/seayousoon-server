"""Sea You Soon — Crew Deck (crew website) + pairing API.

Serves:
  * Crew Deck: a minimal server-rendered crew website (register / log in /
    generate code / revoke links) — plain HTML so it loads on a poor ship link.
    (Named Crew Deck because AIDA's official "Crew Portal" already exists.)
  * a JSON endpoint the family iOS app calls to redeem a code

Run locally:
    uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import segno
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import db
from .codes import SHIPS, generate_code, valid_username

BASE_DIR = os.path.dirname(__file__)
# Public base URL, used to build the links encoded in QR codes.
BASE_URL = os.environ.get("SEAYOUSOON_BASE_URL", "https://crew.oconnell-connect.com")


def qr_data_uri(code: str) -> str:
    """SVG data-URI of a QR pointing at this code's redeem page."""
    return segno.make(f"{BASE_URL}/r/{code}").svg_data_uri(scale=4)

app = FastAPI(title="Sea You Soon — Crew Deck")
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("SEAYOUSOON_SECRET", "dev-secret-change-me"),
)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))


@app.on_event("startup")
def _startup():
    db.init_db()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def current_username(request: Request) -> str | None:
    return request.session.get("username")


def iso_datetime(date_str: str) -> str:
    """'2026-07-13' -> '2026-07-13T00:00:00Z' for the app's ISO-8601 decoder."""
    return f"{date_str}T00:00:00Z"


# ---------------------------------------------------------------------------
# Crew website (HTML)
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    if current_username(request):
        return RedirectResponse("/dashboard", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": None})


@app.get("/register", response_class=HTMLResponse)
def register_form(request: Request):
    return templates.TemplateResponse(
        request, "register.html", {"ships": SHIPS, "error": None}
    )


@app.post("/register", response_class=HTMLResponse)
def register(
    request: Request,
    username: str = Form(...),
    name: str = Form(...),
    ship: str = Form(...),
    embark_date: str = Form(...),
    disembark_date: str = Form(...),
    pin: str = Form(...),
):
    username = username.strip().lower()
    error = None
    if not valid_username(username):
        error = "Username: 3–20 characters, letters/numbers/. _ - only."
    elif len(pin.strip()) < 4:
        error = "Please choose a PIN of at least 4 digits."
    elif db.get_crew(username):
        error = "That username is taken — choose another."
    if error:
        return templates.TemplateResponse(
            request, "register.html", {"ships": SHIPS, "error": error}
        )

    db.create_crew(username, name.strip(), ship, embark_date, disembark_date, pin.strip())
    request.session["username"] = username
    return RedirectResponse("/dashboard", status_code=303)


@app.post("/login", response_class=HTMLResponse)
def login(request: Request, username: str = Form(...), pin: str = Form(...)):
    username = username.strip().lower()
    row = db.get_crew(username)
    if not row or not db.verify_pin(pin.strip(), row["pin_hash"], row["pin_salt"]):
        return templates.TemplateResponse(
            request, "login.html", {"error": "Username or PIN not recognised."}
        )
    request.session["username"] = username
    return RedirectResponse("/dashboard", status_code=303)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    username = current_username(request)
    if not username:
        return RedirectResponse("/", status_code=303)
    crew = db.get_crew(username)
    if not crew:
        request.session.clear()
        return RedirectResponse("/", status_code=303)
    codes = db.active_codes_for(username)
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "crew": crew,
            "ships": SHIPS,
            "codes": codes,
            "qrs": {c["code"]: qr_data_uri(c["code"]) for c in codes},
            "links": db.active_links_for(username),
        },
    )


@app.post("/update-contract")
def update_contract(
    request: Request,
    ship: str = Form(...),
    embark_date: str = Form(...),
    disembark_date: str = Form(...),
):
    """Tours get extended, ships change — followers' apps sync via /api/profile."""
    username = current_username(request)
    if not username:
        return RedirectResponse("/", status_code=303)
    db.update_crew_contract(username, ship, embark_date, disembark_date)
    return RedirectResponse("/dashboard", status_code=303)


@app.post("/generate-code")
def generate(request: Request):
    username = current_username(request)
    if not username:
        return RedirectResponse("/", status_code=303)
    crew = db.get_crew(username)
    if crew:
        code = generate_code(crew["ship"])
        db.save_code(code, username)
    return RedirectResponse("/dashboard", status_code=303)


@app.post("/revoke")
def revoke(request: Request, watch_id: str = Form(...)):
    username = current_username(request)
    if not username:
        return RedirectResponse("/", status_code=303)
    db.revoke_link(watch_id, username)   # scoped to this crew member
    return RedirectResponse("/dashboard", status_code=303)


# ---------------------------------------------------------------------------
# Redeem landing page (target of the QR code — friendly for any camera scan)
# ---------------------------------------------------------------------------
@app.get("/r/{code}", response_class=HTMLResponse)
def redeem_page(request: Request, code: str):
    code = code.strip().upper()
    row = db.get_code(code)
    if not row:
        status = "unknown"
    elif not row["active"] or row["uses"] >= row["max_uses"]:
        status = "used"
    elif datetime.fromisoformat(row["expires_at"]) < datetime.now(timezone.utc):
        status = "expired"
    else:
        status = "valid"
    return templates.TemplateResponse(request, "redeem.html", {"code": code, "status": status})


# ---------------------------------------------------------------------------
# Crew JSON API (consumed by the iOS app's crew mode). Auth = the same
# session cookie as the website; URLSession persists it between launches.
# ---------------------------------------------------------------------------
@app.post("/api/login")
async def api_login(request: Request):
    payload = await request.json()
    username = (payload.get("username") or "").strip().lower()
    pin = (payload.get("pin") or "").strip()
    row = db.get_crew(username)
    if not row or not db.verify_pin(pin, row["pin_hash"], row["pin_salt"]):
        return JSONResponse({"error": "invalid"}, status_code=401)
    request.session["username"] = username
    return {"username": username, "name": row["name"], "ship": row["ship"]}


@app.get("/api/followers")
def api_followers(request: Request):
    username = current_username(request)
    if not username:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    links = db.active_links_for(username)
    return [
        {"watchId": l["watch_id"], "watcherName": l["watcher_name"], "since": l["created_at"]}
        for l in links
    ]


@app.post("/api/generate-code")
def api_generate_code(request: Request):
    """Mint a single-use pairing code from inside the app (same as the
    website's button) so crew can invite family without leaving the app."""
    username = current_username(request)
    if not username:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    crew = db.get_crew(username)
    if not crew:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    code = generate_code(crew["ship"])
    db.save_code(code, username)
    row = db.get_code(code)
    return JSONResponse(
        {"code": code, "expiresAt": row["expires_at"], "link": f"{BASE_URL}/r/{code}"},
        status_code=201,
    )


@app.post("/api/messages")
async def api_send_message(request: Request):
    username = current_username(request)
    if not username:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    payload = await request.json()
    watch_id = (payload.get("watchId") or "").strip()
    body = (payload.get("body") or "").strip()
    if not body or len(body) > 500:
        return JSONResponse({"error": "bad_body"}, status_code=400)
    link = db.get_link(watch_id)
    if not link or link["username"] != username or not link["active"]:
        return JSONResponse({"error": "not_your_follower"}, status_code=403)
    db.create_message(username, watch_id, body)
    return JSONResponse({"ok": True}, status_code=201)


@app.post("/api/revoke")
async def api_revoke(request: Request):
    username = current_username(request)
    if not username:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    payload = await request.json()
    db.revoke_link((payload.get("watchId") or "").strip(), username)
    return {"ok": True}


@app.get("/api/profile/{watch_id}")
def api_family_profile(watch_id: str):
    """Family side: the CURRENT contract (extensions included) for a link.
    The watchId issued at pairing is the credential, as for messages."""
    link = db.get_link(watch_id)
    if not link or not link["active"]:
        return JSONResponse({"error": "unknown_or_revoked"}, status_code=404)
    crew = db.get_crew(link["username"])
    if not crew:
        return JSONResponse({"error": "unknown"}, status_code=404)
    return {
        "crewName": crew["name"],
        "shipName": crew["ship"],
        "embarkDate": iso_datetime(crew["embark_date"]),
        "disembarkDate": iso_datetime(crew["disembark_date"]),
    }


@app.get("/api/messages/{watch_id}")
def api_family_messages(watch_id: str):
    """Family side: the watchId (a UUID issued at pairing) is the credential."""
    link = db.get_link(watch_id)
    if not link or not link["active"]:
        return JSONResponse({"error": "unknown_or_revoked"}, status_code=404)
    crew = db.get_crew(link["username"])
    msgs = db.messages_for_watch(watch_id)
    return {
        "fromName": crew["name"] if crew else "",
        "messages": [
            {"id": m["id"], "body": m["body"], "sentAt": m["created_at"]}
            for m in msgs
        ],
    }


# ---------------------------------------------------------------------------
# Pairing API (consumed by the family iOS app)
# ---------------------------------------------------------------------------
@app.post("/pairing-codes/redeem")
async def redeem(request: Request):
    payload = await request.json()
    code = (payload.get("code") or "").strip().upper()
    watcher_name = (payload.get("watcherName") or "").strip()

    row = db.get_code(code)
    if not row:
        return JSONResponse({"error": "invalid"}, status_code=404)
    if not row["active"] or row["uses"] >= row["max_uses"]:
        return JSONResponse({"error": "used"}, status_code=409)
    if datetime.fromisoformat(row["expires_at"]) < datetime.now(timezone.utc):
        return JSONResponse({"error": "expired"}, status_code=410)

    crew = db.get_crew(row["username"])
    if not crew:
        return JSONResponse({"error": "invalid"}, status_code=404)

    # Consume the code and record the family link (for later revocation).
    watch_id = str(uuid.uuid4())
    db.mark_code_used(code)
    db.create_watch_link(watch_id, crew["username"], watcher_name or "Someone")

    return {
        "accountId": crew["username"],
        "watchId": watch_id,
        "crewName": crew["name"],
        "shipName": crew["ship"],
        "embarkDate": iso_datetime(crew["embark_date"]),
        "disembarkDate": iso_datetime(crew["disembark_date"]),
    }
