"""
Tic-Tac-Toe³ game server · Powered by SCODE
- Public API + WebSocket on 0.0.0.0:8765 (reached through a Cloudflare tunnel)
- Admin hub on 127.0.0.1:5050 (this computer only, never tunnelled)
Run with:  python run.py   (starts this server and the tunnel)
       or: python ttt3_server.py   (server only)
"""
import asyncio, datetime, hashlib, hmac, json, os, random, re, secrets, smtplib, sqlite3, ssl, sys, time
from collections import defaultdict, deque
from email.message import EmailMessage
from email.utils import formataddr

from aiohttp import web, WSMsgType

from engine import State

VERSION = "3.0"
HERE = os.path.dirname(os.path.abspath(__file__))
P = lambda *a: os.path.join(HERE, *a)
START_TIME = time.time()

# ======================= CONFIG =======================
DEFAULT_CONFIG = {
    "port": 8765,
    "admin_port": 5050,
    "allowed_origins": ["https://driftlinegame.github.io"],
    "site_url": "https://driftlinegame.github.io/tictactoe3/",
    "email_from": "scodeai2026@gmail.com",
    "email_name": "SCODE Games",
    "daily_email_limit": 450,
    "turn_seconds": {"1": 30, "2": 60, "3": 90},
    "repo_dir": "..",
    "auto_push_server_json": True,
}


def load_config():
    path = P("config.json")
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(path):
        try:
            cfg.update(json.load(open(path, encoding="utf-8")))
        except Exception as e:
            print("!! config.json is broken, using defaults:", e)
    else:
        json.dump(DEFAULT_CONFIG, open(path, "w", encoding="utf-8"), indent=2)
    return cfg


CFG = load_config()


def read_secret_file(name):
    try:
        v = open(P(name), encoding="utf-8").read().strip()
        return v or None
    except FileNotFoundError:
        return None


def pepper():
    v = read_secret_file("secret.key")
    if not v:
        v = secrets.token_hex(32)
        open(P("secret.key"), "w").write(v)
    return v.encode()


PEPPER = pepper()


def log(*a):
    print(time.strftime("[%H:%M:%S]"), *a, flush=True)


# ======================= DATABASE =======================
db = sqlite3.connect(P("ttt3.db"), check_same_thread=False, isolation_level=None)
db.row_factory = sqlite3.Row
db.execute("PRAGMA journal_mode=WAL")
db.executescript("""
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL COLLATE NOCASE, email TEXT NOT NULL COLLATE NOCASE UNIQUE,
  pw_hash BLOB, pw_salt BLOB, verified INTEGER DEFAULT 0, created REAL, colour TEXT DEFAULT '#8B5CF6', avatar TEXT DEFAULT '🚀',
  banned INTEGER DEFAULT 0, ban_reason TEXT, last_seen REAL);
CREATE INDEX IF NOT EXISTS users_name ON users(name);
CREATE TABLE IF NOT EXISTS ratings(
  user_id INTEGER, mode INTEGER, rating REAL DEFAULT 1000, games INTEGER DEFAULT 0, wins INTEGER DEFAULT 0,
  losses INTEGER DEFAULT 0, draws INTEGER DEFAULT 0, peak REAL DEFAULT 1000, PRIMARY KEY(user_id, mode));
CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, user_id INTEGER, created REAL, expires REAL, ip TEXT);
CREATE TABLE IF NOT EXISTS codes(
  id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT COLLATE NOCASE, purpose TEXT, code_hash TEXT, created REAL, expires REAL,
  tries INTEGER DEFAULT 0, used INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS games(
  id INTEGER PRIMARY KEY AUTOINCREMENT, mode INTEGER, x_id INTEGER, o_id INTEGER, rated INTEGER, winner INTEGER,
  reason TEXT, moves TEXT, started REAL, ended REAL, x_delta REAL DEFAULT 0, o_delta REAL DEFAULT 0);
CREATE INDEX IF NOT EXISTS games_ended ON games(ended);
CREATE TABLE IF NOT EXISTS emails(ts REAL, to_addr TEXT, purpose TEXT, ok INTEGER, error TEXT);
""")


def q(sql, *a):
    return db.execute(sql, a).fetchall()


def q1(sql, *a):
    return db.execute(sql, a).fetchone()


# ======================= SECURITY HELPERS =======================
def hash_pw(pw, salt=None):
    salt = salt or secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode("utf-8"), salt=salt, n=16384, r=8, p=1, dklen=32)
    return h, salt


def check_pw(pw, h, salt):
    if not h or not salt:
        return False
    return hmac.compare_digest(hash_pw(pw, salt)[0], h)


def sha(s):
    return hashlib.sha256(s.encode()).hexdigest()


def code_hash(email, purpose, code):
    return hmac.new(PEPPER, f"{email.lower()}|{purpose}|{code}".encode(), hashlib.sha256).hexdigest()


class Limiter:
    def __init__(self):
        self.hits = defaultdict(deque)

    def ok(self, key, limit, window):
        now = time.time()
        d = self.hits[key]
        while d and d[0] < now - window:
            d.popleft()
        if len(d) >= limit:
            return False
        d.append(now)
        return True


LIMIT = Limiter()

# Name filter: letters, numbers, spaces, _ and -. Checked against a rude-word list after undoing leetspeak.
BAD_WORDS = ["fuck", "fuk", "shit", "bitch", "cunt", "dick", "cock", "pussy", "asshole", "bastard", "slut", "whore", "nigg", "nigga",
             "fag", "retard", "rape", "porn", "sex", "penis", "vagina", "boob", "tits", "cum", "jizz", "nazi", "hitler", "kkk",
             "twat", "wank", "prick", "piss", "dildo", "anal", "molest", "pedo", "spic", "chink", "kike", "tranny", "hoe", "milf",
             "kill", "suicide", "admin", "scode", "moderator", "official"]
LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "8": "b", "@": "a", "$": "s", "!": "i", "|": "i"})


def name_problem(name):
    if not isinstance(name, str):
        return "Pick a name."
    name = name.strip()
    if not 3 <= len(name) <= 16:
        return "Names are 3 to 16 characters."
    if not re.fullmatch(r"[A-Za-z0-9_\- ]+", name) or "  " in name:
        return "Use letters, numbers, spaces, _ or - only."
    flat = re.sub(r"[^a-z]", "", name.lower().translate(LEET))
    if any(w in flat for w in BAD_WORDS):
        return "That name isn't allowed. Try another."
    return None


EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[A-Za-z]{2,}$")
COLOURS = ["#EC4899", "#D946EF", "#8B5CF6", "#6366F1", "#3B82F6", "#0EA5E9", "#14B8A6", "#F59E0B", "#EF4444", "#1F1B2E"]
AVATARS = ["🚀", "🌙", "⭐", "🪐", "☄️", "🔭", "🌌", "👾", "🐙", "🦊", "🐸", "🐼", "🦉", "🐳", "🔥", "⚡", "🎺", "🎯", "🏒", "🥏"]
MODES = (1, 2, 3)
MODE_NAME = {1: "Classic", 2: "Ultimate", 3: "Cubed³"}

# ======================= RATINGS =======================
START_RATING = 1000
PLACEMENT_GAMES = 10
TIERS = [(1850, "Grandmaster"), (1700, "Master"), (1550, "Diamond"), (1400, "Platinum"), (1250, "Gold"), (1100, "Silver"), (0, "Bronze")]


def tier(rating, games):
    if games < PLACEMENT_GAMES:
        return f"Placement {games}/{PLACEMENT_GAMES}"
    for floor, name in TIERS:
        if rating >= floor:
            return name
    return "Bronze"


def k_factor(games, rating):
    if games < PLACEMENT_GAMES:
        return 40
    if games < 30:
        return 24
    return 12 if rating >= 1700 else 16


def get_rating(uid, mode):
    r = q1("SELECT * FROM ratings WHERE user_id=? AND mode=?", uid, mode)
    if not r:
        db.execute("INSERT OR IGNORE INTO ratings(user_id, mode, rating, peak) VALUES(?,?,?,?)", (uid, mode, START_RATING, START_RATING))
        r = q1("SELECT * FROM ratings WHERE user_id=? AND mode=?", uid, mode)
    return dict(r)


def rating_view(r):
    return {"rating": round(r["rating"]), "games": r["games"], "wins": r["wins"], "losses": r["losses"], "draws": r["draws"],
            "peak": round(r["peak"]), "tier": tier(r["rating"], r["games"]), "placed": r["games"] >= PLACEMENT_GAMES}


def apply_rating(mode, x_id, o_id, winner):
    """Elo. Returns (x_delta, o_delta, info dict per side)."""
    rx, ro = get_rating(x_id, mode), get_rating(o_id, mode)
    ex = 1 / (1 + 10 ** ((ro["rating"] - rx["rating"]) / 400))
    sx = 1.0 if winner == 1 else 0.0 if winner == 2 else 0.5
    dx = k_factor(rx["games"], rx["rating"]) * (sx - ex)
    do = k_factor(ro["games"], ro["rating"]) * ((1 - sx) - (1 - ex))
    out = {}
    for side, r, d, res in ((1, rx, dx, sx), (2, ro, do, 1 - sx)):
        new = max(100, r["rating"] + d)
        col = "wins" if res == 1 else "losses" if res == 0 else "draws"
        db.execute(f"UPDATE ratings SET rating=?, games=games+1, {col}={col}+1, peak=MAX(peak, ?) WHERE user_id=? AND mode=?",
                   (new, new, r["user_id"], mode))
        nr = get_rating(r["user_id"], mode)
        out[side] = {"old": round(r["rating"]), "new": round(new), "delta": round(new - r["rating"]), "tier": tier(nr["rating"], nr["games"])}
    return out[1]["delta"], out[2]["delta"], out


# ======================= USERS / SESSIONS =======================
def user_public(u, full=False):
    if not u:
        return {"id": 0, "name": "Deleted player", "colour": "#9A94AE", "avatar": "❔"}
    d = {"id": u["id"], "name": u["name"], "colour": u["colour"], "avatar": u["avatar"]}
    d["ratings"] = {m: rating_view(get_rating(u["id"], m)) for m in MODES}
    if full:
        d["email"] = u["email"]
        d["recent"] = recent_games(u["id"], 10)
    return d


def recent_games(uid, n):
    rows = q("SELECT * FROM games WHERE (x_id=? OR o_id=?) AND winner>0 ORDER BY ended DESC LIMIT ?", uid, uid, n)
    out = []
    for g in rows:
        side = 1 if g["x_id"] == uid else 2
        opp_id = g["o_id"] if side == 1 else g["x_id"]
        opp = q1("SELECT * FROM users WHERE id=?", opp_id)
        res = "D" if g["winner"] == 3 else ("W" if g["winner"] == side else "L")
        out.append({"id": g["id"], "mode": g["mode"], "opp": user_public(opp) if opp else user_public(None), "result": res,
                    "delta": round(g["x_delta"] if side == 1 else g["o_delta"]), "rated": bool(g["rated"]), "reason": g["reason"],
                    "ended": g["ended"]})
    for o in out:
        o["opp"].pop("ratings", None)
    return out


