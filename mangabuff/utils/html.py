from typing import List
from bs4 import BeautifulSoup
from mangabuff.utils.text import norm_text

def extract_login_errors_from_html(html: str) -> List[str]:
    texts: List[str] = []
    try:
        soup = BeautifulSoup(html or "", "html.parser")
        selectors = [
            ".alert.alert-danger", ".alert-danger", ".alert-error", ".alert--danger",
            ".toast-message", ".flash-message", ".flash__message",
            ".errors", ".error", ".form-error", ".invalid-feedback",
            "ul.errors li", "div[class*=error]", "ul[class*=error] li",
        ]
        seen = set()
        for sel in selectors:
            for el in soup.select(sel):
                t = norm_text(el.get_text(" ", strip=True))
                if t and t not in seen:
                    seen.add(t)
                    texts.append(t)
        for el in soup.select("span.help.is-danger, small.text-danger"):
            t = norm_text(el.get_text(" ", strip=True))
            if t and t not in texts:
                texts.append(t)
        title = soup.title.get_text(strip=True) if soup.title else ""
        if title and "ошиб" in title.lower() and title not in texts:
            texts.append(title)
    except Exception:
        pass
    return texts

def select_any(soup: BeautifulSoup, selectors: List[str]) -> List:
    out = []
    seen = set()
    for sel in selectors:
        for el in soup.select(sel):
            if id(el) not in seen:
                seen.add(id(el))
                out.append(el)
    return out

def extract_last_page_number(soup: BeautifulSoup) -> int:
    import re
    pages = []
    for a in soup.select("ul.pagination a[href]"):
        href = a.get("href", "")
        m = re.search(r"[?&]page=(\d+)", href)
        if m:
            try:
                pages.append(int(m.group(1)))
            except ValueError:
                pass
    if pages:
        return max(pages)
    nums = []
    for li in soup.select("ul.pagination li"):
        txt = li.get_text(strip=True)
        if txt.isdigit():
            nums.append(int(txt))
    return max(nums) if nums else 1

def with_page(url: str, page: int) -> str:
    return f"{url}{'&' if '?' in url else '?'}page={page}"