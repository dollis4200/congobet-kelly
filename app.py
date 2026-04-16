import streamlit as st

# Configuration de la page
st.set_page_config(page_title="CongoBet Kelly Assistant", page_icon="💰")

st.title("💰 CongoBet Virtual Assistant")
st.markdown("---")

# --- LOGIQUE MATHÉMATIQUE ---
def kelly_criterion(bankroll, odds, probability, fraction):
    b = odds - 1
    p = probability / 100
    q = 1 - p
    if b <= 0: return 0, 0
    f_star = (p * (b + 1) - 1) / b
    f_adjusted = f_star * fraction
    if f_adjusted <= 0: return 0, 0
    return round(bankroll * f_adjusted, 0), round(f_adjusted * 100, 2)

# --- INTERFACE STREAMLIT ---
with st.sidebar:
    st.header("Paramètres")
    capital = st.number_input("Mon Capital (FCFA)", value=10000, step=500)
    risque = st.select_slider("Niveau de Risque", 
                             options=[0.25, 0.5, 1.0], 
                             value=0.5, 
                             format_func=lambda x: "Prudent" if x==0.25 else ("Modéré" if x==0.5 else "Agressif"))

col1, col2 = st.columns(2)
with col1:
    cote = st.number_input("Cote du match", value=1.85, min_value=1.01, step=0.01)
with col2:
    confiance = st.slider("Confiance (%)", 1, 100, 60)

if st.button("CALCULER LA MISE", use_container_width=True):
    mise, pourcentage = kelly_criterion(capital, cote, confiance, risque)
    
    if mise > 0:
        st.success(f"### ✅ Mise conseillée : {int(mise)} FCFA")
        st.info(f"Cela représente **{pourcentage}%** de votre capital.")
        st.write(f"Gain potentiel : **{int(mise * cote)} FCFA**")
    else:
        st.error("❌ ANALYSE : NE PAS PARIER (Avantage insuffisant)")
