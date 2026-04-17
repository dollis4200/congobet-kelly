import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

APP_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = APP_DIR / 'data'
DATA_DIR.mkdir(parents=True, exist_ok=True)
DEFAULT_DB_PATH = DATA_DIR / 'congobet_streamlit_db.json'
SEED_DB_PATH = DATA_DIR / 'seed_gng_db.json'


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_db() -> Dict[str, Any]:
    return {
        'version': 1,
        'created_at': utc_now_iso(),
        'updated_at': utc_now_iso(),
        'historical_gng_db': {'version': 2, 'affiches': {}, 'lastUpdate': None, 'importCount': 0},
        'standings_snapshots': [],
        'results_snapshots': [],
        'market_snapshots': [],
        'paper_bets': [],
        'bankroll_history': [],
        'settings': {},
        'logs': [],
    }


def _read_json(path: Path) -> Dict[str, Any]:
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_db(path: Optional[Path] = None) -> Dict[str, Any]:
    path = path or DEFAULT_DB_PATH
    if path.exists():
        return _read_json(path)
    db = default_db()
    if SEED_DB_PATH.exists():
        db['historical_gng_db'] = _read_json(SEED_DB_PATH)
        db['updated_at'] = utc_now_iso()
    _write_json(path, db)
    return db


def save_db(db: Dict[str, Any], path: Optional[Path] = None) -> Path:
    path = path or DEFAULT_DB_PATH
    db['updated_at'] = utc_now_iso()
    _write_json(path, db)
    return path


def append_log(db: Dict[str, Any], message: str, level: str = 'INFO') -> None:
    db.setdefault('logs', []).append({'ts': utc_now_iso(), 'level': level, 'message': message})
    db['logs'] = db['logs'][-500:]


def merge_historical_gng(target_db: Dict[str, Any], new_hist_db: Dict[str, Any]) -> int:
    target = target_db.setdefault('historical_gng_db', {'version': 2, 'affiches': {}})
    target.setdefault('affiches', {})
    added = 0
    for affiche_key, affiche in (new_hist_db.get('affiches') or {}).items():
        if affiche_key not in target['affiches']:
            target['affiches'][affiche_key] = affiche
            added += 1
        else:
            tgt_matches = target['affiches'][affiche_key].setdefault('matches', {})
            for match_key, match in (affiche.get('matches') or {}).items():
                if match_key not in tgt_matches:
                    tgt_matches[match_key] = match
    if new_hist_db.get('lastUpdate'):
        target['lastUpdate'] = new_hist_db.get('lastUpdate')
    target['importCount'] = int(target.get('importCount', 0)) + 1
    return added


def upsert_snapshot(db: Dict[str, Any], key: str, payload: Dict[str, Any], max_items: int = 200) -> None:
    bucket = db.setdefault(key, [])
    bucket.append(payload)
    db[key] = bucket[-max_items:]


def record_bankroll(db: Dict[str, Any], bankroll: float, initial_bankroll: float, note: str = '') -> None:
    db.setdefault('bankroll_history', []).append({
        'ts': utc_now_iso(),
        'bankroll': float(bankroll),
        'initial_bankroll': float(initial_bankroll),
        'drawdown_pct': float((bankroll - initial_bankroll) / initial_bankroll) if initial_bankroll else 0.0,
        'note': note,
    })
    db['bankroll_history'] = db['bankroll_history'][-2000:]


def clone_db(db: Dict[str, Any]) -> Dict[str, Any]:
    return deepcopy(db)
