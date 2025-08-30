from typing import Dict

def analyze_har(har_path: str, debug: bool=False) -> Dict[str, int]:
    out = {}
    try:
        import json
        with open(har_path, "r", encoding="utf-8", errors="replace") as f:
            h = json.load(f)
        entries = h.get("log", {}).get("entries", [])
        counts = {}
        for e in entries:
            req = e.get("request", {})
            url = req.get("url", "")
            if not url:
                continue
            path = url.split("://", 1)[-1].split("/", 1)
            host = path[0]
            rest = "/" + (path[1] if len(path) > 1 else "")
            key = f"{host}{rest.split('?',1)[0]}"
            counts[key] = counts.get(key, 0) + 1
        top = dict(sorted(counts.items(), key=lambda x: x[1], reverse=True)[:50])
        return top
    except Exception:
        return {}