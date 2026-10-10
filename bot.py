"""Task 4: the chatbot brain (Gemini with function calling).

Gemini only talks to the customer and calls plan_and_quote(); every price and every
drive figure comes from that function, so the model cannot make up numbers.

Model choice: Google retires model names often, so the bot tries GEMINI_MODEL (if set),
then a list of current names, and keeps the first one that works for your API key.
"""
import os
import time
import streamlit as st
from google import genai
from google import genai
from google.genai import types

from quote import plan_and_quote

# Tried in order on the first message (current names per ai.google.dev/gemini-api/docs/models, Oct 2026)
FALLBACK_MODELS = ["gemini-3.6-flash", "gemini-3.7-flash", "gemini-3.8-flash", "gemini-3.5-flash-lite",
                   "gemini-3.1-flash-lite", "gemini-flash-latest"]

# Error text that means "this model is not usable for your key" -> safe to try the next one
_SWITCH_MARKERS = ("404", "not_found", "not found", "no longer available", "not available",
                   "permission_denied", "403", "429", "resource_exhausted", "quota", "limit: 0")

# Temporary overload / rate errors -> wait and retry, or try another model
_TRANSIENT_MARKERS = ("503", "unavailable", "overloaded", "high demand", "500", "internal", "504",
                      "deadline", "429", "resource_exhausted")


def _transient(e) -> bool:
    msg = str(e).lower()
    return any(m in msg for m in _TRANSIENT_MARKERS)


SYSTEM_PROMPT = """You are the Romford Holidays Switzerland self-drive trip planner.

STEP 1 - Collect the details. Ask for anything still missing, all in one short message:
travel dates (or month + start date), number of days/nights, number of people (adults and the age of each
child), budget (budget / standard / luxury) and interests (lakes, snow mountains, towns...). Customers fly from
Kochi (COK) unless they say otherwise. Do not ask for anything the customer has already told you.

STEP 2 - Once you have everything, call plan_and_quote exactly once. Never work out or guess any price,
distance or time yourself.

STEP 3 - Reply with:
  a) a one-line summary of the trip,
  b) the day-by-day itinerary exactly as returned in itinerary_markdown (it already contains the drive distance
     and drive time for each day),
  c) the full quote table exactly as returned in quote_markdown, with every number unchanged,
  d) the short assumptions list.
Copy these blocks verbatim. Do not round, recompute, reorder or edit any figure.

If status is "incomplete", say which live prices could not be fetched (from missing_live_prices), show the
itinerary only, and do NOT give a total or estimate the missing prices. If status is "needs_car_rate", tell the
operator the car rental price has not been configured.
Tone: friendly, concise, professional. Prices are in INR."""



class Chatbot:
    def __init__(self):
        key = (
            st.secrets.get("GEMINI_API_KEY")
            or os.environ.get("GEMINI_API_KEY")
        )
        if not key:
            raise RuntimeError("GEMINI_API_KEY is not set")

        self.client = genai.Client(api_key=key)

        first = (
            st.secrets.get("GEMINI_MODEL")
            or os.environ.get("GEMINI_MODEL")
        )
        self.candidates = (
            ([first] if first else [])
            + [m for m in FALLBACK_MODELS if m != first]
        )
        self.model = None
        self.chat = None

    def _new_chat(self, model, history=None):
        kwargs = {}
        if history:
            kwargs["history"] = history
        return self.client.chats.create(
            model=model,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                tools=[plan_and_quote],          # automatic function calling
                temperature=0.2,
            ),
            **kwargs,
        )

    def _history(self):
        try:
            return self.chat.get_history()
        except Exception:  # noqa: BLE001
            return None

    def send(self, text: str) -> str:
        """Send a message. Google's models are sometimes overloaded (503), so:
        retry the same model briefly, then move to the next model (keeping the conversation).
        A repeated quote costs no extra SerpApi searches thanks to the 30-minute cache."""
        errors = []
        order = ([self.model] if self.model else []) + [m for m in self.candidates if m != self.model]
        for cycle in range(2):                       # second pass after a pause if everything was busy
            for model in order:
                same = (model == self.model)
                waits = (0, 4, 10) if same else (0,)
                for wait in waits:
                    time.sleep(wait)
                    try:
                        if not same:
                            self.chat = self._new_chat(model, self._history() if self.chat else None)
                        reply = self.chat.send_message(text).text
                    except Exception as e:  # noqa: BLE001
                        errors.append(f"{model}: {str(e)[:120]}")
                        if any(m in str(e).lower() for m in _SWITCH_MARKERS) or _transient(e):
                            print(f"[bot] {model} failed ({str(e)[:70]}...)")
                            continue
                        raise
                    if self.model != model:
                        print(f"[bot] using Gemini model: {model}")
                    self.model = model
                    return reply
            time.sleep(8)
        raise RuntimeError("No Gemini model worked right now. Last errors:\n" + "\n".join(errors[-6:]))