def new_session(uid, ip):
    tok = secrets.token_urlsafe(32)
    db.execute("INSERT INTO sessions VALUES(?,?,?,?,?)", (sha(tok), uid, time.time(), time.time() + 90 * 86400, ip))
    return tok


def user_from_token(tok):
    if not tok or len(tok) > 100:
        return None
    s = q1("SELECT * FROM sessions WHERE token_hash=? AND expires>?", sha(tok), time.time())
    if not s:
        return None
    u = q1("SELECT * FROM users WHERE id=? AND verified=1", s["user_id"])
    if not u or u["banned"]:
        return None
    if not u["last_seen"] or time.time() - u["last_seen"] > 300:
        db.execute("UPDATE users SET last_seen=? WHERE id=?", (time.time(), u["id"]))
    return u


# ======================= EMAIL =======================
def emails_today():
    midnight = datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    return q1("SELECT COUNT(*) c FROM emails WHERE ts>=? AND ok=1", midnight)["c"]


def email_html(name, code, purpose):
    what = "finish creating your account" if purpose == "verify" else "reset your password"
    return f"""<!DOCTYPE html><html><body style="margin:0;background:#F7F5FC;font-family:Segoe UI,Helvetica,Arial,sans-serif;color:#1F1B2E">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="padding:28px 12px"><tr><td align="center">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:440px;background:#ffffff;border-radius:22px;box-shadow:0 8px 30px rgba(80,50,160,.12)">
<tr><td style="height:8px;border-radius:22px 22px 0 0;background:linear-gradient(90deg,#EC4899,#8B5CF6,#3B82F6)"></td></tr>
<tr><td style="padding:26px 28px 8px">
  <div style="font-size:22px;font-weight:900;letter-spacing:-.01em;color:#8B5CF6">Tic-Tac-Toe³</div>
  <p style="font-size:16px;line-height:1.5;margin:14px 0 0">Hi {name or 'there'}! Use this code to {what}:</p>
</td></tr>
<tr><td style="padding:16px 28px">
  <div style="background:linear-gradient(90deg,#EC4899,#8B5CF6,#3B82F6);background-color:#8B5CF6;border-radius:18px;padding:20px 0;text-align:center;
    font-size:40px;font-weight:900;letter-spacing:10px;color:#ffffff;font-family:Consolas,Menlo,monospace">{code}</div>
</td></tr>
<tr><td style="padding:0 28px 22px">
  <p style="font-size:15px;line-height:1.5;margin:0;color:#6B6880">It works for <b>15 minutes</b>. If you didn't ask for this, you can ignore this email.</p>
</td></tr>
<tr><td style="padding:14px 28px 22px;border-top:1px solid #EFE9FC;text-align:center;font-size:13px;color:#6B6880;font-weight:700">
  <span style="display:inline-block;width:18px;height:18px;line-height:18px;border-radius:50%;background:linear-gradient(135deg,#EC4899,#8B5CF6,#3B82F6);background-color:#8B5CF6;color:#fff;font-size:8px;font-weight:900;vertical-align:middle">SC</span>
  &nbsp;Powered by SCODE</td></tr>
</table></td></tr></table></body></html>"""


def send_email_blocking(to, name, code, purpose):
    pw = read_secret_file("email-password.txt")
    subject = f"Your Tic-Tac-Toe³ code: {code}"
    if not pw:
        log(f"(no email-password.txt) CODE for {to} [{purpose}]: {code}")
        db.execute("INSERT INTO emails VALUES(?,?,?,?,?)", (time.time(), to, purpose, 1, "console only"))
        return True
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((CFG["email_name"], CFG["email_from"]))
    msg["To"] = to
    what = "finish creating your account" if purpose == "verify" else "reset your password"
    msg.set_content(f"Hi {name or 'there'}!\n\nYour Tic-Tac-Toe³ code to {what} is:\n\n    {code}\n\n"
                    f"It works for 15 minutes. If you didn't ask for this, ignore this email.\n\nPowered by SCODE")
    msg.add_alternative(email_html(name, code, purpose), subtype="html")
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context(), timeout=20) as s:
            s.login(CFG["email_from"], pw.replace(" ", ""))
            s.send_message(msg)
        db.execute("INSERT INTO emails VALUES(?,?,?,?,?)", (time.time(), to, purpose, 1, None))
        return True
    except Exception as e:
        log("!! email failed:", e)
        db.execute("INSERT INTO emails VALUES(?,?,?,?,?)", (time.time(), to, purpose, 0, str(e)[:300]))
        return False


async def issue_code(email, purpose, name):
    """Returns None on success or an error string."""
    now = time.time()
    last = q1("SELECT created FROM codes WHERE email=? ORDER BY created DESC LIMIT 1", email)
    if last and now - last["created"] < 45:
        return f"Please wait {int(45 - (now - last['created']))} seconds before asking for another code."
    if q1("SELECT COUNT(*) c FROM codes WHERE email=? AND created>?", email, now - 3600)["c"] >= 6:
        return "Too many codes for this email. Try again in an hour."
    if emails_today() >= CFG["daily_email_limit"]:
        return "We've hit today's email limit. Please try again tomorrow."
    code = f"{secrets.randbelow(1000000):06d}"
    db.execute("UPDATE codes SET used=1 WHERE email=? AND purpose=? AND used=0", (email, purpose))
    db.execute("INSERT INTO codes(email, purpose, code_hash, created, expires) VALUES(?,?,?,?,?)",
               (email, purpose, code_hash(email, purpose, code), now, now + 15 * 60))
    ok = await asyncio.to_thread(send_email_blocking, email, name, code, purpose)
    if os.environ.get("TTT3_DEV_CODES") == "1":
        DEV_CODES[email.lower()] = code
    return None if ok else "We couldn't send the email. Try again in a minute."


