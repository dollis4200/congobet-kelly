from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from typing import Optional

import pandas as pd
import plotly.express as px
import streamlit as st
from playwright.sync_api import sync_playwright
from streamlit_autorefresh import st_autorefresh

from core.analytics import bankroll_curve_df
from core.engine import compute_opportunities, create_paper_bets_from_opportunities, market_df_from_snapshot, settle_paper_bets, sync_snapshots_into_db
from core.scraper import CongoBetScraper, URL
from core.storage import DEFAULT_DB_PATH, SEED_DB_PATH, append_log, load_db, merge_historical_gng, record_bankroll, save_db

st.set_page_config(page_title='CongoBet Kelly Monitor', layout='wide')

st.title('📊 CongoBet Kelly Monitor — scraping auto & simulation')
st.caption("Application Streamlit prête pour GitHub/Streamlit Cloud. Cette version fait du scraping, de l'analyse Kelly et du suivi simulé de bankroll. Elle n'automatise pas la validation de tickets.")


def to_json_bytes(data) -> bytes:
    return json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8')


def to_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode('utf-8') if not df.empty else b''


def latest_bankroll(db: dict, initial_bankroll: float) -> float:
    hist = db.get('bankroll_history', [])
    if hist:
        return float(hist[-1]['bankroll'])
    return float(initial_bankroll)


def ensure_initial_bankroll_record(db: dict, initial_bankroll: float) -> None:
    if not db.get('bankroll_history'):
        record_bankroll(db, initial_bankroll, initial_bankroll, note='Initialisation de session')


def fetch_available_rounds() -> list:
    """Récupère la liste des rounds (horaires) disponibles sur le site."""
    scraper = CongoBetScraper(headless=True)
    with sync_playwright() as p:
        browser = scraper._launch_browser(p)
        context = browser.new_context(viewport={'width': 1440, 'height': 2200}, locale='fr-FR')
        page = context.new_page()
        scraper._open_page(page)
        scraper._go_tab(page, 'MATCHS')
        rounds = scraper._round_items(page)
        browser.close()
    return rounds


def run_scrape_cycle(db: dict, initial_bankroll: float, bankroll_current: float, cfg: dict, target_round_index: Optional[int] = None) -> tuple[dict, pd.DataFrame, float]:
    """Exécute un cycle complet de scraping et mise à jour."""
    cycle_no = int(st.session_state.get('cycle_no', 0)) + 1
    st.session_state['cycle_no'] = cycle_no
    need_history = (cycle_no % int(cfg['rescrape_after_affiches']) == 0) or not db.get('standings_snapshots') or not db.get('results_snapshots')

    scraper = CongoBetScraper(headless=True)
    snapshot = scraper.scrape_all(
        min_seconds=int(cfg['min_seconds_before_start']),
        include_results=need_history,
        include_standings=need_history,
        target_round_index=target_round_index
    )
    sync_snapshots_into_db(db, snapshot)

    opp_df = compute_opportunities(
        db=db,
        live_snapshot=snapshot,
        bankroll=bankroll_current,
        kelly_scale=float(cfg['kelly_scale']),
        min_stake=float(cfg['min_stake']),
        max_stake_pct=float(cfg['max_stake_pct']),
    )
    db['last_opportunities'] = opp_df.to_dict(orient='records') if not opp_df.empty else []

    low_bankroll = bankroll_current < 0.40 * initial_bankroll
    auto_track_allowed = bool(cfg['auto_track_paper_bets']) and (bool(cfg['continue_below_40']) or not low_bankroll)
    added = create_paper_bets_from_opportunities(db, opp_df, bankroll_current, auto_track=auto_track_allowed)
    if added:
        append_log(db, f'{added} pari(s) simulé(s) ajouté(s) au suivi.')

    bankroll_after = settle_paper_bets(db, initial_bankroll=initial_bankroll, current_bankroll=bankroll_current)
    if bankroll_after == bankroll_current:
        record_bankroll(db, bankroll_current, initial_bankroll, note=f'Cycle {cycle_no} sans règlement')

    append_log(db, f'Cycle {cycle_no} terminé. Opportunités={len(opp_df)}. Historique rescanné={need_history}.')
    save_db(db)
    return snapshot, opp_df, bankroll_after


def latest_snapshot(db: dict) -> dict:
    return {
        'market_snapshot': db.get('market_snapshots', [{}])[-1] if db.get('market_snapshots') else {},
        'standings_snapshot': db.get('standings_snapshots', [{}])[-1] if db.get('standings_snapshots') else {},
        'results_snapshot': db.get('results_snapshots', [{}])[-1] if db.get('results_snapshots') else {},
    }


