import json
import pathlib
import re
from typing import Dict, Optional, Any, Tuple, List

import requests
from bs4 import BeautifulSoup

from mangabuff.config import BASE_URL
from mangabuff.http.http_utils import build_session_from_profile, get
from mangabuff.services.inventory import fetch_all_cards_by_id
from mangabuff.services.counters import count_by_last_page
from mangabuff.services.card_storage import get_card_storage

<<<<<<< Updated upstream
def find_boost_card_info(profile_data: Dict, profiles_dir: pathlib.Path, club_boost_url: str, debug: bool=False) -> Optional[Tuple[int, pathlib.Path]]:
=======
def find_boost_card_info(profile_data: Dict, profiles_dir: pathlib.Path, club_boost_url: str, debug: bool=False, force_refresh: bool=True) -> Optional[Tuple[int, pathlib.Path]]:
    """
    Находит информацию о карте для вклада в клуб и сохраняет её в единое хранилище.
    
    Args:
        force_refresh: Если True, всегда обновляет данные (по умолчанию True)
    """
>>>>>>> Stashed changes
    session = build_session_from_profile(profile_data)
    club_boost_url = club_boost_url if club_boost_url.startswith("http") else f"{BASE_URL}{club_boost_url}"
    
    try:
        resp = get(session, club_boost_url)
    except requests.RequestException:
        return None
    if resp.status_code != 200:
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    card_link_el = soup.select_one('a.button.button--block[href*="/cards/"]')
    if not card_link_el or not card_link_el.get("href"):
        return None
    
    card_href = card_link_el["href"]
    card_users_url = card_href if card_href.startswith("http") else f"{BASE_URL}{card_href}"

    # Извлекаем card_id из URL
    m = re.search(r"/cards/(\d+)", card_href)
    if not m:
        return None
    card_id = int(m.group(1))
    
    # Получаем единое хранилище карт
    card_storage = get_card_storage(profiles_dir)
    
    # Получаем информацию о карте напрямую со страницы карты
    card_name = ""
    card_rank = ""
    try:
        card_page_url = f"{BASE_URL}/cards/{card_id}"
        card_resp = get(session, card_page_url)
        if card_resp.status_code == 200:
            card_soup = BeautifulSoup(card_resp.text, "html.parser")
            
            # Ищем название карты
            title_selectors = [
                'h1.card-show__title',
                'h1[class*="title"]',
                '.card-show__name',
                '.card-title',
                'h1'
            ]
            for selector in title_selectors:
                title_el = card_soup.select_one(selector)
                if title_el:
                    card_name = title_el.get_text(strip=True)
                    break
            
            # Ищем ранг карты
            rank_selectors = [
                '.card-show__grade',
                '.card-grade',
                '[class*="grade"]',
                '[data-rank]',
                '.card-rank'
            ]
            for selector in rank_selectors:
                rank_el = card_soup.select_one(selector)
                if rank_el:
                    if rank_el.has_attr("data-rank"):
                        card_rank = rank_el.get("data-rank", "")
                    else:
                        card_rank = rank_el.get_text(strip=True)
                    # Оставляем только букву ранга
                    card_rank = re.sub(r'[^A-Z]', '', card_rank.upper())
                    break
                    
        if debug:
            print(f"[CLUB] Card info from page: name='{card_name}', rank='{card_rank}'")
            
    except Exception as e:
        if debug:
            print(f"[CLUB] Failed to get card info from page: {e}")

    try:
        resp = get(session, card_users_url)
    except requests.RequestException:
        return None
    if resp.status_code != 200:
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    user_links = [a for a in soup.find_all("a", href=True) if a["href"].startswith("/users/")]
    if not user_links:
        return None

    last_user_link = user_links[-1]
    user_id = last_user_link["href"].rstrip("/").split("/")[-1]
<<<<<<< Updated upstream
    cards_path, got_cards = fetch_all_cards_by_id(profile_data, profiles_dir, user_id, debug=debug)
=======
    
    # Получаем карты с force_refresh и сохранением в единое хранилище
    cards_path, got_cards = fetch_all_cards_by_id(
        profile_data, 
        profiles_dir, 
        user_id, 
        debug=debug,
        force_refresh=force_refresh,  # Принудительное обновление
        save_to_unified=True  # Сохраняем в единое хранилище
    )
>>>>>>> Stashed changes
    if not got_cards:
        return None

    # Ищем карту среди загруженных карт пользователя
    user_cards = card_storage.get_user_cards(user_id)
    if not user_cards:
        # Fallback на файл
        try:
            with cards_path.open("r", encoding="utf-8") as f:
                user_cards = json.load(f)
        except Exception:
            return None

    target_card_data = None
    for card in user_cards:
        if int(card.get("card_id") or 0) == card_id:
            target_card_data = card
            break

    if not target_card_data:
        if debug:
            print(f"[CLUB] Card {card_id} not found in user {user_id} cards")
        return None

