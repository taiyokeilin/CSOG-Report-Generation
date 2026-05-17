import re
import os
import streamlit as st
from datetime import date

from parsers import parse_file
from drive_upload import (
    drive_secrets_configured, get_drive_service,
    list_input_subfolders, list_files_in_folder, download_drive_file,
)

st.set_page_config(
    page_title="Upload to Database",
    page_icon="⛳",
    layout="wide",
)

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
    st.markdown('<p class="main-header">⛳ Upload Session to Database</p>', unsafe_allow_html=True)
    st.markdown('<p class="sub-header">Upload a launch monitor CSV → fill in session details → save to database</p>', unsafe_allow_html=True)
with logo_col:
    if os.path.exists("logo.png"):
        st.image("logo.png", width=100)


# ── Supabase connection ───────────────────────────────────────────────────────
def get_supabase():
    try:
        from supabase import create_client
        url = st.secrets["supabase"]["url"]
        key = st.secrets["supabase"]["key"]
        return create_client(url, key)
    except Exception as e:
        return None

def supabase_configured() -> bool:
    try:
        return "supabase" in st.secrets and "url" in st.secrets["supabase"] and "key" in st.secrets["supabase"]
    except Exception:
        return False


# ── SECTION 1: Load File ──────────────────────────────────────────────────────
st.markdown('<p class="section-header">1 · Load Data File</p>', unsafe_allow_html=True)

monitor_type = st.selectbox(
    "Launch Monitor",
    ["TrackMan", "Foresight", "FlightScope"],
    help="CSV or XLSX accepted for all launch monitors.",
)

drive_available = drive_secrets_configured()

if drive_available:
    tab_drive, tab_local = st.tabs(["☁️ Browse Google Drive", "💻 Upload from Computer"])
else:
    tab_local = st.container()
    tab_drive = None

df = None
file_name = None


def _parse_and_show(file_bytes, filename):
    try:
        with st.spinner("Parsing file…"):
            result = parse_file(file_bytes, monitor_type.lower())
        try:
            n_shots = len(result)
            n_clubs = result["club"].n_unique()
        except Exception:
            n_shots = len(result)
            n_clubs = result["club"].nunique()
        st.success(f"✅ Loaded **{filename}** — {n_shots} shots across {n_clubs} clubs")
        return result
    except Exception as e:
        st.error(f"❌ Failed to parse file: {e}")
        return None


if drive_available:
    with tab_drive:
        service = get_drive_service()
        if service:
            subfolders = list_input_subfolders(service)
            if subfolders:
                folder_names = [f[0] for f in subfolders]
                folder_ids   = [f[1] for f in subfolders]
                selected_folder_idx = st.selectbox(
                    "Select folder", range(len(folder_names)),
                    format_func=lambda i: folder_names[i],
                    key="db_input_folder",
                )
                files = list_files_in_folder(service, folder_ids[selected_folder_idx])
                if files:
                    file_names = [f["name"] for f in files]
                    selected_name = st.selectbox("Select a file", file_names, key="db_drive_file")
                    selected_file = next(f for f in files if f["name"] == selected_name)
                    if st.button("Load from Drive", key="db_load_drive"):
                        with st.spinner("Downloading…"):
                            fb = download_drive_file(service, selected_file["id"])
                        st.session_state.db_file_bytes = fb
                        st.session_state.db_file_name  = selected_name
                        st.session_state.db_df = _parse_and_show(fb, selected_name)
                else:
                    st.info("No files found in the selected folder.")
            else:
                st.error("Could not access the configured Drive folder.")
        else:
            st.error("Could not connect to Google Drive.")

with tab_local:
    uploaded = st.file_uploader(
        "Upload launch monitor file", type=["csv", "xlsx"], key="db_local_upload"
    )
    if uploaded:
        fb = uploaded.read()
        st.session_state.db_file_bytes = fb
        st.session_state.db_file_name  = uploaded.name
        st.session_state.db_df = _parse_and_show(fb, uploaded.name)

if "db_df" in st.session_state and st.session_state.db_df is not None:
    df = st.session_state.db_df
    file_name = st.session_state.db_file_name


# ── SECTION 2: Session Details ────────────────────────────────────────────────
st.markdown('<p class="section-header">2 · Session Details</p>', unsafe_allow_html=True)

if not supabase_configured():
    st.warning("⚠️ Supabase is not configured. Add `[supabase]` credentials to Streamlit secrets.")

sb = get_supabase() if supabase_configured() else None

# ── Player lookup ─────────────────────────────────────────────────────────────
player_id = None
coach_id  = None

