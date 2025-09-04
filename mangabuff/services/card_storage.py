# mangabuff/services/card_storage.py
import json
import time
import pathlib
from typing import Dict, List, Any, Optional
from datetime import datetime

class UnifiedCardStorage:
    """Единое хранилище для всех пропарсенных карт"""
    
    def __init__(self, profiles_dir: pathlib.Path):
        self.profiles_dir = profiles_dir
        self.storage_file = profiles_dir / "all_parsed_cards.json"
        self.profiles_dir.mkdir(parents=True, exist_ok=True)
        self.data = self._load_data()
    
    def _load_data(self) -> Dict[str, Any]:
        """Загружает данные из файла"""
        if self.storage_file.exists():
            try:
                with self.storage_file.open("r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        
        return {
            "my_cards": [],
            "boost_cards": [],
            "other_users_cards": {},
            "suitable_cards": [],
            "metadata": {
                "created_at": datetime.now().isoformat(),
                "last_updated": datetime.now().isoformat(),
                "total_cards": 0
            }
        }
    
    def _save_data(self) -> None:
        """Сохраняет данные атомарно"""
        # Обновляем метаданные
        self.data["metadata"]["last_updated"] = datetime.now().isoformat()
        self.data["metadata"]["total_cards"] = (
            len(self.data.get("my_cards", [])) +
            len(self.data.get("boost_cards", [])) +
            sum(len(cards) for cards in self.data.get("other_users_cards", {}).values()) +
            len(self.data.get("suitable_cards", []))
        )
        
        # Атомарная запись
        tmp_file = self.storage_file.with_suffix(".json.tmp")
        with tmp_file.open("w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
        tmp_file.replace(self.storage_file)
    
    def save_my_cards(self, cards: List[Dict[str, Any]], user_id: str = "") -> None:
        """Сохраняет карты инвентаря пользователя"""
        self.data["my_cards"] = cards
        self.data["metadata"]["my_user_id"] = user_id
        self.data["metadata"]["my_cards_updated"] = datetime.now().isoformat()
        self._save_data()
        print(f"💾 Сохранено {len(cards)} карт инвентаря в единое хранилище")
    
    def save_boost_card(self, card_data: Dict[str, Any]) -> None:
        """Сохраняет карту для буста клуба"""
        # Ищем существующую карту с тем же card_id
        existing_index = None
        card_id = card_data.get("card_id")
        
        for i, existing in enumerate(self.data.get("boost_cards", [])):
            if existing.get("card_id") == card_id:
                existing_index = i
                break
        
        # Добавляем временную метку
        card_data["parsed_at"] = datetime.now().isoformat()
        card_data["source"] = "boost"
        
        if existing_index is not None:
            # Обновляем существующую карту
            self.data["boost_cards"][existing_index] = card_data
        else:
            # Добавляем новую карту
            self.data["boost_cards"].append(card_data)
        
        self.data["metadata"]["boost_cards_updated"] = datetime.now().isoformat()
        self._save_data()
        print(f"💾 Сохранена карта буста ID={card_id} в единое хранилище")
    
    def save_user_cards(self, user_id: str, cards: List[Dict[str, Any]]) -> None:
        """Сохраняет карты других пользователей"""
        if "other_users_cards" not in self.data:
            self.data["other_users_cards"] = {}
        
        # Добавляем метки к картам
        for card in cards:
            card["parsed_at"] = datetime.now().isoformat()
            card["source"] = f"user_{user_id}"
        
        self.data["other_users_cards"][user_id] = cards
        self.data["metadata"][f"user_{user_id}_updated"] = datetime.now().isoformat()
        self._save_data()
        print(f"💾 Сохранено {len(cards)} карт пользователя {user_id} в единое хранилище")
    
    def save_suitable_cards(self, suitable_cards: List[Dict[str, Any]], target_card: Dict[str, Any]) -> None:
        """Сохраняет подходящие карты для обмена"""
        suitable_data = {
            "target_card": target_card,
            "cards": suitable_cards,
            "updated_at": datetime.now().isoformat(),
            "total": len(suitable_cards)
        }
        
        self.data["suitable_cards"] = suitable_data
        self.data["metadata"]["suitable_cards_updated"] = datetime.now().isoformat()
        self._save_data()
        print(f"💾 Сохранено {len(suitable_cards)} подходящих карт в единое хранилище")
    
    def get_my_cards(self) -> List[Dict[str, Any]]:
        """Получает карты инвентаря"""
        return self.data.get("my_cards", [])
    
    def get_current_boost_card(self) -> Optional[Dict[str, Any]]:
        """Получает текущую карту для буста"""
        boost_cards = self.data.get("boost_cards", [])
        if boost_cards:
            # Возвращаем последнюю добавленную карту
            return max(boost_cards, key=lambda x: x.get("parsed_at", ""))
        return None
    
    def get_user_cards(self, user_id: str) -> List[Dict[str, Any]]:
        """Получает карты конкретного пользователя"""
        return self.data.get("other_users_cards", {}).get(user_id, [])
    
    def get_suitable_cards(self) -> Dict[str, Any]:
        """Получает подходящие карты для обмена"""
        return self.data.get("suitable_cards", {})
    
    def get_statistics(self) -> Dict[str, Any]:
        """Получает статистику по всем картам"""
        my_cards_count = len(self.data.get("my_cards", []))
        boost_cards_count = len(self.data.get("boost_cards", []))
        other_users_count = len(self.data.get("other_users_cards", {}))
        other_cards_count = sum(len(cards) for cards in self.data.get("other_users_cards", {}).values())
        suitable_cards_count = len(self.data.get("suitable_cards", {}).get("cards", []))
        
        return {
            "my_cards": my_cards_count,
            "boost_cards": boost_cards_count,
            "other_users": other_users_count,
            "other_cards": other_cards_count,
            "suitable_cards": suitable_cards_count,
            "total_cards": my_cards_count + boost_cards_count + other_cards_count,
            "last_updated": self.data.get("metadata", {}).get("last_updated"),
            "storage_file": str(self.storage_file)
        }
    
    def cleanup_old_data(self, days: int = 7) -> None:
        """Удаляет старые данные"""
        from datetime import timedelta
        cutoff_date = datetime.now() - timedelta(days=days)
        cutoff_str = cutoff_date.isoformat()
        
        # Чистим карты других пользователей
        users_to_remove = []
        for user_id, cards in self.data.get("other_users_cards", {}).items():
            if cards and isinstance(cards, list) and len(cards) > 0:
                card_date = cards[0].get("parsed_at", "")
                if card_date < cutoff_str:
                    users_to_remove.append(user_id)
        
        for user_id in users_to_remove:
            del self.data["other_users_cards"][user_id]
            # Удаляем соответствующие метаданные
            meta_key = f"user_{user_id}_updated"
            if meta_key in self.data["metadata"]:
                del self.data["metadata"][meta_key]
        
        if users_to_remove:
            self._save_data()
            print(f"🧹 Удалены старые данные для {len(users_to_remove)} пользователей")


# Глобальный экземпляр хранилища
_storage_instance = None

def get_card_storage(profiles_dir: pathlib.Path) -> UnifiedCardStorage:
    """Получает единственный экземпляр хранилища карт"""
    global _storage_instance
    if _storage_instance is None or _storage_instance.profiles_dir != profiles_dir:
        _storage_instance = UnifiedCardStorage(profiles_dir)
    return _storage_instance