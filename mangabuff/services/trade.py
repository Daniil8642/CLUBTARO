import json
import random
import time
from typing import Dict, List, Optional, Any

import requests

from mangabuff.config import BASE_URL, CONNECT_TIMEOUT, READ_TIMEOUT, HUGE_LIST_THRESHOLD, MAX_CONTENT_BYTES, PARTNER_TIMEOUT_LIMIT
from mangabuff.http.http_utils import build_session_from_profile, get, post, read_capped, decode_body_and_maybe_json
from mangabuff.parsing.cards import parse_trade_cards_html, normalize_card_entry, entry_card_id, entry_instance_id
from mangabuff.utils.text import norm_text

class PartnerState:
    def __init__(self) -> None:
        self.blocked = set()
        self.timeouts: Dict[int, int] = {}

    def is_blocked(self, pid: int) -> bool:
        return pid in self.blocked

    def mark_timeout(self, pid: int) -> None:
        self.timeouts[pid] = self.timeouts.get(pid, 0) + 1
        if self.timeouts[pid] >= PARTNER_TIMEOUT_LIMIT:
            self.blocked.add(pid)
            self.timeouts.pop(pid, None)

    def clear_timeout(self, pid: int) -> None:
        self.timeouts.pop(pid, None)

def _build_search_url(partner_id: int, offset: int, q: str) -> str:
    from urllib.parse import quote_plus
    return f"{BASE_URL}/search/cards?user_id={partner_id}&offset={offset}&q={quote_plus(q)}"

def _parse_cards_from_text_or_json(text: str, j: Any) -> List[Dict[str, Any]]:
    if isinstance(j, dict):
        html_content = j.get("content") or j.get("html") or j.get("view")
        if isinstance(html_content, str):
            return parse_trade_cards_html(html_content)
        cards = j.get("cards")
        if isinstance(cards, list):
            return [normalize_card_entry(c) for c in cards]
    if text:
        return parse_trade_cards_html(text)
    return []

def _attempt_search(session: requests.Session, partner_state: PartnerState, partner_id: int, offset: int, q: str, debug: bool=False) -> List[Dict[str, Any]]:
    if len(norm_text(q)) <= 2:
        return []
    url = _build_search_url(partner_id, offset, q)
    try:
        r = get(session, url, stream=True)
    except requests.exceptions.ReadTimeout:
        partner_state.mark_timeout(partner_id)
        return []
    except requests.RequestException:
        return []

    if r.status_code != 200:
        try:
            r.close()
        except Exception:
            pass
        return []

    content, too_big = read_capped(r)
    if too_big:
        partner_state.blocked.add(partner_id)
        partner_state.timeouts.pop(partner_id, None)
        return []

    text, j = decode_body_and_maybe_json(content or b"", r.headers)
    cards = _parse_cards_from_text_or_json(text, j)
    if isinstance(j, dict) and isinstance(j.get("cards"), list):
        if len(j["cards"]) > HUGE_LIST_THRESHOLD:
            partner_state.blocked.add(partner_id)
            return []
    return cards

