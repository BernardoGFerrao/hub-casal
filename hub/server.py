#!/usr/bin/env python3
"""
server.py — Hub Casal
Roda em http://localhost:5001

Dois perfis: Bernardo e Amanda.
Cada perfil tem seu próprio banco SQLite (bernardo.db / amanda.db)
e seus próprios dados de saúde/configurações.
"""

import json
import sys
import sqlite3
import threading
import webbrowser
import importlib.util
import hmac
import hashlib
import os
import time
from pathlib import Path
from http.server import HTTPServer, SimpleHTTPRequestHandler
from http.cookies import SimpleCookie
from urllib.parse import urlparse, parse_qs

HUB_DIR = Path(__file__).parent
PORT    = 5001

# ---------------------------------------------------------------------------
# Usuários
# ---------------------------------------------------------------------------

USERS = {
    "bernardo": {
        "name":     "Bernardo",
        "password": os.getenv("PASSWORD_BERNARDO", ""),
        "db":       HUB_DIR / "data" / "bernardo.db",
        "data_dir": HUB_DIR / "data" / "bernardo",
        "color":    "#3b82f6",
        "avatar":   "👨",
    },
    "amanda": {
        "name":     "Amanda",
        "password": os.getenv("PASSWORD_AMANDA", ""),
        "db":       HUB_DIR / "data" / "amanda.db",
        "data_dir": HUB_DIR / "data" / "amanda",
        "color":    "#ec4899",
        "avatar":   "👩",
    },
}

HUB_SECRET  = os.getenv("HUB_SECRET_KEY", "dev-key-insecure")
COOKIE_NAME = "hub_casal_session"
COOKIE_TTL  = 365 * 24 * 3600

_login_attempts: dict = {}


def _make_token(user_id: str) -> str:
    msg = f"hub:{user_id}".encode()
    sig = hmac.new(HUB_SECRET.encode(), msg, hashlib.sha256).hexdigest()
    return f"hub:{user_id}:{sig}"


def _verify_token(token: str) -> str | None:
    """Retorna user_id se válido, None caso contrário."""
    try:
        parts = token.split(":")
        if len(parts) != 3 or parts[0] != "hub":
            return None
        user_id = parts[1]
        if user_id not in USERS:
            return None
        if hmac.compare_digest(token, _make_token(user_id)):
            return user_id
    except Exception:
        pass
    return None


def _get_cookie(headers) -> str:
    raw = headers.get("Cookie", "")
    c = SimpleCookie()
    c.load(raw)
    m = c.get(COOKIE_NAME)
    return m.value if m else ""


def _get_user_from_request(headers) -> str | None:
    return _verify_token(_get_cookie(headers))


def _rate_limited(ip: str) -> bool:
    now = time.time()
    attempts = [t for t in _login_attempts.get(ip, []) if now - t < 60]
    _login_attempts[ip] = attempts
    return len(attempts) >= 5


def _record_attempt(ip: str):
    _login_attempts.setdefault(ip, []).append(time.time())


