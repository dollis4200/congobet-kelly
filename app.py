import streamlit as st
import pandas as pd
import numpy as np
import requests
from bs4 import BeautifulSoup

# --- CONFIGURATION DE LA PAGE ---
st.set_page_config(page_title="CongoBet Kelly Pro", layout="wide")

# --- 1. LOGIQUE D'ANALYSE (TON CODE ORIGINAL) ---
def analyser_historique(df_historique):
    """
    Cette fonction reprend ta logique d'analyse de données 
    pour estimer la probabilité réelle d'un événement.
    """
    if df_historique.empty:
        return 50 # Probabilité par défaut
    
    # Exemple de calcul basé sur tes fichiers :
    # On compte le nombre de victoires à domicile sur les X derniers matchs
    victoires_home = len(df_historique[df_historique['resultat'] == '1'])
    total_matchs = len(df_historique)
    proba_calculee = (victoires_home / total_matchs) * 100
    
    return round(proba_calculee, 2)

# --- 2. LOGIQUE DE SCRAPING (CONGOBET) ---
def scraper_congobet(type_jeu="virtual"):
    """
    Logique pour extraire les données de la page CongoBet.
    """
    url = "https://www.congobet.net/fr/virtual" # Exemple d'URL
    try:
        # Note: Dans une application réelle, on utilise souvent un Header 
        # pour ne pas être bloqué par le site.
        headers = {'User-Agent': 'Mozilla/5.0'}
        # response = requests.get(url, headers=headers)
        # soup = BeautifulSoup(response.text, 'html.parser')
        
        # Simulation des données scrapées pour la démonstration
        data = {
            'Heure': ['14:05', '14:10', '14:15'],
            'Match': ['RDC vs Congo', 'France vs Brésil', 'Milan vs Inter'],
            'Cote_1': [1.95, 2.10, 1.75],
            'Cote_X': [3.10, 3.20, 3.40],
            'Cote_2': [3.40, 2.80, 4.10]
        }
        return pd.DataFrame(data)
    except Exception as e:
        st.error(f"Erreur de Scraping : {e}")
        return pd.DataFrame()

# --- 3. CALCULATEUR DE MISE (KELLY) ---
def calcul_mise_kelly(capital, cote, proba, risque):
    b = cote - 1
    p = proba / 100
    q = 1 - p
    f_star = (p * (b + 1) - 1) / b
    fraction = f_star * risque
    if fraction <= 0: return 0
    return round(capital * fraction, 0)

# --- INTERFACE UTILISATEUR ---
st.title("🤖 CongoBet Predictor & Kelly Assistant")

# Barre latérale pour les réglages de ton capital
with st.sidebar:
    st.header("Paramètres Bankroll")
    capital_total = st.number_input("Capital disponible (FCFA)", value=5000)
    facteur_securite = st.slider("Facteur de Kelly (Prudence)", 0.1, 1.0, 0.5)

# Zone principale : Scraping et Analyse
st.subheader("📡 Cotes en direct & Analyse statistique")

if st.button("Lancer l'analyse automatique"):
    with st.spinner('Scraping de CongoBet et calcul des probabilités...'):
        # 1. On récupère les cotes
        df_cotes = scraper_congobet()
        
        # 2. On affiche le tableau de bord
        if not df_cotes.empty:
            # On ajoute une colonne probabilité simulée par ton algorithme
            df_cotes['Ma_Proba'] = [55.0, 48.0, 62.0] # Ici ton algo analyse l'historique
            
            # 3. Calcul de la mise pour chaque match
            df_cotes['Mise_Conseillee'] = df_cotes.apply(
                lambda row: calcul_mise_kelly(capital_total, row['Cote_1'], row['Ma_Proba'], facteur_securite), axis=1
            )
            
            # Affichage élégant
            st.table(df_cotes)
            
            # Focus sur la meilleure opportunité
            best_bet = df_cotes.loc[df_cotes['Mise_Conseillee'].idxmax()]
            if best_bet['Mise_Conseillee'] > 0:
                st.success(f"🎯 **Meilleur coup :** {best_bet['Match']} | Mise : {int(best_bet['Mise_Conseillee'])} FCFA")
            else:
                st.warning("⚠️ Aucune opportunité rentable détectée actuellement.")
        else:
            st.error("Impossible de récupérer les données. Vérifiez votre connexion.")

st.markdown("""
---
**Note technique :** Pour que le scraping fonctionne sur CongoBet en temps réel, 
tu devras peut-être utiliser `Selenium` car leur site utilise du JavaScript pour afficher les cotes.
""")
