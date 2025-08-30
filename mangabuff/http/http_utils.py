from typing import Dict, Optional, Tuple, Any, List
import requests

from mangabuff.config import DEFAULT_HEADERS, CONNECT_TIMEOUT, READ_TIMEOUT, MAX_CONTENT_BYTES
from mangabuff.utils.text import parse_charset_from_content_type
from mangabuff.config import UA

def build_session_from_profile(profile_data: Dict) -> requests.Session:
    s = requests.Session()
    s.headers.update(DEFAULT_HEADERS.copy())
    client_headers = profile_data.get("client_headers", {}) or {}
    for k in ("x-csrf-token", "x-requested-with", "User-Agent", "Accept", "Accept-Language", "Accept-Encoding"):
        if client_headers.get(k):
            s.headers[k] = client_headers[k]
            if k.lower() == "x-csrf-token":
                s.headers["X-CSRF-TOKEN"] = client_headers[k]
    cookies = profile_data.get("cookie", {}) or {}
    s.cookies.update({k: v for k, v in cookies.items() if v})
    if "X-Requested-With" not in s.headers:
        s.headers["X-Requested-With"] = "XMLHttpRequest"
    return s

def extract_cookies(jar: requests.cookies.RequestsCookieJar) -> Dict[str, str]:
    allowed_prefixes = ("remember_web",)
    wanted = ("XSRF-TOKEN", "mangabuff_session", "__ddg9_", "theme")
    data = {}
    for k, v in jar.get_dict().items():
        if k in wanted or any(k.startswith(p) for p in allowed_prefixes):
            data[k] = v
    if "theme" not in data:
        data["theme"] = "light"
    return data

def read_capped(resp: requests.Response) -> Tuple[Optional[bytes], bool]:
    c_len = resp.headers.get("Content-Length")
    if c_len:
        try:
            if int(c_len) > MAX_CONTENT_BYTES:
                try:
                    resp.close()
                except Exception:
                    pass
                return None, True
        except Exception:
            pass

    total = 0
    chunks: List[bytes] = []
    try:
        for chunk in resp.iter_content(chunk_size=65536):
            if not chunk:
                continue
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_CONTENT_BYTES:
                return None, True
    finally:
        try:
            resp.close()
        except Exception:
            pass
    return b"".join(chunks), False

def decode_body_and_maybe_json(content: bytes, headers: Dict[str, str]) -> Tuple[str, Optional[Any]]:
    import json
    ctype = (headers.get("Content-Type") or "").lower()
    enc = parse_charset_from_content_type(ctype) or "utf-8"
    try:
        text = content.decode(enc, errors="replace")
    except Exception:
        try:
            text = content.decode("utf-8", errors="replace")
        except Exception:
            text = content.decode("latin-1", errors="replace")

    j = None
    try_json = "json" in ctype or (text.lstrip().startswith("{") or text.lstrip().startswith("["))
    if try_json:
        try:
            j = json.loads(text)
        except Exception:
            j = None
    return text, j

def get(session: requests.Session, url: str, **kwargs) -> requests.Response:
    return session.get(url, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT), **kwargs)

def post(session: requests.Session, url: str, **kwargs) -> requests.Response:
    return session.post(url, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT), **kwargs)

def default_client_headers() -> Dict[str, str]:
    return {
        "User-Agent": UA,
        "Accept": DEFAULT_HEADERS["Accept"],
        "Accept-Language": DEFAULT_HEADERS["Accept-Language"],
        "Accept-Encoding": DEFAULT_HEADERS["Accept-Encoding"],
        "x-csrf-token": "",
        "x-requested-with": "XMLHttpRequest",
    }