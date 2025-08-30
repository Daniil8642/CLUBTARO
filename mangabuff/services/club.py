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

def find_boost_card_info(profile_data: Dict, profiles_dir: pathlib.Path, club_boost_url: str, debug: bool=False) -> Optional[Tuple[int, pathlib.Path]]:
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
    cards_path, got_cards = fetch_all_cards_by_id(profile_data, profiles_dir, user_id, debug=debug)
    if not got_cards:
        return None

    try:
        with cards_path.open("r", encoding="utf-8") as f:
            all_cards = json.load(f)
    except Exception:
        return None

    m = re.search(r"/cards/(\d+)", card_href)
    if not m:
        return None
    card_id = int(m.group(1))

    for card in all_cards:
        if int(card.get("card_id") or 0) == card_id:
            out_path = profiles_dir / f"card_{card_id}_from_{user_id}.json"
            with out_path.open("w", encoding="utf-8") as f:
                json.dump(card, f, ensure_ascii=False, indent=4)
            return card_id, out_path
    return None

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
    return owners_count, wanters_count