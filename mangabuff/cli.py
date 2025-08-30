import argparse
import json
import pathlib
from typing import Optional, Dict, Any, List

from mangabuff.config import BASE_URL
from mangabuff.profiles.store import ProfileStore
from mangabuff.auth.login import update_profile_cookies
from mangabuff.services.club import find_boost_card_info, owners_and_wanters_counts
from mangabuff.services.inventory import ensure_own_inventory
from mangabuff.services.owners import iter_online_owners_by_pages
from mangabuff.services.trade import send_trades_to_online_owners
from mangabuff.services.har import analyze_har

def load_target_card_from_file(profiles_dir: pathlib.Path, card_file: Optional[str] = None, debug: bool=False) -> Optional[Dict[str, Any]]:
    import random
    from mangabuff.utils.text import extract_card_id_from_href
    path: Optional[pathlib.Path] = None
    if card_file:
        p = pathlib.Path(card_file)
        if p.exists():
            path = p
    if not path:
        files = sorted(
            profiles_dir.glob("card_*_from_*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not files:
            return None
        path = files[0]

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
        for k in ("href", "link", "url", "permalink"):
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

    return {"card_id": int(card_id), "name": name or "", "rank": rank or "", "file": str(path)}

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
    parser.add_argument("--trade_card_file", type=str, default="", help="Путь к card_*_from_*.json")
    parser.add_argument("--use_api", type=int, default=1, help="1 = использовать API /trades/create, 0 = форму")
    parser.add_argument("--analyze_har", type=str, default="", help="Путь к HAR-файлу для анализа")

    args = parser.parse_args()

    store = ProfileStore(args.dir)
    profile_path = store.path_for(args.name)

    # Создаём профиль при необходимости
    profile = store.read_by_path(profile_path) or store.default_profile(user_id=str(args.id or "" ), club_name=args.club_name or "")
    store.write_by_path(profile_path, profile)

    # Авторизация/обновление cookies
    ok, info = update_profile_cookies(profile, args.email, args.password, debug=args.debug, skip_check=args.skip_check)
    if not ok:
        msg = info.get("message", "auth error")
        print(f"❌ Ошибка авторизации: {msg}")
        if info.get("html_preview"):
            print(info["html_preview"])
        return
    store.write_by_path(profile_path, profile)
    print(f"{args.name}: ✅ Авторизация ок")

    # Boost-карта (опционально)
    if args.boost_url:
        res = find_boost_card_info(profile, profile_path.parent, args.boost_url, debug=args.debug)
        if res:
            card_id, out_path = res
            owners_cnt, wanters_cnt = owners_and_wanters_counts(profile, card_id, debug=args.debug)
            print(f"✅ Клубная карта {card_id} сохранена в {out_path}")
            print(f"Владельцев: {owners_cnt}, желающих: {wanters_cnt}")
        else:
            print("❌ Не удалось получить информацию о клубной карте")

    # HAR-аналитика (опционально)
    if args.analyze_har:
        top = analyze_har(args.analyze_har, debug=args.debug)
        print("Топ путей из HAR:")
        for k, v in top.items():
            print(f"{k} -> {v}")

    # Определение целевой карты для рассылки обменов
    target_card: Optional[Dict[str, Any]] = None
    if args.trade_card_id and args.trade_rank:
        target_card = {"card_id": int(args.trade_card_id), "name": args.trade_card_name or "", "rank": args.trade_rank}
    else:
        target_card = load_target_card_from_file(profile_path.parent, args.trade_card_file or None, debug=args.debug)

    if not target_card:
        print("ℹ️ Целевая карта не задана. Рассылка обменов пропущена.")
        return

    if args.trade_send_online:
        # инвентарь текущего пользователя
        try:
            inv_path = ensure_own_inventory(profile_path, profile, debug=args.debug)
        except Exception as e:
            print(f"❌ Нет инвентаря: {e}")
            return
        try:
            with inv_path.open("r", encoding="utf-8") as f:
                my_cards: List[Dict[str, Any]] = json.load(f)
        except Exception as e:
            print(f"❌ Ошибка чтения инвентаря {inv_path}: {e}")
            return

        from mangabuff.services.owners import iter_online_owners_by_pages
        card_id = int(target_card["card_id"])
        owners_iter = iter_online_owners_by_pages(profile, card_id, max_pages=args.trade_pages or 0, debug=args.debug)
        stats = send_trades_to_online_owners(
            profile_data=profile,
            target_card=target_card,
            owners_iter=owners_iter,
            my_cards=my_cards,
            dry_run=bool(args.trade_dry_run),
            use_api=bool(args.use_api),
            debug=args.debug,
        )
        print("Результат рассылки:", stats)
    else:
        print("ℹ️ --trade_send_online не указан — рассылка не выполнена.")

if __name__ == "__main__":
    main()