if sb:
    try:
        players_res = sb.table("players").select("player_id, first_name, last_name").execute()
        player_list = players_res.data or []
        player_options = {f"{p['first_name']} {p['last_name']}": p["player_id"] for p in player_list}
    except Exception:
        player_options = {}

    player_mode = st.radio(
        "Player",
        ["Select existing player", "Add new player"],
        horizontal=True,
    )

    if player_mode == "Select existing player":
        if player_options:
            selected_player = st.selectbox(
                "Select player",
                list(player_options.keys()),
                key="existing_player",
            )
            player_id = player_options[selected_player]

            # Show coach for selected player
            try:
                player_rec = next(p for p in player_list if p["player_id"] == player_id)
                coach_res = sb.table("coaches").select("coach_id, first_name, last_name").eq("coach_id", player_rec.get("coach_id", "")).execute()
                if coach_res.data:
                    c = coach_res.data[0]
                    st.caption(f"Coach: {c['first_name']} {c['last_name']}")
                    coach_id = c["coach_id"]
            except Exception:
                pass
        else:
            st.info("No players in database yet. Select 'Add new player'.")
            player_mode = "Add new player"

    if player_mode == "Add new player":
        c1, c2 = st.columns(2)
        new_player_first = c1.text_input("Player first name")
        new_player_last  = c2.text_input("Player last name")

        # Coach lookup/create
        try:
            coaches_res = sb.table("coaches").select("coach_id, first_name, last_name").execute()
            coach_list  = coaches_res.data or []
            coach_options = {f"{c['first_name']} {c['last_name']}": c["coach_id"] for c in coach_list}
        except Exception:
            coach_options = {}

        coach_mode = st.radio("Coach", ["Select existing coach", "Add new coach"], horizontal=True)

        if coach_mode == "Select existing coach" and coach_options:
            selected_coach = st.selectbox("Select coach", list(coach_options.keys()))
            coach_id = coach_options[selected_coach]
        else:
            c3, c4 = st.columns(2)
            new_coach_first = c3.text_input("Coach first name")
            new_coach_last  = c4.text_input("Coach last name")


else:
    c1, c2 = st.columns(2)
    new_player_first = c1.text_input("Player first name")
    new_player_last  = c2.text_input("Player last name")
    c3, c4 = st.columns(2)
    new_coach_first = c3.text_input("Coach first name")
    new_coach_last  = c4.text_input("Coach last name")


# ── Session metadata ──────────────────────────────────────────────────────────
m1, m2, m3, m4 = st.columns(4)
session_date = m1.date_input("Session Date", value=date.today())
week_label   = m2.text_input("Week", placeholder="optional")
location_override = m3.selectbox("Location", ["Buffalo Grove", "Chicago"])
new_program  = m4.selectbox("Program", ["Adult", "Golf for Life", "High School", "Junior", "Program 5"])


# ── SECTION 3: Save to Database ───────────────────────────────────────────────
st.markdown('<p class="section-header">3 · Save to Database</p>', unsafe_allow_html=True)

