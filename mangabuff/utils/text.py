import re
from typing import Any, Optional

def norm_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())

def safe_int(v: Any) -> Optional[int]:
    try:
        return int(v)
    except Exception:
        return None

def parse_charset_from_content_type(content_type: str) -> Optional[str]:
    if not content_type:
        return None
    m = re.search(r"charset=([^\s;]+)", content_type, re.I)
    if m:
        return m.group(1).strip().strip('"').strip("'")
    return None

def extract_card_id_from_href(href: Optional[str]) -> Optional[int]:
    if not href:
        return None
    m = re.search(r"/cards/(\d+)", href)
    if m:
        return safe_int(m.group(1))
    return None