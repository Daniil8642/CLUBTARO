from typing import Any, Dict, List, Optional
from bs4 import BeautifulSoup
from mangabuff.utils.text import safe_int, extract_card_id_from_href, norm_text

def parse_trade_cards_html(html: str) -> List[Dict[str, Any]]:
    soup = BeautifulSoup(html or "", "html.parser")
    items: List[Dict[str, Any]] = []
    candidates = soup.select('[data-id], [data-card-id], .card, [class*="card"], img')
    seen = set()

    for el in candidates:
        inst = el.get("data-id") or el.get("data-instance-id") or el.get("data-instance") or el.get("data-item-id") or None
        if not inst and el.name == "img":
            parent = el.parent
            if parent is not None and hasattr(parent, "attrs"):
                inst = parent.get("data-id") or parent.get("data-instance-id")

        link_el = None
        href = ""
        for sel in ('a[href*="/cards/"]', '[data-href*="/cards/"]', 'a.card-link[href*="/cards/"]'):
            link_el = el.select_one(sel)
            if link_el:
                break
        if link_el:
            href = link_el.get("href") or link_el.get("data-href") or ""

        cid = el.get("data-card-id") or el.get("data-cardid") or None
        if not cid:
            cid = extract_card_id_from_href(href)

        rank = el.get("data-rank") or el.get("data-grade") or ""
        title = ""
        title_el = el.select_one(".card__title, .card-title, [class*='title'], img[alt]")
        if title_el:
            if title_el.name == "img":
                title = title_el.get("alt", "")
            else:
                title = norm_text(title_el.get_text(" ", strip=True))

        unique_key = (inst or cid or href or title)
        if unique_key and unique_key in seen:
            continue
        if unique_key:
            seen.add(unique_key)

        if inst or cid or href or title:
            items.append({
                "id": safe_int(inst) or 0,
                "card_id": safe_int(cid) if cid else None,
                "rank": (rank or "").strip(),
                "title": title,
                "href": href,
            })

    if not items:
        for img in soup.select("img[alt]"):
            parent = img.parent
            inst = parent.get("data-id") if parent else None
            href = ""
            if parent:
                link_el = parent.select_one('a[href*="/cards/"]')
                if link_el:
                    href = link_el.get("href", "")
            cid = extract_card_id_from_href(href)
            items.append({
                "id": safe_int(inst) or 0,
                "card_id": cid or 0,
                "rank": "",
                "title": img.get("alt", ""),
                "href": href,
            })
    return items

def normalize_card_entry(c: Dict[str, Any]) -> Dict[str, Any]:
    cc = dict(c)
    if "id" not in cc:
        for k in ("instance", "instance_id", "instanceId", "card_instance_id", "key", "data-id"):
            if k in cc:
                cc["id"] = cc[k]
                break
    if "card_id" not in cc:
        if isinstance(cc.get("card"), dict):
            cc["card_id"] = cc["card"].get("id")
        else:
            for k in ("cardId", "card_id", "card-id"):
                if k in cc:
                    cc["card_id"] = cc[k]
                    break
    if "rank" not in cc:
        for k in ("rank", "grade", "data-rank"):
            if k in cc:
                cc["rank"] = cc[k]
                break
    if "title" not in cc:
        for k in ("title", "name", "card_name"):
            if k in cc:
                cc["title"] = cc[k]
                break
    return cc

def entry_card_id(c: Dict[str, Any]) -> Optional[int]:
    cid = safe_int(c.get("card_id") or c.get("cardId") or c.get("card-id"))
    if cid:
        return cid
    inner = c.get("card") or {}
    if isinstance(inner, dict):
        cid = safe_int(inner.get("id") or inner.get("card_id"))
        if cid:
            return cid
    for k in ("href", "url", "link", "permalink", "card_url", "path"):
        val = c.get(k)
        if isinstance(val, str):
            found = extract_card_id_from_href(val)
            if found:
                return found
    return None

def entry_instance_id(c: Dict[str, Any]) -> Optional[int]:
    for k in ("id", "instance", "instance_id", "instanceId", "card_instance_id", "key", "data-id"):
        if k in c:
            val = safe_int(c.get(k))
            if val:
                return val
    if isinstance(c.get("id"), dict):
        for k in ("instance", "instance_id", "instanceId"):
            if k in c["id"]:
                val = safe_int(c["id"][k])
                if val:
                    return val
    inner = c.get("card") or {}
    if isinstance(inner, dict):
        for k in ("instance", "instance_id"):
            if k in inner:
                val = safe_int(inner.get(k))
                if val:
                    return val
    return None