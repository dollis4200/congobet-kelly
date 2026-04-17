from __future__ import annotations

from dataclasses import dataclass
from math import exp, factorial
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

TEAM_NAMES = [
    'Liverpool', 'London Blues', 'Brentford', 'London Reds', 'Fulham', 'Manchester Blue', 'Everton',
    'Newcastle', 'West Ham', 'C. Palace', 'Bournemouth', 'Wolverhampton', 'Spurs', 'Manchester Red',
    'A. Villa', 'Burnley', 'Brighton', 'N. Forest', 'Leeds', 'Sunderland'
]


@dataclass
class KellyDecision:
    market: str
    selection: str
    odds: float
    estimated_probability: float
    edge: float
    kelly_fraction: float
    stake: float


def historical_df_from_db(db: Dict) -> pd.DataFrame:
    rows: List[Dict] = []
    hist = (db.get('historical_gng_db') or {}).get('affiches', {})
    for affiche_key, affiche in hist.items():
        for match_key, match in (affiche.get('matches') or {}).items():
            hs = pd.to_numeric(match.get('home_score'), errors='coerce')
            aw = pd.to_numeric(match.get('away_score'), errors='coerce')
            if pd.isna(hs) or pd.isna(aw):
                continue
            rows.append({
                'affiche_key': affiche_key,
                'date': affiche.get('date'),
                'round_time': affiche.get('round_time'),
                'matchday': affiche.get('matchday'),
                'match_key': match_key,
                'home_team': match.get('home_team'),
                'away_team': match.get('away_team'),
                'home_score': int(hs),
                'away_score': int(aw),
                'gng_result': match.get('gng_result') or ('Oui' if hs > 0 and aw > 0 else 'Non'),
            })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df['dt'] = pd.to_datetime(df['date'] + ' ' + df['round_time'], errors='coerce')
    df['home_win'] = (df['home_score'] > df['away_score']).astype(int)
    df['draw'] = (df['home_score'] == df['away_score']).astype(int)
    df['away_win'] = (df['home_score'] < df['away_score']).astype(int)
    df['btts_yes'] = ((df['home_score'] > 0) & (df['away_score'] > 0)).astype(int)
    return df.sort_values(['dt', 'match_key']).reset_index(drop=True)


def _team_history(df: pd.DataFrame, team: str, n: int = 12) -> pd.DataFrame:
    sub = df[(df['home_team'] == team) | (df['away_team'] == team)].copy()
    return sub.sort_values('dt').tail(n)


def _points_from_row(row: pd.Series, team: str) -> int:
    if row['home_team'] == team:
        if row['home_score'] > row['away_score']:
            return 3
        if row['home_score'] == row['away_score']:
            return 1
        return 0
    if row['away_team'] == team:
        if row['away_score'] > row['home_score']:
            return 3
        if row['away_score'] == row['home_score']:
            return 1
        return 0
    return 0


def team_form_points(df: pd.DataFrame, team: str, n: int = 6) -> float:
    hist = _team_history(df, team, n=n)
    if hist.empty:
        return 1.0
    pts = sum(_points_from_row(r, team) for _, r in hist.iterrows())
    return pts / max(len(hist), 1)


def team_goal_profile(df: pd.DataFrame, team: str, n: int = 12) -> Dict[str, float]:
    hist = _team_history(df, team, n=n)
    if hist.empty:
        return {'gf': 1.2, 'ga': 1.2, 'gf_home': 1.2, 'ga_home': 1.2, 'gf_away': 1.0, 'ga_away': 1.0}
    gf, ga, gf_home, ga_home, gf_away, ga_away = [], [], [], [], [], []
    for _, r in hist.iterrows():
        if r['home_team'] == team:
            gf.append(r['home_score']); ga.append(r['away_score'])
            gf_home.append(r['home_score']); ga_home.append(r['away_score'])
        else:
            gf.append(r['away_score']); ga.append(r['home_score'])
            gf_away.append(r['away_score']); ga_away.append(r['home_score'])
    def avg(vals, default):
        return float(np.mean(vals)) if vals else default
    return {
        'gf': avg(gf, 1.2), 'ga': avg(ga, 1.2),
        'gf_home': avg(gf_home, avg(gf, 1.2)), 'ga_home': avg(ga_home, avg(ga, 1.2)),
        'gf_away': avg(gf_away, avg(gf, 1.0)), 'ga_away': avg(ga_away, avg(ga, 1.0)),
    }


def standings_strength(standings_df: pd.DataFrame, team: str) -> float:
    if standings_df is None or standings_df.empty or team not in set(standings_df['team']):
        return 1.0
    row = standings_df.loc[standings_df['team'] == team].iloc[0]
    pts = float(row['points'])
    max_pts = float(standings_df['points'].max()) if len(standings_df) else 50.0
    return 0.8 + 0.4 * (pts / max(max_pts, 1.0))