def _attempt_ajax(session: requests.Session, partner_state: PartnerState, partner_id: int, side: str, rank: Optional[str], search: Optional[str], offset: int, debug: bool=False) -> List[Dict[str, Any]]:
    if partner_state.is_blocked(partner_id):
        return []

    url = f"{BASE_URL}/trades/{partner_id}/availableCardsLoad"
    headers = {
        "Referer": f"{BASE_URL}/trades/offers/{partner_id}",
        "Origin": BASE_URL,
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Accept": "application/json, text/javascript, */*; q=0.01",
    }
    if "X-CSRF-TOKEN" in session.headers:
        headers["X-CSRF-TOKEN"] = session.headers["X-CSRF-TOKEN"]

    small_limit = 60
    attempts: List[Dict[str, Any]] = []

    if rank and search:
        attempts.append({"rank": rank, "search": search, "side": side, "limit": small_limit, "offset": offset})
        attempts.append({"rank": rank, "search": search, "tab": side, "limit": small_limit, "offset": offset})
        attempts.append({"tab": side, "rank": rank, "q": search, "limit": small_limit, "offset": offset})
    if search and rank:
        attempts.append({"search": search, "rank": rank, "limit": small_limit, "offset": offset})
    if rank:
        attempts.append({"rank": rank, "side": side, "limit": small_limit, "offset": offset})
        attempts.append({"data-rank": rank, "tab": side, "limit": small_limit, "offset": offset})
    if search:
        attempts.append({"search": search, "limit": small_limit, "offset": offset})
        attempts.append({"q": search, "limit": small_limit, "offset": offset})

    side_variants = [
        {"side": side},
        {"owner": side},
        {"inventory": side},
        {"tab": side},
        {"from": "creator" if side == "creator" else "receiver"},
        {"isCreator": "1" if side == "creator" else "0"},
        {},
    ]
    for sv in side_variants:
        attempts.append({**sv, "offset": offset, "limit": small_limit})

    for payload in attempts:
        try:
            resp = post(session, url, headers=headers, data=payload, stream=True)
        except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectTimeout):
            partner_state.mark_timeout(partner_id)
            continue
        except requests.RequestException:
            continue

        if resp.status_code != 200:
            try:
                resp.close()
            except Exception:
                pass
            continue

        content, too_big = read_capped(resp)
        if too_big:
            partner_state.blocked.add(partner_id)
            partner_state.timeouts.pop(partner_id, None)
            return []

        text, j = decode_body_and_maybe_json(content or b"", resp.headers)
        partner_state.clear_timeout(partner_id)

        if isinstance(j, dict):
            cards = j.get("cards")
            if isinstance(cards, list):
                if len(cards) > HUGE_LIST_THRESHOLD:
                    partner_state.blocked.add(partner_id)
                    return []
                return [normalize_card_entry(c) for c in cards]
            if isinstance(cards, str):
                parsed = parse_trade_cards_html(cards)
                if parsed:
                    return parsed
            for key in ("html", "view", "content"):
                if isinstance(j.get(key), str):
                    parsed = parse_trade_cards_html(j[key])
                    if parsed:
                        return parsed

        parsed = parse_trade_cards_html(text or "")
        if parsed:
            return parsed

    return []

def load_trade_cards(session: requests.Session, partner_state: PartnerState, partner_id: int, side: str, rank: Optional[str], search: Optional[str], offset: int, debug: bool=False) -> List[Dict[str, Any]]:
    if search:
        found = _attempt_search(session, partner_state, partner_id, offset, search, debug=debug)
        if found:
            return found
    return _attempt_ajax(session, partner_state, partner_id, side, rank, search, offset, debug=debug)

# paste this into mangabuff/services/trade.py replacing the old find_partner_card_instance

def find_partner_card_instance(session: requests.Session, partner_id: int, side: str, card_id: int, rank: str, name: str, debug: bool=False) -> Optional[int]:
    """
    Оптимизированный поиск instance_id карточки у партнёра:
    1) сначала пытаемся распарсить страницу /trades/offers/{partner_id} (один быстрый GET),
       часто там уже есть все карточки;
    2) затем делаем один быстрый поиск по имени (если name длиннее 2 символов);
    3) если всё ещё не найдено — делаем постраничный скан с ограничениями;
    В любых местах — аккуратно ловим таймауты/исключения и помечаем партнёра в state,
    чтобы не застревать на одном пользователе.
    """
    target_id = int(card_id)
    state = PartnerState()

    # --- 1) Парсим offers page (как быстрый путь) ---
    try:
        url = f"{BASE_URL}/trades/offers/{partner_id}"
        # Используем короткий read timeout для этого запроса — чтобы не застревать
        r = session.get(url, timeout=(CONNECT_TIMEOUT, min(READ_TIMEOUT, 5)))
        if r.status_code == 200:
            parsed = parse_trade_cards_html(r.text)
            if parsed:
                for c in parsed:
                    try:
                        if entry_card_id(c) == target_id:
                            inst = entry_instance_id(c)
                            if inst:
                                if debug:
                                    print(f"[FIND] found on offers page for {partner_id}: inst={inst}")
                                return inst
                    except Exception:
                        # не фатально — продолжаем
                        continue
    except requests.exceptions.ReadTimeout:
        # помечаем как таймаут и возвращаем None
        state.mark_timeout(partner_id)
        if debug:
            print(f"[FIND] offers page read timeout for {partner_id}")
        return None
    except requests.RequestException:
        # сетевые проблемы — быстрый выход
        if debug:
            print(f"[FIND] offers page request error for {partner_id}")
        return None
    except Exception:
        # любой неожиданный error — не ломаем процесс
        if debug:
            print(f"[FIND] offers page parse error for {partner_id}")
        # продолжаем к следующему этапу

    # --- 2) Быстрый поиск по имени (одна попытка) ---
    if len(norm_text(name)) > 2:
        try:
            cards = load_trade_cards(session, state, partner_id, side, rank=rank, search=name, offset=0, debug=debug)
            if cards:
                for c in cards:
                    try:
                        if entry_card_id(c) == target_id:
                            inst = entry_instance_id(c)
                            if inst:
                                if debug:
                                    print(f"[FIND] found by quick search for {partner_id}: inst={inst}")
                                return inst
                    except Exception:
                        continue
        except Exception:
            # load_trade_cards внутренне обрабатывает большинство ошибок, но перестрахуемся
            if debug:
                print(f"[FIND] quick search exception for {partner_id}")
            # помечаем таймаут/ошибку и не блокируем всю рассылку
            state.mark_timeout(partner_id)
            return None

    # --- 3) Постраничный обход инвентаря партнёра (fallback) ---
    offset = 0
    page_size = 60
    scanned = 0
    max_scanned_limit = 30000  # бензин: если много — выход
    for _page in range(0, 1000):
        try:
            cards = load_trade_cards(session, state, partner_id, side, rank=rank, search=None, offset=offset, debug=debug)
        except Exception:
            # защищаемся от непредвиденных ошибок
            if debug:
                print(f"[FIND] exception while loading trade cards for {partner_id} offset={offset}")
            state.mark_timeout(partner_id)
            return None

        if not cards:
            # либо последняя страница, либо партнёр заблокирован/нет карточек
            break

        for c in cards:
            try:
                if entry_card_id(c) == target_id:
                    inst = entry_instance_id(c)
                    if inst:
                        if debug:
                            print(f"[FIND] found while paging for {partner_id}: inst={inst}")
                        return inst
            except Exception:
                continue

        scanned += len(cards)
        if len(cards) < page_size:
            # дошли до конца
            break
        offset += len(cards)
        # защита от бесконечного сканирования
        if scanned > max_scanned_limit:
            if debug:
                print(f"[FIND] scanned more than {max_scanned_limit} items for {partner_id}, aborting")
            break
        # чуть пауза, чтобы не троллить сайт
        time.sleep(0.12)

    # --- 4) в конце пытаемся ещё раз offers page с более длительным таймаутом (best-effort) ---
    try:
        r2 = session.get(f"{BASE_URL}/trades/offers/{partner_id}", timeout=(CONNECT_TIMEOUT, min(READ_TIMEOUT, 8)))
        if r2.status_code == 200:
            parsed2 = parse_trade_cards_html(r2.text)
            for c in parsed2:
                try:
                    if entry_card_id(c) == target_id:
                        inst = entry_instance_id(c)
                        if inst:
                            if debug:
                                print(f"[FIND] found on final offers page for {partner_id}: inst={inst}")
                            return inst
                except Exception:
                    continue
    except Exception:
        pass

    # не найдено
    return None

def create_trade_via_api(session: requests.Session, receiver_id: int, my_instance_id: int, his_instance_id: int, debug: bool=False) -> bool:
    url = f"{BASE_URL}/trades/create"
    headers = {
        "Referer": f"{BASE_URL}/trades/offers/{receiver_id}",
        "Origin": BASE_URL,
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    }
    if "X-CSRF-TOKEN" in session.headers:
        headers["X-CSRF-TOKEN"] = session.headers["X-CSRF-TOKEN"]
    data_pairs = [
        ("receiver_id", int(receiver_id)),
        ("creator_card_ids[]", int(my_instance_id)),
        ("receiver_card_ids[]", int(his_instance_id)),
    ]
    try:
        r = post(session, url, data=data_pairs, headers=headers, allow_redirects=False)
    except requests.RequestException:
        return False

    if r.status_code in (301, 302) and "/trades/" in (r.headers.get("Location") or ""):
        return True

    try:
        j = r.json()
        if isinstance(j, dict):
            if j.get("success") or j.get("ok") or (isinstance(j.get("trade"), dict) and j["trade"].get("id")):
                return True
            body = json.dumps(j).lower()
            if "успеш" in body or "отправ" in body or "создан" in body:
                return True
    except ValueError:
        pass
    body = (r.text or "").lower()
    if "успеш" in body or "отправ" in body or "создан" in body:
        return True

    json_payload = {
        "receiver_id": receiver_id,
        "creator_card_ids": [my_instance_id],
        "receiver_card_ids": [his_instance_id],
    }
    try:
        r2 = post(session, url, json=json_payload, headers={**headers, "Content-Type": "application/json"}, allow_redirects=False)
        if r2.status_code in (301, 302) and "/trades/" in (r2.headers.get("Location") or ""):
            return True
        try:
            j2 = r2.json()
            if isinstance(j2, dict):
                if j2.get("success") or j2.get("ok") or (isinstance(j2.get("trade"), dict) and j2["trade"].get("id")):
                    return True
                body2 = json.dumps(j2).lower()
                if "успеш" in body2 or "отправ" in body2 or "создан" in body2:
                    return True
        except ValueError:
            pass
        if "успеш" in (r2.text or "").lower():
            return True
    except requests.RequestException:
        pass
    return False

def trade_form_info(session: requests.Session, partner_id: int, debug: bool=False) -> Optional[Dict[str, Any]]:
    from bs4 import BeautifulSoup
    url = f"{BASE_URL}/trades/offers/{partner_id}"
    try:
        r = get(session, url)
    except requests.RequestException:
        return None
    if r.status_code != 200:
        return None
    soup = BeautifulSoup(r.text, "html.parser")
    token = ""
    meta = soup.select_one('meta[name="csrf-token"]')
    if meta and meta.get("content"):
        token = meta["content"].strip()
    inp_token = soup.select_one('input[name="_token"]')
    if not token and inp_token and inp_token.get("value"):
        token = inp_token["value"].strip()

    form = None
    for sel in [
        'form[action*="/trades/offers"][method="post"]',
        'form[action*="/trades"][method="post"]',
        'form[id*="offer"]',
        'form[name*="offer"]',
        "form",
    ]:
        form = soup.select_one(sel)
        if form:
            break
    if not form:
        return None

    action = form.get("action") or "/trades/offers"
    if not action.startswith("http"):
        action = BASE_URL + action

    hidden: Dict[str, Any] = {}
    for inp in form.select('input[name]'):
        name = inp.get("name")
        typ = (inp.get("type") or "").lower()
        if typ in ("checkbox", "radio") and not inp.has_attr("checked"):
            continue
        val = inp.get("value", "")
        if name in hidden:
            if isinstance(hidden[name], list):
                hidden[name].append(val)
            else:
                hidden[name] = [hidden[name], val]
        else:
            hidden[name] = val

    return {"action": action, "token": token, "hidden": hidden}

def submit_trade_form(session: requests.Session, action_url: str, csrf: str, base_form: Dict[str, Any], my_instance_id: int, partner_instance_id: int, debug: bool=False) -> bool:
    headers = {
        "Referer": action_url,
        "Origin": BASE_URL,
        "Content-Type": "application/x-www-form-urlencoded",
    }
    if csrf:
        headers["X-CSRF-TOKEN"] = csrf

    data: Dict[str, Any] = dict(base_form or {})
    if "_token" not in data and csrf:
        data["_token"] = csrf

    def ensure_list(d: Dict[str, Any], key: str):
        if key not in d:
            d[key] = []
        val = d[key]
        if isinstance(val, list):
            d[key] = [str(x) for x in val if x is not None]
        elif val is None or val == "":
            d[key] = []
        else:
            d[key] = [str(val)]

    ensure_list(data, "creator[]")
    ensure_list(data, "receiver[]")

    data["creator[]"].append(str(my_instance_id))
    data["receiver[]"].append(str(partner_instance_id))

    form_payload = []
    for k, v in data.items():
        if isinstance(v, list):
            for x in v:
                form_payload.append((k, str(x)))
        else:
            form_payload.append((k, str(v)))

    try:
        r = post(session, action_url, data=form_payload, headers=headers, allow_redirects=False)
    except requests.RequestException:
        return False

    if r.status_code in (301, 302):
        loc = r.headers.get("Location", "")
        if any(x in (loc or "") for x in ("/trades", "/messages", "/notifications", "/offers")):
            return True

    try:
        j = r.json()
        if isinstance(j, dict):
            if j.get("success") or j.get("ok") or j.get("status") in ("ok", "success"):
                return True
            body = json.dumps(j).lower()
            if "успеш" in body or "отправ" in body or "создан" in body:
                return True
    except ValueError:
        pass
    body = (r.text or "").lower()
    if "успеш" in body or "отправ" in body or "создан" in body:
        return True
    return False

def send_trades_to_online_owners(profile_data: Dict, target_card: Dict[str, Any], owners_iter, my_cards: List[Dict[str, Any]], dry_run: bool=True, use_api: bool=True, debug: bool=False) -> Dict[str, int]:
    session = build_session_from_profile(profile_data)
    stats = {"checked_pages": 0, "owners_seen": 0, "trades_attempted": 0, "trades_succeeded": 0, "skipped_no_my_cards": 0}

    rank = (target_card.get("rank") or "").strip()
    def instances_any(cards: List[Dict[str, Any]]) -> List[int]:
        out = []
        for c in cards:
            inst = entry_instance_id(c)
            if inst:
                out.append(inst)
        return out

    my_instances: List[int] = []
    if rank:
        for c in my_cards:
            r = (c.get("rank") or c.get("grade") or "").strip()
            if r == rank:
                inst = entry_instance_id(c)
                if inst:
                    my_instances.append(inst)
    if not my_instances:
        my_instances = instances_any(my_cards)

    if not my_instances:
        stats["skipped_no_my_cards"] = 1
        return stats

    card_id = int(target_card.get("card_id") or target_card.get("cardId") or 0)
    name = target_card.get("name") or ""

    for page_num, owners in owners_iter:
        stats["checked_pages"] += 1
        if not owners:
            continue
        for owner_id in owners:
            stats["owners_seen"] += 1
            if str(owner_id) == str(profile_data.get("id")):
                continue
            his_inst = find_partner_card_instance(session, int(owner_id), "receiver", card_id, rank, name, debug=debug)
            if not his_inst:
                continue
            my_inst = random.choice(my_instances)
            stats["trades_attempted"] += 1
            if dry_run:
                print(f"[DRY] {my_inst} -> {his_inst} для {owner_id}")
                continue

            success = False
            if use_api:
                success = create_trade_via_api(session, int(owner_id), int(my_inst), int(his_inst), debug=debug)
            if not success:
                form = trade_form_info(session, int(owner_id), debug=debug)
                if form:
                    success = submit_trade_form(session, form["action"], form.get("token", ""), form.get("hidden", {}), int(my_inst), int(his_inst), debug=debug)
            if success:
                stats["trades_succeeded"] += 1
            time.sleep(0.4 + random.random() * 0.6)
    return stats