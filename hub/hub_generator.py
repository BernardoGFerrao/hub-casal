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
        row  = conn.execute("SELECT data_json FROM user_hub_settings LIMIT 1").fetchone()
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
# Vagas — scrapers por plataforma
# ---------------------------------------------------------------------------

_JOB_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8",
}

_LI_TIME_FILTER = {1: "r86400", 3: "r259200", 7: "r604800", 14: "r1209600", 30: "r2592000"}
_LI_MODE_MAP    = {"remote": "2", "onsite": "1", "hybrid": "3"}
_LI_SEARCH_URL  = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
_LI_DETAIL_URL  = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{}"
_GUPY_API_URL   = "https://employability-portal.gupy.io/api/v1/jobs"
_GUPY_WORKPLACE = {"remote": "remote", "onsite": "on-site", "hybrid": "hybrid"}


def _scrape_linkedin(keywords: list, locations: list, days: int, mode: str, seen: set) -> list:
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        logger.warning("beautifulsoup4/lxml não instalado — LinkedIn ignorado")
        return []

    import re as _re2
    tf   = _LI_TIME_FILTER.get(days, "r604800")
    f_WT = _LI_MODE_MAP.get(mode, "")
    results = []

    # Detecta work_mode pelo texto de localização retornado pelo LinkedIn
    def _detect_mode(loc_text: str) -> str:
        lt = loc_text.lower()
        if any(t in lt for t in ["remote", "remoto", "home office"]):
            return "remote"
        if any(t in lt for t in ["hybrid", "híbrido", "hibrido"]):
            return "hybrid"
        if any(t in lt for t in ["on-site", "onsite", "presencial"]):
            return "on-site"
        return ""

    loc_list = [l["loc"] for l in locations if l.get("loc")] or ["Brasil"]

    # Busca cada keyword separadamente para garantir relevância
    for kw in keywords:
        for loc in loc_list:
            for start in range(0, 50, 25):
                params = {"keywords": kw, "location": loc, "f_TPR": tf, "start": start}
                if f_WT:
                    params["f_WT"] = f_WT
                try:
                    r = requests.get(_LI_SEARCH_URL, params=params, headers=_JOB_HEADERS, timeout=15)
                    r.raise_for_status()
                    soup  = BeautifulSoup(r.text, "lxml")
                    cards = soup.select("li")
                    if not cards:
                        break
                    found = 0
                    for card in cards:
                        try:
                            title_el   = card.select_one("h3.base-search-card__title, span.screen-reader-text")
                            company_el = card.select_one("h4.base-search-card__subtitle a, h4.base-search-card__subtitle")
                            loc_el     = card.select_one("span.job-search-card__location")
                            link_el    = card.select_one("a.base-card__full-link, a[href*='/jobs/view/']")
                            time_el    = card.select_one("time")
                            if not (title_el and company_el and link_el):
                                continue
                            href = link_el.get("href", "").split("?")[0]
                            if href in seen:
                                continue
                            # Filtra pelo título: deve conter alguma keyword relevante
                            title_str = title_el.get_text(strip=True)
                            title_low = title_str.lower()
                            if not any(k.lower() in title_low for k in keywords):
                                continue
                            seen.add(href)
                            pub      = time_el.get("datetime", "") if time_el else ""
                            loc_text = loc_el.get_text(strip=True) if loc_el else loc
                            results.append({
                                "title":    title_str,
                                "company":  company_el.get_text(strip=True),
                                "via":      "LinkedIn",
                                "url":      href,
                                "location": loc_text,
                                "mode":     _detect_mode(loc_text) or mode,
                                "pub":      pub[:10] if pub else "",
                                "platform": "linkedin",
                            })
                            found += 1
                        except Exception:
                            pass
                    if found == 0:
                        break
                    import time as _t; _t.sleep(1.2)
                except Exception as e:
                    logger.warning("LinkedIn request falhou (%s/%s): %s", kw, loc, e)
                    break
        if len(results) >= 80:
            break

    logger.info("LinkedIn: %d vagas", len(results))
    return results