<<<<<<< Updated upstream
    for card in all_cards:
        if int(card.get("card_id") or 0) == card_id:
            # Получаем количество владельцев и желающих
            owners_count, wanters_count = owners_and_wanters_counts(profile_data, card_id, debug=debug)
            
            # Используем название и ранг со страницы карты, если они есть
            name = card_name or card.get("title") or card.get("name") or ""
            rank = card_rank or card.get("rank") or card.get("grade") or ""
            
            # Если название или ранг все еще пустые, пробуем получить из вложенной структуры
            if isinstance(card.get("card"), dict):
                if not name:
                    name = card["card"].get("name") or card["card"].get("title") or ""
                if not rank:
                    rank = card["card"].get("rank") or card["card"].get("grade") or ""
            
            # Формируем расширенную структуру данных
            boost_card_data = {
                "name": name.strip() if name else "",
                "id": card.get("id") or 0,  # instance_id для отправки обмена
                "card_id": card_id,  # ID карты для ссылки
                "rank": rank.strip() if rank else "",
                "wanters_count": wanters_count,  # количество желающих
                "owners_count": owners_count,  # количество владельцев
                "card_url": f"{BASE_URL}/cards/{card_id}/users"  # прямая ссылка на карту
            }
            
            # Сохраняем в файл с красивым форматированием
            out_path = profiles_dir / "card_for_boost.json"
            with out_path.open("w", encoding="utf-8") as f:
                json.dump(boost_card_data, f, ensure_ascii=False, indent=4)
            
            # Удаляем файл с карточками профиля после извлечения карты
            try:
                cards_path.unlink()
                if debug:
                    print(f"[CLUB] Deleted cards file: {cards_path}")
            except Exception as e:
                if debug:
                    print(f"[CLUB] Failed to delete cards file {cards_path}: {e}")
            
            return card_id, out_path
    return None
=======
    # Получаем количество владельцев и желающих
    owners_count, wanters_count = owners_and_wanters_counts(profile_data, card_id, debug=debug)
    
    # Используем название и ранг со страницы карты, если они есть
    name = card_name or target_card_data.get("title") or target_card_data.get("name") or ""
    rank = card_rank or target_card_data.get("rank") or target_card_data.get("grade") or ""
    
    # Если название или ранг все еще пустые, пробуем получить из вложенной структуры
    if isinstance(target_card_data.get("card"), dict):
        if not name:
            name = target_card_data["card"].get("name") or target_card_data["card"].get("title") or ""
        if not rank:
            rank = target_card_data["card"].get("rank") or target_card_data["card"].get("grade") or ""
    
    # Формируем расширенную структуру данных
    boost_card_data = {
        "name": name.strip() if name else "",
        "id": target_card_data.get("id") or 0,  # instance_id для отправки обмена
        "card_id": card_id,  # ID карты для ссылки
        "rank": rank.strip() if rank else "",
        "wanters_count": wanters_count,  # количество желающих
        "owners_count": owners_count,  # количество владельцев
        "card_url": f"{BASE_URL}/cards/{card_id}/users",  # прямая ссылка на карту
        "updated_at": pathlib.Path(cards_path).stat().st_mtime,  # время обновления
        "source": f"boost_from_user_{user_id}",  # источник данных
        "club_boost_url": club_boost_url  # URL страницы буста
    }
    
    # Сохраняем в единое хранилище
    card_storage.save_boost_card(boost_card_data)
    if debug:
        print(f"[CLUB] Saved boost card to unified storage: {card_id}")
    
    # Сохраняем в старый файл для обратной совместимости
    out_path = profiles_dir / "card_for_boost.json"
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(boost_card_data, f, ensure_ascii=False, indent=4)
    
    # Удаляем файл с карточками профиля после извлечения карты (опционально)
    try:
        if cards_path.exists():
            cards_path.unlink()
            if debug:
                print(f"[CLUB] Deleted temporary cards file: {cards_path}")
    except Exception as e:
        if debug:
            print(f"[CLUB] Failed to delete cards file {cards_path}: {e}")
    
    return card_id, out_path
>>>>>>> Stashed changes

def owners_and_wanters_counts(profile_data: Dict, card_id: int, debug: bool=False) -> Tuple[int, int]:
    owners_selectors = [
        "a.card-show__owner",
        'a[class*="card-show__owner"]',
        "a.card-show_owner",
        'a[class*="card-show_owner"]',
    ]
    wanters_selectors = [
        "a.profile__friends-item",
        'a[class*="profile__friends-item"]',
        "a.profile_friends-item",
        'a[class*="profile_friends-item"]',
    ]

    owners_url = f"{BASE_URL}/cards/{card_id}/users"
    owners_count = count_by_last_page(profile_data, owners_url, owners_selectors, per_page=36, debug=debug)

    want_url = f"{BASE_URL}/cards/{card_id}/offers/want"
    wanters_count = count_by_last_page(profile_data, want_url, wanters_selectors, per_page=60, debug=debug)
    
    if debug:
        print(f"[CLUB] Card {card_id} counts: owners={owners_count}, wanters={wanters_count}")
    
    return owners_count, wanters_count

def get_all_boost_cards_from_storage(profiles_dir: pathlib.Path) -> List[Dict[str, Any]]:
    """
    Получает все карты для буста из единого хранилища.
    
    Returns:
        Список всех карт для буста
    """
    card_storage = get_card_storage(profiles_dir)
    return card_storage.data.get("boost_cards", [])

def get_current_boost_card_from_storage(profiles_dir: pathlib.Path) -> Optional[Dict[str, Any]]:
    """
    Получает текущую карту для буста из единого хранилища.
    
    Returns:
        Данные текущей карты для буста или None
    """
    card_storage = get_card_storage(profiles_dir)
    return card_storage.get_current_boost_card()

def save_boost_card_to_storage(profiles_dir: pathlib.Path, card_data: Dict[str, Any]) -> None:
    """
    Сохраняет карту для буста в единое хранилище.
    
    Args:
        profiles_dir: Директория профилей
        card_data: Данные карты для сохранения
    """
    card_storage = get_card_storage(profiles_dir)
    card_storage.save_boost_card(card_data)