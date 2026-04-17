import asyncio
import csv
import json
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

DEFAULT_URL = "https://www.congobet.net/virtual/category/instant-league/8035/matches"


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def ensure_dirs(base_dir: Path) -> Dict[str, Path]:
    latest_dir = base_dir / "latest"
    history_dir = base_dir / "history"
    logs_dir = base_dir / "logs"
    latest_dir.mkdir(parents=True, exist_ok=True)
    history_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    return {"base": base_dir, "latest": latest_dir, "history": history_dir, "logs": logs_dir}


async def _extract_round(page, round_index: int) -> Dict[str, Any]:
    round_items = page.locator("hg-instant-league-round-picker li")
    item = round_items.nth(round_index)
    time_text = normalize_space(await item.locator(".time").inner_text())

    await item.scroll_into_view_if_needed()
    await item.click(force=True)
    await page.wait_for_timeout(1200)

    match_locator = page.locator("div.match.bet-type-1x2")
    count = await match_locator.count()
    matches: List[Dict[str, str]] = []

    for i in range(count):
        card = match_locator.nth(i)
        team_spans = card.locator(".teams span")
        odd_spans = card.locator("span.odds")

        teams = []
        for j in range(await team_spans.count()):
            txt = normalize_space(await team_spans.nth(j).inner_text())
            if txt:
                teams.append(txt)

        odds = []
        for j in range(await odd_spans.count()):
            txt = normalize_space(await odd_spans.nth(j).inner_text())
            if txt:
                odds.append(txt)

        if len(teams) >= 2 and len(odds) >= 3:
            matches.append(
                {
                    "home": teams[0],
                    "away": teams[1],
                    "odds_1": odds[0],
                    "odds_x": odds[1],
                    "odds_2": odds[2],
                }
            )

    return {"round_index": round_index, "round_time": time_text, "matches": matches}


async def scrape_once(url: str = DEFAULT_URL) -> Dict[str, Any]:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(locale="fr-FR", viewport={"width": 1600, "height": 3000})
        await page.goto(url, wait_until="networkidle", timeout=120000)
        await page.wait_for_timeout(3000)

        round_items = page.locator("hg-instant-league-round-picker li")
        round_count = await round_items.count()

        rounds = []
        for idx in range(round_count):
            rounds.append(await _extract_round(page, idx))

        payload = {
            "source_url": url,
            "title": await page.title(),
            "scraped_at": datetime.utcnow().isoformat() + "Z",
            "round_count": len(rounds),
            "rounds": rounds,
        }
        await browser.close()
        return payload


def flatten_rounds(payload: Dict[str, Any]) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    for rnd in payload.get("rounds", []):
        for match in rnd.get("matches", []):
            rows.append(
                {
                    "heure": rnd.get("round_time", ""),
                    "domicile": match.get("home", ""),
                    "exterieur": match.get("away", ""),
                    "cote_1": match.get("odds_1", ""),
                    "cote_X": match.get("odds_x", ""),
                    "cote_2": match.get("odds_2", ""),
                }
            )
    return rows


def export_payload(payload: Dict[str, Any], out_dir: str = "./runtime_data") -> Dict[str, str]:
    base_dir = Path(out_dir)
    dirs = ensure_dirs(base_dir)
    latest_dir = dirs["latest"]
    history_dir = dirs["history"]

    rows = flatten_rounds(payload)
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

    latest_json = latest_dir / "congobet_1x2_latest.json"
    latest_csv = latest_dir / "congobet_1x2_latest.csv"
    latest_txt = latest_dir / "congobet_1x2_latest.txt"

    history_json = history_dir / f"congobet_1x2_{timestamp}.json"
    history_csv = history_dir / f"congobet_1x2_{timestamp}.csv"
    history_txt = history_dir / f"congobet_1x2_{timestamp}.txt"

    json_text = json.dumps(payload, ensure_ascii=False, indent=2)
    latest_json.write_text(json_text, encoding="utf-8")
    history_json.write_text(json_text, encoding="utf-8")

    def write_csv(path: Path):
        with path.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(["heure", "domicile", "exterieur", "cote_1", "cote_X", "cote_2"])
            for row in rows:
                writer.writerow([
                    row["heure"],
                    row["domicile"],
                    row["exterieur"],
                    row["cote_1"],
                    row["cote_X"],
                    row["cote_2"],
                ])

    write_csv(latest_csv)
    write_csv(history_csv)

    lines = []
    for rnd in payload.get("rounds", []):
        lines.append(f"### Heure: {rnd.get('round_time', '')}")
        for match in rnd.get("matches", []):
            lines.append(
                f"- {match.get('home', '')} vs {match.get('away', '')} | 1={match.get('odds_1', '')} | X={match.get('odds_x', '')} | 2={match.get('odds_2', '')}"
            )
        lines.append("")
    txt_text = "\n".join(lines)
    latest_txt.write_text(txt_text, encoding="utf-8")
    history_txt.write_text(txt_text, encoding="utf-8")

    return {
        "latest_json": str(latest_json.resolve()),
        "latest_csv": str(latest_csv.resolve()),
        "latest_txt": str(latest_txt.resolve()),
        "history_json": str(history_json.resolve()),
        "history_csv": str(history_csv.resolve()),
        "history_txt": str(history_txt.resolve()),
    }