DEV_CODES = {}


def check_code(email, purpose, code):
    row = q1("SELECT * FROM codes WHERE email=? AND purpose=? AND used=0 ORDER BY created DESC LIMIT 1", email, purpose)
    if not row or row["expires"] < time.time():
        return "That code has expired. Ask for a new one."
    if row["tries"] >= 5:
        return "Too many wrong tries. Ask for a new code."
    if not isinstance(code, str) or not hmac.compare_digest(row["code_hash"], code_hash(email, purpose, code.strip())):
        db.execute("UPDATE codes SET tries=tries+1 WHERE id=?", (row["id"],))
        left = 4 - row["tries"]
        return f"Wrong code. {left} tr{'y' if left == 1 else 'ies'} left." if left > 0 else "Too many wrong tries. Ask for a new code."
    db.execute("UPDATE codes SET used=1 WHERE id=?", (row["id"],))
    return None


# ======================= HTTP HELPERS =======================
def client_ip(request):
    peer = request.remote or ""
    if peer in ("127.0.0.1", "::1") and request.headers.get("CF-Connecting-IP"):
        return request.headers["CF-Connecting-IP"][:64]
    return peer


def origin_ok(origin):
    if not origin:
        return False
    if origin in CFG["allowed_origins"]:
        return True
    return bool(re.fullmatch(r"http://(localhost|127\.0\.0\.1)(:\d+)?", origin))


def err(msg, status=400, **extra):
    return web.json_response({"ok": False, "error": msg, **extra}, status=status)


def ok(**data):
    return web.json_response({"ok": True, **data})


@web.middleware
async def cors_mw(request, handler):
    origin = request.headers.get("Origin")
    if request.method == "OPTIONS":
        resp = web.Response(status=204)
    else:
        ip = client_ip(request)
        if request.path.startswith("/api/") and not LIMIT.ok(("api", ip), 180, 60):
            resp = err("Slow down a little.", 429)
        else:
            try:
                resp = await handler(request)
            except web.HTTPException as e:
                resp = e
            except Exception as e:
                log("!! error in", request.path, repr(e))
                resp = err("Server error.", 500)
    if origin_ok(origin):
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Vary"] = "Origin"
        resp.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        resp.headers["Access-Control-Max-Age"] = "600"
    return resp


async def body(request):
    try:
        if request.content_length and request.content_length > 4096:
            return {}
        d = await request.json()
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def auth_user(request):
    h = request.headers.get("Authorization", "")
    return user_from_token(h[7:].strip()) if h.startswith("Bearer ") else None


def norm_email(e):
    return e.strip().lower() if isinstance(e, str) else ""


# ======================= ACCOUNT API =======================
routes = web.RouteTableDef()


@routes.get("/api/status")
async def api_status(request):
    return ok(version=VERSION, online=len(HUB.conns), games=len(HUB.games),
              queue={m: len(HUB.queue[m]) for m in MODES}, turn_seconds=CFG["turn_seconds"])


@routes.post("/api/signup")
async def api_signup(request):
    ip, d = client_ip(request), await body(request)
    if not LIMIT.ok(("signup", ip), 6, 3600):
        return err("Too many sign-ups from here. Try again later.", 429)
    name, email, pw = (d.get("name") or "").strip(), norm_email(d.get("email")), d.get("password") or ""
    if (p := name_problem(name)):
        return err(p, field="name")
    if not EMAIL_RE.match(email):
        return err("That email doesn't look right.", field="email")
    if not isinstance(pw, str) or not 8 <= len(pw) <= 200:
        return err("Passwords need at least 8 characters.", field="password")
    existing = q1("SELECT * FROM users WHERE email=?", email)
    if existing and existing["verified"]:
        return err("There's already an account with that email. Sign in instead.", field="email")
    taken = q1("SELECT id FROM users WHERE name=? AND verified=1", name)
    if taken:
        return err("That name is taken.", field="name")
    h, salt = hash_pw(pw)
    if existing:
        db.execute("UPDATE users SET name=?, pw_hash=?, pw_salt=?, created=? WHERE id=?", (name, h, salt, time.time(), existing["id"]))
    else:
        db.execute("INSERT INTO users(name, email, pw_hash, pw_salt, verified, created, colour, avatar) VALUES(?,?,?,?,0,?,?,?)",
                   (name, email, h, salt, time.time(), random.choice(COLOURS[:7]), random.choice(AVATARS)))
    e = await issue_code(email, "verify", name)
    if e:
        return err(e)
    return ok(need_code=True, email=email)


@routes.post("/api/verify")
async def api_verify(request):
    ip, d = client_ip(request), await body(request)
    email = norm_email(d.get("email"))
    if not LIMIT.ok(("code", ip), 30, 600) or not LIMIT.ok(("code", email), 12, 600):
        return err("Too many tries. Wait a few minutes.", 429)
    u = q1("SELECT * FROM users WHERE email=?", email)
    if not u:
        return err("No sign-up found for that email.")
    if (e := check_code(email, "verify", d.get("code"))):
        return err(e, field="code")
    if q1("SELECT id FROM users WHERE name=? AND verified=1 AND id<>?", u["name"], u["id"]):
        db.execute("UPDATE users SET name=? WHERE id=?", (f"{u['name'][:11]}{secrets.randbelow(9999)}", u["id"]))
    db.execute("UPDATE users SET verified=1 WHERE id=?", (u["id"],))
    log(f"New account: {u['name']} <{email}>")
    u = q1("SELECT * FROM users WHERE id=?", u["id"])
    return ok(token=new_session(u["id"], ip), user=user_public(u, True))