def _scrape_gupy(keywords: list, days: int, mode: str, seen: set) -> list:
    wp = _GUPY_WORKPLACE.get(mode)
    results = []
    cutoff  = datetime.now(timezone.utc) - timedelta(days=days)

    for kw in keywords:
        offset = 0
        while True:
            params: dict = {"jobName": kw, "limit": 20, "offset": offset}
            if wp:
                params["workplaceType"] = wp
            try:
                r = requests.get(_GUPY_API_URL, params=params, headers=_JOB_HEADERS, timeout=15)
                r.raise_for_status()
                data  = r.json()
                items = data.get("data", [])
                if not items:
                    break
                found = 0
                for item in items:
                    try:
                        url = item.get("jobUrl") or f"https://portal.gupy.io/job/{item.get('id')}"
                        if url in seen:
                            continue
                        pub = item.get("publishedDate", "")
                        if pub and cutoff:
                            from datetime import timezone as _tz
                            pub_dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))
                            if pub_dt < cutoff:
                                continue
                        seen.add(url)
                        city  = item.get("city") or ""
                        state = item.get("state") or ""
                        loc   = ", ".join(p for p in [city, state] if p) or "Brasil"
                        results.append({
                            "title":    (item.get("name") or "").strip(),
                            "company":  (item.get("careerPageName") or "").strip(),
                            "via":      "Gupy",
                            "url":      url,
                            "location": loc,
                            "mode":     item.get("workplaceType", mode),
                            "pub":      pub[:10] if pub else "",
                            "platform": "gupy",
                        })
                        found += 1
                    except Exception:
                        pass
                if found == 0 or len(items) < 20:
                    break
                offset += 20
                import time as _t; _t.sleep(0.6)
                if offset >= 100:
                    break
            except Exception as e:
                logger.warning("Gupy falhou para '%s': %s", kw, e)
                break

    logger.info("Gupy: %d vagas", len(results))
    return results


def _scrape_google_news_jobs(keywords: list, locations: list, days: int, level: str, seen: set) -> list:
    import xml.etree.ElementTree as ET
    import html as html_module
    from urllib.parse import quote_plus
    from email.utils import parsedate_to_datetime

    cutoff  = datetime.now(timezone.utc) - timedelta(days=days)
    results = []

    combos = []
    if locations:
        for kw in keywords:
            for loc_entry in locations:
                combos.append((kw, loc_entry.get("loc", ""), loc_entry.get("mode", "")))
    else:
        for kw in keywords:
            combos.append((kw, "", ""))

    for kw, loc, mode in combos:
        parts = [kw]
        if loc:   parts.append(loc)
        if mode:  parts.append(mode)
        if level: parts.append(level)
        parts.append("vaga")
        q   = quote_plus(" ".join(parts))
        url = f"https://news.google.com/rss/search?q={q}&hl=pt-BR&gl=BR&ceid=BR:pt-419"
        try:
            r    = requests.get(url, headers=_JOB_HEADERS, timeout=15)
            root = ET.fromstring(r.content)
            for item in root.findall(".//item")[:8]:
                link = item.findtext("link", "") or ""
                if link in seen:
                    continue
                seen.add(link)
                title_raw = html_module.unescape(item.findtext("title", "") or "")
                source = ""
                if " - " in title_raw:
                    parts_t = title_raw.rsplit(" - ", 1)
                    title_raw, source = parts_t[0].strip(), parts_t[1].strip()
                pub_raw = item.findtext("pubDate", "") or ""
                try:
                    pub_dt  = parsedate_to_datetime(pub_raw)
                    if pub_dt < cutoff:
                        continue
                    pub_str = pub_dt.strftime("%Y-%m-%d")
                except Exception:
                    pub_str = ""
                results.append({
                    "title":    title_raw,
                    "company":  source,
                    "via":      "Google News",
                    "url":      link,
                    "location": loc,
                    "mode":     mode,
                    "pub":      pub_str,
                    "platform": "google_news",
                })
        except Exception as e:
            logger.warning("Google News falhou para '%s': %s", kw, e)

    logger.info("Google News: %d vagas", len(results))
    return results