def append_log(out_dir: str, message: str) -> None:
    dirs = ensure_dirs(Path(out_dir))
    log_path = dirs["logs"] / "app.log"
    stamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    with log_path.open("a", encoding="utf-8") as f:
        f.write(f"[{stamp}] {message}\n")


def write_status(out_dir: str, status: Dict[str, Any]) -> None:
    dirs = ensure_dirs(Path(out_dir))
    status_path = dirs["base"] / "status.json"
    status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")


def read_status(out_dir: str = "./runtime_data") -> Dict[str, Any]:
    status_path = Path(out_dir) / "status.json"
    if not status_path.exists():
        return {
            "running": False,
            "interval_seconds": None,
            "last_success": None,
            "last_error": None,
            "iterations": 0,
            "url": DEFAULT_URL,
        }
    return json.loads(status_path.read_text(encoding="utf-8"))


def read_latest_payload(out_dir: str = "./runtime_data") -> Dict[str, Any]:
    latest_path = Path(out_dir) / "latest" / "congobet_1x2_latest.json"
    if not latest_path.exists():
        return {}
    return json.loads(latest_path.read_text(encoding="utf-8"))


def list_history_files(out_dir: str = "./runtime_data") -> List[Path]:
    history_dir = Path(out_dir) / "history"
    if not history_dir.exists():
        return []
    return sorted(history_dir.glob("*"), reverse=True)


def run_scrape_once_sync(url: str = DEFAULT_URL, out_dir: str = "./runtime_data") -> Dict[str, Any]:
    payload = asyncio.run(scrape_once(url))
    exported = export_payload(payload, out_dir)
    append_log(out_dir, f"Scraping manuel terminé. {payload.get('round_count', 0)} tour(s) exporté(s).")
    status = read_status(out_dir)
    status.update(
        {
            "running": status.get("running", False),
            "interval_seconds": status.get("interval_seconds"),
            "last_success": payload.get("scraped_at"),
            "last_error": None,
            "url": url,
        }
    )
    write_status(out_dir, status)
    payload["exported_files"] = exported
    return payload


class ContinuousScraperService:
    def __init__(self):
        self._thread = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, url: str, out_dir: str, interval_seconds: int) -> bool:
        with self._lock:
            if self.is_running():
                return False
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run_loop,
                args=(url, out_dir, interval_seconds),
                daemon=True,
            )
            self._thread.start()
            return True

    def stop(self) -> bool:
        with self._lock:
            if not self.is_running():
                return False
            self._stop_event.set()
            return True

    def _run_loop(self, url: str, out_dir: str, interval_seconds: int) -> None:
        status = read_status(out_dir)
        status.update(
            {
                "running": True,
                "interval_seconds": interval_seconds,
                "last_error": None,
                "url": url,
                "iterations": status.get("iterations", 0),
            }
        )
        write_status(out_dir, status)
        append_log(out_dir, f"Service continu démarré. Intervalle={interval_seconds}s")

        while not self._stop_event.is_set():
            try:
                payload = asyncio.run(scrape_once(url))
                export_payload(payload, out_dir)
                status = read_status(out_dir)
                status.update(
                    {
                        "running": True,
                        "interval_seconds": interval_seconds,
                        "last_success": payload.get("scraped_at"),
                        "last_error": None,
                        "url": url,
                        "iterations": int(status.get("iterations", 0)) + 1,
                    }
                )
                write_status(out_dir, status)
                append_log(
                    out_dir,
                    f"Cycle OK #{status['iterations']} - {payload.get('round_count', 0)} tour(s), {len(flatten_rounds(payload))} match(s).",
                )
            except Exception as exc:
                status = read_status(out_dir)
                status.update(
                    {
                        "running": True,
                        "interval_seconds": interval_seconds,
                        "last_error": str(exc),
                        "url": url,
                    }
                )
                write_status(out_dir, status)
                append_log(out_dir, f"ERREUR scraping: {exc}")

            for _ in range(interval_seconds):
                if self._stop_event.is_set():
                    break
                time.sleep(1)

        status = read_status(out_dir)
        status["running"] = False
        write_status(out_dir, status)
        append_log(out_dir, "Service continu arrêté.")
