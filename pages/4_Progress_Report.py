import os
from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st

from db import get_supabase
from pdf_reports import build_progress_pdf

st.set_page_config(page_title="Progress Report", page_icon="⛳", layout="wide")

st.markdown("""
<style>
    .main-header {font-size: 2rem; font-weight: 700; color: #1F4E79; margin-bottom: 0;}
    .sub-header {font-size: 1rem; color: #555; margin-bottom: 1.5rem;}
    .section-header {font-size: 1.2rem; font-weight: 600; color: #2E75B6;
                     border-bottom: 2px solid #BDD7EE; padding-bottom: 4px; margin-top: 1.5rem;}
</style>
""", unsafe_allow_html=True)

title_col, logo_col = st.columns([5, 1])
with title_col:
    st.markdown('<p class="main-header">⛳ Progress Report</p>', unsafe_allow_html=True)
    st.markdown('<p class="sub-header">Pick a player and a date range → download a progress report PDF</p>',
                unsafe_allow_html=True)
with logo_col:
    if os.path.exists("csog_logo.png"):
        st.image("csog_logo.png", width=150)

try:
    sb = get_supabase()
except Exception:
    st.error("Supabase isn't configured. Add a `[supabase]` section (url and key) to the Streamlit secrets.")
    st.stop()

PAGE = 1000
SHOT_FIELDS = (
    "session_id, club, ball_speed_mph, club_speed_mph, smash_factor, carry_yd, total_yd, offline_yd"
)
MONITOR_LABELS = {
    "trackman": "TrackMan", "trackman_normalized": "TrackMan (Normalized)",
    "foresight": "Foresight", "flightscope": "FlightScope",
}


def _fetch_all(make_query):
    rows, start = [], 0
    while True:
        batch = make_query().range(start, start + PAGE - 1).execute().data or []
        rows.extend(batch)
        if len(batch) < PAGE:
            return rows
        start += PAGE


@st.cache_data(ttl=60)
def load_players():
    rows = (get_supabase().table("players").select("player_id, first_name, last_name, coach_id")
            .eq("active", True).execute().data or [])
    return sorted(rows, key=lambda p: (p["last_name"].lower(), p["first_name"].lower()))


@st.cache_data(ttl=60)
def load_coach_names():
    rows = get_supabase().table("coaches").select("coach_id, first_name, last_name").execute().data or []
    return {c["coach_id"]: f"{c['first_name']} {c['last_name']}" for c in rows}


@st.cache_data(ttl=60, show_spinner="Loading sessions…")
def load_period(player_id: str, date_from: str, date_to: str):
    """Sessions in the range, each with its shots and the targets that were in force that day."""
    sb = get_supabase()
    sessions = _fetch_all(lambda: (
        sb.table("sessions").select("session_id, session_date, launch_monitor_type, week")
        .eq("player_id", player_id).gte("session_date", date_from).lte("session_date", date_to)
        .order("session_date")
    ))
    if not sessions:
        return []
    ids = [s["session_id"] for s in sessions]
    shots = []
    for i in range(0, len(ids), 40):
        chunk = ids[i:i + 40]
        shots.extend(_fetch_all(lambda: (
            sb.table("shots").select(SHOT_FIELDS).in_("session_id", chunk).order("shot_id")
        )))
    shots_df = pd.DataFrame(shots, columns=SHOT_FIELDS.split(", "))
    for col in shots_df.columns:
        if col not in ("session_id", "club"):
            shots_df[col] = pd.to_numeric(shots_df[col], errors="coerce")

    target_cache = {}
    out = []
    for s in sessions:
        d = s["session_date"]
        if d not in target_cache:
            target_cache[d] = sb.rpc("player_targets_as_of", {"p_player_id": player_id, "p_date": d}).execute().data or []
        sdf = shots_df[shots_df["session_id"] == s["session_id"]].reset_index(drop=True)
        out.append({
            "date": datetime.strptime(d, "%Y-%m-%d").date(),
            "monitor": MONITOR_LABELS.get(s["launch_monitor_type"], s["launch_monitor_type"]),
            "df": sdf,
            "targets": target_cache[d],
        })
    return out


# ── Selection ─────────────────────────────────────────────────────────────────
players = load_players()
if not players:
    st.info("No players yet. Upload a session on the **Upload and Save Data** page first.")
    st.stop()

plabel = {p["player_id"]: f"{p['last_name']}, {p['first_name']}" for p in players}
st.markdown('<p class="section-header">1 · Player and Period</p>', unsafe_allow_html=True)
c1, c2, c3 = st.columns([2, 1, 1])
player_id = c1.selectbox("Player", [p["player_id"] for p in players], index=None,
                         format_func=lambda i: plabel[i], placeholder="Type to search…")
date_from = c2.date_input("From", value=date.today() - timedelta(days=180))
date_to = c3.date_input("To", value=date.today())

if not player_id:
    st.stop()
if date_from > date_to:
    st.warning("The start date is after the end date.")
    st.stop()

sessions = load_period(player_id, date_from.isoformat(), date_to.isoformat())
sessions = [s for s in sessions if not s["df"].empty]

st.markdown('<p class="section-header">2 · Sessions Included</p>', unsafe_allow_html=True)
if len(sessions) < 2:
    st.info("A progress report needs at least two sessions in the selected period. "
            "Try a wider date range, or upload more sessions.")
    st.stop()

with_targets = sum(1 for s in sessions if s["targets"])
st.dataframe(
    [{"Date": s["date"].strftime("%b %d, %Y"), "Launch monitor": s["monitor"],
      "Shots": len(s["df"]), "Clubs": s["df"]["club"].nunique(),
      "Saved targets": "Yes" if s["targets"] else "No"} for s in sessions],
    hide_index=True, use_container_width=True,
)
if with_targets == 0:
    st.warning("This player has no saved targets for these sessions, so the report will show club metrics "
               "but no goal results. Save targets on the Report Generator page to include them.")
elif with_targets < len(sessions):
    st.caption(f"Goal results are calculated for the {with_targets} sessions that have saved targets in force.")

# ── Generate ──────────────────────────────────────────────────────────────────
st.markdown('<p class="section-header">3 · Generate Report</p>', unsafe_allow_html=True)
player = next(p for p in players if p["player_id"] == player_id)
player_name = f"{player['first_name']} {player['last_name']}"

if st.button("📄 Generate Progress Report", type="primary"):
    with st.spinner("Building report…"):
        try:
            logo = "csog_logo.png" if os.path.exists("csog_logo.png") else None
            st.session_state.progress_pdf = build_progress_pdf(
                player_name, load_coach_names().get(player.get("coach_id"), ""),
                sessions, date_from, date_to, logo_path=logo,
            )
            st.session_state.progress_pdf_name = (
                f"{player['first_name']}_{player['last_name']}_Progress_{date_to.strftime('%Y%m%d')}.pdf"
            ).replace(" ", "_")
            st.success("✅ Report generated!")
        except Exception as e:
            st.error(f"❌ Couldn't build the report: {e}")
            raise

if "progress_pdf" in st.session_state:
    st.download_button("⬇️ Download PDF", data=st.session_state.progress_pdf,
                       file_name=st.session_state.progress_pdf_name, mime="application/pdf",
                       type="primary")
