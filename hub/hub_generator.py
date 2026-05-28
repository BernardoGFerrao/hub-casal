"""
hub_generator.py — Gera os JSONs de dados para cada perfil do Hub Casal.

Estrutura esperada:
  hub/
  ├── hub.html
  ├── hub_generator.py
  └── data/
      ├── bernardo/
      │   ├── health_today.json
      │   ├── news_today.json
      │   ├── daily_briefing.json
      │   └── calendar_today.json
      ├── amanda/
      │   └── ... (mesma estrutura)
      ├── bernardo.db   ← tarefas, hábitos, etc. do Bernardo
      └── amanda.db     ← tarefas, hábitos, etc. da Amanda

Setup inicial (uma vez por usuário):
    python hub/hub_generator.py --auth-health --user bernardo
    python hub/hub_generator.py --auth-calendar --user bernardo
"""

import os
import json
import sqlite3
import logging
import webbrowser
from datetime import datetime, date, timezone, timedelta
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

ROOT    = Path(__file__).parent.parent
HUB_DIR = Path(__file__).parent

load_dotenv(ROOT / ".env")
logger = logging.getLogger("hub_generator")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

USERS = {
    "bernardo": {"name": "Bernardo", "color": "#3b82f6", "avatar": "👨"},
    "amanda":   {"name": "Amanda",   "color": "#ec4899", "avatar": "👩"},
}


def _data_dir(user_id: str) -> Path:
    d = HUB_DIR / "data" / user_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _db_path(user_id: str) -> Path:
    return HUB_DIR / "data" / f"{user_id}.db"


def _token_file(user_id: str, kind: str) -> Path:
    """kind = 'health' | 'calendar'"""
    return HUB_DIR / "data" / user_id / f"token_{kind}.json"


def _credentials_file(user_id: str) -> Path:
    return HUB_DIR / "data" / user_id / "credentials.json"


# ---------------------------------------------------------------------------
# Helpers de data local
# ---------------------------------------------------------------------------

def local_today() -> date:
    return datetime.now().astimezone().date()


def local_now() -> datetime:
    return datetime.now().astimezone()


# ---------------------------------------------------------------------------
# Google OAuth — separado por usuário e por escopo
# ---------------------------------------------------------------------------

HEALTH_SCOPES = [
    "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly",
    "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly",
    "https://www.googleapis.com/auth/googlehealth.nutrition.readonly",
    "https://www.googleapis.com/auth/googlehealth.sleep.readonly",
]
CALENDAR_SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
]

BASE_HEALTH_URL = "https://health.googleapis.com/v4/users/me"
CALENDAR_API    = "https://www.googleapis.com/calendar/v3"


def _load_credentials(user_id: str) -> dict:
    f = _credentials_file(user_id)
    if not f.exists():
        raise FileNotFoundError(
            f"credentials.json não encontrado em {f}\n"
            f"Baixe do Google Cloud Console e salve como: {f}"
        )
    raw  = json.loads(f.read_text())
    info = raw.get("web") or raw.get("installed") or {}
    return {"client_id": info["client_id"], "client_secret": info["client_secret"]}


def _load_token(path: Path) -> Optional[dict]:
    return json.loads(path.read_text()) if path.exists() else None


def _save_token(token: dict, path: Path):
    path.write_text(json.dumps(token, indent=2))


def _refresh_token(token: dict, creds: dict, path: Path) -> Optional[dict]:
    r = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id":     creds["client_id"],
            "client_secret": creds["client_secret"],
            "refresh_token": token["refresh_token"],
            "grant_type":    "refresh_token",
        },
        timeout=15,
    )
    if r.ok:
        new_token = {**token, **r.json(), "obtained_at": local_now().isoformat()}
        _save_token(new_token, path)
        return new_token
    logger.error("Falha ao renovar token: %s", r.text)
    return None


def _get_access_token(user_id: str, kind: str) -> Optional[str]:
    path  = _token_file(user_id, kind)
    token = _load_token(path)
    if not token:
        logger.warning("Token %s/%s não encontrado. Execute --auth-%s --user %s", user_id, kind, kind, user_id)
        return None
    creds = _load_credentials(user_id)
    obtained_at = token.get("obtained_at")
    expires_in  = int(token.get("expires_in", 3600))
    if obtained_at:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(obtained_at)).total_seconds()
        if age >= expires_in - 300:
            token = _refresh_token(token, creds, path)
            if not token:
                return None
    return token.get("access_token")


