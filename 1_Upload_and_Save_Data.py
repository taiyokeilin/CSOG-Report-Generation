import re
import os
import math
import hashlib
from datetime import date

import streamlit as st

from db import get_supabase
from parsers import parse_file
from drive_upload import (
    drive_secrets_configured, get_drive_service,
    list_input_subfolders, list_files_in_folder, download_drive_file,
)

st.set_page_config(page_title="Upload Session", page_icon="⛳", layout="wide")

st.markdown("""
<style>
    .main-header {font-size: 2rem; font-weight: 700; color: #1F4E79; margin-bottom: 0;}
    .sub-header {font-size: 1rem; color: #555; margin-bottom: 1.5rem;}
    .section-header {font-size: 1.2rem; font-weight: 600; color: #2E75B6;
                     border-bottom: 2px solid #BDD7EE; padding-bottom: 4px; margin-top: 1.5rem;}
</style>
""", unsafe_allow_html=True)

# ── Header ────────────────────────────────────────────────────────────────────
title_col, logo_col = st.columns([5, 1])
with title_col:
    st.markdown('<p class="main-header">⛳ Upload Session</p>', unsafe_allow_html=True)
    st.markdown('<p class="sub-header">Load a launch monitor file → fill in session details → save</p>',
                unsafe_allow_html=True)
with logo_col:
    for _logo in ("csog_logo.png", "logo.png"):
        if os.path.exists(_logo):
            st.image(_logo, width=100)
            break

# ── Constants ─────────────────────────────────────────────────────────────────
MONITOR_TYPES = {
    "TrackMan": "trackman",
    "TrackMan (Normalized)": "trackman_normalized",
    "Foresight": "foresight",
    "FlightScope": "flightscope",
}

SHOT_COLUMNS = [
    "shot_num_session", "shot_num_club", "club",
    "ball_speed_mph", "launch_angle_deg", "side_angle_deg",
    "backspin_rpm", "side_spin_rpm", "tilt_angle_deg", "total_spin_rpm",
    "carry_yd", "total_yd", "offline_yd", "descent_angle_deg",
    "peak_height_ft", "to_pin_ft", "club_speed_mph", "smash_factor",
    "angle_of_attack_deg", "club_path_deg", "face_angle_deg", "face_to_path_deg",
    "dynamic_lie_deg", "dynamic_loft_deg", "closure_rate_dps",
    "face_impact_horizontal_mm", "face_impact_vertical_mm", "face_impact_from_center_mm",
]


# ── Database helpers ──────────────────────────────────────────────────────────
try:
    sb = get_supabase()
except Exception:
    sb = None

if sb is None:
    st.error("Supabase isn't configured. Add a `[supabase]` section (url and key) to the Streamlit secrets.")
    st.stop()


@st.cache_data(ttl=30)
def load_locations():
    return get_supabase().table("locations").select("location_id, name").eq("active", True).order("name").execute().data or []


@st.cache_data(ttl=30)
def load_programs():
    return get_supabase().table("programs").select("program_id, name").eq("active", True).order("name").execute().data or []


@st.cache_data(ttl=30)
def load_coaches():
    rows = (get_supabase().table("coaches")
            .select("coach_id, first_name, last_name, location_id")
            .eq("active", True).execute().data or [])
    return sorted(rows, key=lambda c: (c["last_name"].lower(), c["first_name"].lower()))


@st.cache_data(ttl=30)
def load_players():
    rows = (get_supabase().table("players")
            .select("player_id, first_name, last_name, coach_id, location_id, program_id")
            .eq("active", True).execute().data or [])
    return sorted(rows, key=lambda p: (p["last_name"].lower(), p["first_name"].lower()))


def clear_lookups():
    for fn in (load_locations, load_programs, load_coaches, load_players):
        fn.clear()


def _clean(v):
    """Make a value JSON-safe: NaN -> None, numpy scalars -> python."""
    if v is None:
        return None
    if hasattr(v, "item"):
        try:
            v = v.item()
        except Exception:
            pass
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


def df_to_records(df):
    try:
        return df.to_dicts()          # polars
    except AttributeError:
        return df.to_dict("records")  # pandas


