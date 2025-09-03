import json
import pathlib
import random
import time
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timedelta

from mangabuff.parsing.cards import entry_instance_id
from mangabuff.services.counters import count_by_last_page
from mangabuff.config import BASE_URL
from mangabuff.http.http_utils import build_session_from_profile

# Константы
CACHE_LIFETIME_HOURS = 24


class CardWantersCache:
    """Кэш для хранения информации о желающих на карты"""
    
    def __init__(self, cache_dir: pathlib.Path):
        self.cache_file = cache_dir / "card_wanters_cache.json"
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_data = self._load_cache()
    
    def _load_cache(self) -> Dict[str, Any]:
        """Загружает кэш из файла"""
        if self.cache_file.exists():
            try:
                with self.cache_file.open("r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}
    
    def _save_cache(self) -> None:
        """Сохраняет кэш в файл атомарно"""
        tmp_file = self.cache_file.with_suffix(".json.tmp")
        with tmp_file.open("w", encoding="utf-8") as f:
            json.dump(self.cache_data, f, ensure_ascii=False, indent=2)
        tmp_file.replace(self.cache_file)
    
    def get_wanters_count(self, card_id: int) -> Optional[int]:
        """Получает количество желающих из кэша, если не устарело"""
        key = str(card_id)
        if key not in self.cache_data:
            return None
        
        entry = self.cache_data[key]
        timestamp = entry.get("timestamp", 0)
        
        # Проверяем срок жизни кэша
        current_time = time.time()
        if current_time - timestamp > CACHE_LIFETIME_HOURS * 3600:
            # Кэш устарел, удаляем запись
            del self.cache_data[key]
            self._save_cache()
            return None
        
        return entry.get("wanters_count")
    
    def set_card_info(self, card_id: int, wanters_count: int, card_info: Dict[str, Any]) -> None:
        """Сохраняет информацию о карте в кэш"""
        key = str(card_id)
        
        # Извлекаем название из различных мест
        name = card_info.get("title") or card_info.get("name") or ""
        if not name and isinstance(card_info.get("card"), dict):
            name = card_info["card"].get("name") or card_info["card"].get("title") or ""
        
        # Извлекаем ранг из различных мест
        rank = card_info.get("rank") or card_info.get("grade") or ""
        if not rank and isinstance(card_info.get("card"), dict):
            rank = card_info["card"].get("rank") or card_info["card"].get("grade") or ""
        
        # Извлекаем instance_id
        instance_id = card_info.get("id") or 0
        if not instance_id:
            instance_id = entry_instance_id(card_info) or 0
        
        self.cache_data[key] = {
            "card_id": card_id,
            "name": name.strip() if name else "",
            "rank": rank.strip() if rank else "",
            "wanters_count": wanters_count,
            "timestamp": time.time(),
            "cached_at": datetime.now().isoformat(),
            "instance_id": instance_id
        }
        
        self._save_cache()
    
    def cleanup_old_entries(self) -> None:
        """Удаляет устаревшие записи из кэша"""
        current_time = time.time()
        keys_to_delete = []
        
        for key, entry in self.cache_data.items():
            timestamp = entry.get("timestamp", 0)
            if current_time - timestamp > CACHE_LIFETIME_HOURS * 3600:
                keys_to_delete.append(key)
        
        if keys_to_delete:
            for key in keys_to_delete:
                del self.cache_data[key]
            self._save_cache()


def get_card_wanters_count(profile_data: Dict, card_id: int, cache: CardWantersCache, debug: bool = False) -> int:
    """
    Получает количество желающих на карту, используя кэш или делая запрос.
    """
    # Пробуем получить из кэша
    cached_count = cache.get_wanters_count(card_id)
    if cached_count is not None:
        if debug:
            print(f"[SELECTOR] Using cached wanters count for card {card_id}: {cached_count}")
        return cached_count
    
    # Если не в кэше, делаем запрос
    wanters_selectors = [
        "a.profile__friends-item",
        'a[class*="profile__friends-item"]',
        "a.profile_friends-item",
        'a[class*="profile_friends-item"]',
    ]
    
    want_url = f"{BASE_URL}/cards/{card_id}/offers/want"
    wanters_count = count_by_last_page(profile_data, want_url, wanters_selectors, per_page=60, debug=debug)
    
    if debug:
        print(f"[SELECTOR] Fetched wanters count for card {card_id}: {wanters_count}")
    
    return wanters_count


def select_suitable_card_for_trade(
    profile_data: Dict,
    my_cards: List[Dict[str, Any]],
    target_card: Dict[str, Any],
    cache_dir: pathlib.Path,
    debug: bool = False
) -> Optional[Tuple[int, Dict[str, Any]]]:
    """
    Выбирает подходящую карту для обмена из инвентаря пользователя.
    Новая упрощенная логика: выбирает карты с количеством желающих ≤ целевой карте.
    
    Args:
        profile_data: Данные профиля
        my_cards: Список карт в инвентаре пользователя
        target_card: Целевая карта (из вкладов)
        cache_dir: Директория для кэша
        debug: Режим отладки
    
    Returns:
        Tuple из (instance_id, card_info) или None если подходящая карта не найдена
    """
    cache = CardWantersCache(cache_dir)
    
    # Периодически чистим старые записи кэша
    if random.random() < 0.1:  # В 10% случаев
        cache.cleanup_old_entries()
    
    target_rank = (target_card.get("rank") or "").strip()
    target_wanters = target_card.get("wanters_count", 0)
    
    if debug:
        print(f"[SELECTOR] Target card: rank={target_rank}, wanters={target_wanters}")
    
    # Фильтруем карты по рангу и извлекаем instance_id
    suitable_cards = []
    for card in my_cards:
        # Получаем ранг из различных возможных мест
        card_rank = None
        
        # Прямые поля
        if card.get("rank"):
            card_rank = str(card.get("rank")).strip()
        elif card.get("grade"):
            card_rank = str(card.get("grade")).strip()
        # Вложенный объект card
        elif isinstance(card.get("card"), dict):
            if card["card"].get("rank"):
                card_rank = str(card["card"].get("rank")).strip()
            elif card["card"].get("grade"):
                card_rank = str(card["card"].get("grade")).strip()
        
        if card_rank == target_rank:
            inst_id = entry_instance_id(card)
            if inst_id:
                # Получаем card_id из разных мест
                card_id = card.get("card_id")
                if not card_id and isinstance(card.get("card"), dict):
                    card_id = card["card"].get("id")
                
                if card_id:
                    suitable_cards.append({
                        "instance_id": inst_id,
                        "card_id": int(card_id),
                        "card_info": card
                    })
    
    if not suitable_cards:
        if debug:
            print(f"[SELECTOR] No cards with rank {target_rank} found in inventory")
        return None
    
    if debug:
        print(f"[SELECTOR] Found {len(suitable_cards)} cards with rank {target_rank}")
    
    # Перемешиваем список для случайного выбора среди подходящих
    random.shuffle(suitable_cards)
    
    # Список всех проверенных карт с их количеством желающих
    checked_cards = []
    
    # Проверяем все карты нужного ранга
    for candidate in suitable_cards:
        card_id = candidate["card_id"]
        card_info = candidate["card_info"]
        
        # Получаем количество желающих (из кэша или запросом)
        card_wanters = get_card_wanters_count(profile_data, card_id, cache, debug=debug)
        
        # Сохраняем информацию о проверенной карте
        checked_cards.append({
            "candidate": candidate,
            "wanters": card_wanters
        })
        
        # Сохраняем в кэш для будущего использования
        cache.set_card_info(card_id, card_wanters, card_info)
        
        # Проверяем, подходит ли карта (количество желающих ≤ целевой)
        if card_wanters <= target_wanters:
            if debug:
                print(f"[SELECTOR] Selected card {card_id} with {card_wanters} wanters (≤ {target_wanters})")
            return candidate["instance_id"], card_info
        else:
            if debug:
                print(f"[SELECTOR] Rejected card {card_id} with {card_wanters} wanters (> {target_wanters})")
    
    # Если не нашли карту с количеством желающих ≤ целевой,
    # выбираем карту с наиболее близким количеством желающих
    if checked_cards:
        if debug:
            print(f"[SELECTOR] No card with wanters ≤ {target_wanters} found. Selecting closest match.")
        
        # Сортируем по разнице с целевым количеством желающих
        best_match = min(checked_cards, key=lambda x: abs(x["wanters"] - target_wanters))
        
        if debug:
            print(f"[SELECTOR] Selected closest match: card {best_match['candidate']['card_id']} "
                  f"with {best_match['wanters']} wanters (target: {target_wanters})")
        
        return best_match["candidate"]["instance_id"], best_match["candidate"]["card_info"]
    
    # Не должны сюда попасть, но на всякий случай
    return None


def get_random_card_same_rank(
    my_cards: List[Dict[str, Any]], 
    target_rank: str
) -> Optional[int]:
    """
    Простая функция для обратной совместимости.
    Возвращает случайный instance_id карты того же ранга.
    """
    suitable = []
    for card in my_cards:
        # Получаем ранг из различных возможных мест
        card_rank = None
        
        # Прямые поля
        if card.get("rank"):
            card_rank = str(card.get("rank")).strip()
        elif card.get("grade"):
            card_rank = str(card.get("grade")).strip()
        # Вложенный объект card
        elif isinstance(card.get("card"), dict):
            if card["card"].get("rank"):
                card_rank = str(card["card"].get("rank")).strip()
            elif card["card"].get("grade"):
                card_rank = str(card["card"].get("grade")).strip()
        
        if card_rank == target_rank:
            inst_id = entry_instance_id(card)
            if inst_id:
                suitable.append(inst_id)
    
    if suitable:
        return random.choice(suitable)
    return None