def paper_bets_df(db: dict) -> pd.DataFrame:
    df = pd.DataFrame(db.get('paper_bets', []))
    if not df.empty:
        for col in ['created_at', 'settled_at']:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors='coerce')
    return df.sort_values('created_at', ascending=False) if not df.empty else df


def render_metrics(snapshot: dict, opp_df: pd.DataFrame, bankroll_current: float, initial_bankroll: float) -> None:
    target = (snapshot.get('market_snapshot') or {}).get('target_round', {})
    rounds = (snapshot.get('market_snapshot') or {}).get('rounds', [])
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric('💰 Bankroll courant', f"{bankroll_current:,.0f} FCFA".replace(',', ' '), delta=f"{(bankroll_current-initial_bankroll):,.0f} FCFA".replace(',', ' '))
    c2.metric('🎯 Opportunités', len(opp_df))
    c3.metric('🕒 Affiche ciblée', target.get('selected_label', target.get('label', '-')))
    c4.metric('⏳ Démarrage mini', target.get('seconds_to_start', '-'))
    c5.metric('📚 Onglets horaires', len(rounds))


def render_bankroll_graph(db: dict):
    curve = bankroll_curve_df(db)
    if curve.empty:
        st.info('Aucune courbe de bankroll disponible pour le moment.')
        return
    fig = px.line(curve, x='ts', y='bankroll', title='Évolution de la bankroll simulée', markers=True)
    st.plotly_chart(fig, use_container_width=True)


def merge_uploaded_db(db: dict, upload) -> dict:
    try:
        content = json.load(upload)
        if 'affiches' in content:
            added = merge_historical_gng(db, content)
            append_log(db, f'Base historique fusionnée: {added} affiche(s) ajoutée(s).')
        else:
            db.update(content)
            append_log(db, 'Base complète remplacée depuis upload utilisateur.', level='WARN')
        save_db(db)
    except Exception as e:
        st.error(f'Impossible de charger le JSON: {e}')
    return db


# Chargement base
if 'db' not in st.session_state:
    st.session_state['db'] = load_db()

db = st.session_state['db']

# Sidebar
with st.sidebar:
    st.header('⚙️ Paramètres')
    initial_bankroll = st.number_input('Solde initial manuel (FCFA)', min_value=0.0, value=float(db.get('settings', {}).get('initial_bankroll', 350.0)), step=10.0)
    kelly_choice = st.selectbox('Fraction Kelly', options=['1/8', '1/4', '1/2', '1'], index=0)
    kelly_scale = {'1/8': 1/8, '1/4': 1/4, '1/2': 1/2, '1': 1.0}[kelly_choice]
    min_stake = st.number_input('Mise minimale Kelly (FCFA)', min_value=0.0, value=float(db.get('settings', {}).get('min_stake', 50.0)), step=5.0)
    max_stake_pct = st.slider('Cap de mise (% bankroll)', min_value=0.05, max_value=0.50, value=float(db.get('settings', {}).get('max_stake_pct', 0.20)), step=0.01)
    min_seconds_before_start = st.number_input('Ne pas viser une affiche si sec <', min_value=0, value=int(db.get('settings', {}).get('min_seconds_before_start', 20)), step=1)
    refresh_secs = st.slider('Auto-refresh (secondes)', min_value=15, max_value=180, value=int(db.get('settings', {}).get('refresh_secs', 30)), step=5)
    auto_refresh = st.toggle('Boucle auto de scraping', value=bool(st.session_state.get('auto_refresh', False)))
    st.session_state['auto_refresh'] = auto_refresh
    auto_track_paper_bets = st.toggle('Suivre automatiquement les opportunités (paper bets)', value=bool(db.get('settings', {}).get('auto_track_paper_bets', True)))
    continue_below_40 = st.checkbox('Continuer si bankroll < 40% du capital initial', value=bool(db.get('settings', {}).get('continue_below_40', False)))
    rescrape_after_affiches = st.number_input('Re-scraper résultats/classement après N cycles', min_value=1, value=int(db.get('settings', {}).get('rescrape_after_affiches', 20)), step=1)

    # --- Section : choix manuel de l'affiche ---
    st.divider()
    st.subheader("🎯 Choix manuel de l'affiche")
    if st.button("📡 Récupérer les horaires disponibles", use_container_width=True):
        with st.spinner("Scraping des horaires..."):
            try:
                rounds = fetch_available_rounds()
                st.session_state['available_rounds'] = rounds
                st.session_state['selected_round_index'] = 0
                st.success(f"{len(rounds)} horaires trouvés")
            except Exception as e:
                st.error(f"Erreur : {e}")

    available_rounds = st.session_state.get('available_rounds', [])
    if available_rounds:
        round_labels = [r['text'] for r in available_rounds]
        selected_idx = st.session_state.get('selected_round_index', 0)
        selected_label = st.selectbox("Choisir une affiche", round_labels, index=selected_idx)
        st.session_state['selected_round_index'] = round_labels.index(selected_label)
        st.caption("L'affiche sélectionnée sera utilisée lors du prochain cycle.")
    else:
        st.info("Cliquez sur 'Récupérer les horaires' pour voir la liste.")

    # --- Section : contrôle du cycle ---
    if st.button("⏹️ Arrêter le cycle", use_container_width=True):
        st.session_state['auto_refresh'] = False
        st.rerun()

    # --- Section : bankroll manuel ---
    manual_bankroll = st.number_input('Ajuster le bankroll courant (manuel)', min_value=0.0, value=float(latest_bankroll(db, initial_bankroll)), step=10.0)
    col1, col2 = st.columns(2)
    with col1:
        if st.button('Appliquer le bankroll manuel', use_container_width=True):
            record_bankroll(db, manual_bankroll, initial_bankroll, note='Ajustement manuel utilisateur')
            save_db(db)
            st.success('Bankroll manuel appliqué.')
    with col2:
        if st.button('💰 Réinitialiser la bankroll', use_container_width=True):
            record_bankroll(db, initial_bankroll, initial_bankroll, note='Réinitialisation utilisateur')
            save_db(db)
            st.success(f"Bankroll remise à {initial_bankroll} FCFA")
            st.rerun()

    # --- Section : upload/fusion ---
    uploaded = st.file_uploader('Charger/Fusionner une base JSON', type=['json'])
    if uploaded is not None and st.button('Fusionner le JSON uploadé'):
        db = merge_uploaded_db(db, uploaded)
        st.session_state['db'] = db