@routes.post("/api/resend")
async def api_resend(request):
    ip, d = client_ip(request), await body(request)
    email, purpose = norm_email(d.get("email")), d.get("purpose")
    if purpose not in ("verify", "reset") or not LIMIT.ok(("resend", ip), 10, 3600):
        return err("Can't send another code right now.", 429)
    u = q1("SELECT * FROM users WHERE email=?", email)
    if u and (purpose == "verify") == (not u["verified"]):
        e = await issue_code(email, purpose, u["name"])
        if e:
            return err(e)
    return ok()


@routes.post("/api/login")
async def api_login(request):
    ip, d = client_ip(request), await body(request)
    email, pw = norm_email(d.get("email")), d.get("password") or ""
    if not LIMIT.ok(("login", ip), 20, 600) or not LIMIT.ok(("login", email), 10, 600):
        return err("Too many sign-in attempts. Wait a few minutes.", 429)
    u = q1("SELECT * FROM users WHERE email=?", email)
    if not u or not check_pw(pw, u["pw_hash"], u["pw_salt"]):
        return err("Wrong email or password.")
    if u["banned"]:
        return err(f"This account is banned. {u['ban_reason'] or ''}".strip(), 403)
    if not u["verified"]:
        e = await issue_code(email, "verify", u["name"])
        return ok(need_code=True, email=email, note=e)
    return ok(token=new_session(u["id"], ip), user=user_public(u, True))


@routes.post("/api/logout")
async def api_logout(request):
    h = request.headers.get("Authorization", "")
    if h.startswith("Bearer "):
        db.execute("DELETE FROM sessions WHERE token_hash=?", (sha(h[7:].strip()),))
    return ok()


@routes.post("/api/forgot")
async def api_forgot(request):
    ip, d = client_ip(request), await body(request)
    email = norm_email(d.get("email"))
    if not LIMIT.ok(("forgot", ip), 6, 3600):
        return err("Too many requests. Try again later.", 429)
    u = q1("SELECT * FROM users WHERE email=? AND verified=1", email)
    if u and not u["banned"]:
        e = await issue_code(email, "reset", u["name"])
        if e and "wait" in e.lower():
            return err(e)
    return ok()  # same answer either way, so nobody can check which emails have accounts


@routes.post("/api/reset")
async def api_reset(request):
    ip, d = client_ip(request), await body(request)
    email, pw = norm_email(d.get("email")), d.get("password") or ""
    if not LIMIT.ok(("code", ip), 30, 600):
        return err("Too many tries. Wait a few minutes.", 429)
    if not isinstance(pw, str) or not 8 <= len(pw) <= 200:
        return err("Passwords need at least 8 characters.", field="password")
    u = q1("SELECT * FROM users WHERE email=? AND verified=1", email)
    if not u:
        return err("That code has expired. Ask for a new one.", field="code")
    if (e := check_code(email, "reset", d.get("code"))):
        return err(e, field="code")
    h, salt = hash_pw(pw)
    db.execute("UPDATE users SET pw_hash=?, pw_salt=? WHERE id=?", (h, salt, u["id"]))
    db.execute("DELETE FROM sessions WHERE user_id=?", (u["id"],))
    return ok(token=new_session(u["id"], ip), user=user_public(u, True))


@routes.get("/api/me")
async def api_me(request):
    u = auth_user(request)
    if not u:
        return err("Signed out.", 401)
    return ok(user=user_public(u, True), active_game=HUB.user_game.get(u["id"]) is not None)


@routes.post("/api/profile")
async def api_profile(request):
    u = auth_user(request)
    if not u:
        return err("Signed out.", 401)
    d = await body(request)
    if "name" in d and d["name"].strip() != u["name"]:
        if not LIMIT.ok(("rename", u["id"]), 5, 86400):
            return err("You can only change your name 5 times a day.", field="name")
        name = d["name"].strip()
        if (p := name_problem(name)):
            return err(p, field="name")
        if q1("SELECT id FROM users WHERE name=? AND verified=1 AND id<>?", name, u["id"]):
            return err("That name is taken.", field="name")
        db.execute("UPDATE users SET name=? WHERE id=?", (name, u["id"]))
    if d.get("colour") in COLOURS:
        db.execute("UPDATE users SET colour=? WHERE id=?", (d["colour"], u["id"]))
    if d.get("avatar") in AVATARS:
        db.execute("UPDATE users SET avatar=? WHERE id=?", (d["avatar"], u["id"]))
    return ok(user=user_public(q1("SELECT * FROM users WHERE id=?", u["id"]), True))


@routes.post("/api/delete")
async def api_delete(request):
    u = auth_user(request)
    if not u:
        return err("Signed out.", 401)
    d = await body(request)
    if not check_pw(d.get("password") or "", u["pw_hash"], u["pw_salt"]):
        return err("Wrong password.", field="password")
    delete_user(u["id"])
    log(f"Account deleted by user: {u['name']}")
    return ok()


def delete_user(uid):
    for tbl, col in (("sessions", "user_id"), ("ratings", "user_id"), ("users", "id")):
        db.execute(f"DELETE FROM {tbl} WHERE {col}=?", (uid,))
    ws = HUB.conns.get(uid)
    if ws:
        asyncio.ensure_future(ws.close())


