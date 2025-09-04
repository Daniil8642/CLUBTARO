import argparse
import json
import pathlib
from typing import Optional, Dict, Any, List
import time

from mangabuff.config import BASE_URL
from mangabuff.profiles.store import ProfileStore
from mangabuff.auth.login import update_profile_cookies
from mangabuff.services.club import find_boost_card_info
from mangabuff.services.inventory import ensure_own_inventory
from mangabuff.services.owners import iter_online_owners_by_pages
from mangabuff.services.trade import send_trades_to_online_owners
from mangabuff.services.har import analyze_har
from mangabuff.services.boost_monitor import BoostMonitor
from mangabuff.services.card_selector import select_suitable_card_for_trade

def load_target_card_from_file(profiles_dir: pathlib.Path, card_file: Optional[str] = None, debug: bool=False) -> Optional[Dict[str, Any]]:
    import random
    from mangabuff.utils.text import extract_card_id_from_href
    path: Optional[pathlib.Path] = None
    
    if card_file:
        p = pathlib.Path(card_file)
        if p.exists():
            path = p
    
    if not path:
        card_for_boost = profiles_dir / "card_for_boost.json"
        if card_for_boost.exists():
            path = card_for_boost
        else:
            files = sorted(
                profiles_dir.glob("card_*_from_*.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if files:
                path = files[0]
            else:
                return None

    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None

    chosen = None
    if isinstance(data, dict):
        if any(k in data for k in ("card_id", "card", "id", "name", "rank")) and not any(isinstance(v, list) for v in data.values()):
            chosen = data
        else:
            candidates = []
            if "cards" in data and isinstance(data["cards"], list):
                candidates = data["cards"]
            else:
                for v in data.values():
                    if isinstance(v, list):
                        candidates = v
                        break
            if candidates:
                chosen = random.choice(candidates)
    elif isinstance(data, list):
        if not data:
            return None
        chosen = random.choice(data)

    if chosen is None:
        return None

    card_block = chosen.get("card") if isinstance(chosen, dict) else None
    card_id = None
    for key in ("card_id", "cardId", "id"):
        if key in (chosen or {}):
            card_id = chosen.get(key)
            break
    if card_id is None and card_block:
        card_id = card_block.get("id")
    if not card_id:
        for k in ("href", "link", "url", "permalink", "card_url"):
            href = chosen.get(k)
            if isinstance(href, str):
                found = extract_card_id_from_href(href)
                if found:
                    card_id = found
                    break
    if not card_id:
        return None

    name = chosen.get("name") or (card_block and card_block.get("name")) or chosen.get("title") or ""
    rank = (chosen.get("rank") or (card_block and card_block.get("rank")) or "").strip()
    wanters_count = chosen.get("wanters_count", 0)
    owners_count = chosen.get("owners_count", 0)

    return {
        "card_id": int(card_id), 
        "name": name or "", 
        "rank": rank or "", 
        "wanters_count": wanters_count,
        "owners_count": owners_count,
        "file": str(path)
    }

def save_suitable_cards(
    profile_data: Dict,
    my_cards: List[Dict[str, Any]], 
    target_card: Dict[str, Any],
    profiles_dir: pathlib.Path,
    debug: bool = False
) -> None:
    """
    Сохраняет карты, подходящие для обмена на целевую карту.
    """
    from mangabuff.services.card_selector import CardWantersCache, get_card_wanters_count
    from mangabuff.parsing.cards import entry_instance_id
    
    cache = CardWantersCache(profiles_dir)
    target_rank = (target_card.get("rank") or "").strip()
    target_wanters = target_card.get("wanters_count", 0)
    
    suitable_cards = []
    
    # Фильтруем карты по рангу
    for card in my_cards:
        card_rank = None
        
        # Получаем ранг из различных мест
        if card.get("rank"):
            card_rank = str(card.get("rank")).strip()
        elif card.get("grade"):
            card_rank = str(card.get("grade")).strip()
        elif isinstance(card.get("card"), dict):
            if card["card"].get("rank"):
                card_rank = str(card["card"].get("rank")).strip()
            elif card["card"].get("grade"):
                card_rank = str(card["card"].get("grade")).strip()
        
        if card_rank == target_rank:
            inst_id = entry_instance_id(card)
            if inst_id:
                card_id = card.get("card_id")
                if not card_id and isinstance(card.get("card"), dict):
                    card_id = card["card"].get("id")
                
                if card_id:
                    # Получаем количество желающих
                    wanters = get_card_wanters_count(profile_data, int(card_id), cache, debug=debug)
                    
                    # Получаем имя карты
                    name = card.get("title") or card.get("name") or ""
                    if not name and isinstance(card.get("card"), dict):
                        name = card["card"].get("name") or card["card"].get("title") or ""
                    
                    suitable_cards.append({
                        "instance_id": inst_id,
                        "card_id": int(card_id),
                        "name": name,
                        "rank": card_rank,
                        "wanters_count": wanters,
                        "suitable": wanters <= target_wanters
                    })
    
    # Сортируем по количеству желающих
    suitable_cards.sort(key=lambda x: x["wanters_count"])
    
    # Сохраняем в файл
    out_path = profiles_dir / "suitable_cards_for_trade.json"
    with out_path.open("w", encoding="utf-8") as f:
        json.dump({
            "target_card": {
                "card_id": target_card.get("card_id"),
                "name": target_card.get("name"),
                "rank": target_card.get("rank"),
                "wanters_count": target_card.get("wanters_count")
            },
            "suitable_cards": suitable_cards,
            "total": len(suitable_cards),
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")
        }, f, ensure_ascii=False, indent=2)
    
    print(f"💾 Сохранено {len(suitable_cards)} подходящих карт в suitable_cards_for_trade.json")
    if suitable_cards:
        best_cards = [c for c in suitable_cards if c["suitable"]][:3]
        if best_cards:
            print(f"   Лучшие варианты (желающих ≤ {target_wanters}):")
            for c in best_cards:
                print(f"   - {c['name']}: {c['wanters_count']} желающих")

def monitor_boost_with_trades_loop(
    profile_data: Dict,
    profiles_dir: pathlib.Path,
    boost_url: str,
    monitor_interval: float = 5.0,
    trade_pages: int = 0,
    dry_run: bool = True,
    debug: bool = False
) -> None:
    """
    Цикл мониторинга буста с автоматическим перезапуском обменов при изменении карты.
    """
    monitor = BoostMonitor(profile_data, profiles_dir, boost_url, debug=debug)
    
    print(f"🔍 Запуск мониторинга буста (интервал: {monitor_interval}с)")
    print(f"📊 Режим: {'DRY-RUN (тестовый)' if dry_run else 'БОЕВОЙ (реальные обмены)'}")
    
    try:
        while True:
            # Проверяем страницу буста
            changes, card_id, can_donate, has_find_button = monitor.parse_boost_page()
            
            # Логируем состояние периодически
            current_time = time.time()
            if current_time - monitor.last_check > 60:
                print(f"📊 Статус буста: замены={changes}/10, карта={card_id}, донейт={'да' if can_donate else 'нет'}")
                monitor.last_check = current_time
            
            # Если можем пожертвовать карту
            if can_donate and not monitor.can_donate:
                print(f"💎 Обнаружена возможность пожертвовать карту!")
                monitor.can_donate = True
                
                # Выполняем донейт
                if monitor.donate_card():
                    time.sleep(3)  # Даем время серверу обновиться
                    
                    # Получаем новую карту для вклада
                    print(f"🔍 Получаем новую карту для вклада...")
                    res = find_boost_card_info(profile_data, profiles_dir, boost_url, debug=debug)
                    
                    if res:
                        new_card_id, out_path = res
                        monitor.current_card_id = new_card_id
                        
                        try:
                            with out_path.open("r", encoding="utf-8") as f:
                                card_data = json.load(f)
                            print(f"✅ Новая карта для вклада:")
                            print(f"   Название: {card_data.get('name', '')}")
                            print(f"   ID: {card_data.get('card_id')} | Ранг: {card_data.get('rank')}")
                            print(f"   Владельцев: {card_data.get('owners_count')} | Желающих: {card_data.get('wanters_count')}")
                        except Exception:
                            print(f"✅ Новая карта {new_card_id} загружена")
                    else:
                        print(f"⚠️ Не удалось получить новую карту для вклада")
                
                monitor.can_donate = False
            
            # Загружаем целевую карту
            target_card = load_target_card_from_file(profiles_dir, debug=debug)
            if not target_card:
                print("⏸️  Ожидание карты для вклада...")
                time.sleep(monitor_interval)
                continue
            
            # Проверяем изменилась ли карта
            if card_id and card_id != monitor.current_card_id:
                print(f"🔄 Карта изменилась: {monitor.current_card_id} → {card_id}")
                monitor.current_card_id = card_id
                
                # Обновляем информацию о карте
                res = find_boost_card_info(profile_data, profiles_dir, boost_url, debug=debug)
                if res:
                    target_card = load_target_card_from_file(profiles_dir, debug=debug)
            
            # Получаем инвентарь
            try:
                inv_path = ensure_own_inventory(pathlib.Path(profiles_dir), profile_data, debug=debug)
                with inv_path.open("r", encoding="utf-8") as f:
                    my_cards = json.load(f)
            except Exception as e:
                print(f"❌ Ошибка получения инвентаря: {e}")
                time.sleep(30)
                continue
            
            # Сохраняем подходящие карты
            save_suitable_cards(profile_data, my_cards, target_card, profiles_dir, debug=debug)
            
            # Запускаем обмены
            print(f"\n🎯 Обмены для карты: ID={target_card['card_id']}, Ранг={target_card['rank']}")
            
            owners_iter = iter_online_owners_by_pages(
                profile_data, 
                int(target_card["card_id"]), 
                max_pages=trade_pages, 
                debug=debug
            )
            
            stats = send_trades_to_online_owners(
                profile_data=profile_data,
                target_card=target_card,
                owners_iter=owners_iter,
                my_cards=my_cards,
                dry_run=dry_run,
                debug=debug,
                profiles_dir=profiles_dir
            )
            
            # Если обработали всех, ждем перед повтором
            if stats.get("owners_seen", 0) == 0:
                print("💤 Нет доступных владельцев, ожидание...")
                time.sleep(60)
            else:
                time.sleep(monitor_interval)
    
    except KeyboardInterrupt:
        print("\n⛔ Остановка по Ctrl+C")

def main():
    parser = argparse.ArgumentParser(description="MangaBuff helper (modular)")
    parser.add_argument("--dir", type=str, default=".", help="Рабочая папка")
    parser.add_argument("--name", required=True, help="Имя профиля")
    parser.add_argument("--email", required=True, help="Email")
    parser.add_argument("--password", required=True, help="Password")
    parser.add_argument("--club_name", help="Название клуба")
    parser.add_argument("--id", type=int, help="user id")
    parser.add_argument("--boost_url", help="boost url")
    parser.add_argument("--debug", action="store_true", help="Debug mode")
    parser.add_argument("--skip_check", action="store_true", help="Skip some checks")
    parser.add_argument("--trade_card_id", type=int, default=0, help="ID карты для обмена")
    parser.add_argument("--trade_card_name", type=str, default="", help="Имя карты для поиска")
    parser.add_argument("--trade_rank", type=str, default="", help="Ранг карты (буква)")
    parser.add_argument("--trade_pages", type=int, default=0, help="Сколько страниц онлайн пользователей обрабатывать (0 = все)")
    parser.add_argument("--trade_send_online", action="store_true", help="Рассылка обменов онлайн владельцам карты")
    parser.add_argument("--trade_dry_run", type=int, default=1, help="1 = dry-run, 0 = реально отправлять")
    parser.add_argument("--trade_card_file", type=str, default="", help="Путь к файлу с карточкой (card_for_boost.json)")
    parser.add_argument("--use_api", type=int, default=1, help="1 = использовать API /trades/create, 0 = форму (не используется)")
    parser.add_argument("--analyze_har", type=str, default="", help="Путь к HAR-файлу для анализа")
    parser.add_argument("--monitor_boost", action="store_true", help="Включить мониторинг буста с автодонейтом")
    parser.add_argument("--monitor_interval", type=float, default=5.0, help="Интервал проверки буста в секундах")

    args = parser.parse_args()

    store = ProfileStore(args.dir)
    profile_path = store.path_for(args.name)

    # Создаём профиль при необходимости
    profile = store.read_by_path(profile_path) or store.default_profile(user_id=str(args.id or "" ), club_name=args.club_name or "")
    store.write_by_path(profile_path, profile)

    # Авторизация/обновление cookies
    ok, info = update_profile_cookies(profile, args.email, args.password, debug=False, skip_check=args.skip_check)
    if not ok:
        msg = info.get("message", "auth error")
        print(f"❌ Ошибка авторизации: {msg}")
        return
    store.write_by_path(profile_path, profile)
    print(f"✅ Авторизация успешна")

    # Если включен мониторинг буста
    if args.monitor_boost:
        if not args.boost_url:
            print("❌ Для мониторинга буста требуется указать --boost_url")
            return
        
        # Сначала получаем начальную карту
        res = find_boost_card_info(profile, profile_path.parent, args.boost_url, debug=args.debug)
        if res:
            card_id, out_path = res
            try:
                with out_path.open("r", encoding="utf-8") as f:
                    card_data = json.load(f)
                print(f"✅ Начальная карта для вклада:")
                print(f"   Название: {card_data.get('name', '')}")
                print(f"   ID: {card_data.get('card_id')} | Ранг: {card_data.get('rank')}")
                print(f"   Владельцев: {card_data.get('owners_count')} | Желающих: {card_data.get('wanters_count')}")
            except Exception:
                print(f"✅ Начальная карта {card_id} загружена")
        
        # Запускаем мониторинг с обменами
        monitor_boost_with_trades_loop(
            profile_data=profile,
            profiles_dir=profile_path.parent,
            boost_url=args.boost_url,
            monitor_interval=args.monitor_interval,
            trade_pages=args.trade_pages or 0,
            dry_run=bool(args.trade_dry_run),
            debug=args.debug
        )
        return

    # Обычный режим без мониторинга
    
    # Boost-карта (опционально)
    if args.boost_url:
        res = find_boost_card_info(profile, profile_path.parent, args.boost_url, debug=args.debug)
        if res:
            card_id, out_path = res
            try:
                with out_path.open("r", encoding="utf-8") as f:
                    card_data = json.load(f)
                print(f"✅ Клубная карта сохранена:")
                print(f"   Название: {card_data.get('name', '')}")
                print(f"   ID карты: {card_data.get('card_id')} | Instance ID: {card_data.get('id')}")
                print(f"   Ранг: {card_data.get('rank')} | Владельцев: {card_data.get('owners_count')} | Желающих: {card_data.get('wanters_count')}")
                print(f"   Файл: {out_path}")
            except Exception:
                print(f"✅ Клубная карта {card_id} сохранена в: {out_path}")

    # HAR-аналитика (опционально)
    if args.analyze_har:
        top = analyze_har(args.analyze_har, debug=args.debug)

    # Определение целевой карты для рассылки обменов
    target_card: Optional[Dict[str, Any]] = None
    if args.trade_card_id and args.trade_rank:
        target_card = {"card_id": int(args.trade_card_id), "name": args.trade_card_name or "", "rank": args.trade_rank}
    else:
        target_card = load_target_card_from_file(profile_path.parent, args.trade_card_file or None, debug=args.debug)

    if not target_card:
        return

    # Получаем инвентарь
    try:
        inv_path = ensure_own_inventory(profile_path, profile, debug=args.debug)
    except Exception as e:
        print(f"❌ Ошибка получения инвентаря: {e}")
        return
    
    try:
        with inv_path.open("r", encoding="utf-8") as f:
            my_cards: List[Dict[str, Any]] = json.load(f)
    except Exception:
        print(f"❌ Ошибка чтения инвентаря")
        return

    # Сохраняем подходящие карты для обмена
    save_suitable_cards(profile, my_cards, target_card, profile_path.parent, debug=args.debug)

    if args.trade_send_online:
        # Запускаем рассылку
        print(f"\n🎯 Целевая карта: ID={target_card['card_id']}, Ранг={target_card['rank']}, Имя={target_card.get('name', '')}")
        print(f"🔍 Поиск владельцев онлайн...\n")
        
        card_id = int(target_card["card_id"])
        owners_iter = iter_online_owners_by_pages(profile, card_id, max_pages=args.trade_pages or 0, debug=args.debug)
        
        stats = send_trades_to_online_owners(
            profile_data=profile,
            target_card=target_card,
            owners_iter=owners_iter,
            my_cards=my_cards,
            dry_run=bool(args.trade_dry_run),
            debug=args.debug,
            profiles_dir=profile_path.parent
        )

if __name__ == "__main__":
    main()