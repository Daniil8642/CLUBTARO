import json
import pathlib
from typing import Optional, Dict

from mangabuff.http.http_utils import default_client_headers

class ProfileStore:
    def __init__(self, root_dir: str) -> None:
        self.root = pathlib.Path(root_dir)
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, name: str) -> pathlib.Path:
        return self.root / f"{name}.json"

    def read_by_path(self, path: pathlib.Path) -> Optional[Dict]:
        try:
            if not path.exists():
                return None
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def read(self, name: str) -> Optional[Dict]:
        return self.read_by_path(self.path_for(name))

    def write_by_path(self, path: pathlib.Path, data: Dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        tmp.replace(path)

    def write(self, name: str, data: Dict) -> None:
        self.write_by_path(self.path_for(name), data)

    def default_profile(self, user_id: Optional[str] = None, club_name: Optional[str] = None) -> Dict:
        return {
            "cookie": {
                "XSRF-TOKEN": "",
                "mangabuff_session": "",
                "__ddg9_": "",
                "theme": "light",
            },
            "client_headers": default_client_headers(),
            "id": user_id or "",
            "club_name": club_name or "",
        }