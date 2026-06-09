"""
LogSense — Streamlit frontend.

Three-column layout:
  LEFT   — Watchdog alert panel (real-time via WebSocket polling)
  CENTER — Conversational chat with the agent
  RIGHT  — Blast Radius + Post-Mortem panel
"""

import asyncio
import json
import threading
import time
import uuid
from datetime import datetime

import requests
import streamlit as st
import websocket  # websocket-client

BACKEND = "http://localhost:8000"
WS_BACKEND = "ws://localhost:8000"

st.set_page_config(
    page_title="LogSense — Incident Co-pilot",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── session state defaults ──────────────────────────────────────────────────

if "session_id"   not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())
if "messages"     not in st.session_state:
    st.session_state.messages = []
if "alerts"       not in st.session_state:
    st.session_state.alerts = []
if "blast_radius" not in st.session_state:
    st.session_state.blast_radius = None
if "postmortem"   not in st.session_state:
    st.session_state.postmortem = None
if "ws_started"   not in st.session_state:
    st.session_state.ws_started = False
if "incident_start" not in st.session_state:
    st.session_state.incident_start = "2026-06-09T14:52:00Z"
if "incident_end"   not in st.session_state:
    st.session_state.incident_end = "2026-06-09T15:09:00Z"


# ── WebSocket listener (background thread) ──────────────────────────────────

def _ws_listener(session_id: str):
    """Receives watchdog alerts and appends them to session state."""
    url = f"{WS_BACKEND}/ws/{session_id}"

    def on_message(ws, message):
        try:
            alert = json.loads(message)
            st.session_state.alerts.insert(0, alert)  # newest first
        except Exception:
            pass

    def on_error(ws, error):
        pass

    def on_close(ws, *args):
        pass

    ws = websocket.WebSocketApp(url, on_message=on_message,
                                 on_error=on_error, on_close=on_close)
    ws.run_forever(ping_interval=20)


def _start_ws():
    if not st.session_state.ws_started:
        t = threading.Thread(
            target=_ws_listener,
            args=(st.session_state.session_id,),
            daemon=True,
        )
        t.start()
        st.session_state.ws_started = True


_start_ws()


# ── helpers ─────────────────────────────────────────────────────────────────

def _chat(message: str) -> str:
    resp = requests.post(f"{BACKEND}/chat", json={
        "message":    message,
        "session_id": st.session_state.session_id,
    }, timeout=120)
    resp.raise_for_status()
    data = resp.json()
    st.session_state.session_id = data["session_id"]
    return data["response"]