def _login_html(error: str = "") -> str:
    error_html = f'<p class="error">{error}</p>' if error else ""
    return f"""\
<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Hub Casal 💑</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:#0f172a;color:#e2e8f0;font-family:system-ui,sans-serif;
       display:flex;align-items:center;justify-content:center;min-height:100vh}}
  .card{{background:#1e293b;border:1px solid #334155;border-radius:16px;
         padding:2.5rem 2rem;width:100%;max-width:380px}}
  h1{{font-size:1.5rem;text-align:center;margin-bottom:.3rem}}
  .sub{{text-align:center;color:#94a3b8;font-size:.875rem;margin-bottom:2rem}}
  .user-btns{{display:grid;grid-template-columns:1fr 1fr;gap:.75rem;margin-bottom:1.5rem}}
  .user-btn{{padding:.75rem;border:2px solid #334155;border-radius:12px;background:transparent;
             color:#e2e8f0;font-size:1rem;cursor:pointer;transition:all .2s;text-align:center}}
  .user-btn:hover,.user-btn.active{{border-color:var(--c);background:color-mix(in srgb,var(--c) 15%,transparent)}}
  .user-btn[data-id="bernardo"]{{--c:#3b82f6}}
  .user-btn[data-id="amanda"]{{--c:#ec4899}}
  label{{display:block;font-size:.8rem;color:#94a3b8;margin-bottom:.4rem}}
  input{{width:100%;padding:.75rem 1rem;background:#0f172a;border:1px solid #334155;
         border-radius:10px;color:#e2e8f0;font-size:1rem;margin-bottom:1rem;outline:none}}
  input:focus{{border-color:#3b82f6}}
  button[type=submit]{{width:100%;padding:.75rem;background:#3b82f6;border:none;border-radius:10px;
          color:#fff;font-size:1rem;font-weight:600;cursor:pointer}}
  button[type=submit]:hover{{background:#2563eb}}
  .error{{color:#f87171;text-align:center;margin-top:1rem;font-size:.875rem}}
</style>
</head>
<body>
<div class="card">
  <h1>💑 Hub Casal</h1>
  <p class="sub">Quem é você?</p>
  <div class="user-btns">
    <button class="user-btn active" data-id="bernardo" onclick="selectUser('bernardo')">👨 Bernardo</button>
    <button class="user-btn" data-id="amanda" onclick="selectUser('amanda')">👩 Amanda</button>
  </div>
  <form method="POST" action="/login" id="loginForm">
    <input type="hidden" name="user_id" id="userId" value="bernardo">
    <label for="pwd">Senha</label>
    <input type="password" id="pwd" name="password" placeholder="••••••••" autofocus>
    <button type="submit">Entrar</button>
  </form>
  {error_html}
</div>
<script>
function selectUser(id) {{
  document.getElementById('userId').value = id;
  document.querySelectorAll('.user-btn').forEach(b => b.classList.toggle('active', b.dataset.id === id));
}}
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# DB — por usuário
# ---------------------------------------------------------------------------

def _ensure_data_dirs():
    for u in USERS.values():
        u["data_dir"].mkdir(parents=True, exist_ok=True)
    (HUB_DIR / "data").mkdir(parents=True, exist_ok=True)


def get_conn(user_id: str) -> sqlite3.Connection:
    conn = sqlite3.connect(USERS[user_id]["db"])
    conn.row_factory = sqlite3.Row
    return conn


def init_user_tables(user_id: str):
    conn = get_conn(user_id)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS user_tasks (
            date      TEXT NOT NULL PRIMARY KEY,
            data_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS user_habits (
            data_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS user_habit_completions (
            data_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS user_meals (
            data_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS user_settings (
            data_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS user_mood (
            date      TEXT PRIMARY KEY,
            data_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS user_grocery (
            data_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS user_kanban (
            data_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS health_history (
            date        TEXT PRIMARY KEY,
            steps       INTEGER DEFAULT 0,
            steps_goal  INTEGER DEFAULT 10000,
            sleep_min   INTEGER DEFAULT 0,
            sleep_light INTEGER DEFAULT 0,
            sleep_deep  INTEGER DEFAULT 0,
            sleep_rem   INTEGER DEFAULT 0,
            water_ml    INTEGER DEFAULT 0,
            heart_rate  INTEGER DEFAULT 0,
            synced_at   TEXT
        );
        CREATE TABLE IF NOT EXISTS news_history (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            date        TEXT NOT NULL,
            title       TEXT,
            description TEXT,
            source      TEXT,
            url         TEXT,
            region      TEXT,
            pub_date    TEXT
        );
    """)
    conn.commit()
    conn.close()


def db_get(user_id: str, table: str):
    conn = get_conn(user_id)
    try:
        row = conn.execute(f"SELECT data_json FROM {table} LIMIT 1").fetchone()
        return json.loads(row["data_json"]) if row else None
    except Exception:
        return None
    finally:
        conn.close()