@routes.get("/api/user/{uid}")
async def api_user(request):
    try:
        uid = int(request.match_info["uid"])
    except ValueError:
        return err("Not found.", 404)
    u = q1("SELECT * FROM users WHERE id=? AND verified=1 AND banned=0", uid)
    if not u:
        return err("Not found.", 404)
    pub = user_public(u)
    pub["recent"] = recent_games(uid, 8)
    pub["online"] = uid in HUB.conns
    return ok(user=pub)


@routes.get("/api/options")
async def api_options(request):
    return ok(colours=COLOURS, avatars=AVATARS, tiers=[[f, n] for f, n in TIERS], placement=PLACEMENT_GAMES, start=START_RATING)


# ======================= LEADERBOARDS =======================
def period_start(period):
    now = datetime.datetime.now()
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "day":
        return day.timestamp()
    if period == "week":
        return (day - datetime.timedelta(days=day.weekday())).timestamp()
    if period == "month":
        return day.replace(day=1).timestamp()
    return 0


def leaderboard(mode, period, limit=50):
    rows = []
    if period == "all":
        for r in q("""SELECT r.*, u.name, u.colour, u.avatar FROM ratings r JOIN users u ON u.id=r.user_id
                      WHERE r.mode=? AND r.games>=? AND u.banned=0 AND u.verified=1 ORDER BY r.rating DESC LIMIT ?""", mode, PLACEMENT_GAMES, limit):
            rows.append({"id": r["user_id"], "name": r["name"], "colour": r["colour"], "avatar": r["avatar"], "rating": round(r["rating"]),
                         "score": round(r["rating"]), "games": r["games"], "wins": r["wins"], "tier": tier(r["rating"], r["games"])})
        return rows
    since = period_start(period)
    for r in q("""SELECT uid, SUM(d) gain, SUM(w) wins, COUNT(*) games FROM (
                    SELECT x_id uid, x_delta d, winner=1 w FROM games WHERE rated=1 AND mode=? AND ended>=? AND winner IN (1,2,3)
                    UNION ALL SELECT o_id, o_delta, winner=2 FROM games WHERE rated=1 AND mode=? AND ended>=? AND winner IN (1,2,3))
                  GROUP BY uid ORDER BY gain DESC, wins DESC LIMIT ?""", mode, since, mode, since, limit * 2):
        u = q1("SELECT * FROM users WHERE id=? AND banned=0 AND verified=1", r["uid"])
        if not u:
            continue
        rt = get_rating(u["id"], mode)
        rows.append({"id": u["id"], "name": u["name"], "colour": u["colour"], "avatar": u["avatar"], "rating": round(rt["rating"]),
                     "score": round(r["gain"]), "games": r["games"], "wins": r["wins"], "tier": tier(rt["rating"], rt["games"])})
    return rows[:limit]


@routes.get("/api/leaderboard")
async def api_leaderboard(request):
    try:
        mode = int(request.query.get("mode", "3"))
    except ValueError:
        mode = 3
    period = request.query.get("period", "all")
    if mode not in MODES or period not in ("day", "week", "month", "all"):
        return err("Bad leaderboard.")
    return ok(mode=mode, period=period, rows=leaderboard(mode, period))


# ======================= LIVE GAMES (WebSocket hub) =======================
class Game:
    def __init__(self, gid, mode, x_id, o_id, rated, code=None):
        self.id, self.mode, self.rated, self.code = gid, mode, rated, code
        self.players = {1: x_id, 2: o_id}
        self.state = State(mode)
        self.moves = []
        self.started = time.time()
        self.deadline = 0.0
        self.timer = None
        self.over = False
        self.turn_secs = int(CFG["turn_seconds"].get(str(mode), 60))

    def side_of(self, uid):
        return 1 if self.players[1] == uid else 2 if self.players[2] == uid else 0