def _blast_radius() -> dict:
    resp = requests.post(f"{BACKEND}/blast-radius", json={
        "start": st.session_state.incident_start,
        "end":   st.session_state.incident_end,
    }, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _postmortem() -> str:
    resp = requests.post(f"{BACKEND}/postmortem", json={
        "conversation":   st.session_state.messages,
        "blast_radius":   st.session_state.blast_radius,
        "incident_start": st.session_state.incident_start,
        "incident_end":   st.session_state.incident_end,
        "severity":       "P1",
    }, timeout=60)
    resp.raise_for_status()
    return resp.json()["markdown"]


def _severity_color(sev: str) -> str:
    return {"P1": "🔴", "P2": "🟠", "P3": "🟡"}.get(sev, "⚪")


# ── layout ───────────────────────────────────────────────────────────────────

st.markdown("""
<style>
.alert-box { border-left: 4px solid #e74c3c; padding: 8px 12px;
             background: #1a0000; border-radius: 4px; margin-bottom: 8px; }
.alert-p2  { border-left-color: #e67e22; background: #1a0f00; }
.alert-p3  { border-left-color: #f1c40f; background: #1a1a00; }
.metric-card { background: #0d1117; border: 1px solid #30363d;
               padding: 12px; border-radius: 8px; text-align: center; }
</style>
""", unsafe_allow_html=True)

st.title("🔍 LogSense — Autonomous Incident Response Co-pilot")
st.caption("Powered by Google Gemini · Elastic MCP · Google Cloud ADK")

col_left, col_center, col_right = st.columns([1, 2, 1.2])

# ── LEFT: Watchdog Alerts ────────────────────────────────────────────────────

with col_left:
    st.subheader("🚨 Watchdog Alerts")
    st.caption("Auto-refreshes every 5s")

    if st.button("🔄 Refresh", key="refresh_alerts"):
        st.rerun()

    if not st.session_state.alerts:
        st.info("Watchdog is monitoring… No anomalies detected yet.")
    else:
        for alert in st.session_state.alerts[:10]:
            sev   = alert.get("severity", "P3")
            css   = {"P1": "alert-box", "P2": "alert-box alert-p2",
                     "P3": "alert-box alert-p3"}.get(sev, "alert-box")
            icon  = _severity_color(sev)
            ts    = alert.get("timestamp", "")[:19].replace("T", " ")
            svcs  = ", ".join(alert.get("affected_services", []))
            summ  = alert.get("summary", "")
            conf  = int(alert.get("confidence", 0) * 100)

            st.markdown(f"""
<div class="{css}">
  <strong>{icon} {sev}</strong> <small>{ts}</small><br/>
  <small>{svcs}</small><br/>
  {summ}<br/>
  <small>Confidence: {conf}%</small>
</div>""", unsafe_allow_html=True)

            if alert.get("investigation"):
                with st.expander("🤖 Auto-investigation"):
                    st.markdown(alert["investigation"])

    # Auto-refresh every 5 seconds
    time.sleep(5)
    st.rerun()


# ── CENTER: Chat ─────────────────────────────────────────────────────────────

with col_center:
    st.subheader("💬 Incident Investigation Chat")

    # Starter prompts
    starter_cols = st.columns(2)
    starters = [
        "Why did checkout fail at 14:52?",
        "Show error rate trend for payment-service",
        "Was there a deployment before the incident?",
        "Which users were affected between 14:52 and 15:09?",
    ]
    for i, prompt in enumerate(starters):
        with starter_cols[i % 2]:
            if st.button(prompt, key=f"starter_{i}", use_container_width=True):
                st.session_state.messages.append({"role": "user", "content": prompt})
                with st.spinner("Investigating…"):
                    try:
                        reply = _chat(prompt)
                    except Exception as e:
                        reply = f"Error: {e}"
                st.session_state.messages.append({"role": "assistant", "content": reply})
                st.rerun()

    st.divider()

    # Chat history
    chat_container = st.container(height=500)
    with chat_container:
        for msg in st.session_state.messages:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

    # Input
    if user_input := st.chat_input("Ask about your logs…"):
        st.session_state.messages.append({"role": "user", "content": user_input})
        with st.spinner("Agent is investigating…"):
            try:
                reply = _chat(user_input)
            except Exception as e:
                reply = f"❌ Error: {e}"
        st.session_state.messages.append({"role": "assistant", "content": reply})
        st.rerun()


# ── RIGHT: Blast Radius + Post-Mortem ────────────────────────────────────────

with col_right:
    st.subheader("💥 Blast Radius")

    with st.expander("⚙️ Incident Window", expanded=False):
        st.session_state.incident_start = st.text_input(
            "Start (UTC)", st.session_state.incident_start)
        st.session_state.incident_end = st.text_input(
            "End (UTC)", st.session_state.incident_end)

    if st.button("📊 Calculate Impact", use_container_width=True):
        with st.spinner("Querying Elasticsearch…"):
            try:
                st.session_state.blast_radius = _blast_radius()
            except Exception as e:
                st.error(f"Error: {e}")

    if st.session_state.blast_radius:
        br = st.session_state.blast_radius
        m1, m2 = st.columns(2)
        m1.metric("Users Affected",     f"{br.get('users_affected', 0):,}")
        m2.metric("Transactions Failed", f"{br.get('transactions_failed', 0):,}")
        m1.metric("Revenue Lost",        f"${br.get('revenue_lost', 0):,.0f}")
        m2.metric("Proj. Loss/hr",       f"${br.get('revenue_projected_1h', 0):,.0f}")

        with st.expander("Downstream Failures"):
            downstream = br.get("downstream_failures", [])
            if downstream:
                for svc in downstream:
                    st.markdown(f"- `{svc}`")
            else:
                st.write("No downstream failures detected.")

        with st.expander("Affected Services"):
            for svc in br.get("services_affected", []):
                st.markdown(f"- `{svc}`")

    st.divider()
    st.subheader("📄 Post-Mortem")
    st.caption("Requires a chat investigation first")

    if st.button("✍️ Generate Post-Mortem", use_container_width=True,
                 disabled=len(st.session_state.messages) == 0):
        with st.spinner("Generating post-mortem with Gemini…"):
            try:
                st.session_state.postmortem = _postmortem()
            except Exception as e:
                st.error(f"Error: {e}")

    if st.session_state.postmortem:
        with st.expander("📋 View Post-Mortem", expanded=True):
            st.markdown(st.session_state.postmortem)
        st.download_button(
            "⬇️ Download (.md)",
            data=st.session_state.postmortem,
            file_name="postmortem.md",
            mime="text/markdown",
            use_container_width=True,
        )