def run_auth(user_id: str, scopes: list, kind: str):
    from http.server import HTTPServer, BaseHTTPRequestHandler
    from urllib.parse import urlencode
    import secrets

    creds_file = _credentials_file(user_id)
    if not creds_file.exists():
        print(f"\n❌ {creds_file} não encontrado.")
        print("   1. Google Cloud Console → APIs e Serviços → Credenciais")
        print("   2. Baixe o JSON do seu OAuth client")
        print(f"   3. Salve como: {creds_file}")
        return

    creds        = _load_credentials(user_id)
    redirect_uri = "http://localhost:8765/callback"
    state        = secrets.token_urlsafe(16)

    auth_url = (
        "https://accounts.google.com/o/oauth2/v2/auth?"
        + urlencode({
            "client_id":     creds["client_id"],
            "redirect_uri":  redirect_uri,
            "response_type": "code",
            "scope":         " ".join(scopes),
            "access_type":   "offline",
            "prompt":        "consent",
            "state":         state,
        })
    )

    print(f"\n🔑 Autorizando {USERS[user_id]['name']} — {kind}...")
    webbrowser.open(auth_url)

    auth_code = [None]

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(self.path).query)
            auth_code[0] = qs.get("code", [None])[0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"<h2>Autorizado! Pode fechar esta aba.</h2>")
        def log_message(self, *args):
            pass

    server = HTTPServer(("localhost", 8765), Handler)
    server.handle_request()

    if not auth_code[0]:
        print("❌ Nenhum código recebido.")
        return

    r = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code":          auth_code[0],
            "client_id":     creds["client_id"],
            "client_secret": creds["client_secret"],
            "redirect_uri":  redirect_uri,
            "grant_type":    "authorization_code",
        },
        timeout=15,
    )

    if not r.ok:
        print(f"❌ Erro ao obter token: {r.text}")
        return

    token = {**r.json(), "obtained_at": local_now().isoformat()}
    token_path = _token_file(user_id, kind)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    _save_token(token, token_path)
    print(f"\n✅ Token salvo em: {token_path}")


# ---------------------------------------------------------------------------
# Google Health API
# ---------------------------------------------------------------------------

def _health_post(path: str, token: str, body: dict) -> Optional[dict]:
    r = requests.post(
        f"{BASE_HEALTH_URL}{path}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=body, timeout=20,
    )
    return r.json() if r.ok else None


def _health_get(path: str, token: str, params: dict = None) -> Optional[dict]:
    r = requests.get(
        f"{BASE_HEALTH_URL}{path}",
        headers={"Authorization": f"Bearer {token}"},
        params=params, timeout=20,
    )
    return r.json() if r.ok else None


def _build_daily_range(d: date) -> dict:
    return {
        "range": {
            "start": {"date": {"year": d.year, "month": d.month, "day": d.day},
                      "time": {"hours": 0, "minutes": 0, "seconds": 0}},
            "end":   {"date": {"year": d.year, "month": d.month, "day": d.day},
                      "time": {"hours": 23, "minutes": 59, "seconds": 59}},
        },
        "windowSizeDays": 1,
    }


def _fetch_steps(token: str, today: date) -> int:
    data = _health_post("/dataTypes/steps/dataPoints:dailyRollUp", token, _build_daily_range(today))
    if data:
        pts = data.get("rollupDataPoints", [])
        if pts:
            return int(pts[0].get("steps", {}).get("countSum", 0))
    return 0


