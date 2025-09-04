import json
import pathlib
import time
from typing import Dict, Tuple
import logging

import requests

from mangabuff.config import BASE_URL, CONNECT_TIMEOUT, READ_TIMEOUT, HUGE_LIST_THRESHOLD
from mangabuff.http.http_utils import build_session_from_profile, post
from mangabuff.parsing.cards import parse_trade_cards_html, normalize_card_entry
from mangabuff.services.card_storage import get_card_storage

logger = logging.getLogger(__name__)


def fetch_all_cards_by_id(
    profile_data: Dict,
    profiles_dir: pathlib.Path,
    user_id: str,
    page_size_hint: int = 60,
    max_pages: int = 500,
    debug: bool = False,
    allow_huge: bool = True,
<<<<<<< Updated upstream
    is_own_inventory: bool = False,  # Новый параметр для определения собственного инвентаря
) -> Tuple[pathlib.Path, bool]:
=======
    is_own_inventory: bool = False,
    force_refresh: bool = True,
    save_to_unified: bool = True,  # Новый параметр для сохранения в единое хранилище
) -> Tuple[pathlib.Path, bool]:
    """
    Получает все карты пользователя через API и сохраняет в единое хранилище.
    
    Args:
        save_to_unified: Если True, сохраняет в единое хранилище (по умолчанию True)
        force_refresh: Если True, всегда делает новый запрос (по умолчанию True)
    """

    # Определяем имя файла (для обратной совместимости)
    if is_own_inventory:
        cards_path = profiles_dir / "my_cards.json"
    else:
        cards_path = profiles_dir / f"{user_id}.json"
    
    # Получаем единое хранилище
    card_storage = get_card_storage(profiles_dir) if save_to_unified else None
    
    # Если force_refresh=False и есть данные в едином хранилище, используем их
    if not force_refresh and card_storage:
        if is_own_inventory:
            existing_cards = card_storage.get_my_cards()
            if existing_cards:
                if debug:
                    print(f"[INV] Using cards from unified storage for own inventory ({len(existing_cards)} cards)")
                # Сохраняем в старый файл для совместимости
                with cards_path.open("w", encoding="utf-8") as f:
                    json.dump(existing_cards, f, ensure_ascii=False, indent=4)
                return cards_path, True
        else:
            existing_cards = card_storage.get_user_cards(user_id)
            if existing_cards:
                if debug:
                    print(f"[INV] Using cards from unified storage for user {user_id} ({len(existing_cards)} cards)")
                # Сохраняем в старый файл для совместимости
                with cards_path.open("w", encoding="utf-8") as f:
                    json.dump(existing_cards, f, ensure_ascii=False, indent=4)
                return cards_path, True

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
                    # Сохраняем в единое хранилище если нужно
                    if card_storage:
                        if is_own_inventory:
                            card_storage.save_my_cards(data, user_id)
                        else:
                            card_storage.save_user_cards(user_id, data)
                    return cards_path, True
            except Exception:
                pass  # Если ошибка чтения, продолжаем с новым запросом
>>>>>>> Stashed changes

    session = build_session_from_profile(profile_data)

    all_cards = []
    offset = 0
    pages = 0

    print(f"🔍 Загружаем карты {'инвентаря' if is_own_inventory else f'пользователя {user_id}'}...")

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

        # If server returns a really large list, previously the function broke out.
        # Now we allow continuing when allow_huge=True. We still emit a warning so
        # maintainers are aware.
        if isinstance(cards, list) and len(cards) > HUGE_LIST_THRESHOLD:
            msg = f"[INV] too big list {len(cards)} for {user_id}"
            if debug:
                print(msg)
            logger.warning(msg)
            if not allow_huge:
                # preserve previous behaviour when explicitly disabled
                break
            # else: continue processing (do NOT break)

        if isinstance(cards, str):
            parsed = parse_trade_cards_html(cards)
            if parsed:
                all_cards.extend(parsed)
            else:
                break
        elif isinstance(cards, list):
            # normalize entries safely: if normalization of an item fails we skip it
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

        # Progress indicator
        if pages % 5 == 0:
            print(f"   📄 Обработано страниц: {pages}, карт: {len(all_cards)}")

        # stopping conditions
        if (isinstance(cards, list) and len(cards) < page_size_hint) or pages >= max_pages:
            break

        time.sleep(0.25)

