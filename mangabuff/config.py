import os

BASE_URL = os.getenv("MANGABUFF_BASE_URL", "https://mangabuff.ru")
UA = os.getenv("MANGABUFF_UA", "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:136.0) Gecko/20100101 Firefox/136.0")

DEFAULT_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
}

CONNECT_TIMEOUT = int(os.getenv("MANGABUFF_CONNECT_TIMEOUT", "4"))
READ_TIMEOUT = int(os.getenv("MANGABUFF_READ_TIMEOUT", "8"))

HUGE_LIST_THRESHOLD = int(os.getenv("MANGABUFF_HUGE_LIST_THRESHOLD", "5000"))
MAX_CONTENT_BYTES = int(os.getenv("MANGABUFF_MAX_CONTENT_BYTES", "2000000"))
PARTNER_TIMEOUT_LIMIT = int(os.getenv("MANGABUFF_PARTNER_TIMEOUT_LIMIT", "2"))