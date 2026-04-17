from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List

import pandas as pd

from .analytics import best_market_decisions, estimate_match_probabilities, historical_df_from_db, standings_df_from_snapshot
from .storage import append_log, record_bankroll, upsert_snapshot


def market_df_from_snapshot(snapshot: Dict) -> pd.DataFrame:
    rows = (snapshot or {}).get('matches', [])
    df = pd.DataFrame(rows)
    return df


def compute_opportunities(db: Dict, live_snapshot: Dict, bankroll: float, kelly_scale: float, min_stake: float, max_stake_pct: float) -> pd.DataFrame:
    hist_df = historical_df_from_db(db)
    standings_df = standings_df_from_snapshot(live_snapshot.get('standings_snapshot') or {})
    market_df = market_df_from_snapshot(live_snapshot.get('market_snapshot') or {})
    if market_df.empty:
        return pd.DataFrame()

    rows = []
    for _, row in market_df.iterrows():
        probs = estimate_match_probabilities(hist_df, standings_df, row['home_team'], row['away_team'])
        decisions = best_market_decisions(row.to_dict(), probs, bankroll, kelly_scale, min_stake, max_stake_pct)
        base = row.to_dict()
        base.update({
            'p_1': probs.get('1'), 'p_X': probs.get('X'), 'p_2': probs.get('2'),
            'p_oui': probs.get('Oui'), 'p_non': probs.get('Non'),
            'lambda_home': probs.get('lambda_home'), 'lambda_away': probs.get('lambda_away'),
            'form_home_ppm': probs.get('form_home_ppm'), 'form_away_ppm': probs.get('form_away_ppm'),
        })
        if decisions:
            for d in decisions:
                out = dict(base)
                out.update({
                    'recommended_market': d.market,
                    'selection': d.selection,
                    'odds': d.odds,
                    'estimated_probability': d.estimated_probability,
                    'edge': d.edge,
                    'kelly_fraction': d.kelly_fraction,
                    'stake': d.stake,
                    'paper_bet_id': f"{base['round_time']}|{base['home_team']}|{base['away_team']}|{d.market}|{d.selection}",
                })
                rows.append(out)
        else:
            out = dict(base)
            out.update({'recommended_market': None, 'selection': None, 'odds': None, 'estimated_probability': None, 'edge': None, 'kelly_fraction': None, 'stake': 0.0})
            rows.append(out)
    df = pd.DataFrame(rows)
    if not df.empty and 'edge' in df.columns:
        df = df.sort_values(['round_time', 'home_team', 'edge'], ascending=[True, True, False]).reset_index(drop=True)
    return df


def sync_snapshots_into_db(db: Dict, snapshot: Dict) -> None:
    if snapshot.get('standings_snapshot'):
        upsert_snapshot(db, 'standings_snapshots', snapshot['standings_snapshot'], max_items=200)
    if snapshot.get('results_snapshot'):
        upsert_snapshot(db, 'results_snapshots', snapshot['results_snapshot'], max_items=200)
    if snapshot.get('market_snapshot'):
        upsert_snapshot(db, 'market_snapshots', snapshot['market_snapshot'], max_items=500)


def _match_result_from_recent(db: Dict, bet: Dict) -> Dict | None:
    for snap in reversed(db.get('results_snapshots', [])):
        for result in reversed(snap.get('results', [])):
            if result.get('home_team') == bet.get('home_team') and result.get('away_team') == bet.get('away_team') and result.get('round_time') == bet.get('round_time'):
                return result
    return None


def create_paper_bets_from_opportunities(db: Dict, opp_df: pd.DataFrame, bankroll: float, auto_track: bool = True) -> int:
    if not auto_track or opp_df.empty:
        return 0
    existing_ids = {b['paper_bet_id'] for b in db.get('paper_bets', [])}
    added = 0
    for _, r in opp_df.iterrows():
        if not r.get('recommended_market') or float(r.get('stake', 0) or 0) <= 0:
            continue
        bet_id = r['paper_bet_id']
        if bet_id in existing_ids:
            continue
        db.setdefault('paper_bets', []).append({
            'paper_bet_id': bet_id,
            'created_at': datetime.now(timezone.utc).isoformat(),
            'status': 'open',
            'round_time': r['round_time'],
            'home_team': r['home_team'],
            'away_team': r['away_team'],
            'market': r['recommended_market'],
            'selection': r['selection'],
            'odds': float(r['odds']),
            'stake': float(r['stake']),
            'estimated_probability': float(r['estimated_probability']),
            'edge': float(r['edge']),
            'bankroll_ref': float(bankroll),
        })
        existing_ids.add(bet_id)
        added += 1
    db['paper_bets'] = db['paper_bets'][-3000:]
    return added


def settle_paper_bets(db: Dict, initial_bankroll: float, current_bankroll: float) -> float:
    bankroll = float(current_bankroll)
    changed = 0
    for bet in db.get('paper_bets', []):
        if bet.get('status') != 'open':
            continue
        result = _match_result_from_recent(db, bet)
        if not result:
            continue
        if bet['market'] == '1X2':
            won = result['result_1x2'] == bet['selection']
        else:
            won = result['result_gng'] == bet['selection']
        pnl = bet['stake'] * (bet['odds'] - 1.0) if won else -bet['stake']
        bankroll += pnl
        bet.update({
            'status': 'won' if won else 'lost',
            'settled_at': datetime.now(timezone.utc).isoformat(),
            'score': result['score'],
            'result_1x2': result['result_1x2'],
            'result_gng': result['result_gng'],
            'pnl': float(pnl),
            'bankroll_after': float(bankroll),
        })
        changed += 1
    if changed:
        record_bankroll(db, bankroll, initial_bankroll, note=f'{changed} pari(s) simulés réglé(s)')
        append_log(db, f'{changed} pari(s) simulés réglé(s). Bankroll={bankroll:.2f} FCFA')
    return bankroll