def _fetch_sleep(token: str, today: date) -> dict:
    result = {"sleep_minutes": 0, "sleep_light": 0, "sleep_deep": 0, "sleep_rem": 0}
    data = _health_get("/dataTypes/sleep/dataPoints", token)
    if not data:
        return result

    tz_local    = timedelta(hours=-3)
    today_start = datetime(today.year, today.month, today.day, tzinfo=timezone(tz_local))
    today_end   = today_start + timedelta(days=1)

    def parse_dt(s):
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None

    relevant = [
        pt for pt in data.get("dataPoints", [])
        if (end := parse_dt(pt.get("sleep", {}).get("interval", {}).get("endTime", "")))
        and today_start <= end < today_end
    ]

    stage_map = {"LIGHT": "sleep_light", "DEEP": "sleep_deep", "REM": "sleep_rem"}
    total_min = 0
    for pt in relevant:
        sl = pt.get("sleep", {})
        iv = sl.get("interval", {})
        s  = parse_dt(iv.get("startTime", ""))
        e  = parse_dt(iv.get("endTime", ""))
        if s and e:
            total_min += int((e - s).total_seconds() / 60)
        for stage in sl.get("stages", []):
            stype = stage.get("type", "").upper()
            if stype in stage_map:
                ss = parse_dt(stage.get("startTime", ""))
                se = parse_dt(stage.get("endTime", ""))
                if ss and se:
                    result[stage_map[stype]] += int((se - ss).total_seconds())
        for stage in sl.get("summary", {}).get("stagesSummary", []):
            t = stage.get("type", "").upper()
            m = int(stage.get("minutes", 0))
            if t == "LIGHT":  result["sleep_light"] = m * 60
            elif t == "DEEP": result["sleep_deep"]  = m * 60
            elif t == "REM":  result["sleep_rem"]   = m * 60

    result["sleep_minutes"] = total_min
    return result


def _fetch_water(token: str, today: date) -> int:
    data = _health_get(
        "/dataTypes/hydration-log/dataPoints", token,
        params={"filter": f'hydration_log.interval.civil_start_time >= "{today.isoformat()}"'},
    )
    if data:
        total = sum(int(pt.get("hydrationLog", {}).get("amountConsumed", {}).get("milliliters", 0))
                    for pt in data.get("dataPoints", []))
        if total > 0:
            return total
    data2 = _health_post("/dataTypes/hydration-log/dataPoints:dailyRollUp", token, _build_daily_range(today))
    if data2:
        pts = data2.get("rollupDataPoints", [])
        if pts:
            return int(pts[0].get("hydrationLog", {}).get("amountConsumed", {}).get("millilitersSum", 0))
    return 0


def _fetch_heart_rate(token: str) -> int:
    data = _health_get("/dataTypes/daily-resting-heart-rate/dataPoints", token)
    if data:
        for pt in data.get("dataPoints", []):
            bpm = pt.get("dailyRestingHeartRate", {}).get("beatsPerMinute", 0)
            if bpm:
                return int(bpm)
    return 0


def fetch_health_today(user_id: str) -> dict:
    token = _get_access_token(user_id, "health")
    if not token:
        return {}
    today    = local_today()
    steps    = _fetch_steps(token, today)
    sleep    = _fetch_sleep(token, today)
    water_ml = _fetch_water(token, today)
    hr       = _fetch_heart_rate(token)
    return {
        "date":       today.isoformat(),
        "synced_at":  local_now().strftime("%H:%M"),
        "steps":      steps,
        "steps_goal": 10000,
        "water_ml":   water_ml,
        "heart_rate": hr,
        **sleep,
    }


def generate_health_json(user_id: str):
    try:
        data = fetch_health_today(user_id)
        if data:
            (_data_dir(user_id) / "health_today.json").write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            _save_health_to_db(user_id, data)
            logger.info("[%s] Saúde: %d passos | %dmin sono | %dml água",
                        user_id, data["steps"], data.get("sleep_minutes", 0), data["water_ml"])
    except Exception as e:
        logger.error("[%s] Erro health: %s", user_id, e)


