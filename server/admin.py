"""Admin hub for the Tic-Tac-Toe³ server. Bound to 127.0.0.1 only, so it opens on this computer and never through the tunnel."""
import datetime, hmac, os, secrets, time
from aiohttp import web

HERE = os.path.dirname(os.path.abspath(__file__))


def admin_password():
    path = os.path.join(HERE, "admin-password.txt")
    if not os.path.exists(path):
        pw = secrets.token_urlsafe(12)
        open(path, "w").write(pw)
        print(f"\n*** Admin password created: {pw}  (saved in admin-password.txt)\n", flush=True)
    return open(path).read().strip()


def make_admin_app(S):
    PW = admin_password()
    tokens = {}  # token -> expiry
    fails = []

    def authed(request):
        t = request.cookies.get("adm")
        return bool(t and tokens.get(t, 0) > time.time())

    @web.middleware
    async def guard(request, handler):
        # refuse anything that isn't a local browser (blocks DNS-rebinding style tricks)
        host = (request.host or "").split(":")[0]
        if host not in ("127.0.0.1", "localhost"):
            return web.Response(status=403, text="Local only")
        if request.path in ("/", "/login") or authed(request):
            return await handler(request)
        return web.json_response({"ok": False, "error": "Sign in"}, status=401)

    r = web.RouteTableDef()

    @r.get("/")
    async def page(request):
        return web.FileResponse(os.path.join(HERE, "admin.html"))

    @r.post("/login")
    async def login(request):
        now = time.time()
        fails[:] = [f for f in fails if f > now - 300]
        if len(fails) >= 10:
            return web.json_response({"ok": False, "error": "Too many tries. Wait 5 minutes."}, status=429)
        d = await request.json()
        if not hmac.compare_digest(str(d.get("password", "")).encode(), PW.encode()):
            fails.append(now)
            return web.json_response({"ok": False, "error": "Wrong password"}, status=401)
        t = secrets.token_urlsafe(24)
        tokens[t] = now + 12 * 3600
        resp = web.json_response({"ok": True})
        resp.set_cookie("adm", t, httponly=True, samesite="Strict", max_age=12 * 3600)
        return resp

    @r.post("/logout")
    async def logout(request):
        tokens.pop(request.cookies.get("adm", ""), None)
        return web.json_response({"ok": True})

    def brief(uid):
        u = S.q1("SELECT id, name, avatar, colour FROM users WHERE id=?", uid)
        return dict(u) if u else {"id": uid, "name": "(deleted)", "avatar": "❔", "colour": "#999"}

    @r.get("/api/overview")
    async def overview(request):
        H = S.HUB
        live = []
        for g in list(H.games.values()):
            live.append({"id": g.id, "mode": S.MODE_NAME[g.mode], "rated": g.rated, "code": g.code, "moves": len(g.moves),
                         "x": brief(g.players[1]), "o": brief(g.players[2]), "started": g.started,
                         "turn": "X" if g.state.turn == 1 else "O", "left": max(0, int(g.deadline - time.time()))})
        online = [brief(uid) | {"in_game": uid in H.user_game} for uid in list(H.conns)]
        midnight = datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        emails = [dict(e) for e in S.q("SELECT * FROM emails WHERE ts>=? ORDER BY ts DESC", midnight)]
        return web.json_response({
            "version": S.VERSION, "uptime": int(time.time() - S.START_TIME), "online": online, "live": live,
            "queue": {S.MODE_NAME[m]: [brief(e["uid"]) for e in H.queue[m]] for m in S.MODES},
            "rooms": [{"code": c, "mode": S.MODE_NAME[x["mode"]], "host": brief(x["host"])} for c, x in H.rooms.items()],
            "emails": emails, "emails_sent": sum(1 for e in emails if e["ok"]), "email_limit": S.CFG["daily_email_limit"],
            "email_ready": bool(S.read_secret_file("email-password.txt")),
            "accounts": S.q1("SELECT COUNT(*) c FROM users WHERE verified=1")["c"],
            "games_today": S.q1("SELECT COUNT(*) c FROM games WHERE ended>=?", midnight)["c"],
        })

    @r.get("/api/users")
    async def users(request):
        qs = (request.query.get("q") or "").strip()
        like = f"%{qs}%"
        rows = S.q("""SELECT * FROM users WHERE (name LIKE ? OR email LIKE ? OR CAST(id AS TEXT)=?) ORDER BY id DESC LIMIT 100""", like, like, qs)
        out = []
        for u in rows:
            d = {k: u[k] for k in ("id", "name", "email", "verified", "created", "colour", "avatar", "banned", "ban_reason", "last_seen")}
            d["ratings"] = {m: S.rating_view(S.get_rating(u["id"], m)) for m in S.MODES}
            d["online"] = u["id"] in S.HUB.conns
            out.append(d)
        return web.json_response({"ok": True, "users": out})

    async def uid_of(request):
        return int(request.match_info["uid"])

    @r.post("/api/users/{uid}/ban")
    async def ban(request):
        uid, d = await uid_of(request), await request.json()
        S.db.execute("UPDATE users SET banned=1, ban_reason=? WHERE id=?", (str(d.get("reason", ""))[:200], uid))
        S.db.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
        ws = S.HUB.conns.get(uid)
        if ws:
            await ws.close()
        return web.json_response({"ok": True})

    @r.post("/api/users/{uid}/unban")
    async def unban(request):
        S.db.execute("UPDATE users SET banned=0, ban_reason=NULL WHERE id=?", (await uid_of(request),))
        return web.json_response({"ok": True})

    @r.post("/api/users/{uid}/delete")
    async def delete(request):
        S.delete_user(await uid_of(request))
        return web.json_response({"ok": True})

    @r.post("/api/users/{uid}/reset_stats")
    async def reset_stats(request):
        S.db.execute("DELETE FROM ratings WHERE user_id=?", (await uid_of(request),))
        return web.json_response({"ok": True})

    @r.post("/api/users/{uid}/rename")
    async def rename(request):
        uid, d = await uid_of(request), await request.json()
        name = str(d.get("name", "")).strip()
        if not 3 <= len(name) <= 16:
            return web.json_response({"ok": False, "error": "3 to 16 characters"})
        if S.q1("SELECT id FROM users WHERE name=? AND id<>?", name, uid):
            return web.json_response({"ok": False, "error": "Name taken"})
        S.db.execute("UPDATE users SET name=? WHERE id=?", (name, uid))
        return web.json_response({"ok": True})

    @r.post("/api/users/{uid}/rating")
    async def set_rating(request):
        uid, d = await uid_of(request), await request.json()
        mode, rating = int(d.get("mode", 3)), float(d.get("rating", 1000))
        S.get_rating(uid, mode)
        S.db.execute("UPDATE ratings SET rating=?, peak=MAX(peak, ?) WHERE user_id=? AND mode=?", (rating, rating, uid, mode))
        return web.json_response({"ok": True})

    @r.post("/api/games/{gid}/abort")
    async def abort(request):
        g = S.HUB.games.get(int(request.match_info["gid"]))
        if g:
            await S.HUB.finish(g, 0, "abort")
        return web.json_response({"ok": True})

    @r.get("/api/leaderboard")
    async def lb(request):
        return web.json_response({"ok": True, "rows": S.leaderboard(int(request.query.get("mode", 3)), request.query.get("period", "all"), 100)})

    app = web.Application(middlewares=[guard])
    app.add_routes(r)
    return app
