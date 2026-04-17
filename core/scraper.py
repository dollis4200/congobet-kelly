from __future__ import annotations

import re
import subprocess
import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional

from playwright.sync_api import sync_playwright

from .analytics import TEAM_NAMES

URL = 'https://www.congobet.net/virtual/category/instant-league/8035/matches'
TEAM_SET = set(TEAM_NAMES)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class CongoBetScraper:
    def __init__(self, headless: bool = True, timeout_ms: int = 120000):
        self.headless = headless
        self.timeout_ms = timeout_ms

    def _launch_browser(self, playwright):
        try:
            return playwright.chromium.launch(headless=self.headless)
        except Exception:
            subprocess.run([sys.executable, '-m', 'playwright', 'install', 'chromium'], check=False)
            return playwright.chromium.launch(headless=self.headless)

    def _clean_lines(self, text: str) -> List[str]:
        return [ln.strip() for ln in text.splitlines() if ln.strip()]

    def _open_page(self, page) -> None:
        page.goto(URL, wait_until='domcontentloaded', timeout=self.timeout_ms)
        page.wait_for_timeout(7000)
        for txt in ['Accepter', 'Accept', "J'accepte", 'Tout accepter', 'OK']:
            try:
                btn = page.get_by_role('button', name=txt)
                if btn.count() > 0:
                    btn.first.click(timeout=1500)
                    page.wait_for_timeout(500)
                    break
            except Exception:
                pass

    def _go_tab(self, page, label: str) -> None:
        loc = page.locator('div.tab-picker > div', has_text=label).first
        loc.click(force=True, timeout=5000)
        page.wait_for_timeout(1500)

    def _round_items(self, page) -> List[Dict]:
        items = []
        loc = page.locator('hg-instant-league-round-picker li')
        for i in range(loc.count()):
            item = loc.nth(i)
            text = item.inner_text().strip()
            klass = item.get_attribute('class') or ''
            items.append({'index': i, 'text': text, 'active': 'active' in klass, 'class': klass})
        return items

    def _choose_target_round(self, rounds: List[Dict], min_seconds: int) -> Dict:
        if not rounds:
            return {'index': 0, 'label': None, 'reason': 'no-rounds'}
        first = rounds[0]['text']
        if re.fullmatch(r'\d{2}:\d{2}', first):
            mm, ss = map(int, first.split(':'))
            countdown = mm * 60 + ss
            if countdown >= min_seconds:
                return {'index': 0, 'label': first, 'reason': 'countdown-ok', 'seconds_to_start': countdown}
            if len(rounds) > 1:
                return {'index': 1, 'label': rounds[1]['text'], 'reason': 'countdown-too-low', 'seconds_to_start': countdown}
        if 'Live' in first or "'" in first:
            if len(rounds) > 1:
                return {'index': 1, 'label': rounds[1]['text'], 'reason': 'live-active'}
        return {'index': 0, 'label': first, 'reason': 'fallback'}

    def _click_round(self, page, round_index: int) -> str:
        loc = page.locator('hg-instant-league-round-picker li').nth(round_index)
        try:
            label = loc.locator('.time').inner_text().strip()
        except Exception:
            label = loc.inner_text().strip()
        loc.click(force=True, timeout=5000)
        page.wait_for_timeout(1200)
        return label

    def _ensure_market(self, page, market: str) -> None:
        if market == '1X2':
            page.locator('button', has_text='1X2').first.click(force=True, timeout=5000)
            page.wait_for_timeout(800)
            return
        if market == 'G/NG':
            try:
                page.locator('hg-select .selected').first.click(force=True, timeout=5000)
                page.wait_for_timeout(500)
            except Exception:
                pass
            try:
                page.locator('div.option', has_text='G/NG').first.click(force=True, timeout=5000)
            except Exception:
                page.evaluate("""
                () => {
                  const opt = [...document.querySelectorAll('div.option')].find(el => (el.innerText || '').trim() === 'G/NG');
                  if (opt) opt.click();
                }
                """)
            page.wait_for_timeout(1200)

    def _parse_match_cards(self, page, market: str) -> List[Dict]:
        rows = []
        cards = page.locator('div.match')
        for i in range(cards.count()):
            card = cards.nth(i)
            teams = [card.locator('.teams span').nth(j).inner_text().strip() for j in range(card.locator('.teams span').count())]
            odds = [card.locator('.odds').nth(j).inner_text().strip().replace(',', '.') for j in range(card.locator('.odds').count())]
            if len(teams) < 2:
                continue
            row = {'home_team': teams[0], 'away_team': teams[1], 'market': market}
            try:
                odds = [float(x) for x in odds]
            except Exception:
                odds = []
            if market == '1X2' and len(odds) >= 3:
                row.update({'odds_home': odds[0], 'odds_draw': odds[1], 'odds_away': odds[2]})
            elif market == 'G/NG' and len(odds) >= 2:
                row.update({'odds_gng_oui': odds[0], 'odds_gng_non': odds[1]})
            else:
                continue
            rows.append(row)
        return rows

    def scrape_match_markets(self, page, min_seconds: int = 20) -> Dict:
        self._go_tab(page, 'MATCHS')
        rounds = self._round_items(page)
        target = self._choose_target_round(rounds, min_seconds=min_seconds)
        label = self._click_round(page, target['index'])
        self._ensure_market(page, '1X2')
        one_x_two = self._parse_match_cards(page, '1X2')
        self._ensure_market(page, 'G/NG')
        gng = self._parse_match_cards(page, 'G/NG')

        merged = {}
        for row in one_x_two + gng:
            key = f"{label}|{row['home_team']}|{row['away_team']}"
            merged.setdefault(key, {'round_time': label, 'home_team': row['home_team'], 'away_team': row['away_team']})
            merged[key].update(row)
        return {
            'snapshot_ts': utc_now_iso(),
            'rounds': rounds,
            'target_round': {**target, 'selected_label': label},
            'matches': list(merged.values()),
        }

    def scrape_standings(self, page) -> Dict:
        self._go_tab(page, 'CLASSEMENT')
        body = page.locator('body').inner_text()
        lines = self._clean_lines(body)
        rows = []
        i = 0
        while i < len(lines):
            if lines[i].isdigit() and i + 2 < len(lines) and lines[i + 1] in TEAM_SET and re.fullmatch(r'\d+', lines[i + 2]):
                rows.append({'rank': int(lines[i]), 'team': lines[i + 1], 'points': int(lines[i + 2])})
                i += 3
                continue
            i += 1
        return {'snapshot_ts': utc_now_iso(), 'standings': rows, 'raw_text': body[:5000]}

    def _parse_results_text(self, text: str) -> List[Dict]:
        lines = self._clean_lines(text)
        results = []
        i = 0
        current_header = None
        current_matchday = None
        current_round_time = None
        while i < len(lines):
            line = lines[i]
            if line.startswith('Journée '):
                current_header = line
                md = re.search(r'Journée\s+(\d+)', line)
                tm = re.search(r'(\d{2}:\d{2})', line)
                current_matchday = int(md.group(1)) if md else None
                current_round_time = tm.group(1) if tm else None
                i += 1
                continue
            if line in TEAM_SET:
                home = line
                i += 1
                # home events until score
                while i < len(lines) and not re.fullmatch(r'\d+:\d+', lines[i]):
                    if lines[i].startswith('Journée ') or lines[i] in TEAM_SET:
                        break
                    i += 1
                if i >= len(lines) or not re.fullmatch(r'\d+:\d+', lines[i]):
                    continue
                score = lines[i]
                i += 1
                if i < len(lines) and lines[i].startswith('MT:'):
                    halftime = lines[i]
                    i += 1
                else:
                    halftime = None
                if i >= len(lines) or lines[i] not in TEAM_SET:
                    continue
                away = lines[i]
                i += 1
                away_events = []
                while i < len(lines) and not lines[i].startswith('Journée ') and lines[i] not in TEAM_SET and lines[i] != 'PANIER':
                    away_events.append(lines[i])
                    i += 1
                hs, aw = [int(x) for x in score.split(':')]
                results.append({
                    'round_label': current_header,
                    'matchday': current_matchday,
                    'round_time': current_round_time,
                    'home_team': home,
                    'away_team': away,
                    'score': score,
                    'home_score': hs,
                    'away_score': aw,
                    'ht': halftime,
                    'result_1x2': '1' if hs > aw else ('X' if hs == aw else '2'),
                    'result_gng': 'Oui' if hs > 0 and aw > 0 else 'Non',
                    'away_events': away_events,
                })
                continue
            i += 1
        return results

    def scrape_results(self, page) -> Dict:
        self._go_tab(page, 'RÉSULTATS')
        body = page.locator('body').inner_text()
        parsed = self._parse_results_text(body)
        return {'snapshot_ts': utc_now_iso(), 'results': parsed, 'raw_text': body[:10000]}

    def scrape_all(self, min_seconds: int = 20, include_results: bool = True, include_standings: bool = True) -> Dict:
        with sync_playwright() as p:
            browser = self._launch_browser(p)
            context = browser.new_context(viewport={'width': 1440, 'height': 2200}, locale='fr-FR')
            page = context.new_page()
            self._open_page(page)
            market_snapshot = self.scrape_match_markets(page, min_seconds=min_seconds)
            standings_snapshot = self.scrape_standings(page) if include_standings else None
            results_snapshot = self.scrape_results(page) if include_results else None
            browser.close()
            return {
                'source_url': URL,
                'scraped_at_utc': utc_now_iso(),
                'market_snapshot': market_snapshot,
                'standings_snapshot': standings_snapshot,
                'results_snapshot': results_snapshot,
            }
