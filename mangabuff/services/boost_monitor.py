# mangabuff/services/boost_monitor.py
import json
import time
import pathlib
from typing import Dict, Optional, Tuple, Any
from datetime import datetime
import threading

import requests
from bs4 import BeautifulSoup

from mangabuff.config import BASE_URL
from mangabuff.http.http_utils import build_session_from_profile, get, post
from mangabuff.services.club import find_boost_card_info


class BoostMonitor:
    """Монитор страницы буста клуба с автоматическим донейтом"""
    
    def __init__(self, profile_data: Dict, profiles_dir: pathlib.Path, boost_url: str, debug: bool = False):
        self.profile_data = profile_data
        self.profiles_dir = profiles_dir
        self.boost_url = boost_url if boost_url.startswith("http") else f"{BASE_URL}{boost_url}"
        self.debug = debug
        
        # Состояние
        self.current_card_id: Optional[int] = None
        self.changes_available: int = 0
        self.can_donate: bool = False
        self.should_stop_trades: bool = False
        self.monitoring: bool = False
        self.last_check: float = 0
        
        # Threading
        self._monitor_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        
        # Загружаем начальную карту если есть
        self._load_current_card()
    
    def _load_current_card(self) -> None:
        """Загружает текущую карту из файла если есть"""
        card_file = self.profiles_dir / "card_for_boost.json"
        if card_file.exists():
            try:
                with card_file.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                self.current_card_id = data.get("card_id")
                if self.debug:
                    print(f"[MONITOR] Loaded current card ID: {self.current_card_id}")
            except Exception as e:
                if self.debug:
                    print(f"[MONITOR] Failed to load current card: {e}")
    
    def parse_boost_page(self) -> Tuple[int, Optional[int], bool, bool]:
        """
        Парсит страницу буста и возвращает:
        - количество доступных замен (club-boost__change)
        - ID текущей карты для вклада
        - есть ли кнопка пожертвовать
        - есть ли кнопка найти карту
        """
        session = build_session_from_profile(self.profile_data)
        
        try:
            resp = get(session, self.boost_url)
            if resp.status_code != 200:
                return 0, None, False, False
        except requests.RequestException as e:
            if self.debug:
                print(f"[MONITOR] Failed to get boost page: {e}")
            return 0, None, False, False
        
        soup = BeautifulSoup(resp.text, "html.parser")
        
        # 1. Количество замен - ищем текст вида "0 / 10" в club-boost__change
        changes = 0
        change_el = soup.select_one('.club-boost__change div, .club-boost__change span')
        if change_el:
            text = change_el.get_text(strip=True)
            # Парсим формат "X / Y"
            import re
            match = re.search(r'(\d+)\s*/\s*\d+', text)
            if match:
                changes = int(match.group(1))
        
        # 2. ID текущей карты для вклада
        card_id = None
        card_link = soup.select_one('a.button.button--block[href*="/cards/"]')
        if card_link:
            href = card_link.get("href", "")
            import re
            match = re.search(r'/cards/(\d+)', href)
            if match:
                card_id = int(match.group(1))
        
        # 3. Кнопка пожертвовать карту
        # Ищем кнопку с текстом "Пожертвовать карту" или подобным
        can_donate = False
        donate_button = None
        
        # Проверяем различные селекторы для кнопки донейта
        button_selectors = [
            'button.club-boost__btn',
            'button[class*="donate"]',
            'button[class*="boost"]',
            '.club-boost__action button',
            'button'  # Fallback на все кнопки
        ]
        
        for selector in button_selectors:
            buttons = soup.select(selector)
            for btn in buttons:
                btn_text = btn.get_text(strip=True).lower()
                if any(word in btn_text for word in ['пожертв', 'отдать', 'внести', 'добав']):
                    donate_button = btn
                    can_donate = True
                    break
            if donate_button:
                break
        
        # Альтернативный способ - проверить есть ли у нас карта в инвентаре
        # по тексту "У вас есть эта карта"
        has_card_text = soup.find(text=lambda t: t and 'у вас есть' in t.lower())
        if has_card_text and not can_donate:
            can_donate = True
        
        # 4. Кнопка "Найти карту" 
        has_find_button = bool(card_link and 'найти' in card_link.get_text(strip=True).lower())
        
        if self.debug:
            print(f"[MONITOR] Parse result: changes={changes}, card_id={card_id}, can_donate={can_donate}, has_find={has_find_button}")
        
        return changes, card_id, can_donate, has_find_button
    
    def donate_card(self) -> bool:
        """Отправляет POST запрос на жертвование карты"""
        session = build_session_from_profile(self.profile_data)
        
        # URL для донейта
        donate_url = f"{BASE_URL}/clubs/boost"
        
        # Заголовки как в HAR файле
        headers = {
            "Accept": "*/*",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "Content-Length": "0",
            "Origin": BASE_URL,
            "Referer": self.boost_url,
            "X-Requested-With": "XMLHttpRequest",
        }
        
        # Добавляем CSRF токен из профиля
        if "X-CSRF-TOKEN" in session.headers:
            headers["X-CSRF-TOKEN"] = session.headers["X-CSRF-TOKEN"]
        elif self.profile_data.get("client_headers", {}).get("x-csrf-token"):
            headers["X-CSRF-TOKEN"] = self.profile_data["client_headers"]["x-csrf-token"]
        
        try:
            # POST запрос с пустым телом
            resp = post(session, donate_url, headers=headers, data="")
            
            if resp.status_code == 200:
                # Проверяем ответ
                try:
                    result = resp.json()
                    if self.debug:
                        print(f"[MONITOR] Donate response: {json.dumps(result, ensure_ascii=False)[:200]}")
                    
                    # Проверяем успешность по сообщению
                    message = result.get("message", "").lower()
                    if "внесли вклад" in message or "успеш" in message or "принят" in message:
                        print(f"✅ Карта успешно пожертвована в клуб!")
                        return True
                except Exception:
                    # Если не JSON, проверяем текст
                    if "успеш" in resp.text.lower():
                        print(f"✅ Карта успешно пожертвована в клуб!")
                        return True
                
                print(f"❌ Не удалось пожертвовать карту: {resp.status_code}")
                return False
            else:
                print(f"❌ Ошибка при жертвовании карты: HTTP {resp.status_code}")
                return False
                
        except requests.RequestException as e:
            print(f"❌ Сетевая ошибка при жертвовании: {e}")
            return False
    
    def check_and_process(self) -> bool:
        """
        Проверяет страницу и обрабатывает изменения.
        Возвращает True если нужно обновить целевую карту для обменов.
        """
        changes, card_id, can_donate, has_find_button = self.parse_boost_page()
        
        self.changes_available = changes
        card_changed = False
        
        # Проверяем изменилась ли карта
        if card_id and card_id != self.current_card_id:
            print(f"🔄 Клубная карта изменилась: {self.current_card_id} → {card_id}")
            self.current_card_id = card_id
            card_changed = True
        
        # Если можем пожертвовать карту
        if can_donate and not self.can_donate:
            print(f"💎 Обнаружена возможность пожертвовать карту!")
            self.can_donate = True
            self.should_stop_trades = True
            
            # Выполняем донейт
            if self.donate_card():
                # После успешного донейта обновляем карту
                time.sleep(2)  # Даем время серверу обновиться
                
                # Парсим новую карту для вклада
                print(f"🔍 Получаем новую карту для вклада...")
                res = find_boost_card_info(self.profile_data, self.profiles_dir, self.boost_url, debug=self.debug)
                
                if res:
                    new_card_id, out_path = res
                    self.current_card_id = new_card_id
                    
                    # Читаем данные новой карты
                    try:
                        with out_path.open("r", encoding="utf-8") as f:
                            card_data = json.load(f)
                        print(f"✅ Новая карта для вклада:")
                        print(f"   Название: {card_data.get('name', '')}")
                        print(f"   ID: {card_data.get('card_id')} | Ранг: {card_data.get('rank')}")
                        print(f"   Владельцев: {card_data.get('owners_count')} | Желающих: {card_data.get('wanters_count')}")
                    except Exception:
                        print(f"✅ Новая карта {new_card_id} загружена")
                    
                    card_changed = True
                    self.should_stop_trades = False  # Можно продолжать обмены
                else:
                    print(f"⚠️ Не удалось получить новую карту для вклада")
            
            self.can_donate = False  # Сбрасываем флаг после попытки
        
        # Логирование состояния (раз в минуту)
        current_time = time.time()
        if current_time - self.last_check > 60:
            print(f"📊 Мониторинг буста: замены={changes}/10, карта={card_id}, донейт={'да' if can_donate else 'нет'}")
            self.last_check = current_time
        
        return card_changed
    
    def start_monitoring(self, check_interval: float = 5.0):
        """Запускает мониторинг в отдельном потоке"""
        if self.monitoring:
            return
        
        self.monitoring = True
        self._stop_event.clear()
        
        def monitor_loop():
            print(f"🔍 Запущен мониторинг страницы буста (интервал: {check_interval}с)")
            
            while not self._stop_event.is_set():
                try:
                    card_changed = self.check_and_process()
                    
                    if card_changed:
                        # Уведомляем об изменении карты
                        print(f"📢 Карта для вклада обновлена! Необходимо перезапустить обмены.")
                    
                except Exception as e:
                    if self.debug:
                        print(f"[MONITOR] Error in monitoring loop: {e}")
                
                # Ждем перед следующей проверкой
                self._stop_event.wait(check_interval)
            
            print(f"🛑 Мониторинг буста остановлен")
        
        self._monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
        self._monitor_thread.start()
    
    def stop_monitoring(self):
        """Останавливает мониторинг"""
        if not self.monitoring:
            return
        
        self.monitoring = False
        self._stop_event.set()
        
        if self._monitor_thread:
            self._monitor_thread.join(timeout=2.0)
            self._monitor_thread = None
    
    def should_pause_trades(self) -> bool:
        """Возвращает True если нужно приостановить отправку обменов"""
        return self.should_stop_trades