<<<<<<< Updated upstream
<<<<<<< Updated upstream
    # Определяем имя файла: my_cards.json для собственного инвентаря, иначе {user_id}.json
    if is_own_inventory:
        cards_path = profiles_dir / "my_cards.json"
    else:
        cards_path = profiles_dir / f"{user_id}.json"
    
    # write atomically: write to .tmp then rename
=======
    # Записываем атомарно в старый файл для совместимости
>>>>>>> Stashed changes
=======
    # Записываем атомарно в старый файл для совместимости
>>>>>>> Stashed changes
    tmp_path = cards_path.with_suffix(cards_path.suffix + ".tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(all_cards, f, ensure_ascii=False, indent=4)
        tmp_path.replace(cards_path)
    except Exception:
        logger.exception("failed to write cards file for %s", user_id)
        # fallback: try direct write (best-effort)
        with cards_path.open("w", encoding="utf-8") as f:
            json.dump(all_cards, f, ensure_ascii=False, indent=4)

<<<<<<< Updated upstream
<<<<<<< Updated upstream
    return cards_path, bool(all_cards)


def ensure_own_inventory(profile_path: pathlib.Path, profile_data: Dict, debug: bool = False) -> pathlib.Path:
    my_id = profile_data.get("id") or profile_data.get("ID") or profile_data.get("user_id")
    if not my_id:
        raise RuntimeError("no user id in profile")
    # Передаем is_own_inventory=True для использования my_cards.json
=======
=======
>>>>>>> Stashed changes
    # Сохраняем в единое хранилище
    if card_storage and all_cards:
        if is_own_inventory:
            card_storage.save_my_cards(all_cards, user_id)
        else:
            card_storage.save_user_cards(user_id, all_cards)

    if debug:
        print(f"[INV] Saved {len(all_cards)} cards for {user_id} to {cards_path.name}")
    else:
        print(f"✅ Загружено {len(all_cards)} карт {'в инвентарь' if is_own_inventory else f'пользователя {user_id}'}")

    return cards_path, bool(all_cards)


def ensure_own_inventory(
    profile_path: pathlib.Path, 
    profile_data: Dict, 
    debug: bool = False,
    force_refresh: bool = True,
    save_to_unified: bool = True  # Новый параметр
) -> pathlib.Path:
    """
    Гарантирует наличие актуального инвентаря текущего пользователя.
    
    Args:
        force_refresh: Если True, всегда обновляет инвентарь (по умолчанию True)
        save_to_unified: Если True, сохраняет в единое хранилище (по умолчанию True)
    """
    my_id = profile_data.get("id") or profile_data.get("ID") or profile_data.get("user_id")
    if not my_id:
        raise RuntimeError("no user id in profile")
    
    # Передаем оба новых параметра
<<<<<<< Updated upstream
>>>>>>> Stashed changes
=======
>>>>>>> Stashed changes
    cards_path, got = fetch_all_cards_by_id(
        profile_data, 
        profile_path.parent, 
        str(my_id), 
        debug=debug, 
        allow_huge=True,
<<<<<<< Updated upstream
        is_own_inventory=True  # Указываем что это собственный инвентарь
=======
        is_own_inventory=True,
        force_refresh=force_refresh,
        save_to_unified=save_to_unified
<<<<<<< Updated upstream
>>>>>>> Stashed changes
=======
>>>>>>> Stashed changes
    )
    if not got:
        raise RuntimeError("inventory empty")
<<<<<<< Updated upstream
    return cards_path
=======
    
    if debug:
        print(f"[INV] Inventory refreshed: {cards_path.name}")
    
    return cards_path


def get_my_cards_from_storage(profiles_dir: pathlib.Path) -> list:
    """
    Получает карты инвентаря из единого хранилища.
    
    Returns:
        Список карт из инвентаря или пустой список если данных нет
    """
    card_storage = get_card_storage(profiles_dir)
    return card_storage.get_my_cards()


def get_user_cards_from_storage(profiles_dir: pathlib.Path, user_id: str) -> list:
    """
    Получает карты пользователя из единого хранилища.
    
    Args:
        profiles_dir: Директория профилей
        user_id: ID пользователя
        
    Returns:
        Список карт пользователя или пустой список если данных нет
    """
    card_storage = get_card_storage(profiles_dir)
<<<<<<< Updated upstream
    return card_storage.get_user_cards(user_id)
>>>>>>> Stashed changes
=======
    return card_storage.get_user_cards(user_id)
>>>>>>> Stashed changes
