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
    Если debug=True — печатает подробную диагностику по каждому найденному кандидату.
    """
    soup = BeautifulSoup(html or "", "html.parser")
    user_ids: List[int] = []
    seen = set()

    # --- находим контейнер владельцев (попробуем несколько вариантов) ---
    owners_container = soup.select_one("div.card-show__owner-wrapper div.card-show__owners")
    if not owners_container:
        owners_container = soup.select_one("div.card-show__owner-wrapper")
    if not owners_container:
        owners_container = soup.select_one("div.card-show__owners")
    if not owners_container:
        if debug:
            print("[DEBUG][OWNERS] owners container not found")
        return []

    # --- собираем кандидатов: элементы с card-show__owner или ссылки внутри контейнера ---
    candidates = []
    # прямые узлы владельцев (div или a с классом содержащим card-show__owner)
    candidates.extend(owners_container.select('[class*="card-show__owner"], [class*="card-show_owner"]'))
    # плюс все ссылки внутри контейнера (на случай другой структуры)
    candidates.extend(owners_container.select('a[href^="/users/"]'))
    # уникализируем, сохраняя порядок
    seen_nodes = set()
    uniq_candidates = []
    for n in candidates:
        key = str(getattr(n, "sourceline", id(n))) + "_" + (n.name or "")
        if key not in seen_nodes:
            seen_nodes.add(key)
            uniq_candidates.append(n)

    if debug:
        print(f"[DEBUG][OWNERS] candidates found in container: {len(uniq_candidates)}")

    def cls_list(n):
        try:
            return [c.lower() for c in (n.get("class") or [])]
        except Exception:
            return []

    def online_here(n):
        classes = cls_list(n)
        reasons = []
        # точный модификатор owner--online
        for c in classes:
            if c.endswith("owner--online") or c.endswith("__owner--online") or c == "is-online":
                reasons.append(f"class:{c}")
        # распространённые маркеры в потомках
        if n.select_one(".online, .is-online, .user-online, .avatar__online, .status--online, .badge--online"):
            reasons.append("descendant:online-indicator")
        # как последняя мера - подстрока online в классах
        if any("online" in c for c in classes):
            reasons.append("class-substring-online")
        return (len(reasons) > 0, reasons)

    def lock_here(n):
        classes = cls_list(n)
        reasons = []
        # явные варианты
        lock_classes = ("trade-lock", "card-show__owner-icon--trade-lock", "icon-lock", "icon--lock", "locked")
        for c in classes:
            if c in lock_classes:
                reasons.append(f"class:{c}")
            if c.endswith("-lock") or c.endswith("__lock") or "-lock" in c:
                reasons.append(f"class-like-lock:{c}")
        # data-locked
        try:
            if n.has_attr("data-locked") and str(n.get("data-locked")).strip() == "1":
                reasons.append("data-locked=1")
        except Exception:
            pass
        # иконки/элементы-замки среди потомков
        if n.select_one(".card-show__owner-icon--trade-lock, .trade-lock, .icon-lock, .icon--lock, .locked"):
            reasons.append("descendant:lock-icon")
        return (len(reasons) > 0, reasons)

    # Теперь проходим кандидатов и пытаемся извлечь uid и статус
    for idx, node in enumerate(uniq_candidates, start=1):
        # ищем ссылку на пользователя в пределах узла; если node сам <a> — используем его
        a = None
        if node.name == "a" and (node.get("href") or "").startswith("/users/"):
            a = node
        else:
            a = node.select_one('a[href^="/users/"]')
        if not a:
            # не нашли ссылку в этом кандидате — пропускаем
            if debug:
                # краткий дамп: классы узла
                print(f"[DEBUG][OWNERS] candidate #{idx}: no user anchor, classes={cls_list(node)[:5]}")
            continue

        href = a.get("href") or ""
        m = re.search(r"/users/(\d+)", href)
        if not m:
            if debug:
                print(f"[DEBUG][OWNERS] candidate #{idx}: anchor href no uid -> {href}")
            continue
        uid = safe_int(m.group(1))
        if not uid or uid in seen:
            continue

        # проверим онлайн и замок в приоритетном порядке:
        # сначала смотрим на сам узел 'node', затем на ссылку 'a'
        online_flag, online_reasons = online_here(node)
        if not online_flag:
            of_a, r_a = online_here(a)
            if of_a:
                online_flag = True
                online_reasons = r_a

        # также проверим в соседях и родителях (как раньше)
        if not online_flag:
            # родители до 3 уровней
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

        if debug:
            print(f"[DEBUG][OWNERS] candidate #{idx}: uid={uid}, node_cls={cls_list(node)[:4]}, a_cls={cls_list(a)[:4]}, online={online_flag} ({online_reasons}), locked={locked_flag} ({locked_reasons})")

        if online_flag and not locked_flag:
            seen.add(uid)
            user_ids.append(uid)

    if debug:
        print(f"[DEBUG][OWNERS] final owners (online & unlocked): {len(user_ids)} -> {user_ids[:30]}")

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
    if debug:
        print(f"[OWNERS] page 1: {len(owners1)} online unlocked, last_page={last_page}")
    yield 1, owners1

    for p in range(2, last_page + 1):
        try:
            rp = get(session, with_page(owners_url, p))
        except requests.RequestException:
            break
        if rp.status_code != 200:
            break
        owners_p = parse_online_unlocked_owners(rp.text, debug=debug)
        if debug:
            print(f"[OWNERS] page {p}: {len(owners_p)} online unlocked")
        yield p, owners_p
        time.sleep(0.2)