def db_set(user_id: str, table: str, data):
    conn = get_conn(user_id)
    try:
        conn.execute(f"DELETE FROM {table}")
        conn.execute(f"INSERT INTO {table} (data_json) VALUES (?)", (json.dumps(data, ensure_ascii=False),))
        conn.commit()
    finally:
        conn.close()


def db_get_tasks(user_id: str, date: str) -> list:
    conn = get_conn(user_id)
    try:
        row = conn.execute("SELECT data_json FROM user_tasks WHERE date = ?", (date,)).fetchone()
        return json.loads(row["data_json"]) if row else []
    finally:
        conn.close()


def db_set_tasks(user_id: str, date: str, tasks: list):
    conn = get_conn(user_id)
    try:
        conn.execute("INSERT OR REPLACE INTO user_tasks (date, data_json) VALUES (?, ?)",
                     (date, json.dumps(tasks, ensure_ascii=False)))
        conn.commit()
    finally:
        conn.close()


def db_get_all_tasks(user_id: str) -> dict:
    conn = get_conn(user_id)
    try:
        rows = conn.execute("SELECT date, data_json FROM user_tasks").fetchall()
        return {r["date"]: json.loads(r["data_json"]) for r in rows}
    finally:
        conn.close()


def db_get_mood(user_id: str, date: str):
    conn = get_conn(user_id)
    try:
        row = conn.execute("SELECT data_json FROM user_mood WHERE date = ?", (date,)).fetchone()
        return json.loads(row["data_json"]) if row else None
    finally:
        conn.close()


def db_set_mood(user_id: str, date: str, data: dict):
    conn = get_conn(user_id)
    try:
        conn.execute("INSERT OR REPLACE INTO user_mood (date, data_json) VALUES (?, ?)",
                     (date, json.dumps(data, ensure_ascii=False)))
        conn.commit()
    finally:
        conn.close()


def db_get_all_moods(user_id: str) -> dict:
    conn = get_conn(user_id)
    try:
        rows = conn.execute("SELECT date, data_json FROM user_mood").fetchall()
        return {r["date"]: json.loads(r["data_json"]) for r in rows}
    finally:
        conn.close()


def db_get_health_history(user_id: str) -> dict:
    conn = get_conn(user_id)
    try:
        rows = conn.execute("SELECT * FROM health_history ORDER BY date DESC LIMIT 60").fetchall()
        result = {}
        for r in rows:
            d = dict(r)
            result[d["date"]] = {
                "date":          d["date"],
                "steps":         d["steps"],
                "steps_goal":    d["steps_goal"],
                "sleep_minutes": d["sleep_min"],
                "sleep_light":   d["sleep_light"],
                "sleep_deep":    d["sleep_deep"],
                "sleep_rem":     d["sleep_rem"],
                "water_ml":      d["water_ml"],
                "heart_rate":    d["heart_rate"],
                "synced_at":     d["synced_at"],
            }
        return result
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Pontuação da competição
# ---------------------------------------------------------------------------

def _raw_items(user_id: str, date_str: str) -> dict:
    """Retorna contagens brutas de tarefas e hábitos para uso no cálculo normalizado."""
    tasks        = db_get_tasks(user_id, date_str)
    habits       = db_get(user_id, "user_habits") or []
    completions  = db_get(user_id, "user_habit_completions") or {}
    return {
        "total_tasks":  len(tasks),
        "done_tasks":   sum(1 for t in tasks if t.get("done")),
        "total_habits": len(habits),
        "done_habits":  sum(1 for h in habits if f"{h['id']}:{date_str}" in completions),
    }