if df is not None and supabase_configured():
    if st.button("💾 Save to Database", type="primary"):
        import math

        def _safe(val):
            if val is None:
                return None
            if isinstance(val, float) and math.isnan(val):
                return None
            return val

        try:
            # ── Resolve coach ──────────────────────────────────────────────
            if coach_id is None:
                # Create new coach
                coach_res = sb.table("coaches").select("coach_id").eq("first_name", new_coach_first).eq("last_name", new_coach_last).execute()
                if coach_res.data:
                    coach_id = coach_res.data[0]["coach_id"]
                else:
                    ins = sb.table("coaches").insert({
                        "first_name": new_coach_first,
                        "last_name":  new_coach_last,
                        "location":   location_override or None,
                    }).execute()
                    coach_id = ins.data[0]["coach_id"]

            # ── Resolve player ─────────────────────────────────────────────
            if player_id is None:
                player_res = sb.table("players").select("player_id").eq("first_name", new_player_first).eq("last_name", new_player_last).execute()
                if player_res.data:
                    player_id = player_res.data[0]["player_id"]
                else:
                    ins = sb.table("players").insert({
                        "first_name": new_player_first,
                        "last_name":  new_player_last,
                        "coach_id":   coach_id,
                        "location":   location_override or None,
                        "program":    new_program or None,
                    }).execute()
                    player_id = ins.data[0]["player_id"]

            # ── Check for duplicate session ────────────────────────────────
            existing = sb.table("sessions").select("session_id").eq("player_id", player_id).eq("session_date", session_date.isoformat()).eq("launch_monitor_type", monitor_type.lower()).execute()
            if existing.data:
                st.warning(f"⚠️ A {monitor_type} session for this player on {session_date} already exists in the database.")
            else:
                # ── Upload raw file to storage ─────────────────────────────
                storage_path = None
                try:
                    storage_path = f"{monitor_type.lower()}/{new_player_last if player_mode == 'Add new player' else selected_player.split()[-1]}/{session_date.isoformat()}_{file_name}"
                    sb.storage.from_("raw-csvs").upload(
                        path=storage_path,
                        file=st.session_state.db_file_bytes,
                        file_options={"content-type": "text/csv", "upsert": "true"},
                    )
                except Exception as e:
                    st.warning(f"⚠️ Storage upload failed (continuing): {e}")
                    storage_path = None

                # ── Insert session ─────────────────────────────────────────
                session_res = sb.table("sessions").insert({
                    "player_id":           player_id,
                    "coach_id":            coach_id,
                    "session_date":        session_date.isoformat(),
                    "week":                week_label or None,
                    "launch_monitor_type": monitor_type.lower(),
                    "location":            location_override or None,
                    "raw_file_name":       file_name,
                    "raw_file_path":       storage_path,
                }).execute()
                session_id = session_res.data[0]["session_id"]

                # ── Insert shots ───────────────────────────────────────────
                try:
                    rows = df.to_dicts()
                except AttributeError:
                    rows = df.to_dict("records")

                shot_rows = [{
                    "session_id":                  session_id,
                    "player_id":                   player_id,
                    "shot_num_session":            row.get("shot_num_session"),
                    "shot_num_club":               row.get("shot_num_club"),
                    "club":                        row.get("club"),
                    "ball_speed_mph":              _safe(row.get("ball_speed_mph")),
                    "launch_angle_deg":            _safe(row.get("launch_angle_deg")),
                    "side_angle_deg":              _safe(row.get("side_angle_deg")),
                    "backspin_rpm":                _safe(row.get("backspin_rpm")),
                    "side_spin_rpm":               _safe(row.get("side_spin_rpm")),
                    "tilt_angle_deg":              _safe(row.get("tilt_angle_deg")),
                    "total_spin_rpm":              _safe(row.get("total_spin_rpm")),
                    "carry_yd":                    _safe(row.get("carry_yd")),
                    "total_yd":                    _safe(row.get("total_yd")),
                    "offline_yd":                  _safe(row.get("offline_yd")),
                    "descent_angle_deg":           _safe(row.get("descent_angle_deg")),
                    "peak_height_ft":              _safe(row.get("peak_height_ft")),
                    "to_pin_ft":                   _safe(row.get("to_pin_ft")),
                    "club_speed_mph":              _safe(row.get("club_speed_mph")),
                    "smash_factor":                _safe(row.get("smash_factor")),
                    "angle_of_attack_deg":         _safe(row.get("angle_of_attack_deg")),
                    "club_path_deg":               _safe(row.get("club_path_deg")),
                    "face_angle_deg":              _safe(row.get("face_angle_deg")),
                    "face_to_path_deg":            _safe(row.get("face_to_path_deg")),
                    "dynamic_lie_deg":             _safe(row.get("dynamic_lie_deg")),
                    "dynamic_loft_deg":            _safe(row.get("dynamic_loft_deg")),
                    "closure_rate_dps":            _safe(row.get("closure_rate_dps")),
                    "face_impact_horizontal_mm":   _safe(row.get("face_impact_horizontal_mm")),
                    "face_impact_vertical_mm":     _safe(row.get("face_impact_vertical_mm")),
                    "face_impact_from_center_mm":  _safe(row.get("face_impact_from_center_mm")),
                    "shot_date":                   row.get("date"),
                } for row in rows]

                batch_size = 500
                for i in range(0, len(shot_rows), batch_size):
                    sb.table("shots").insert(shot_rows[i:i + batch_size]).execute()

                # ── Insert raw device rows ─────────────────────────────────
                raw_table = f"{monitor_type.lower()}_raw"
                raw_rows = [{"session_id": session_id, "player_id": player_id, "raw_data": {k: _safe(v) for k, v in row.items()}} for row in rows]
                for i in range(0, len(raw_rows), batch_size):
                    sb.table(raw_table).insert(raw_rows[i:i + batch_size]).execute()

                st.success(f"✅ Saved! {len(shot_rows)} shots from {session_date} added to database.")

        except Exception as e:
            st.error(f"❌ Error saving to database: {e}")
            raise e

elif df is None:
    st.info("Load a file above to get started.")
elif not supabase_configured():
    st.info("Configure Supabase credentials in Streamlit secrets to enable database saving.")