def sanitize_filename(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", name or "file")


@st.cache_data(show_spinner=False)
def parse_cached(file_bytes: bytes, monitor_key: str):
    return parse_file(file_bytes, monitor_key)


# ── Flash message from the last save ─────────────────────────────────────────
if "last_saved" in st.session_state:
    st.success(st.session_state.pop("last_saved"))

if "uploader_n" not in st.session_state:
    st.session_state.uploader_n = 0


# ── SECTION 1: Load file ──────────────────────────────────────────────────────
st.markdown('<p class="section-header">1 · Load Data File</p>', unsafe_allow_html=True)

monitor_label = st.selectbox(
    "Launch Monitor", list(MONITOR_TYPES.keys()),
    help="CSV or XLSX accepted for all launch monitors.",
)
monitor_key = MONITOR_TYPES[monitor_label]

drive_available = drive_secrets_configured()
if drive_available:
    tab_drive, tab_local = st.tabs(["☁️ Browse Google Drive", "💻 Upload from Computer"])
else:
    tab_local = st.container()
    tab_drive = None

if drive_available:
    with tab_drive:
        service = get_drive_service()
        if service:
            subfolders = list_input_subfolders(service)
            if subfolders:
                folder_names = [f[0] for f in subfolders]
                folder_ids = [f[1] for f in subfolders]
                idx = st.selectbox("Select folder", range(len(folder_names)),
                                   format_func=lambda i: folder_names[i], key="db_input_folder")
                files = list_files_in_folder(service, folder_ids[idx])
                if files:
                    names = [f["name"] for f in files]
                    sel = st.selectbox("Select a file", names, key="db_drive_file")
                    sel_file = next(f for f in files if f["name"] == sel)
                    if st.button("Load from Drive", key="db_load_drive"):
                        with st.spinner("Downloading…"):
                            st.session_state.db_file_bytes = download_drive_file(service, sel_file["id"])
                        st.session_state.db_file_name = sel
                else:
                    st.info("No files found in the selected folder.")
            else:
                st.error("Could not access the configured Drive folder.")
        else:
            st.error("Could not connect to Google Drive.")

with tab_local:
    uploaded = st.file_uploader(
        "Upload launch monitor file", type=["csv", "xlsx"],
        key=f"db_local_upload_{st.session_state.uploader_n}",
    )
    if uploaded:
        st.session_state.db_file_bytes = uploaded.getvalue()
        st.session_state.db_file_name = uploaded.name

df = None
file_bytes = st.session_state.get("db_file_bytes")
file_name = st.session_state.get("db_file_name")
file_hash = None

if file_bytes:
    file_hash = hashlib.sha256(file_bytes).hexdigest()
    try:
        with st.spinner("Parsing file…"):
            df = parse_cached(file_bytes, monitor_key)
    except Exception as e:
        st.error(f"❌ Couldn't read **{file_name}** as {monitor_label}: {e}. "
                 "Check that the Launch Monitor selection matches the file.")
        df = None

records = []
if df is not None:
    records = df_to_records(df)
    clubs_in_file = sorted({r.get("club") for r in records if r.get("club")})
    st.success(f"✅ **{file_name}** — {len(records)} shots across {len(clubs_in_file)} clubs")
    with st.expander("Shots per club"):
        counts = {}
        for r in records:
            counts[r.get("club")] = counts.get(r.get("club"), 0) + 1
        st.dataframe([{"Club": c, "Shots": n} for c, n in sorted(counts.items(), key=lambda kv: str(kv[0]))],
                     hide_index=True, use_container_width=True)

# Exact same file already saved?
already_saved = None
if file_hash:
    try:
        hit = (sb.table("sessions")
               .select("session_id, session_date, players(first_name, last_name)")
               .eq("file_sha256", file_hash).limit(1).execute().data)
        if hit:
            already_saved = hit[0]
    except Exception:
        already_saved = None
    if already_saved:
        p = already_saved.get("players") or {}
        who = f"{p.get('first_name', '')} {p.get('last_name', '')}".strip() or "a player"
        st.warning(f"⚠️ This exact file was already saved as a session for **{who}** on "
                   f"{already_saved['session_date']}. It can't be saved twice.")


# ── SECTION 2: Session details ────────────────────────────────────────────────
st.markdown('<p class="section-header">2 · Session Details</p>', unsafe_allow_html=True)

locations = load_locations()
programs = load_programs()
coaches = load_coaches()
players = load_players()

loc_by_id = {l["location_id"]: l["name"] for l in locations}
prog_by_id = {p["program_id"]: p["name"] for p in programs}
coach_label = {c["coach_id"]: f"{c['first_name']} {c['last_name']}" for c in coaches}
coach_ids = [c["coach_id"] for c in coaches]
loc_ids = [l["location_id"] for l in locations]
prog_ids = [p["program_id"] for p in programs]

# ── Uploader (coach) ──────────────────────────────────────────────────────────
uploader_id = st.selectbox(
    "Coach (you)", coach_ids, index=None,
    format_func=lambda i: coach_label[i],
    placeholder="Select your name…" if coach_ids else "No coaches yet — add one below",
    key="uploader_id",
)

with st.expander("Not in the list? Add a coach"):
    a1, a2, a3 = st.columns(3)
    nc_first = a1.text_input("First name", key="nc_first")
    nc_last = a2.text_input("Last name", key="nc_last")
    nc_email = a3.text_input("Google email", key="nc_email",
                             help="Used for sign-in later. Stored in lowercase.")
    if st.button("Add coach", key="add_coach"):
        if not (nc_first.strip() and nc_last.strip() and nc_email.strip()):
            st.warning("Enter first name, last name and email.")
        else:
            try:
                sb.table("coaches").insert({
                    "first_name": nc_first.strip(),
                    "last_name": nc_last.strip(),
                    "email": nc_email.strip().lower(),
                }).execute()
                clear_lookups()
                st.success(f"Added {nc_first.strip()} {nc_last.strip()}.")
                st.rerun()
            except Exception as e:
                st.error(f"Couldn't add coach (is the email already used?): {e}")

# ── Player ────────────────────────────────────────────────────────────────────
player_mode = st.radio("Player", ["Select existing player", "Add new player"], horizontal=True)

player_id = None
player_rec = None
new_first = new_last = ""
new_coach_id = None

if player_mode == "Select existing player":
    if players:
        player_label = {p["player_id"]: f"{p['last_name']}, {p['first_name']}" for p in players}
        player_id = st.selectbox(
            "Select player", [p["player_id"] for p in players], index=None,
            format_func=lambda i: player_label[i], placeholder="Type to search…",
            key="existing_player",
        )
        if player_id:
            player_rec = next(p for p in players if p["player_id"] == player_id)
            st.caption(f"Coach: {coach_label.get(player_rec.get('coach_id'), '—')}")
    else:
        st.info("No players yet. Choose **Add new player**.")
else:
    n1, n2 = st.columns(2)
    new_first = n1.text_input("Player first name", key="np_first").strip()
    new_last = n2.text_input("Player last name", key="np_last").strip()
    default_idx = coach_ids.index(uploader_id) if uploader_id in coach_ids else None
    new_coach_id = st.selectbox(
        "Player's coach", coach_ids, index=default_idx,
        format_func=lambda i: coach_label[i], placeholder="Select a coach…",
        key=f"np_coach_{uploader_id}",
    )
    if new_first and new_last:
        dupes = [p for p in players
                 if p["first_name"].lower() == new_first.lower() and p["last_name"].lower() == new_last.lower()]
        if dupes:
            st.warning(f"A player named {new_first} {new_last} already exists. "
                       "Switch to **Select existing player** unless this is a different person.")

# ── Session metadata ──────────────────────────────────────────────────────────
default_loc = loc_ids.index(player_rec["location_id"]) if player_rec and player_rec.get("location_id") in loc_ids else None
default_prog = prog_ids.index(player_rec["program_id"]) if player_rec and player_rec.get("program_id") in prog_ids else None

m1, m2, m3, m4 = st.columns(4)
session_date = m1.date_input("Session Date", value=date.today())
week_label = m2.text_input("Week", placeholder="Optional")
location_id = m3.selectbox("Location", loc_ids, index=default_loc,
                           format_func=lambda i: loc_by_id[i], placeholder="Select a location…",
                           key=f"loc_{player_id}")
program_id = m4.selectbox("Program", prog_ids, index=default_prog,
                          format_func=lambda i: prog_by_id[i], placeholder="Select a program…",
                          key=f"prog_{player_id}")

# Same player + same day + same monitor already saved (different file)?
same_day = []
if player_id and not already_saved:
    try:
        same_day = (sb.table("sessions").select("session_id")
                    .eq("player_id", player_id).eq("session_date", session_date.isoformat())
                    .eq("launch_monitor_type", monitor_key).execute().data or [])
    except Exception:
        same_day = []
allow_same_day = True
if same_day:
    st.warning(f"This player already has {len(same_day)} {monitor_label} session(s) on {session_date}. "
               "Players can have two sessions in a day, so this is allowed if it's a different file.")
    allow_same_day = st.checkbox("Yes, save this as an additional session", key="allow_same_day")


# ── SECTION 3: Save ───────────────────────────────────────────────────────────
st.markdown('<p class="section-header">3 · Save to Database</p>', unsafe_allow_html=True)

problems = []
if df is None:
    problems.append("Load a file above.")
if already_saved:
    problems.append("This file has already been saved.")
if not uploader_id:
    problems.append("Select your name as the coach.")
if player_mode == "Select existing player" and not player_id:
    problems.append("Select a player.")
if player_mode == "Add new player":
    if not (new_first and new_last):
        problems.append("Enter the player's first and last name.")
    if not new_coach_id:
        problems.append("Select the player's coach.")
if not location_id:
    problems.append("Select a location.")
if not allow_same_day:
    problems.append("Confirm the additional session.")

for p in problems:
    st.caption(f"• {p}")

if st.button("💾 Save to Database", type="primary", disabled=bool(problems)):
    session_id = None
    try:
        with st.spinner("Saving…"):
            # Player
            pid = player_id
            if player_mode == "Add new player":
                ins = sb.table("players").insert({
                    "first_name": new_first,
                    "last_name": new_last,
                    "coach_id": new_coach_id,
                    "location_id": location_id,
                    "program_id": program_id,
                }).execute()
                pid = ins.data[0]["player_id"]
                player_coach_id = new_coach_id
            else:
                player_coach_id = player_rec.get("coach_id")

            # Original file to Storage
            storage_path = None
            try:
                storage_path = f"{monitor_key}/{pid}/{session_date.isoformat()}_{file_hash[:8]}_{sanitize_filename(file_name)}"
                ctype = ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                         if file_name.lower().endswith(".xlsx") else "text/csv")
                sb.storage.from_("raw-csvs").upload(
                    path=storage_path, file=file_bytes,
                    file_options={"content-type": ctype, "upsert": "true"},
                )
            except Exception as e:
                st.warning(f"⚠️ The original file couldn't be stored (the data will still be saved): {e}")
                storage_path = None

            # Session
            sess = sb.table("sessions").insert({
                "player_id": pid,
                "coach_id": player_coach_id or uploader_id,
                "uploaded_by": uploader_id,
                "session_date": session_date.isoformat(),
                "week": week_label or None,
                "launch_monitor_type": monitor_key,
                "location_id": location_id,
                "program_id": program_id,
                "raw_file_name": file_name,
                "raw_file_path": storage_path,
                "file_sha256": file_hash,
            }).execute()
            session_id = sess.data[0]["session_id"]

            # Shots
            shot_rows = []
            for r in records:
                row = {"session_id": session_id, "player_id": pid, "shot_date": _clean(r.get("date"))}
                for col in SHOT_COLUMNS:
                    row[col] = _clean(r.get(col))
                shot_rows.append(row)
            for i in range(0, len(shot_rows), 500):
                sb.table("shots").insert(shot_rows[i:i + 500]).execute()

        clear_lookups()
        st.session_state["last_saved"] = (
            f"✅ Saved {len(shot_rows)} shots from {session_date.strftime('%b %d, %Y')}. "
            "Ready for the next file."
        )
        for k in ("db_file_bytes", "db_file_name", "allow_same_day", "np_first", "np_last"):
            st.session_state.pop(k, None)
        st.session_state.uploader_n += 1
        st.rerun()

    except Exception as e:
        # Don't leave a half-saved session behind
        if session_id:
            try:
                sb.table("sessions").delete().eq("session_id", session_id).execute()
            except Exception:
                pass
        st.error(f"❌ Nothing was saved. Error: {e}")
