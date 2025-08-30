from typing import Dict, Optional, Tuple
from bs4 import BeautifulSoup
import requests

from mangabuff.config import BASE_URL
from mangabuff.http.http_utils import get, post, extract_cookies, build_session_from_profile
from mangabuff.utils.html import extract_login_errors_from_html
from mangabuff.utils.text import norm_text

def get_csrf_token(session: requests.Session, debug: bool = False) -> Optional[str]:
    try:
        response = get(session, f"{BASE_URL}/login")
        if debug:
            print(f"[CSRF] GET /login -> {response.status_code}")
    except requests.RequestException:
        return None
    if response.status_code != 200:
        return None
    soup = BeautifulSoup(response.text, "html.parser")
    token_meta = soup.select_one('meta[name="csrf-token"]')
    if token_meta and token_meta.get("content"):
        return token_meta["content"].strip()
    token_input = soup.find("input", {"name": "_token"})
    if token_input and token_input.get("value"):
        return token_input["value"].strip()
    return None

def do_login(session: requests.Session, email: str, password: str, csrf_token: str, debug: bool = False) -> Tuple[bool, Dict]:
    headers = {
        "Referer": f"{BASE_URL}/login",
        "Origin": BASE_URL,
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": session.headers.get("Accept", "*/*"),
        "Accept-Language": session.headers.get("Accept-Language", "ru,en;q=0.8"),
        "X-CSRF-TOKEN": csrf_token,
    }
    data = {"email": email, "password": password, "_token": csrf_token}
    try:
        resp = post(session, f"{BASE_URL}/login", data=data, headers=headers, allow_redirects=True)
    except requests.RequestException as e:
        if debug:
            print(f"[LOGIN] error POST /login: {e}")
        return False, {"message": f"network error: {e}"}

    if "mangabuff_session" in session.cookies.keys():
        return True, {}

    messages = extract_login_errors_from_html(resp.text)
    message = "; ".join(messages) if messages else ""
    html_preview = norm_text(resp.text)[:2000]
    if not message and "csrf" in resp.text.lower():
        message = "CSRF token problem"
    if not message and resp.status_code in (401, 403):
        message = f"HTTP {resp.status_code}"
    if not message and "/login" in resp.url:
        message = "Still on /login (auth not completed)"
    return False, {"message": message or "Login failed", "html_preview": html_preview, "status": resp.status_code, "url": resp.url}

def check_authenticated(session: requests.Session, debug: bool = False) -> bool:
    import requests as rq
    try:
        r = session.get(f"{BASE_URL}/login", allow_redirects=False, timeout=(4, 8))
        loc = r.headers.get("Location", "")
        if r.status_code in (301, 302) and "/login" not in loc:
            return True
    except rq.RequestException:
        pass
    try:
        r = session.get(BASE_URL, timeout=(4, 8))
        if r.status_code == 200 and ("/logout" in r.text or "Выйти" in r.text or "notifications" in r.text):
            return True
    except rq.RequestException:
        pass
    try:
        r = session.get(f"{BASE_URL}/notifications", timeout=(4, 8), allow_redirects=False)
        if r.status_code == 200:
            return True
        if r.status_code == 403 and "mangabuff_session" in session.cookies.keys():
            return True
    except rq.RequestException:
        pass
    return False

def update_profile_cookies(profile_data: Dict, email: str, password: str, debug: bool = False, skip_check: bool = False) -> Tuple[bool, Dict]:
    session = build_session_from_profile(profile_data)

    csrf = get_csrf_token(session, debug=debug)
    if not csrf:
        return False, {"message": "No CSRF token"}
    ok, info = do_login(session, email, password, csrf, debug=debug)
    if not ok:
        return False, info

    cookie = extract_cookies(session.cookies)

    profile_data["client_headers"]["x-csrf-token"] = csrf
    profile_data["cookie"] = cookie
    profile_data["cookie"]["theme"] = profile_data["cookie"].get("theme") or "light"

    if skip_check:
        return True, {}

    if not check_authenticated(session, debug=debug):
        return False, {"message": "Auth check failed"}

    return True, {}