class Hub:
    def __init__(self):
        self.conns = {}  # uid -> ws
        self.queue = {m: [] for m in MODES}  # entries: dict(uid, rating, t)
        self.rooms = {}  # code -> dict(mode, host, t)
        self.games = {}  # gid -> Game
        self.user_game = {}  # uid -> gid
        self.next_id = int(time.time() * 1000) % 10_000_000

    # ---------- messaging ----------
    async def send(self, uid, msg):
        ws = self.conns.get(uid)
        if ws is not None and not ws.closed:
            try:
                await ws.send_str(json.dumps(msg, separators=(",", ":")))
            except Exception:
                pass

    def users_brief(self, uid, mode):
        u = q1("SELECT * FROM users WHERE id=?", uid)
        r = get_rating(uid, mode)
        return {"id": uid, "name": u["name"] if u else "Player", "colour": u["colour"] if u else "#8B5CF6",
                "avatar": u["avatar"] if u else "🚀", "rating": round(r["rating"]), "tier": tier(r["rating"], r["games"])}

    def game_payload(self, g, uid):
        side = g.side_of(uid)
        return {"t": "state", "id": g.id, "mode": g.mode, "you": side, "rated": g.rated, "code": g.code,
                "players": {1: self.users_brief(g.players[1], g.mode), 2: self.users_brief(g.players[2], g.mode)},
                "moves": g.moves, "left": max(0, int((g.deadline - time.time()) * 1000)), "turn_secs": g.turn_secs,
                "opp_online": g.players[3 - side] in self.conns}

    # ---------- lobby ----------
    def leave_lobby(self, uid):
        for m in MODES:
            self.queue[m] = [e for e in self.queue[m] if e["uid"] != uid]
        for code in [c for c, r in self.rooms.items() if r["host"] == uid]:
            del self.rooms[code]

    async def enqueue(self, uid, mode):
        if uid in self.user_game:
            return await self.resume(uid)
        self.leave_lobby(uid)
        self.queue[mode].append({"uid": uid, "rating": get_rating(uid, mode)["rating"], "t": time.time()})
        await self.send(uid, {"t": "queued", "mode": mode})

    async def matchmaker(self):
        while True:
            await asyncio.sleep(1)
            try:
                now = time.time()
                for m in MODES:
                    qq = sorted(self.queue[m], key=lambda e: e["t"])
                    paired = set()
                    win = lambda e: min(1200, 150 + 25 * (now - e["t"]))  # rating gap allowed grows the longer you wait
                    for a in qq:
                        if a["uid"] in paired:
                            continue
                        cand = [b for b in qq if b["uid"] != a["uid"] and b["uid"] not in paired
                                and abs(b["rating"] - a["rating"]) <= max(win(a), win(b))]
                        if not cand:
                            continue
                        b = min(cand, key=lambda e: abs(e["rating"] - a["rating"]))
                        paired |= {a["uid"], b["uid"]}
                        self.queue[m] = [e for e in self.queue[m] if e["uid"] not in (a["uid"], b["uid"])]
                        x, o = (a["uid"], b["uid"]) if random.random() < .5 else (b["uid"], a["uid"])
                        await self.start_game(m, x, o, rated=True)
                for code in [c for c, r in self.rooms.items() if now - r["t"] > 900]:
                    host = self.rooms.pop(code)["host"]
                    await self.send(host, {"t": "room_expired"})
            except Exception as e:
                log("!! matchmaker:", repr(e))

    async def create_room(self, uid, mode):
        if uid in self.user_game:
            return await self.resume(uid)
        self.leave_lobby(uid)
        for _ in range(50):
            code = f"{secrets.randbelow(10000):04d}"
            if code not in self.rooms:
                break
        self.rooms[code] = {"mode": mode, "host": uid, "t": time.time()}
        await self.send(uid, {"t": "room", "code": code, "mode": mode})

    async def join_room(self, uid, code):
        if uid in self.user_game:
            return await self.resume(uid)
        r = self.rooms.get(str(code))
        if not r:
            return await self.send(uid, {"t": "error", "msg": "No game with that code. Check the 4 digits."})
        if r["host"] == uid:
            return await self.send(uid, {"t": "error", "msg": "That's your own game. Send the code to a friend!"})
        del self.rooms[str(code)]
        self.leave_lobby(uid)
        x, o = (r["host"], uid) if random.random() < .5 else (uid, r["host"])
        await self.start_game(r["mode"], x, o, rated=False, code=str(code))

    # ---------- games ----------
    async def start_game(self, mode, x, o, rated, code=None):
        self.next_id += 1
        g = Game(self.next_id, mode, x, o, rated, code)
        self.games[g.id] = g
        self.user_game[x] = self.user_game[o] = g.id
        self.arm_timer(g)
        log(f"Game {g.id} started: {MODE_NAME[mode]} {'ranked' if rated else 'friend'}  X={x} O={o}")
        for uid in (x, o):
            await self.send(uid, self.game_payload(g, uid))

    def arm_timer(self, g):
        if g.timer:
            g.timer.cancel()
        g.deadline = time.time() + g.turn_secs
        n = len(g.moves)
        g.timer = asyncio.get_event_loop().call_later(g.turn_secs + 1.5, lambda: asyncio.ensure_future(self.timeout(g, n)))

    async def timeout(self, g, n):
        if g.over or len(g.moves) != n:
            return
        loser = g.state.turn
        made = sum(1 for i in range(len(g.moves)) if (i % 2 == 0) == (loser == 1))
        if made == 0:
            await self.finish(g, 0, "abort")
        else:
            await self.finish(g, 3 - loser, "time")

    async def resume(self, uid):
        gid = self.user_game.get(uid)
        if gid and gid in self.games:
            g = self.games[gid]
            await self.send(uid, self.game_payload(g, uid))
            await self.send(g.players[3 - g.side_of(uid)], {"t": "opp", "online": True})

    async def move(self, uid, cell, worm, n):
        gid = self.user_game.get(uid)
        g = self.games.get(gid)
        if not g or g.over:
            return
        side = g.side_of(uid)
        if side != g.state.turn or n != len(g.moves) or not isinstance(cell, int) or not g.state.is_legal(cell, bool(worm)):
            await self.send(uid, self.game_payload(g, uid) | {"t": "sync", "msg": "Move rejected, board re-synced."})
            return
        g.state.play(cell, bool(worm))
        g.moves.append([cell, 1 if worm else 0])
        if g.state.winner:
            await self.broadcast_move(g, cell, worm)
            await self.finish(g, g.state.winner, "draw" if g.state.winner == 3 else "line")
        else:
            self.arm_timer(g)
            await self.broadcast_move(g, cell, worm)

    async def broadcast_move(self, g, cell, worm):
        msg = {"t": "move", "cell": cell, "worm": 1 if worm else 0, "n": len(g.moves) - 1, "left": max(0, int((g.deadline - time.time()) * 1000))}
        for uid in g.players.values():
            await self.send(uid, msg)

    async def resign(self, uid):
        g = self.games.get(self.user_game.get(uid))
        if not g or g.over:
            return
        side = g.side_of(uid)
        await self.finish(g, 0 if not g.moves else 3 - side, "abort" if not g.moves else "resign")

    async def finish(self, g, winner, reason):
        if g.over:
            return
        g.over = True
        if g.timer:
            g.timer.cancel()
        x, o = g.players[1], g.players[2]
        info, dx, do = {}, 0, 0
        if g.rated and winner in (1, 2, 3):
            dx, do, info = apply_rating(g.mode, x, o, winner)
        db.execute("INSERT INTO games(mode, x_id, o_id, rated, winner, reason, moves, started, ended, x_delta, o_delta) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                   (g.mode, x, o, 1 if g.rated else 0, winner, reason, json.dumps(g.moves), g.started, time.time(), dx, do))
        log(f"Game {g.id} over: winner={winner} reason={reason} rated={g.rated} deltas={dx:+.0f}/{do:+.0f}")
        for uid in (x, o):
            self.user_game.pop(uid, None)
        self.games.pop(g.id, None)
        msg = {"t": "end", "winner": winner, "reason": reason, "rated": g.rated, "ratings": info}
        for uid in (x, o):
            await self.send(uid, msg)

    # ---------- connections ----------
    async def on_connect(self, uid, ws):
        old = self.conns.get(uid)
        self.conns[uid] = ws
        if old is not None and old is not ws:
            try:
                await old.send_str(json.dumps({"t": "kicked"}))
                await old.close()
            except Exception:
                pass
        if uid in self.user_game:
            await self.resume(uid)

    async def on_disconnect(self, uid, ws):
        if self.conns.get(uid) is ws:
            del self.conns[uid]
            self.leave_lobby(uid)
            g = self.games.get(self.user_game.get(uid))
            if g:
                await self.send(g.players[3 - g.side_of(uid)], {"t": "opp", "online": False})


HUB = Hub()


@routes.get("/ws")
async def ws_handler(request):
    if not origin_ok(request.headers.get("Origin")):
        return web.Response(status=403, text="Origin not allowed")
    ip = client_ip(request)
    if not LIMIT.ok(("ws", ip), 30, 60):
        return web.Response(status=429)
    ws = web.WebSocketResponse(heartbeat=25, max_msg_size=4096)
    await ws.prepare(request)
    uid = None
    try:
        # first message must be {"t":"auth","token":...}
        msg = await ws.receive(timeout=10)
        d = json.loads(msg.data) if msg.type == WSMsgType.TEXT else {}
        u = user_from_token(d.get("token")) if d.get("t") == "auth" else None
        if not u:
            await ws.send_str(json.dumps({"t": "auth_fail"}))
            await ws.close()
            return ws
        uid = u["id"]
        await ws.send_str(json.dumps({"t": "hello", "user": {"id": uid, "name": u["name"]}, "online": len(HUB.conns) + 1}))
        await HUB.on_connect(uid, ws)
        bucket = deque()
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            now = time.time()
            bucket.append(now)
            while bucket and bucket[0] < now - 5:
                bucket.popleft()
            if len(bucket) > 40:
                continue
            try:
                d = json.loads(msg.data)
            except ValueError:
                continue
            t = d.get("t")
            mode = d.get("mode") if d.get("mode") in MODES else 3
            if t == "queue":
                await HUB.enqueue(uid, mode)
            elif t == "cancel":
                HUB.leave_lobby(uid)
                await HUB.send(uid, {"t": "cancelled"})
            elif t == "create":
                await HUB.create_room(uid, mode)
            elif t == "join":
                await HUB.join_room(uid, str(d.get("code", ""))[:4])
            elif t == "move":
                await HUB.move(uid, d.get("cell"), d.get("worm"), d.get("n"))
            elif t == "resign":
                await HUB.resign(uid)
            elif t == "resume":
                await HUB.resume(uid)
            elif t == "ping":
                await HUB.send(uid, {"t": "pong"})
    except (asyncio.TimeoutError, ValueError):
        pass
    except Exception as e:
        log("!! ws error:", repr(e))
    finally:
        if uid is not None:
            await HUB.on_disconnect(uid, ws)
    return ws


if os.environ.get("TTT3_DEV_CODES") == "1":  # testing only: lets a local test script read codes
    @routes.get("/api/dev/code")
    async def dev_code(request):
        if client_ip(request) not in ("127.0.0.1", "::1"):
            return err("no", 404)
        return ok(code=DEV_CODES.get(norm_email(request.query.get("email"))))


@routes.get("/")
async def root(request):
    return web.Response(text=f"Tic-Tac-Toe³ game server v{VERSION} · Powered by SCODE\nPlay at {CFG['site_url']}\n")


# ======================= HOUSEKEEPING =======================
async def housekeeping():
    while True:
        try:
            now = time.time()
            db.execute("DELETE FROM sessions WHERE expires<?", (now,))
            db.execute("DELETE FROM codes WHERE created<?", (now - 86400,))
            db.execute("DELETE FROM users WHERE verified=0 AND created<?", (now - 86400,))
            db.execute("DELETE FROM emails WHERE ts<?", (now - 60 * 86400,))
        except Exception as e:
            log("!! housekeeping:", repr(e))
        await asyncio.sleep(3600)


# ======================= ADMIN (127.0.0.1 only) =======================
from admin import make_admin_app  # noqa: E402


async def main():
    app = web.Application(middlewares=[cors_mw], client_max_size=8192)
    app.add_routes(routes)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", CFG["port"]).start()
    admin_runner = web.AppRunner(make_admin_app(sys.modules[__name__]), access_log=None)
    await admin_runner.setup()
    await web.TCPSite(admin_runner, "127.0.0.1", CFG["admin_port"]).start()
    log(f"Tic-Tac-Toe³ server v{VERSION} on port {CFG['port']} · admin on http://127.0.0.1:{CFG['admin_port']}")
    if not read_secret_file("email-password.txt"):
        log("No email-password.txt yet: sign-up codes will be printed here instead of emailed.")
    asyncio.ensure_future(HUB.matchmaker())
    asyncio.ensure_future(housekeeping())
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