cfg = {
    'initial_bankroll': initial_bankroll,
    'kelly_scale': kelly_scale,
    'min_stake': min_stake,
    'max_stake_pct': max_stake_pct,
    'min_seconds_before_start': min_seconds_before_start,
    'refresh_secs': refresh_secs,
    'auto_track_paper_bets': auto_track_paper_bets,
    'continue_below_40': continue_below_40,
    'rescrape_after_affiches': rescrape_after_affiches,
}
db['settings'] = cfg
save_db(db)
ensure_initial_bankroll_record(db, initial_bankroll)

if auto_refresh:
    st_autorefresh(interval=int(refresh_secs * 1000), key='congobet_refresh')

cbtn1, cbtn2 = st.columns([1, 2])
manual_cycle = cbtn1.button('▶️ Lancer un cycle maintenant', use_container_width=True)
if cbtn2.button('💾 Sauvegarder la base locale', use_container_width=True):
    save_db(db)
    st.success(f'Base sauvegardée dans {DEFAULT_DB_PATH.name}')

snapshot = latest_snapshot(db)
opp_df = pd.DataFrame(db.get('last_opportunities', [])) if db.get('last_opportunities') else compute_opportunities(db, snapshot, latest_bankroll(db, initial_bankroll), kelly_scale, min_stake, max_stake_pct)
bankroll_current = latest_bankroll(db, initial_bankroll)

if manual_cycle or auto_refresh:
    target_idx = st.session_state.get('selected_round_index') if st.session_state.get('available_rounds') else None
    with st.spinner('Scraping CongoBet en cours...'):
        try:
            snapshot, opp_df, bankroll_current = run_scrape_cycle(db, initial_bankroll, bankroll_current, cfg, target_round_index=target_idx)
            st.session_state['db'] = db
            st.success('Cycle terminé.')
        except Exception as e:
            st.error(f'Cycle échoué: {e}')
            append_log(db, f'Erreur de cycle: {e}', level='ERROR')
            save_db(db)

render_metrics(snapshot, opp_df, bankroll_current, initial_bankroll)

if bankroll_current < 0.40 * initial_bankroll:
    st.warning('⚠️ La bankroll est passée sous 40% du capital initial. Active l’option de continuité si tu veux poursuivre les nouveaux paris simulés.')

