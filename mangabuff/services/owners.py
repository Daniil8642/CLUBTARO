import re
import time
from typing import List, Generator, Tuple, Dict

import requests
from bs4 import BeautifulSoup

from mangabuff.config import BASE_URL
from mangabuff.http.http_utils import build_session_from_profile, get
from mangabuff.utils.text import safe_int
from mangabuff.utils.html import with_page, extract_last_page_number


def parse_online_unlocked_owners(html: str, debug: bool = False) -> List[int]:
    """
    Возвращает список user_id владельцев карты, которые:
      - находятся в блоке владельцев (card-show__owner-wrapper > card-show__owners),
      - помечены как онлайн,
      - и у которых нет признака «замка».
    """
    soup = BeautifulSoup(html or "", "html.parser")
    user_ids: List[int] = []
    seen = set()

    # находим контейнер владельцев
    owners_container = soup.select_one("div.card-show__owner-wrapper div.card-show__owners")
    if not owners_container:
        owners_container = soup.select_one("div.card-show__owner-wrapper")
    if not owners_container:
        owners_container = soup.select_one("div.card-show__owners")
    if not owners_container:
        return []

    # собираем кандидатов
    candidates = []
    candidates.extend(owners_container.select('[class*="card-show__owner"], [class*="card-show_owner"]'))
    candidates.extend(owners_container.select('a[href^="/users/"]'))
    
    # уникализируем
    seen_nodes = set()
    uniq_candidates = []
    for n in candidates:
        key = str(getattr(n, "sourceline", id(n))) + "_" + (n.name or "")
        if key not in seen_nodes:
            seen_nodes.add(key)
            uniq_candidates.append(n)

    def cls_list(n):
        try:
            return [c.lower() for c in (n.get("class") or [])]
        except Exception:
            return []

    def online_here(n):
        classes = cls_list(n)
        reasons = []
        for c in classes:
            if c.endswith("owner--online") or c.endswith("__owner--online") or c == "is-online":
                reasons.append(f"class:{c}")
        if n.select_one(".online, .is-online, .user-online, .avatar__online, .status--online, .badge--online"):
            reasons.append("descendant:online-indicator")
        if any("online" in c for c in classes):
            reasons.append("class-substring-online")
        return (len(reasons) > 0, reasons)

    def lock_here(n):
        classes = cls_list(n)
        reasons = []
        lock_classes = ("trade-lock", "card-show__owner-icon--trade-lock", "icon-lock", "icon--lock", "locked")
        for c in classes:
            if c in lock_classes:
                reasons.append(f"class:{c}")
            if c.endswith("-lock") or c.endswith("__lock") or "-lock" in c:
                reasons.append(f"class-like-lock:{c}")
        try:
            if n.has_attr("data-locked") and str(n.get("data-locked")).strip() == "1":
                reasons.append("data-locked=1")
        except Exception:
            pass
        if n.select_one(".card-show__owner-icon--trade-lock, .trade-lock, .icon-lock, .icon--lock, .locked"):
            reasons.append("descendant:lock-icon")
        return (len(reasons) > 0, reasons)

    # проходим кандидатов и извлекаем uid и статус
    for idx, node in enumerate(uniq_candidates, start=1):
        a = None
        if node.name == "a" and (node.get("href") or "").startswith("/users/"):
            a = node
        else:
            a = node.select_one('a[href^="/users/"]')
        if not a:
            continue

        href = a.get("href") or ""
        m = re.search(r"/users/(\d+)", href)
        if not m:
            continue
        uid = safe_int(m.group(1))
        if not uid or uid in seen:
            continue

        # проверяем онлайн и замок
        online_flag, online_reasons = online_here(node)
        if not online_flag:
            of_a, r_a = online_here(a)
            if of_a:
                online_flag = True
                online_reasons = r_a

        if not online_flag:
            p = node
            for _ in range(3):
                p = getattr(p, "parent", None)
                if not p:
                    break
                of_p, rp = online_here(p)
                if of_p:
                    online_flag = True
                    online_reasons = rp
                    break

        locked_flag, locked_reasons = lock_here(node)
        if not locked_flag:
            lf_a, lr_a = lock_here(a)
            if lf_a:
                locked_flag = True
                locked_reasons = lr_a

        if online_flag and not locked_flag:
            seen.add(uid)
            user_ids.append(uid)

    return user_ids


def iter_online_owners_by_pages(
    profile_data: Dict,
    card_id: int,
    max_pages: int = 0,
    debug: bool = False
) -> Generator[Tuple[int, List[int]], None, None]:
    """
    Итератор по страницам владельцев: на каждой странице отдаёт список user_id,
    которые онлайн и без замка.
    """
    session = build_session_from_profile(profile_data)
    owners_url = f"{BASE_URL}/cards/{card_id}/users"

    try:
        r1 = get(session, with_page(owners_url, 1))
    except requests.RequestException:
        return
    if r1.status_code != 200:
        return

    soup1 = BeautifulSoup(r1.text or "", "html.parser")
    last_page = extract_last_page_number(soup1)
    if max_pages and max_pages > 0:
        last_page = min(last_page, max_pages)

    owners1 = parse_online_unlocked_owners(r1.text, debug=debug)
    
    # Минимальный вывод - только список ID
    if owners1:
        print(f"📄 Страница 1: онлайн без замков: {owners1}")
    
    yield 1, owners1

    for p in range(2, last_page + 1):
        try:
            rp = get(session, with_page(owners_url, p))
        except requests.RequestException:
            break
        if rp.status_code != 200:
            break
        owners_p = parse_online_unlocked_owners(rp.text, debug=debug)
        
        # Минимальный вывод - только список ID
        if owners_p:
            print(f"📄 Страница {p}: онлайн без замков: {owners_p}")
        
        yield p, owners_p
        time.sleep(0.2)