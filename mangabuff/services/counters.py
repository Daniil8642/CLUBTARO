from typing import List, Dict
import requests
from bs4 import BeautifulSoup

from mangabuff.http.http_utils import build_session_from_profile, get
from mangabuff.utils.html import with_page, extract_last_page_number, select_any

def count_by_last_page(profile_data: Dict, url: str, selectors: List[str], per_page: int, debug: bool = False) -> int:
    session = build_session_from_profile(profile_data)
    try:
        r1 = get(session, with_page(url, 1))
    except requests.RequestException:
        return 0
    if r1.status_code != 200:
        return 0

    soup1 = BeautifulSoup(r1.text, "html.parser")
    lst1 = select_any(soup1, selectors)
    count1 = len(lst1)
    last_page = extract_last_page_number(soup1)
    if last_page <= 1:
        return count1

    try:
        rl = get(session, with_page(url, last_page))
    except requests.RequestException:
        return (last_page - 1) * per_page
    if rl.status_code != 200:
        return (last_page - 1) * per_page

    soupl = BeautifulSoup(rl.text, "html.parser")
    lstl = select_any(soupl, selectors)
    countl = len(lstl)
    return (last_page - 1) * per_page + countl