with st.expander('ℹ️ Détails de la cible de scraping'):
    st.write({'source_url': URL, 'target_round': (snapshot.get('market_snapshot') or {}).get('target_round', {}), 'db_path': str(DEFAULT_DB_PATH), 'seed_path': str(SEED_DB_PATH)})

live_tab, opp_tab, bets_tab, bankroll_tab, data_tab, logs_tab = st.tabs(['📺 Vue live', '🎯 Opportunités Kelly', '🧾 Paris simulés', '📈 Bankroll', '🗂️ Données', '🪵 Logs'])

with live_tab:
    market_df = market_df_from_snapshot(snapshot.get('market_snapshot') or {})
    standings_rows = (snapshot.get('standings_snapshot') or {}).get('standings', [])
    standings_df = pd.DataFrame(standings_rows)
    st.subheader('Rencontres actuelles ciblées')
    if not market_df.empty:
        st.dataframe(market_df, use_container_width=True, hide_index=True)
    else:
        st.info('Aucune rencontre disponible dans le dernier snapshot.')
    st.subheader('Classement actuel')
    if not standings_df.empty:
        st.dataframe(standings_df, use_container_width=True, hide_index=True)
    else:
        st.info('Classement non encore scrapé.')

with opp_tab:
    st.subheader('Opportunités en temps réel')
    if not opp_df.empty:
        display_df = opp_df.copy()
        cols = ['round_time', 'home_team', 'away_team', 'recommended_market', 'selection', 'odds', 'estimated_probability', 'edge', 'stake', 'p_1', 'p_X', 'p_2', 'p_oui', 'p_non']
        cols = [c for c in cols if c in display_df.columns]
        st.dataframe(display_df[cols], use_container_width=True, hide_index=True)
        st.download_button('⬇️ Télécharger les opportunités (CSV)', data=to_csv_bytes(display_df), file_name='opportunites_kelly.csv', mime='text/csv')
    else:
        st.info('Aucune opportunité détectée pour les paramètres actuels.')

with bets_tab:
    st.subheader('Suivi des paris simulés')
    bets_df = paper_bets_df(db)
    if not bets_df.empty:
        open_df = bets_df[bets_df['status'] == 'open']
        settled_df = bets_df[bets_df['status'].isin(['won', 'lost'])]
        c1, c2, c3 = st.columns(3)
        c1.metric('Paris ouverts', len(open_df))
        c2.metric('Paris gagnés', int((bets_df['status'] == 'won').sum()))
        c3.metric('Paris perdus', int((bets_df['status'] == 'lost').sum()))
        st.dataframe(bets_df, use_container_width=True, hide_index=True)
        st.download_button('⬇️ Télécharger les paris simulés (CSV)', data=to_csv_bytes(bets_df), file_name='paper_bets.csv', mime='text/csv')
    else:
        st.info('Aucun pari simulé enregistré.')

with bankroll_tab:
    st.subheader('Suivi bankroll')
    render_bankroll_graph(db)
    curve_df = bankroll_curve_df(db)
    if not curve_df.empty:
        st.dataframe(curve_df.tail(50), use_container_width=True, hide_index=True)
        st.download_button('⬇️ Télécharger la courbe bankroll (CSV)', data=to_csv_bytes(curve_df), file_name='bankroll_history.csv', mime='text/csv')

with data_tab:
    st.subheader('Exports')
    results_latest = pd.DataFrame((snapshot.get('results_snapshot') or {}).get('results', []))
    standings_latest = pd.DataFrame((snapshot.get('standings_snapshot') or {}).get('standings', []))
    market_latest = market_df_from_snapshot(snapshot.get('market_snapshot') or {})
    st.download_button('⬇️ Télécharger la base complète (JSON)', data=to_json_bytes(db), file_name='congobet_streamlit_db.json', mime='application/json')
    if not results_latest.empty:
        st.download_button('⬇️ Télécharger résultats récents (CSV)', data=to_csv_bytes(results_latest), file_name='results_recent.csv', mime='text/csv')
    if not standings_latest.empty:
        st.download_button('⬇️ Télécharger classement (CSV)', data=to_csv_bytes(standings_latest), file_name='standings.csv', mime='text/csv')
    if not market_latest.empty:
        st.download_button('⬇️ Télécharger marchés actuels (CSV)', data=to_csv_bytes(market_latest), file_name='markets_current.csv', mime='text/csv')

with logs_tab:
    logs_df = pd.DataFrame(db.get('logs', []))
    if not logs_df.empty:
        st.dataframe(logs_df.sort_values('ts', ascending=False), use_container_width=True, hide_index=True)
    else:
        st.info('Aucun log pour le moment.')