def poisson_probs(lambda_home: float, lambda_away: float, max_goals: int = 7) -> Dict[str, float]:
    hg = [exp(-lambda_home) * (lambda_home ** k) / factorial(k) for k in range(max_goals + 1)]
    ag = [exp(-lambda_away) * (lambda_away ** k) / factorial(k) for k in range(max_goals + 1)]
    p_home = p_draw = p_away = p_btts_yes = 0.0
    for i, ph in enumerate(hg):
        for j, pa in enumerate(ag):
            p = ph * pa
            if i > j:
                p_home += p
            elif i == j:
                p_draw += p
            else:
                p_away += p
            if i > 0 and j > 0:
                p_btts_yes += p
    p_btts_no = 1.0 - p_btts_yes
    return {
        '1': p_home,
        'X': p_draw,
        '2': p_away,
        'Oui': p_btts_yes,
        'Non': p_btts_no,
    }


def estimate_match_probabilities(hist_df: pd.DataFrame, standings_df: pd.DataFrame, home_team: str, away_team: str) -> Dict[str, float]:
    if hist_df is None or hist_df.empty:
        return {'1': 0.42, 'X': 0.26, '2': 0.32, 'Oui': 0.52, 'Non': 0.48}

    base_home = float(hist_df['home_score'].mean()) if len(hist_df) else 1.45
    base_away = float(hist_df['away_score'].mean()) if len(hist_df) else 1.15

    hp = team_goal_profile(hist_df, home_team, n=12)
    ap = team_goal_profile(hist_df, away_team, n=12)
    form_home = team_form_points(hist_df, home_team, n=6)
    form_away = team_form_points(hist_df, away_team, n=6)
    strength_home = standings_strength(standings_df, home_team)
    strength_away = standings_strength(standings_df, away_team)

    lambda_home = base_home * (hp['gf_home'] / max(base_home, 0.3)) * (ap['ga_away'] / max(base_home, 0.3))
    lambda_away = base_away * (ap['gf_away'] / max(base_away, 0.3)) * (hp['ga_home'] / max(base_away, 0.3))

    lambda_home *= (strength_home / max(strength_away, 0.5)) ** 0.35
    lambda_away *= (strength_away / max(strength_home, 0.5)) ** 0.35

    form_delta = (form_home - form_away) / 6.0
    lambda_home *= 1.0 + 0.12 * form_delta
    lambda_away *= 1.0 - 0.12 * form_delta

    lambda_home = float(np.clip(lambda_home, 0.35, 3.4))
    lambda_away = float(np.clip(lambda_away, 0.25, 3.2))

    probs = poisson_probs(lambda_home, lambda_away, max_goals=7)
    probs['lambda_home'] = lambda_home
    probs['lambda_away'] = lambda_away
    probs['form_home_ppm'] = float(form_home)
    probs['form_away_ppm'] = float(form_away)
    return probs


def kelly_fraction(prob: float, odds: float) -> float:
    if pd.isna(prob) or pd.isna(odds) or odds <= 1.0:
        return 0.0
    b = odds - 1.0
    q = 1.0 - prob
    f = (b * prob - q) / b
    return max(0.0, float(f))


def best_market_decisions(match_row: Dict, probs: Dict[str, float], bankroll: float, kelly_scale: float, min_stake: float, max_stake_pct: float) -> List[KellyDecision]:
    decisions: List[KellyDecision] = []
    one_x_two = {
        '1': match_row.get('odds_home'),
        'X': match_row.get('odds_draw'),
        '2': match_row.get('odds_away'),
    }
    gng = {
        'Oui': match_row.get('odds_gng_oui'),
        'Non': match_row.get('odds_gng_non'),
    }
    for market, mapping in [('1X2', one_x_two), ('G/NG', gng)]:
        market_decisions = []
        for selection, odds in mapping.items():
            if odds is None or pd.isna(odds) or float(odds) <= 1.0:
                continue
            p = float(probs.get(selection, 0.0))
            edge = p * float(odds) - 1.0
            raw_kelly = kelly_fraction(p, float(odds))
            stake = min(bankroll * raw_kelly * kelly_scale, bankroll * max_stake_pct)
            if edge > 0 and stake >= min_stake:
                market_decisions.append(KellyDecision(market, selection, float(odds), p, edge, raw_kelly * kelly_scale, float(stake)))
        if market_decisions:
            decisions.append(sorted(market_decisions, key=lambda x: x.edge, reverse=True)[0])
    return decisions


def standings_df_from_snapshot(snapshot: Dict) -> pd.DataFrame:
    rows = snapshot.get('standings', []) if snapshot else []
    df = pd.DataFrame(rows)
    if not df.empty and 'points' in df.columns:
        df['points'] = pd.to_numeric(df['points'], errors='coerce')
    return df


def bankroll_curve_df(db: Dict) -> pd.DataFrame:
    df = pd.DataFrame(db.get('bankroll_history', []))
    if not df.empty:
        df['ts'] = pd.to_datetime(df['ts'], errors='coerce')
    return df
