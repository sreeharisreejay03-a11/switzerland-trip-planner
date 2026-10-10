"""Streamlit chat UI.  Run:  streamlit run app.py"""
import os

import streamlit as st

try:                                   # optional .env support
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass
st.image("romford_holidays_logo.png")
st.set_page_config(page_title="Romford Holidays – Switzerland Planner")
st.title(" Romford Holidays – Switzerland Self-Drive Planner")

with st.sidebar:
    st.header("Setup")
    st.caption("Keys are read from environment variables / .env. Never commit them.")
    st.write("SerpApi key:", "✅" if os.environ.get("SERPAPI_KEY") else "❌ missing")
    st.write("Gemini key:", "✅" if os.environ.get("GEMINI_API_KEY") else "❌ missing")
    st.subheader("Car rental (from a rental website)")
    car = st.number_input("Price per day (CHF)", min_value=0.0, value=float(os.environ.get("CAR_RATE_CHF_PER_DAY", 0) or 0), step=5.0)
    src = st.text_input("Source (site + date checked)", value=os.environ.get("CAR_RATE_SOURCE", ""))
    if car > 0:
        os.environ["CAR_RATE_CHF_PER_DAY"] = str(car)
    if src:
        os.environ["CAR_RATE_SOURCE"] = src

if "bot" not in st.session_state:
    from bot import Chatbot
    st.session_state.bot = Chatbot()
    st.session_state.history = [("assistant",
        "Hello! I'm the Romford Holidays trip planner. Tell me your travel dates, number of days, who is "
        "travelling (with children's ages), your budget and what you like, and I'll plan your Switzerland "
        "self-drive trip with live prices.")]

for role, text in st.session_state.history:
    with st.chat_message(role):
        st.markdown(text)

if prompt := st.chat_input("Describe your trip…"):
    st.session_state.history.append(("user", prompt))
    with st.chat_message("user"):
        st.markdown(prompt)
    with st.chat_message("assistant"):
        with st.spinner("Planning and fetching live prices…"):
            try:
                reply = st.session_state.bot.send(prompt)
            except Exception as e:
                reply = f"Sorry, something went wrong: {e}"
        st.markdown(reply)
    st.session_state.history.append(("assistant", reply))
