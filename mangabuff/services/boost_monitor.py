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
from mangabuff.services.card_storage import get_card_storage


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
        
        # Единое хранилище карт
        self.card_storage = get_card_storage(profiles_dir)
        
        # Загружаем начальную карту если есть
        self._load_current_card()
    
    def _load_current_card(self) -> None:
        """Загружает текущую карту из единого хранилища"""
        current_boost_card = self.card_storage.get_current_boost_card()
        if current_boost_card:
            self.current_card_id = current_boost_card.get("card_id")
            if self.debug:
                print(f"[MONITOR] Loaded current card ID from storage: {self.current_card_id}")
        
        # Fallback на старый файл для совместимости
        card_file = self.profiles_dir / "card_for_boost.json"
        if not self.current_card_id and card_file.exists():
            try:
                with card_file.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                self.current_card_id = data.get("card_id")
                if self.debug:
                    print(f"[MONITOR] Loaded current card ID from legacy file: {self.current_card_id}")
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
                if self.debug:
                    print(f"[MONITOR] Failed to get boost page: HTTP {resp.status_code}")
                return 0, None, False, False
        except requests.RequestException as e:
            if self.debug:
                print(f"[MONITOR] Failed to get boost page: {e}")
            return 0, None, False, False
        
        soup = BeautifulSoup(resp.text, "html.parser")
        
        # 1. Количество замен - ищем текст вида "0 / 10" или "1 / 10"
        changes = 0
        change_selectors = [
            '.club-boost__change div',
            '.club-boost__change span', 
            '.club-boost__change',
            '[class*="boost"] [class*="change"]',
            '.boost-changes'
        ]
        
        for selector in change_selectors:
            change_el = soup.select_one(selector)
            if change_el:
                text = change_el.get_text(strip=True)
                # Парсим формат "X / Y"
                import re
                match = re.search(r'(\d+)\s*/\s*(\d+)', text)
                if match:
                    changes = int(match.group(1))
                    total_changes = int(match.group(2))
                    if self.debug:
                        print(f"[MONITOR] Found changes text: '{text}' -> {changes}/{total_changes}")
                    break
        
        # 2. ID текущей карты для вклада
        card_id = None
        card_link_selectors = [
            'a.button.button--block[href*="/cards/"]',
            'a[href*="/cards/"][class*="button"]',
            'a[href*="/cards/"]'
        ]
        
        for selector in card_link_selectors:
            card_link = soup.select_one(selector)
            if card_link:
                href = card_link.get("href", "")
                import re
                match = re.search(r'/cards/(\d+)', href)
                if match:
                    card_id = int(match.group(1))
                    if self.debug:
                        print(f"[MONITOR] Found card link: {href} -> ID {card_id}")
                    break
        
        # 3. Кнопка пожертвовать карту - более точная проверка
        can_donate = False
        
        # Ищем кнопку "Пожертвовать карту" по тексту
        donate_buttons = soup.find_all(['button', 'a', 'input'])
        for btn in donate_buttons:
            btn_text = btn.get_text(strip=True).lower()
            btn_value = (btn.get('value') or '').lower()
            btn_title = (btn.get('title') or '').lower()
            
            all_text = f"{btn_text} {btn_value} {btn_title}"
            
            # Более точные ключевые слова для пожертвования
            donate_keywords = [
                'пожертвовать карту',
                'внести вклад', 
                'отдать карту',
                'добавить карту',
                'donate card',
                'submit card'
            ]
            
            if any(keyword in all_text for keyword in donate_keywords):
                # Проверяем, что кнопка не заблокирована
                is_disabled = (
                    btn.get('disabled') or 
                    'disabled' in btn.get('class', []) or
                    btn.get('aria-disabled') == 'true'
                )
                
                if not is_disabled:
                    can_donate = True
                    if self.debug:
                        print(f"[MONITOR] Found donate button: '{btn_text}' (enabled)")
                    break
                else:
                    if self.debug:
                        print(f"[MONITOR] Found donate button but disabled: '{btn_text}'")
        
        # Альтернативная проверка - ищем текст "Могут внести:"
        if not can_donate:
            page_text = soup.get_text()
            
            # Проверяем наличие указателей на возможность внести карту
            donation_indicators = [
                'могут внести:',
                'могу внести',
                'у вас есть эта карта',
                'в вашем инвентаре',
                'можете пожертвовать',
                'можно внести'
            ]
            
            for indicator in donation_indicators:
                if indicator in page_text.lower():
                    # Дополнительная проверка - есть ли список с нашим именем
                    if 'могут внести:' in page_text.lower():
                        # Ищем раздел с пользователями которые могут внести
                        can_donate_section = soup.find(text=re.compile(r'могут внести', re.I))
                        if can_donate_section:
                            # Ищем аватары или имена пользователей в этом разделе
                            parent = can_donate_section.parent
                            if parent:
                                avatars = parent.find_all(['img', 'div'], class_=re.compile(r'avatar|user|profile'))
                                if avatars:
                                    can_donate = True
                                    if self.debug:
                                        print(f"[MONITOR] Found donation possibility in 'Могут внести' section")
                                    break
                    else:
                        can_donate = True
                        if self.debug:
                            print(f"[MONITOR] Found donation indicator: '{indicator}'")
                        break
        
        # 4. Кнопка "Найти карту" 
        has_find_button = False
        find_buttons = soup.find_all(['button', 'a'])
        for btn in find_buttons:
            btn_text = btn.get_text(strip=True).lower()
            find_keywords = ['найти карту', 'найти', 'find card', 'search card']
            if any(keyword in btn_text for keyword in find_keywords):
                has_find_button = True
                break
        
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
                    success_keywords = ['внесли вклад', 'успеш', 'принят', 'пожертвован', 'добавлен']
                    if any(word in message for word in success_keywords):
                        print(f"✅ Карта успешно пожертвована в клуб! ({result.get('message', 'OK')})")
                        return True
                except Exception:
                    # Если не JSON, проверяем текст
                    text = resp.text.lower()
                    success_keywords = ['успеш', 'внесли', 'пожертвован', 'добавлен']
                    if any(word in text for word in success_keywords):
                        print(f"✅ Карта успешно пожертвована в клуб!")
                        return True
                
                print(f"❌ Не удалось пожертвовать карту: {resp.status_code}")
                if self.debug:
                    print(f"[MONITOR] Response text: {resp.text[:500]}")
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
                time.sleep(3)  # Даем время серверу обновиться
                
                # Парсим новую карту для вклада
                print(f"🔍 Получаем новую карту для вклада...")
                res = find_boost_card_info(self.profile_data, self.profiles_dir, self.boost_url, debug=self.debug)
                
                if res:
                    new_card_id, out_path = res
                    self.current_card_id = new_card_id
                    
                    # Читаем данные новой карты и сохраняем в единое хранилище
                    try:
                        with out_path.open("r", encoding="utf-8") as f:
                            card_data = json.load(f)
                        
                        # Сохраняем в единое хранилище
                        self.card_storage.save_boost_card(card_data)
                        
                        print(f"✅ Новая карта для вклада:")
                        print(f"   Название: {card_data.get('name', '')}")
                        print(f"   ID: {card_data.get('card_id')} | Ранг: {card_data.get('rank')}")
                        print(f"   Владельцев: {card_data.get('owners_count')} | Желающих: {card_data.get('wanters_count')}")
                    except Exception as e:
                        print(f"✅ Новая карта {new_card_id} загружена (ошибка чтения деталей: {e})")
                    
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
    
    def start_monitoring(self, check_interval: float = 4.0):  # Изменили на 4 секунды
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
                    else:
                        print(f"⚠️ Ошибка мониторинга: {e}")
                
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
    check_interval: float = 4.0,  # Изменили на 4 секунды
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
        check_interval: Интервал проверки в секундах (по умолчанию 4 секунды)
        debug: Режим отладки
    """
    monitor = BoostMonitor(profile_data, profiles_dir, boost_url, debug=debug)
    card_storage = get_card_storage(profiles_dir)
    
    # Запускаем мониторинг
    monitor.start_monitoring(check_interval)
    
    try:
        while True:
            # Проверяем нужно ли приостановить обмены
            if monitor.should_pause_trades():
                print("⏸️  Обмены приостановлены (ожидание донейта)")
                time.sleep(5)
                continue
            
            # Загружаем актуальную карту для обменов из единого хранилища
            current_boost_card = card_storage.get_current_boost_card()
            if not current_boost_card:
                # Fallback на старый файл для совместимости
                card_file = profiles_dir / "card_for_boost.json"
                if card_file.exists():
                    try:
                        with card_file.open("r", encoding="utf-8") as f:
                            current_boost_card = json.load(f)
                        # Сохраняем в новое хранилище
                        card_storage.save_boost_card(current_boost_card)
                    except Exception as e:
                        print(f"❌ Ошибка чтения файла карты: {e}")
                        time.sleep(10)
                        continue
                else:
                    print("❌ Карта для вклада не найдена")
                    time.sleep(10)
                    continue
            
            # Обновляем target_card в аргументах
            trade_kwargs["target_card"] = current_boost_card
            
            # Запускаем обмены
            card_id = current_boost_card.get('card_id', 'Unknown')
            print(f"🚀 Запуск обменов для карты ID={card_id}")
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
                if debug:
                    import traceback
                    traceback.print_exc()
                time.sleep(30)
    
    except KeyboardInterrupt:
        print("\n⛔ Остановка по Ctrl+C")
    finally:
        monitor.stop_monitoring()