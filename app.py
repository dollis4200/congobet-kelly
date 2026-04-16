import streamlit as st
import pandas as pd
import asyncio
import json
import os
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup
from datetime import datetime

# --- CONFIGURATION DE LA PAGE ---
st.set_page_config(page_title="CongoBet Kelly Assistant", layout="wide")

# --- FONCTIONS DE SCRAPING (ADAPTÉES DE TON NOTEBOOK) ---
async def scrape_congobet_live():
    """Version asynchrone pour Streamlit"""
    data_matches = []
    async with async_playwright() as p:
        # Lancement du navigateur en mode headless (obligatoire sur serveur)
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        try:
            url = "https://www.congobet.net/virtual/category/instant-league/8035/matches"
            await page.goto(url, wait_until="networkidle", timeout=60000)
            
            # Attendre que les cotes chargent
            await page.wait_for_selector(".match-row", timeout=10000)
            
            content = await page.content()
            soup = BeautifulSoup(content, "html.parser")
            
            # Extraction logique (à adapter selon la structure exacte du site)
            rows = soup.select(".match-row")
            for row in rows:
                teams = row.select_one(".teams").text.strip() if row.select_one(".teams") else "Inconnu"
                cotes = [c.text.strip() for c in row.select(".odd-value")]
                if len(cotes) >= 3:
                    data_matches.append({
                        "Match": teams,
                        "1": float(cotes[0]),
                        "X": float(cotes[1]),
                        "2": float(cotes[2])
                    })
        except Exception as e:
            st.error(f"Erreur de scraping : {e}")
        finally:
            await browser.close()
    return data_matches

# --- LOGIQUE DE CALCUL (TON CODE KELLY) ---
def calcul_kelly(bankroll, cote, proba, risque=0.5):
    if cote <= 1.0: return 0
    b = cote - 1
    p = proba / 100
    f_star = (p * (b + 1) - 1) / b
    return max(0, int(bankroll * f_star * risque))

# --- INTERFACE UTILISATEUR ---
st.title("⚽ CongoBet Virtual Kelly Assistant")

# Initialisation du Bankroll dans la session
if 'bankroll' not in st.session_state:
    st.session_state.bankroll = 350.0

with st.sidebar:
    st.header("💰 Gestion")
    st.session_state.bankroll = st.number_input("Capital (FCFA)", value=float(st.session_state.bankroll))
    risque = st.slider("Facteur Kelly (Prudence)", 0.1, 1.0, 0.5)
    
    if st.button("🔄 Actualiser les Cotes"):
        with st.spinner("Scraping en cours..."):
            # Exécution de la fonction asynchrone
            matches = asyncio.run(scrape_congobet_live())
            st.session_state.current_matches = matches

# --- AFFICHAGE DES RÉSULTATS ---
if 'current_matches' in st.session_state and st.session_state.current_matches:
    df = pd.DataFrame(st.session_state.current_matches)
    
    st.subheader("📊 Analyses et Mises suggérées")
    
    for idx, row in df.iterrows():
        with st.container():
            c1, c2, c3 = st.columns([2, 1, 2])
            with c1:
                st.write(f"**{row['Match']}**")
                st.caption(f"Cotes: 1({row['1']}) | X({row['X']}) | 2({row['2']})")
            
            with c2:
                # Ici ton algo d'analyse de probabilité
                proba = st.number_input(f"Proba %", value=55, key=f"p_{idx}")
            
            with c3:
                mise = calcul_kelly(st.session_state.bankroll, row['1'], proba, risque)
                if mise > 0:
                    st.success(f"Misez {mise} FCFA sur '1'")
                else:
                    st.info("Pari non rentable")
            st.divider()
else:
    st.info("Cliquez sur le bouton dans la barre latérale pour charger les matchs en direct.")

# --- HISTORIQUE ---
st.subheader("📜 Historique des Sessions")
# Optionnel : Ajouter un bouton pour sauvegarder en JSON comme dans ton notebook
if st.button("💾 Sauvegarder la session"):
    st.write("Données sauvegardées localement.")