def _calc_score(user_id: str, date_str: str, max_items: int) -> dict:
    """Calcula pontuação normalizada.

    max_items: total de tarefas+hábitos do usuário com mais itens no dia.
    Garante que 100% de conclusão vale sempre 40 pts independente da quantidade.
    """
    score = 0
    breakdown = {}

    raw = _raw_items(user_id, date_str)
    own_total = raw["total_tasks"] + raw["total_habits"]
    own_done  = raw["done_tasks"]  + raw["done_habits"]

    # Tarefas+hábitos: normalizado pelo maior total entre os dois jogadores.
    # Quem tem menos itens precisa concluir proporcionalmente menos para pontuar igual.
    TASK_HABIT_POOL = 40
    if max_items > 0 and own_total > 0:
        # peso de cada item = POOL × (own_total / max_items) / own_total
        #                   = POOL / max_items
        pts_th = round(own_done * TASK_HABIT_POOL / max_items)
        score += pts_th
        breakdown["tarefas+hábitos"] = pts_th

    # Saúde — passos (1 pt a cada 1000 passos, máx 10)
    conn = get_conn(user_id)
    try:
        row = conn.execute(
            "SELECT steps, water_ml, sleep_min FROM health_history WHERE date = ?",
            (date_str,)
        ).fetchone()
    finally:
        conn.close()

    if row:
        steps_pts = min(10, (row["steps"] or 0) // 1000)
        score += steps_pts
        breakdown["passos"] = steps_pts

        if (row["water_ml"] or 0) >= 2000:
            score += 2
            breakdown["água"] = 2

        if (row["sleep_min"] or 0) >= 420:
            score += 3
            breakdown["sono"] = 3

    # Humor registrado (2 pts)
    if db_get_mood(user_id, date_str):
        score += 2
        breakdown["humor"] = 2

    return {"total": score, "breakdown": breakdown}


def get_competition_data(date_str: str) -> dict:
    # Busca totais de ambos para definir o denominador compartilhado
    raw = {uid: _raw_items(uid, date_str) for uid in USERS}
    max_items = max(
        raw[uid]["total_tasks"] + raw[uid]["total_habits"] for uid in USERS
    )

    scores  = {uid: _calc_score(uid, date_str, max_items) for uid in USERS}
    b_total = scores["bernardo"]["total"]
    a_total = scores["amanda"]["total"]
    leader  = "bernardo" if b_total > a_total else "amanda" if a_total > b_total else "empate"
    return {
        "date":      date_str,
        "scores":    scores,
        "leader":    leader,
        "diff":      abs(b_total - a_total),
        "max_items": max_items,
    }


# ---------------------------------------------------------------------------
# hub_generator runner
# ---------------------------------------------------------------------------

def run_generator(user_id: str):
    try:
        spec = importlib.util.spec_from_file_location("hub_generator", HUB_DIR / "hub_generator.py")
        mod  = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.generate_hub_data(user_id)
        return True, "OK"
    except Exception as e:
        return False, str(e)


def run_all_generators():
    for uid in USERS:
        run_generator(uid)


# ---------------------------------------------------------------------------
# HTTP Handler
# ---------------------------------------------------------------------------

class HubHandler(SimpleHTTPRequestHandler):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(HUB_DIR), **kwargs)

    def _serve_login(self, error: str = ""):
        page = _login_html(error)
        payload = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(payload))
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass

    def _handle_login_post(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8", errors="replace")
        params = parse_qs(body)
        user_id  = params.get("user_id",  [""])[0].lower()
        password = params.get("password", [""])[0]
        ip = self.client_address[0]

        if _rate_limited(ip):
            self._serve_login("Muitas tentativas. Aguarde 1 minuto.")
            return

        if user_id not in USERS:
            self._serve_login("Usuário inválido.")
            return

        expected_pwd = USERS[user_id]["password"]
        if expected_pwd and password != expected_pwd:
            _record_attempt(ip)
            self._serve_login("Senha incorreta.")
            return

        token = _make_token(user_id)
        self.send_response(302)
        self.send_header("Location", "/")
        self.send_header(
            "Set-Cookie",
            f"{COOKIE_NAME}={token}; Path=/; Max-Age={COOKIE_TTL}; HttpOnly; SameSite=Strict",
        )
        self.end_headers()

    def _require_auth(self) -> str | None:
        """Retorna user_id se autenticado, redireciona para /login caso contrário."""
        uid = _get_user_from_request(self.headers)
        if uid:
            return uid
        self.send_response(302)
        self.send_header("Location", "/login")
        self.end_headers()
        return None

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        qs   = parse_qs(urlparse(self.path).query)

        if path == "/login":
            self._serve_login()
            return

        uid = self._require_auth()
        if not uid:
            return

        # Qual perfil visualizar (pode ver o do parceiro também)
        view_user = qs.get("u", [uid])[0]
        if view_user not in USERS:
            view_user = uid

        if path == "/refresh":
            def _bg():
                run_generator(uid)
                run_generator("amanda" if uid == "bernardo" else "bernardo")
            threading.Thread(target=_bg, daemon=True).start()
            self._json({"ok": True, "msg": "Atualizando dados..."})
            return

        if path == "/":
            self.send_response(302)
            self.send_header("Location", "/hub.html")
            self.end_headers()
            return

        if path == "/api/load":
            self._json({
                "viewer":             uid,
                "tasks":              db_get_all_tasks(view_user),
                "habits":             db_get(view_user, "user_habits") or [],
                "habit_completions":  db_get(view_user, "user_habit_completions") or {},
                "meals":              db_get(view_user, "user_meals") or [],
                "settings":           db_get(view_user, "user_settings") or {},
                "moods":              db_get_all_moods(view_user),
                "grocery":            db_get(view_user, "user_grocery") or [],
                "kanban":             db_get(view_user, "user_kanban") or {},
                "jobs":               db_get(view_user, "user_jobs") or [],
                "profile":            view_user,
                "profile_name":       USERS[view_user]["name"],
                "profile_color":      USERS[view_user]["color"],
                "profile_avatar":     USERS[view_user]["avatar"],
            })
            return

        if path == "/api/hub-data":
            from datetime import date as _d
            today_str = str(_d.today())
            b_data = self._read_user_data("bernardo")
            a_data = self._read_user_data("amanda")
            competition = get_competition_data(today_str)
            self._json({
                "bernardo":   b_data,
                "amanda":     a_data,
                "competition": competition,
                "viewer":     uid,
            })
            return

        if path == "/api/competition":
            from datetime import date as _d
            date_str = qs.get("date", [str(_d.today())])[0]
            self._json(get_competition_data(date_str))
            return

        if path == "/api/calendario":
            try:
                from hub_generator import fetch_calendar_events
                events = fetch_calendar_events(uid)
                self._json({"ok": True, "events": events})
            except Exception as e:
                self._json({"ok": False, "events": [], "error": str(e)})
            return

        if path == "/api/calendar/events":
            # Agrega eventos dos dois perfis (o viewer vê tudo)
            try:
                from hub_generator import fetch_calendar_events
                all_events = []
                for user_id in USERS:
                    try:
                        evs = fetch_calendar_events(user_id)
                        for ev in evs:
                            ev["user"] = user_id
                        all_events.extend(evs)
                    except Exception:
                        pass
                all_events.sort(key=lambda e: e.get("start", ""))
                self._json({"ok": True, "events": all_events})
            except Exception as e:
                self._json({"ok": False, "events": [], "error": str(e)})
            return

        if path == "/hub.html":
            self._serve_hub(uid)
            return

        super().do_GET()

    def _read_user_data(self, user_id: str) -> dict:
        data_dir = USERS[user_id]["data_dir"]
        def read_json(name, default):
            f = data_dir / name
            try:
                return json.loads(f.read_text(encoding="utf-8")) if f.exists() else default
            except Exception:
                return default
        return {
            "health":   read_json("health_today.json", {}),
            "news":     read_json("news_today.json", []),
            "briefing": read_json("daily_briefing.json", {}),
            "name":     USERS[user_id]["name"],
            "color":    USERS[user_id]["color"],
            "avatar":   USERS[user_id]["avatar"],
        }

    def _serve_hub(self, uid: str):
        import re
        from datetime import date as _d
        html_file = HUB_DIR / "hub.html"
        if not html_file.exists():
            self.send_error(404)
            return

        try:
            html = html_file.read_bytes().decode("utf-8", errors="replace")
        except Exception:
            html = html_file.read_text(encoding="latin-1", errors="replace")

        today_str = str(_d.today())
        b_data    = self._read_user_data("bernardo")
        a_data    = self._read_user_data("amanda")
        competition = get_competition_data(today_str)

        b_health_hist = db_get_health_history("bernardo")
        a_health_hist = db_get_health_history("amanda")

        from datetime import datetime
        ts = datetime.now().astimezone().strftime("%d/%m/%Y %H:%M")

        def safe_dumps(obj):
            try:
                return json.dumps(obj, ensure_ascii=True, default=str)
            except Exception:
                def fix(o):
                    if isinstance(o, str):
                        return o.encode("utf-8", "replace").decode("utf-8", "replace")
                    if isinstance(o, dict):
                        return {k: fix(v) for k, v in o.items()}
                    if isinstance(o, list):
                        return [fix(i) for i in o]
                    return o
                return json.dumps(fix(obj), ensure_ascii=True, default=str)

        data_block = (
            f'<script id="hub-data">\n'
            f'// Servido em {ts}\n'
            f'window.HUB_DATA = {{\n'
            f'  viewer:      {json.dumps(uid)},\n'
            f'  bernardo:    {safe_dumps(b_data)},\n'
            f'  amanda:      {safe_dumps(a_data)},\n'
            f'  competition: {safe_dumps(competition)},\n'
            f'  b_history:   {safe_dumps(b_health_hist)},\n'
            f'  a_history:   {safe_dumps(a_health_hist)},\n'
            f'}};\n'
            f'</script>'
        )

        html = re.sub(r'<script id="hub-data">.*?</script>', '', html, flags=re.DOTALL)
        html = html.replace("</head>", data_block + "\n</head>")
        # Injeta tema do viewer como classe no <html>
        theme_class = f"theme-{uid}"
        html = re.sub(r'<html([^>]*)>', lambda m: f'<html{m.group(1)} class="{theme_class}">', html, count=1)

        payload = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(payload))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass

    def do_POST(self):
        path = urlparse(self.path).path

        if path == "/login":
            self._handle_login_post()
            return

        if path == "/logout":
            self.send_response(302)
            self.send_header("Location", "/login")
            self.send_header(
                "Set-Cookie",
                f"{COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict",
            )
            self.end_headers()
            return

        uid = self._require_auth()
        if not uid:
            return

        length = int(self.headers.get("Content-Length", 0))
        body   = json.loads(self.rfile.read(length)) if length else {}

        # Todas as rotas de save escrevem no perfil do usuário logado
        if path == "/api/save/tasks":
            date  = body.get("date")
            tasks = body.get("tasks", [])
            if date:
                db_set_tasks(uid, date, tasks)
            self._json({"ok": True})

        elif path == "/api/save/habits":
            db_set(uid, "user_habits", body.get("habits", []))
            self._json({"ok": True})

        elif path == "/api/save/habit_completions":
            db_set(uid, "user_habit_completions", body.get("completions", {}))
            self._json({"ok": True})

        elif path == "/api/save/meals":
            db_set(uid, "user_meals", body.get("meals", []))
            self._json({"ok": True})

        elif path == "/api/save/settings":
            db_set(uid, "user_settings", body.get("settings", {}))
            self._json({"ok": True})

        elif path == "/api/save/mood":
            date = body.get("date")
            mood = body.get("mood")
            if date and mood:
                db_set_mood(uid, date, mood)
            self._json({"ok": True})

        elif path == "/api/save/grocery":
            db_set(uid, "user_grocery", body.get("grocery", []))
            self._json({"ok": True})

        elif path == "/api/save/kanban":
            db_set(uid, "user_kanban", body.get("kanban", {}))
            self._json({"ok": True})

        elif path == "/api/save/jobs":
            db_set(uid, "user_jobs", body.get("jobs", []))
            self._json({"ok": True})

        elif path == "/api/ai":
            self._handle_ai(uid, body)

        elif path == "/api/calendar/create-event":
            try:
                from hub_generator import create_calendar_event
                result = create_calendar_event(
                    user_id      = uid,
                    title        = body.get("title", "Evento"),
                    date_str     = body.get("date", ""),
                    time_str     = body.get("time"),
                    duration_min = int(body.get("duration", 60))
                )
                self._json(result)
            except Exception as e:
                self._json({"error": str(e)})

        elif path == "/api/regenerate-briefing":
            try:
                if "hub_generator" in sys.modules:
                    del sys.modules["hub_generator"]
                from hub_generator import generate_daily_briefing
                generate_daily_briefing(uid, force=True)
                bf_file = USERS[uid]["data_dir"] / "daily_briefing.json"
                if not bf_file.exists():
                    self._json({"ok": False, "error": "Briefing não gerado — verifique GEMINI_API_KEY no .env"})
                    return
                briefing = json.loads(bf_file.read_text(encoding="utf-8"))
                from datetime import date as _d
                if briefing.get("date") == str(_d.today()):
                    self._json({"ok": True, "briefing": briefing})
                else:
                    self._json({"ok": False, "error": "Geração falhou"})
            except Exception as e:
                self._json({"ok": False, "error": str(e)})

        else:
            self.send_response(404)
            self.end_headers()

    def _handle_ai(self, uid: str, body: dict):
        import requests as _req
        from datetime import date as _date, timedelta as _td

        api_key = os.getenv("GEMINI_API_KEY", "")
        if not api_key:
            self._json({"error": "GEMINI_API_KEY não configurada", "actions": [{"type": "reply", "message": "GEMINI_API_KEY não configurada."}], "message": "Erro"})
            return

        user_text = body.get("text", "").strip()
        if not user_text:
            self._json({"actions": [], "message": ""})
            return

        context   = body.get("context", {})
        today     = context.get("today", str(_date.today()))
        today_dt  = _date.fromisoformat(today)
        tomorrow  = str(today_dt + _td(days=1))
        habits    = context.get("habits", [])
        grocery   = context.get("grocery", [])
        name      = USERS[uid]["name"]

        _days_pt  = ["Segunda-feira","Terça-feira","Quarta-feira","Quinta-feira","Sexta-feira","Sábado","Domingo"]
        today_dow = _days_pt[today_dt.weekday()]

        _dow_names = ["segunda","terça","quarta","quinta","sexta","sábado","domingo"]
        next_days_lines = []
        for i, n in enumerate(_dow_names):
            delta = (i - today_dt.weekday()) % 7
            if delta == 0:
                delta = 7
            next_days_lines.append(f"- próxima {n}-feira = {today_dt + _td(days=delta)}")
        next_days_str = "\n".join(next_days_lines)

        habits_str  = "\n".join(f'- id="{h["id"]}" nome="{h["name"]}"' for h in habits) if habits else "(nenhum)"
        grocery_str = "\n".join(f'- "{item["text"]}"' for item in grocery if not item.get("checked")) if grocery else "(lista vazia)"

        system_prompt = f"""Você é o assistente do Hub Casal pessoal de {name}. Responda SEMPRE em português do Brasil.
Hoje é {today} ({today_dow}, amanhã é {tomorrow}).

Hábitos cadastrados:
{habits_str}

Lista de mercado atual (itens pendentes):
{grocery_str}

Sua tarefa: interpretar a mensagem do usuário e retornar um JSON com as ações a executar no hub.

Ações disponíveis:
- addTask: adicionar tarefa. Campos: text (string), date ("YYYY-MM-DD", padrão=hoje), priority ("normal"|"high"|"low")
- toggleHabitDone: marcar hábito como feito. Campos: habitId (string), date ("YYYY-MM-DD", padrão=hoje)
- addGroceryItem: adicionar item à lista de mercado. Campos: text (string)
- removeGroceryItem: marcar item como comprado. Campos: text (string)
- addHabit: criar novo hábito. Campos: name (string), recurrenceType ("daily"|"weekdays"|"monthly"), days (array [0-6] apenas para "weekdays"), monthDay (1-31 apenas para "monthly")
- createCalendarEvent: criar evento no Google Agenda. Campos: title (string), date ("YYYY-MM-DD"), time ("HH:MM", opcional), duration (minutos, padrão 60)
- reply: quando não há ação clara. Campos: message (string em português)

Quando o usuário pedir para marcar/agendar um compromisso COM data e/ou hora, dispare createCalendarEvent + addTask juntos.

Regras de data:
- "hoje" = {today}
- "amanhã" = {tomorrow}
- próximos dias da semana:
{next_days_str}

Retorne APENAS JSON válido, no formato:
{{"actions": [...], "message": "resumo em português, máximo 1 linha"}}"""

        try:
            resp = _req.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}",
                headers={"Content-Type": "application/json"},
                json={
                    "system_instruction": {"parts": [{"text": system_prompt}]},
                    "contents": [{"parts": [{"text": user_text}]}],
                    "generationConfig": {"maxOutputTokens": 512, "temperature": 0.1},
                },
                timeout=15
            )
            resp.raise_for_status()
            parts   = resp.json()["candidates"][0]["content"]["parts"]
            content = "\n".join(p["text"] for p in parts if not p.get("thought")).strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1] if "\n" in content else content
                content = content.rsplit("```", 1)[0].strip()
            result = json.loads(content)
            self._json(result)
        except json.JSONDecodeError:
            self._json({"actions": [{"type": "reply", "message": "Não consegui interpretar a resposta da IA."}], "message": "Erro"})
        except Exception as e:
            self._json({"error": str(e), "actions": [{"type": "reply", "message": f"Erro ao chamar a IA: {e}"}], "message": "Erro"})

    def _json(self, data):
        payload = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", len(payload))
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass

    def log_message(self, format, *args):
        msg = str(args[0]) if args else ""
        if any(x in msg for x in ["favicon", "hub.html", "/api/load"]):
            return
        try:
            print(f"  [{self.address_string()}] {format % args}")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def open_browser():
    time.sleep(1.2)
    webbrowser.open(f"http://localhost:{PORT}")


def main():
    _ensure_data_dirs()
    for uid in USERS:
        init_user_tables(uid)

    import socket
    try:
        local_ip = socket.gethostbyname(socket.gethostname())
    except Exception:
        local_ip = "?.?.?.?"

    print(f"\n💑 Hub Casal")
    print(f"   PC:      http://localhost:{PORT}")
    print(f"   Celular: http://{local_ip}:{PORT}  (mesma rede Wi-Fi)")
    print(f"   Pasta:   {HUB_DIR}")
    print(f"   Pressione Ctrl+C para parar\n")

    def _bg_init():
        print("  Atualizando dados...")
        for uid in USERS:
            run_generator(uid)
        print("  Dados atualizados!\n")

    threading.Thread(target=_bg_init, daemon=True).start()
    threading.Thread(target=open_browser, daemon=True).start()

    server = HTTPServer(("0.0.0.0", PORT), HubHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Hub encerrado.")
        server.shutdown()


if __name__ == "__main__":
    main()
