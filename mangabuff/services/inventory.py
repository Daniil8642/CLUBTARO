import json
import pathlib
import time
from typing import Dict, Tuple
import logging

import requests

from mangabuff.config import BASE_URL, CONNECT_TIMEOUT, READ_TIMEOUT, HUGE_LIST_THRESHOLD
from mangabuff.http.http_utils import build_session_from_profile, post
from mangabuff.parsing.cards import parse_trade_cards_html, normalize_card_entry

logger = logging.getLogger(__name__)


def fetch_all_cards_by_id(
    profile_data: Dict,
    profiles_dir: pathlib.Path,
    user_id: str,
    page_size_hint: int = 60,
    max_pages: int = 500,
    debug: bool = False,
    allow_huge: bool = True,
    is_own_inventory: bool = False,
    force_refresh: bool = True,  # Новый параметр для принудительного обновления
) -> Tuple[pathlib.Path, bool]:
    """
    Получает все карты пользователя через API.
    
    Args:
        force_refresh: Если True, всегда делает новый запрос (по умолчанию True)
    """

    # Определяем имя файла
    if is_own_inventory:
        cards_path = profiles_dir / "my_cards.json"
    else:
        cards_path = profiles_dir / f"{user_id}.json"
    
    # Если force_refresh=False и файл существует и свежий (менее 5 минут), используем его
    if not force_refresh and cards_path.exists():
        file_age = time.time() - cards_path.stat().st_mtime
        if file_age < 300:  # 5 минут
            if debug:
                print(f"[INV] Using cached file for {user_id} (age: {file_age:.0f}s)")
            try:
                with cards_path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                if data:
                    return cards_path, True
            except Exception:
                pass  # Если ошибка чтения, продолжаем с новым запросом

    session = build_session_from_profile(profile_data)

    all_cards = []
    offset = 0
    pages = 0

    while True:
        url = f"{BASE_URL}/trades/{user_id}/availableCardsLoad"
        payload = {"offset": offset}
        try:
            resp = post(
                session,
                url,
                headers={
                    "Referer": f"{BASE_URL}/trades/{user_id}",
                    "Origin": BASE_URL,
                    "X-Requested-With": "XMLHttpRequest",
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                },
                data=payload,
            )
        except requests.RequestException as e:
            if debug:
                print(f"[INV] request error offset={offset}: {e}")
            logger.exception("request error while fetching cards for %s at offset %s", user_id, offset)
            break

        if resp.status_code != 200:
            if debug:
                print(f"[INV] status {resp.status_code} offset={offset}")
            logger.warning("unexpected status %s for %s at offset %s", resp.status_code, user_id, offset)
            break

        try:
            data = resp.json()
        except ValueError:
            data = {"cards": parse_trade_cards_html(resp.text)}

        cards = data.get("cards", [])
        if not cards:
            # empty page -> done
            break

        # Проверка на слишком большой список
        if isinstance(cards, list) and len(cards) > HUGE_LIST_THRESHOLD:
            msg = f"[INV] too big list {len(cards)} for {user_id}"
            if debug:
                print(msg)
            logger.warning(msg)
            if not allow_huge:
                break

        if isinstance(cards, str):
            parsed = parse_trade_cards_html(cards)
            if parsed:
                all_cards.extend(parsed)
            else:
                break
        elif isinstance(cards, list):
            # normalize entries safely
            norm = []
            for c in cards:
                try:
                    norm.append(normalize_card_entry(c))
                except Exception:
                    logger.exception("failed to normalize card entry for user %s offset %s", user_id, offset)
            all_cards.extend(norm)
        else:
            # unknown format: stop
            logger.error("unknown cards format (%s) for %s at offset %s", type(cards), user_id, offset)
            break

        # advance offset
        offset += len(cards) if isinstance(cards, list) else page_size_hint
        pages += 1

        # stopping conditions
        if (isinstance(cards, list) and len(cards) < page_size_hint) or pages >= max_pages:
            break

        time.sleep(0.25)

    # Записываем атомарно
    tmp_path = cards_path.with_suffix(cards_path.suffix + ".tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(all_cards, f, ensure_ascii=False, indent=4)
        tmp_path.replace(cards_path)
    except Exception:
        logger.exception("failed to write cards file for %s", user_id)
        # fallback: direct write
        with cards_path.open("w", encoding="utf-8") as f:
            json.dump(all_cards, f, ensure_ascii=False, indent=4)

    if debug:
        print(f"[INV] Saved {len(all_cards)} cards for {user_id} to {cards_path.name}")

    return cards_path, bool(all_cards)


def ensure_own_inventory(
    profile_path: pathlib.Path, 
    profile_data: Dict, 
    debug: bool = False,
    force_refresh: bool = True  # Новый параметр
) -> pathlib.Path:
    """
    Гарантирует наличие актуального инвентаря текущего пользователя.
    
    Args:
        force_refresh: Если True, всегда обновляет инвентарь (по умолчанию True)
    """
    my_id = profile_data.get("id") or profile_data.get("ID") or profile_data.get("user_id")
    if not my_id:
        raise RuntimeError("no user id in profile")
    
    # Передаем force_refresh для контроля обновления
    cards_path, got = fetch_all_cards_by_id(
        profile_data, 
        profile_path.parent, 
        str(my_id), 
        debug=debug, 
        allow_huge=True,
        is_own_inventory=True,
        force_refresh=force_refresh  # Передаем параметр
    )
    
    if not got:
        raise RuntimeError("inventory empty")
    
    if debug:
        print(f"[INV] Inventory refreshed: {cards_path.name}")
    
    return cards_path