def generate_jobs_json(user_id: str):
    logger.info("[%s] Buscando vagas...", user_id)
    try:
        hs       = _get_hub_settings(user_id)
        keywords = hs.get("job_keywords") or []
        raw_locs = hs.get("job_locations") or []
        locations = []
        for l in raw_locs:
            if isinstance(l, dict):
                locations.append({"loc": l.get("loc", ""), "mode": l.get("mode", "")})
            else:
                locations.append({"loc": str(l), "mode": ""})

        level = hs.get("job_level", "")
        days  = int(hs.get("job_days", 7))
        mode  = locations[0]["mode"] if locations and locations[0].get("mode") else "any"

        if not keywords:
            logger.info("[%s] Nenhuma palavra-chave configurada, pulando", user_id)
            (_data_dir(user_id) / "jobs_today.json").write_text("[]", encoding="utf-8")
            return

        seen    = set()
        results = []

        # LinkedIn público (sem autenticação, sem Playwright)
        li_jobs = _scrape_linkedin(keywords, locations, days, mode, seen)
        results.extend(li_jobs)

        # Gupy API pública
        gupy_jobs = _scrape_gupy(keywords, days, mode, seen)
        results.extend(gupy_jobs)

        # Google News como complemento
        gn_jobs = _scrape_google_news_jobs(keywords, locations, days, level, seen)
        results.extend(gn_jobs)

        # Adiciona id estável por URL (usado como PK no banco de vagas)
        import hashlib as _hl
        for j in results:
            if not j.get("id"):
                j["id"] = _hl.md5(j.get("url", j.get("title", "")).encode()).hexdigest()[:16]

        # Filtro de localização: vagas híbridas/presenciais só passam se forem
        # de cidades aceitas (configuradas em job_locations) ou remotas
        local_city_norms = set()
        for l in locations:
            loc_str = l.get("loc", "")
            if loc_str:
                from unicodedata import normalize as _unorm
                local_city_norms.add(_unorm("NFKD", loc_str.lower()).encode("ascii","ignore").decode())

        import re as _re
        from unicodedata import normalize as _un2
        def _norm(s):
            return _un2("NFKD", s.lower()).encode("ascii","ignore").decode()

        _REMOTE_RE  = _re.compile(r"\b(remot[ao]|remote|home.?office|teletrabalho)\b", _re.IGNORECASE)
        _HYBRID_SET = {"hybrid", "hibrido", "hibrida"}
        _ONSITE_SET = {"on-site", "onsite", "presencial"}
        # Localizações genéricas/nacionais — não filtrar por cidade
        _GENERIC_RE = _re.compile(
            r"^(brasil|brazil|brazil \(remote\)|brasil \(remoto\)|"
            r"rio grande do sul|rs|br|todo o brasil|nacional|"
            r"qualquer lugar|anywhere|remote|remoto)$"
        )

        before_loc = len(results)
        filtered = []
        for job in results:
            jloc  = _norm(job.get("location", "")).strip()
            jmode = _norm(job.get("mode", "")).strip()

            # Remota explícita ou localização genérica nacional → passa sempre
            is_remote  = jmode == "remote" or bool(_REMOTE_RE.search(jloc)) or bool(_REMOTE_RE.search(jmode))
            is_generic = not jloc or bool(_GENERIC_RE.match(jloc))

            if is_remote or is_generic:
                filtered.append(job)
                continue

            # Híbrida ou presencial com cidade específica → só passa se for cidade aceita
            is_hybrid_onsite = jmode in _HYBRID_SET or jmode in _ONSITE_SET
            if is_hybrid_onsite or jloc:
                if any(city in jloc for city in local_city_norms):
                    filtered.append(job)
                # senão descarta silenciosamente
            else:
                filtered.append(job)

        results = filtered
        removed = before_loc - len(results)
        if removed:
            logger.info("[%s] Filtro loc removeu %d vagas fora da região", user_id, removed)

        # Filtro de senioridade
        if level and level.lower() in ("júnior", "junior", "estágio", "estagio"):
            _SENIOR_RE = _re.compile(
                r"\b(s[êe]nior|sr\.?|pleno|pl\.?|especialista|specialist|"
                r"coordenador|gerente|diretor|manager|lead|principal|staff|"
                r"arquiteto|architect)\b",
                _re.IGNORECASE,
            )
            before_sen = len(results)
            results = [j for j in results if not _SENIOR_RE.search(j.get("title", ""))]
            removed_sen = before_sen - len(results)
            if removed_sen:
                logger.info("[%s] Filtro senioridade removeu %d vagas sênior/pleno", user_id, removed_sen)

        # Formata pub como dd/mm para exibição
        for j in results:
            pub = j.get("pub", "")
            if pub and len(pub) == 10 and "-" in pub:
                try:
                    parts = pub.split("-")
                    j["pub"] = f"{parts[2]}/{parts[1]}"
                except Exception:
                    pass
            j["is_new"] = True

        (_data_dir(user_id) / "jobs_today.json").write_text(
            json.dumps(results[:50], ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info("[%s] Total: %d vagas (%d LinkedIn, %d Gupy, %d Google News)",
                    user_id, len(results), len(li_jobs), len(gupy_jobs), len(gn_jobs))
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
