from pathlib import Path
import json

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from congobet_service import (
    DEFAULT_URL,
    ContinuousScraperService,
    flatten_rounds,
    list_history_files,
    read_latest_payload,
    read_status,
    run_scrape_once_sync,
)

st.set_page_config(page_title="Congobet 1X2 Monitor", page_icon="⚽", layout="wide")


@st.cache_resource
def get_service():
    return ContinuousScraperService()


def inject_auto_refresh(seconds: int):
    components.html(
        f"""
        <script>
        setTimeout(function() {{ window.parent.location.reload(); }}, {seconds * 1000});
        </script>
        """,
        height=0,
    )


def safe_read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="ignore")


service = get_service()

st.title("⚽ Congobet Instant League — Monitor 1X2")
st.caption("Interface Streamlit pour scraping Playwright, export des cotes 1X2 et fonctionnement en continu.")

with st.sidebar:
    st.header("Configuration")
    url = st.text_input("URL cible", value=DEFAULT_URL)
    out_dir = st.text_input("Dossier de sortie", value="./runtime_data")
    interval_seconds = st.number_input("Intervalle en secondes", min_value=30, max_value=3600, value=120, step=30)
    auto_refresh_ui = st.checkbox("Rafraîchir l'interface automatiquement", value=True)
    refresh_ui_seconds = st.number_input("Rafraîchissement UI (s)", min_value=5, max_value=300, value=15, step=5)

    col1, col2 = st.columns(2)
    start_clicked = col1.button("▶️ Démarrer", use_container_width=True)
    stop_clicked = col2.button("⏹️ Arrêter", use_container_width=True)
    scrape_now_clicked = st.button("🧪 Lancer un scraping manuel", use_container_width=True)

    st.divider()
    st.markdown("**Arborescence générée**")
    st.code("latest/\nhistory/\nlogs/app.log\nstatus.json")

if auto_refresh_ui:
    inject_auto_refresh(int(refresh_ui_seconds))

if start_clicked:
    started = service.start(url=url, out_dir=out_dir, interval_seconds=int(interval_seconds))
    if started:
        st.success("Service continu démarré.")
    else:
        st.warning("Le service continu est déjà actif.")

if stop_clicked:
    stopped = service.stop()
    if stopped:
        st.info("Arrêt demandé. Attends quelques secondes pour la fin du cycle en cours.")
    else:
        st.warning("Aucun service actif à arrêter.")

if scrape_now_clicked:
    with st.spinner("Scraping manuel en cours..."):
        payload = run_scrape_once_sync(url=url, out_dir=out_dir)
    st.success(f"Scraping terminé. {payload.get('round_count', 0)} tour(s) exporté(s).")

status = read_status(out_dir)
latest_payload = read_latest_payload(out_dir)
rows = flatten_rounds(latest_payload) if latest_payload else []
df = pd.DataFrame(rows)

latest_dir = Path(out_dir) / "latest"
log_path = Path(out_dir) / "logs" / "app.log"
latest_json = latest_dir / "congobet_1x2_latest.json"
latest_csv = latest_dir / "congobet_1x2_latest.csv"
latest_txt = latest_dir / "congobet_1x2_latest.txt"

metric_cols = st.columns(5)
metric_cols[0].metric("Statut", "🟢 Actif" if status.get("running") else "⚪ Arrêté")
metric_cols[1].metric("Intervalle", f"{status.get('interval_seconds') or '-'} s")
metric_cols[2].metric("Dernier succès", status.get("last_success") or "-")
metric_cols[3].metric("Tours", latest_payload.get("round_count", 0) if latest_payload else 0)
metric_cols[4].metric("Matchs", len(rows))

if status.get("last_error"):
    st.error(f"Dernière erreur : {status['last_error']}")

st.subheader("Fichiers générés")
file_cols = st.columns(3)
for col, file_path, label in [
    (file_cols[0], latest_json, "Télécharger JSON"),
    (file_cols[1], latest_csv, "Télécharger CSV"),
    (file_cols[2], latest_txt, "Télécharger TXT"),
]:
    if file_path.exists():
        col.download_button(label, data=file_path.read_bytes(), file_name=file_path.name, use_container_width=True)
    else:
        col.button(label, disabled=True, use_container_width=True)


tab1, tab2, tab3, tab4 = st.tabs(["📊 Tableau", "🧾 JSON", "🗂️ Historique", "📜 Logs"])

with tab1:
    if df.empty:
        st.info("Aucune donnée disponible pour le moment. Lance un scraping manuel ou démarre le mode continu.")
    else:
        available_times = sorted(df["heure"].dropna().unique().tolist())
        selected_times = st.multiselect("Filtrer par heure", available_times, default=available_times)
        filtered_df = df[df["heure"].isin(selected_times)] if selected_times else df
        st.dataframe(filtered_df, use_container_width=True, hide_index=True)
        st.caption(f"{len(filtered_df)} ligne(s) affichée(s).")

with tab2:
    if latest_payload:
        st.json(latest_payload)
    else:
        st.info("Le fichier JSON le plus récent n'existe pas encore.")

with tab3:
    history_files = list_history_files(out_dir)
    if not history_files:
        st.info("Aucun historique disponible.")
    else:
        st.write(f"{len(history_files)} fichier(s) d'historique trouvés.")
        for path in history_files[:30]:
            st.write(f"- {path.name}")

with tab4:
    log_text = safe_read_text(log_path)
    if log_text:
        st.text_area("Journal applicatif", value=log_text[-20000:], height=400)
    else:
        st.info("Aucun log pour l'instant.")

st.divider()
with st.expander("Procédure rapide de lancement"):
    st.code(
        "pip install -r requirements.txt\n"
        "playwright install chromium\n"
        "sudo apt-get install -y libatk1.0-0 libatk-bridge2.0-0\n"
        "playwright install-deps chromium\n"
        "streamlit run app.py --server.address 0.0.0.0 --server.port 8501",
        language="bash",
    )
    st.write(
        "Le service continu exécute des cycles de scraping réguliers en arrière-plan et archive automatiquement chaque export."
    )