def monitor_boost_with_trades(
    profile_data: Dict,
    profiles_dir: pathlib.Path, 
    boost_url: str,
    trade_function,  # Функция для отправки обменов
    trade_kwargs: Dict[str, Any],  # Аргументы для функции обменов
    check_interval: float = 5.0,
    debug: bool = False
) -> None:
    """
    Интегрированная функция мониторинга буста с автоматическим перезапуском обменов.
    
    Args:
        profile_data: Данные профиля
        profiles_dir: Директория профилей
        boost_url: URL страницы буста
        trade_function: Функция для отправки обменов (send_trades_to_online_owners)
        trade_kwargs: Словарь с аргументами для trade_function
        check_interval: Интервал проверки в секундах
        debug: Режим отладки
    """
    monitor = BoostMonitor(profile_data, profiles_dir, boost_url, debug=debug)
    
    # Запускаем мониторинг
    monitor.start_monitoring(check_interval)
    
    try:
        while True:
            # Проверяем нужно ли приостановить обмены
            if monitor.should_pause_trades():
                print("⏸️  Обмены приостановлены (ожидание донейта)")
                time.sleep(5)
                continue
            
            # Загружаем актуальную карту для обменов
            card_file = profiles_dir / "card_for_boost.json"
            if not card_file.exists():
                print("❌ Файл с картой для вклада не найден")
                time.sleep(10)
                continue
            
            try:
                with card_file.open("r", encoding="utf-8") as f:
                    target_card = json.load(f)
            except Exception as e:
                print(f"❌ Ошибка чтения карты: {e}")
                time.sleep(10)
                continue
            
            # Обновляем target_card в аргументах
            trade_kwargs["target_card"] = target_card
            
            # Запускаем обмены
            print(f"🚀 Запуск обменов для карты ID={target_card.get('card_id')}")
            try:
                stats = trade_function(**trade_kwargs)
                
                # Если обработали всех владельцев, ждем перед повтором
                if stats.get("owners_seen", 0) == 0:
                    print("💤 Нет доступных владельцев, ожидание 60 секунд...")
                    time.sleep(60)
                else:
                    # Небольшая пауза между циклами
                    time.sleep(5)
                    
            except Exception as e:
                print(f"❌ Ошибка при отправке обменов: {e}")
                time.sleep(30)
    
    except KeyboardInterrupt:
        print("\n⛔ Остановка по Ctrl+C")
    finally:
        monitor.stop_monitoring()