def _save_health_to_db(user_id: str, health: dict):
    if not health:
        return
    conn = sqlite3.connect(_db_path(user_id))
    conn.execute("""
        INSERT OR REPLACE INTO health_history
        (date, steps, steps_goal, sleep_min, sleep_light, sleep_deep, sleep_rem, water_ml, heart_rate, synced_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        health.get("date", local_today().isoformat()),
        health.get("steps", 0),
        health.get("steps_goal", 10000),
        health.get("sleep_minutes", 0),
        health.get("sleep_light", 0),
        health.get("sleep_deep", 0),
        health.get("sleep_rem", 0),
        health.get("water_ml", 0),
        health.get("heart_rate", 0),
        health.get("synced_at", ""),
    ))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Google Calendar
# ---------------------------------------------------------------------------

def fetch_calendar_events(user_id: str) -> list:
    token = _get_access_token(user_id, "calendar")
    if not token:
        return []

    today    = local_today()
    time_min = datetime(today.year, today.month, today.day, tzinfo=timezone.utc).isoformat()
    time_max = (datetime(today.year, today.month, today.day, tzinfo=timezone.utc) + timedelta(days=30)).isoformat()
    headers  = {"Authorization": f"Bearer {token}"}
    params   = {"timeMin": time_min, "timeMax": time_max, "singleEvents": "true", "orderBy": "startTime", "maxResults": 50}

    r = requests.get(f"{CALENDAR_API}/calendars/primary/events", headers=headers, params=params, timeout=15)
    if not r.ok:
        return []

    def parse_items(items, kind):
        result = []
        for item in items:
            start   = item.get("start", {})
            end     = item.get("end", {})
            all_day = "date" in start and "dateTime" not in start
            result.append({
                "id":      item.get("id", ""),
                "title":   item.get("summary", "(sem título)"),
                "start":   start.get("dateTime") or start.get("date", ""),
                "end":     end.get("dateTime")   or end.get("date", ""),
                "all_day": all_day,
                "kind":    kind,
            })
        return result

    events = parse_items(r.json().get("items", []), "personal")

    try:
        holiday_cal = "pt.brazilian%23holiday%40group.v.calendar.google.com"
        rh = requests.get(f"{CALENDAR_API}/calendars/{holiday_cal}/events", headers=headers, params=params, timeout=10)
        if rh.ok:
            events.extend(parse_items(rh.json().get("items", []), "holiday"))
    except Exception:
        pass

    events.sort(key=lambda e: e["start"])
    return events


def create_calendar_event(user_id: str, title: str, date_str: str, time_str: str = None, duration_min: int = 60) -> dict:
    token = _get_access_token(user_id, "calendar")
    if not token:
        return {"error": "Token de calendário não encontrado. Execute --auth-calendar."}
    try:
        tz_offset = "-03:00"
        if time_str:
            start_dt = datetime.fromisoformat(f"{date_str}T{time_str}:00")
            end_dt   = start_dt + timedelta(minutes=duration_min)
            body = {
                "summary": title,
                "start": {"dateTime": f"{start_dt.isoformat()}{tz_offset}"},
                "end":   {"dateTime": f"{end_dt.isoformat()}{tz_offset}"},
            }
        else:
            next_day = (datetime.fromisoformat(date_str) + timedelta(days=1)).date().isoformat()
            body = {
                "summary": title,
                "start": {"date": date_str},
                "end":   {"date": next_day},
            }
        r = requests.post(
            f"{CALENDAR_API}/calendars/primary/events",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=body, timeout=15
        )
        if r.ok:
            ev = r.json()
            return {"ok": True, "id": ev.get("id"), "title": ev.get("summary")}
        return {"error": f"Calendar API {r.status_code}: {r.text[:200]}"}
    except Exception as e:
        return {"error": str(e)}


def generate_calendar_json(user_id: str):
    try:
        events = fetch_calendar_events(user_id)
        (_data_dir(user_id) / "calendar_today.json").write_text(
            json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as e:
        logger.error("[%s] Erro calendário: %s", user_id, e)


# ---------------------------------------------------------------------------
# Notícias
# ---------------------------------------------------------------------------

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GNEWS_API_KEY  = os.getenv("GNEWS_API_KEY", "")
HUB_CITY       = os.getenv("HUB_CITY", "")


def _fetch_news_rss(region: str, query: str = None) -> list:
    import re, xml.etree.ElementTree as ET, html as html_module
    from urllib.parse import quote_plus
    if query:
        q    = quote_plus(query)
        lang = "pt-BR" if region == "br" else "en"
        gl   = "BR"    if region == "br" else "US"
        url  = f"https://news.google.com/rss/search?q={q}&hl={lang}&gl={gl}&ceid={gl}:{lang.split('-')[0]}"
    else:
        urls = {
            "br":    "https://news.google.com/rss/search?q=Brasil+noticias&hl=pt-BR&gl=BR&ceid=BR:pt-419",
            "world": "https://news.google.com/rss/headlines/section/topic/WORLD?hl=pt-BR&gl=BR&ceid=BR:pt-419",
        }
        url = urls.get(region, urls["br"])

    headers = {"User-Agent": "Mozilla/5.0", "Accept-Language": "pt-BR,pt;q=0.9"}
    try:
        r    = requests.get(url, headers=headers, timeout=15)
        root = ET.fromstring(r.content)
        result = []
        for item in root.findall(".//item")[:5]:
            title    = html_module.unescape(item.findtext("title", "") or "")
            link     = item.findtext("link", "") or ""
            desc_raw = item.findtext("description", "") or ""
            desc     = html_module.unescape(re.sub(r"<[^>]+>", "", desc_raw))[:180]
            source   = "Brasil" if region == "br" else "Mundo"
            if " - " in title:
                parts = title.rsplit(" - ", 1)
                title, source = parts[0].strip(), parts[1].strip()
            result.append({"title": title, "description": desc.strip(), "source": source,
                           "url": link, "region": region, "pubDate": item.findtext("pubDate", "") or ""})
        return result
    except Exception as e:
        logger.warning("RSS %s falhou: %s", region, e)
        return []


def generate_news_json(user_id: str):
    logger.info("[%s] Buscando notícias...", user_id)
    try:
        hs           = _get_hub_settings(user_id)
        topics_br    = hs.get("news_topics_br")    or ["Brasil", "tecnologia", "economia"]
        topics_world = hs.get("news_topics_world") or ["world news", "technology"]
        news = []
        for topic in topics_br:
            for item in _fetch_news_rss("br", query=topic):
                item["topic"] = topic
                news.append(item)
        for topic in topics_world:
            for item in _fetch_news_rss("world", query=topic):
                item["topic"] = topic
                news.append(item)

        seen, unique = set(), []
        for n in news:
            if n["url"] not in seen:
                seen.add(n["url"])
                unique.append(n)

        (_data_dir(user_id) / "news_today.json").write_text(
            json.dumps(unique, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as e:
        logger.error("[%s] Erro notícias: %s", user_id, e)


# ---------------------------------------------------------------------------
# Briefing diário com IA
# ---------------------------------------------------------------------------

_WEATHER_PT = {
    "Sunny": "Ensolarado", "Clear": "Céu limpo", "Partly cloudy": "Parcialmente nublado",
    "Cloudy": "Nublado", "Overcast": "Encoberto", "Mist": "Névoa", "Fog": "Névoa",
    "Light rain": "Chuva leve", "Moderate rain": "Chuva moderada", "Heavy rain": "Chuva forte",
    "Rain": "Chuva", "Thunderstorm": "Tempestade", "Drizzle": "Garoa",
}


def _fetch_weather() -> str:
    if not HUB_CITY:
        return "cidade não configurada (defina HUB_CITY no .env)"
    try:
        r = requests.get(f"https://wttr.in/{HUB_CITY}?format=j1", timeout=10)
        if r.ok:
            c    = r.json()["current_condition"][0]
            desc = _WEATHER_PT.get(c["weatherDesc"][0]["value"], c["weatherDesc"][0]["value"])
            return f"{c['temp_C']}°C (sensação {c['FeelsLikeC']}°C), {desc}, umidade {c['humidity']}%"
    except Exception:
        pass
    return "indisponível"


def _get_hub_settings(user_id: str) -> dict:
    try:
        conn = sqlite3.connect(_db_path(user_id))
        row  = conn.execute("SELECT value FROM kv_store WHERE key = 'user_hub_settings'").fetchone()
        conn.close()
        return json.loads(row[0]) if row else {}
    except Exception:
        return {}


def _get_today_tasks(user_id: str, date_str: str) -> list:
    try:
        conn = sqlite3.connect(_db_path(user_id))
        row  = conn.execute("SELECT data_json FROM user_tasks WHERE date = ?", (date_str,)).fetchone()
        conn.close()
        return json.loads(row[0]) if row else []
    except Exception:
        return []


def _get_habits_summary(user_id: str, date_str: str) -> dict:
    try:
        conn = sqlite3.connect(_db_path(user_id))
        h_row = conn.execute("SELECT data_json FROM user_habits LIMIT 1").fetchone()
        c_row = conn.execute("SELECT data_json FROM user_habit_completions LIMIT 1").fetchone()
        conn.close()
        habits      = json.loads(h_row[0]) if h_row else []
        completions = json.loads(c_row[0]) if c_row else {}
        done = sum(1 for h in habits if f"{h['id']}:{date_str}" in completions)
        return {"total": len(habits), "done": done}
    except Exception:
        return {"total": 0, "done": 0}


def generate_daily_briefing(user_id: str, force: bool = False):
    api_key = os.getenv("GEMINI_API_KEY", "") or GEMINI_API_KEY
    if not api_key:
        return

    today      = local_today()
    today_str  = today.isoformat()
    yesterday  = (today - timedelta(days=1)).isoformat()
    out_file   = _data_dir(user_id) / "daily_briefing.json"
    name       = USERS[user_id]["name"]

    if not force and out_file.exists():
        try:
            existing = json.loads(out_file.read_text(encoding="utf-8"))
            if existing.get("date") == today_str:
                return
        except Exception:
            pass

    tasks   = _get_today_tasks(user_id, today_str)
    habits  = _get_habits_summary(user_id, yesterday)
    weather = _fetch_weather()

    cal_file  = _data_dir(user_id) / "calendar_today.json"
    cal_today = []
    if cal_file.exists():
        try:
            all_ev = json.loads(cal_file.read_text(encoding="utf-8"))
            cal_today = [e for e in all_ev if e.get("start", "").startswith(today_str)]
        except Exception:
            pass

    _days_pt = ["segunda-feira","terça-feira","quarta-feira","quinta-feira","sexta-feira","sábado","domingo"]
    weekday  = _days_pt[today.weekday()]
    hour     = local_now().hour
    greeting = "Bom dia" if hour < 12 else ("Boa tarde" if hour < 18 else "Boa noite")

    tasks_txt = "\n".join(f"- {'✓' if t.get('done') else '○'} {t['text']}" for t in tasks) or "(sem tarefas)"
    cal_txt   = "\n".join(
        f"- {e['title']} às {e['start'][11:16]}" if len(e['start']) > 10 else f"- {e['title']} (dia todo)"
        for e in cal_today
    ) or "(sem compromissos)"

    # Saúde de ontem
    try:
        conn = sqlite3.connect(_db_path(user_id))
        row  = conn.execute("SELECT * FROM health_history WHERE date = ?", (yesterday,)).fetchone()
        conn.close()
        if row:
            r       = dict(row)
            sh, sm  = r["sleep_min"] // 60, r["sleep_min"] % 60
            health_txt = (
                f"\nSaúde de ontem: {r['steps']:,} passos | Sono {sh}h{sm:02d}min | "
                f"{r['water_ml']}ml água | Hábitos {habits['done']}/{habits['total']}"
            )
        else:
            health_txt = ""
    except Exception:
        health_txt = ""

    prompt = f"""Responda SEMPRE em português do Brasil.

{greeting}, {name}! Hoje é {today_str} ({weekday}).

Clima em {HUB_CITY or 'sua cidade'}: {weather}

Compromissos de hoje:
{cal_txt}

Tarefas de hoje:
{tasks_txt}
{health_txt}

Gere um resumo diário matinal (máximo 8-10 linhas). Comece com "{greeting}, {name}!",
destaque compromissos e tarefas importantes, comente os dados de saúde de ontem (se houver),
e termine com uma motivação curta. Use **negrito** para os itens mais importantes. Tom amigável."""

    try:
        resp = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}",
            headers={"Content-Type": "application/json"},
            json={"contents": [{"parts": [{"text": prompt}]}],
                  "generationConfig": {"maxOutputTokens": 2048, "temperature": 0.7}},
            timeout=30
        )
        resp.raise_for_status()
        parts   = resp.json()["candidates"][0]["content"]["parts"]
        content = "\n".join(p["text"] for p in parts if not p.get("thought")).strip()
        out_file.write_text(json.dumps(
            {"date": today_str, "content": content, "generated_at": local_now().strftime("%H:%M")},
            ensure_ascii=False, indent=2
        ), encoding="utf-8")
        logger.info("[%s] Briefing gerado", user_id)
    except Exception as e:
        logger.error("[%s] Erro briefing: %s", user_id, e)


# ---------------------------------------------------------------------------
# Vagas (Job Scout via Google News RSS)
# ---------------------------------------------------------------------------

def generate_jobs_json(user_id: str):
    logger.info("[%s] Buscando vagas...", user_id)
    try:
        hs       = _get_hub_settings(user_id)
        keywords = hs.get("job_keywords") or []
        # job_locations: lista de {loc, mode} ou strings legadas
        raw_locs = hs.get("job_locations") or []
        locations = []
        for l in raw_locs:
            if isinstance(l, dict):
                locations.append({"loc": l.get("loc", ""), "mode": l.get("mode", "")})
            else:
                locations.append({"loc": str(l), "mode": ""})

        level = hs.get("job_level", "")
        days  = int(hs.get("job_days", 7))

        if not keywords:
            logger.info("[%s] Nenhuma palavra-chave de vaga configurada, pulando", user_id)
            (_data_dir(user_id) / "jobs_today.json").write_text("[]", encoding="utf-8")
            return

        from urllib.parse import quote_plus
        import xml.etree.ElementTree as ET, html as html_module
        from datetime import timezone
        from email.utils import parsedate_to_datetime

        cutoff  = datetime.now(timezone.utc) - timedelta(days=days)
        headers = {"User-Agent": "Mozilla/5.0", "Accept-Language": "pt-BR,pt;q=0.9"}
        seen, results = set(), []

        # Gera uma query por combinação (keyword × localidade+modalidade)
        # Se não há localidades, faz uma busca sem localidade
        combos = []
        if locations:
            for kw in keywords:
                for loc_entry in locations:
                    combos.append((kw, loc_entry["loc"], loc_entry["mode"]))
        else:
            for kw in keywords:
                combos.append((kw, "", ""))

        for kw, loc, mode in combos:
            parts = [kw]
            if loc:   parts.append(loc)
            if mode:  parts.append(mode)
            if level: parts.append(level)
            parts.append("vaga emprego")
            q   = quote_plus(" ".join(parts))
            url = f"https://news.google.com/rss/search?q={q}&hl=pt-BR&gl=BR&ceid=BR:pt-419"
            try:
                r    = requests.get(url, headers=headers, timeout=15)
                root = ET.fromstring(r.content)
                for item in root.findall(".//item")[:6]:
                    link = item.findtext("link", "") or ""
                    if link in seen: continue
                    seen.add(link)
                    title_raw = html_module.unescape(item.findtext("title", "") or "")
                    source = ""
                    if " - " in title_raw:
                        parts_t = title_raw.rsplit(" - ", 1)
                        title_raw, source = parts_t[0].strip(), parts_t[1].strip()
                    pub_raw = item.findtext("pubDate", "") or ""
                    try:
                        pub_dt  = parsedate_to_datetime(pub_raw)
                        if pub_dt < cutoff: continue
                        pub_str = pub_dt.strftime("%d/%m")
                    except Exception:
                        pub_str = ""
                    results.append({
                        "title":    title_raw,
                        "company":  source,
                        "via":      source,
                        "url":      link,
                        "keyword":  kw,
                        "location": loc,
                        "mode":     mode,
                        "pub":      pub_str,
                        "is_new":   True,
                    })
            except Exception as e:
                logger.warning("[%s] Vaga RSS falhou para '%s' / '%s': %s", user_id, kw, loc, e)

        (_data_dir(user_id) / "jobs_today.json").write_text(
            json.dumps(results[:30], ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info("[%s] %d vagas encontradas", user_id, len(results))
    except Exception as e:
        logger.error("[%s] Erro vagas: %s", user_id, e)
        (_data_dir(user_id) / "jobs_today.json").write_text("[]", encoding="utf-8")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def generate_hub_data(user_id: str):
    logger.info("=== Atualizando dados: %s ===", USERS[user_id]["name"])
    generate_health_json(user_id)
    generate_news_json(user_id)
    generate_jobs_json(user_id)
    generate_calendar_json(user_id)
    generate_daily_briefing(user_id)
    logger.info("=== %s atualizado ===", USERS[user_id]["name"])


if __name__ == "__main__":
    import sys
    args = sys.argv[1:]

    user_id = "bernardo"
    if "--user" in args:
        idx = args.index("--user")
        if idx + 1 < len(args):
            user_id = args[idx + 1].lower()

    if user_id not in USERS:
        print(f"Usuário inválido: {user_id}. Use --user bernardo ou --user amanda")
        sys.exit(1)

    if "--auth-health" in args or "--auth" in args:
        run_auth(user_id, HEALTH_SCOPES, "health")
    elif "--auth-calendar" in args:
        run_auth(user_id, CALENDAR_SCOPES, "calendar")
    else:
        generate_hub_data(user_id)
