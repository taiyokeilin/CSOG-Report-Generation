import re
import os
import streamlit as st
import polars as pl
from datetime import date

from parsers import parse_file
from report_builder import build_excel_report
from drive_upload import (
    drive_secrets_configured, get_drive_service,
    list_input_files, download_drive_file,
    list_output_subfolders, upload_to_drive,
)

st.set_page_config(
    page_title="Golf Practice Report Generator",
    page_icon="⛳",
    layout="wide",
)

st.markdown("""
<style>
    .main-header {font-size: 2rem; font-weight: 700; color: #1F4E79; margin-bottom: 0;}
    .sub-header {font-size: 1rem; color: #555; margin-bottom: 1.5rem;}
    .section-header {font-size: 1.2rem; font-weight: 600; color: #2E75B6;
                     border-bottom: 2px solid #BDD7EE; padding-bottom: 4px; margin-top: 1.5rem;}
    .stDataFrame {font-size: 0.85rem;}
</style>
""", unsafe_allow_html=True)

# ── Header ────────────────────────────────────────────────────────────────────
title_col, logo_col = st.columns([5, 1])
with title_col:
    st.markdown('<p class="main-header">⛳ Golf Practice Report Generator</p>', unsafe_allow_html=True)
    st.markdown('<p class="sub-header">Upload a launch monitor CSV → configure clubs → download your report</p>', unsafe_allow_html=True)
with logo_col:
    if os.path.exists("csog_logo.png"):
        st.image("csog_logo.png", width=150)


# ── SECTION 1: Load Data File ─────────────────────────────────────────────
st.markdown('<p class="section-header">1 · Load Data File</p>', unsafe_allow_html=True)

monitor_type = st.selectbox(
    "Launch Monitor",
    ["TrackMan", "Foresight", "FlightScope"],
    help="TrackMan and Foresight: CSV. FlightScope: XLSX.",
)

drive_available = drive_secrets_configured()

if drive_available:
    tab_drive, tab_local = st.tabs(["☁️ Browse Google Drive", "💻 Upload from Computer"])
else:
    tab_local = st.container()
    tab_drive = None

df = None


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
        with st.expander("Preview raw data", expanded=False):
            try:
                st.dataframe(result.head(30).to_pandas(), use_container_width=True)
            except Exception:
                st.dataframe(result.head(30), use_container_width=True)
        return result
    except Exception as e:
        st.error(f"❌ Failed to parse file: {e}")
        return None


if drive_available:
    with tab_drive:
        # Require Google login to access Drive
        if not st.user.is_logged_in:
            st.info("Sign in with Google to browse your Shared Drive files.")
            st.button("Log in with Google", on_click=st.login, key="google_login")
        else:
            service = get_drive_service()
            if service:
                st.caption(f"Signed in as {st.user.name} · [Log out](javascript:void(0))")
                if st.button("Log out", key="logout_btn"):
                    st.logout()
                files = list_input_files(service)
                if files:
                    file_names = [f["name"] for f in files]
                    selected_name = st.selectbox(
                        "Select a file from Drive",
                        file_names,
                        key="drive_file_select",
                    )
                    selected_file = next(f for f in files if f["name"] == selected_name)
                    if st.button("Load from Drive", key="load_drive"):
                        with st.spinner("Downloading from Drive…"):
                            file_bytes = download_drive_file(service, selected_file["id"])
                        st.session_state.file_bytes = file_bytes
                        st.session_state.file_name = selected_name
                        st.session_state.df = _parse_and_show(file_bytes, selected_name)
                    elif "df" in st.session_state and st.session_state.get("file_name") == selected_name:
                        df = st.session_state.df
                else:
                    st.info("No CSV or XLSX files found in the configured Drive folder.")
            else:
                st.error("Could not connect to Google Drive. Try logging out and back in.")
                if st.button("Log out", key="logout_err"):
                    st.logout()

with tab_local:
    uploaded_file = st.file_uploader(
        "Upload your launch monitor file",
        type=["csv", "xlsx"],
        help="TrackMan and Foresight: CSV. FlightScope: XLSX.",
        key="local_upload",
    )
    if uploaded_file:
        file_bytes = uploaded_file.read()
        st.session_state.file_bytes = file_bytes
        st.session_state.file_name = uploaded_file.name
        st.session_state.df = _parse_and_show(file_bytes, uploaded_file.name)

if "df" in st.session_state and st.session_state.df is not None:
    df = st.session_state.df


# ── SECTION 2: Session Info ───────────────────────────────────────────────
st.markdown('<p class="section-header">2 · Session Details</p>', unsafe_allow_html=True)

c1, c2, c3, c4 = st.columns(4)
with c1:
    player_name = st.text_input("Player Name", placeholder="e.g. Dave Maslen")
with c2:
    coach_name = st.text_input("Coach Name", placeholder="e.g. Dave Maslen")
with c3:
    session_date = st.date_input("Session Date", value=date.today())
with c4:
    week_label = st.text_input("Week", placeholder="e.g. 5")


# ── SECTION 3: Club Configuration ────────────────────────────────────────
st.markdown('<p class="section-header">3 · Configure Clubs</p>', unsafe_allow_html=True)

SKILLS = ["Putting", "Wedge Play", "Approach", "Driving", "Other"]

SKILL_TARGET_TYPES = {
    "Wedge Play": ["Proximity", "Distance Control"],
    "Approach":   ["Proximity", "Distance Control"],
    "Driving":    ["Distance", "Dispersion"],
    "Putting":    ["Proximity", "Distance Control"],
    "Other":      ["Proximity", "Distance Control", "Distance", "Dispersion"],
}

DEFAULT_SELECTED = {
    "Wedge Play": ["Proximity", "Distance Control"],
    "Approach":   ["Proximity", "Distance Control"],
    "Driving":    ["Distance", "Dispersion"],
    "Putting":    ["Proximity"],
    "Other":      ["Proximity"],
}


def _infer_distance(club_name):
    if not club_name:
        return None
    m = re.match(r"^(\d+)", club_name.strip())
    if m:
        val = int(m.group(1))
        if 10 <= val <= 300:
            return val
    return None


def _infer_section(club_name):
    name = (club_name or "").lower()
    if any(x in name for x in ["putter", "putt"]):
        return "Putting"
    if any(x in name for x in ["yard", "chip", "pitch", "lob", "sand", "gap", "sw", "lw", "58", "60", "52", "56"]):
        if _infer_distance(club_name) and (_infer_distance(club_name) or 999) <= 130:
            return "Wedge Play"
    if any(x in name for x in ["driver", "driv", "1w"]):
        return "Driving"
    if any(x in name for x in ["wood", "hybrid", "iron"]):
        return "Approach"
    if _infer_distance(club_name) and (_infer_distance(club_name) or 0) <= 130:
        return "Wedge Play"
    return "Other"


def _default_club_state(club):
    section = _infer_section(club)
    return {
        "club": club,
        "level": 5,
        "selected_target_types": DEFAULT_SELECTED.get(section, ["Proximity"]),
        "distance_yd": _infer_distance(club),
        "skill": section,
        "include": True,
    }


if df is not None:
    try:
        clubs = df["club"].unique().sort().to_list()
    except Exception:
        clubs = sorted(df["club"].unique().tolist())

    if "club_configs" not in st.session_state:
        st.session_state.club_configs = [_default_club_state(c) for c in clubs]
    else:
        existing_clubs = {cfg["club"] for cfg in st.session_state.club_configs}
        if set(clubs) != existing_clubs:
            st.session_state.club_configs = [_default_club_state(c) for c in clubs]

    configs = st.session_state.club_configs

    level_info = """**Level Multipliers**

| Level | Rate Mult | Prox Mult |
|-------|-----------|-----------|
| 1 | 0.10 | 3.00 |
| 2 | 0.20 | 2.50 |
| 3 | 0.30 | 2.25 |
| 4 | 0.40 | 2.00 |
| 5 | 0.50 | 1.75 |
| 6 | 0.60 | 1.50 |
| 7 | 0.70 | 1.25 |
| 8 | 0.80 | 1.00 |
| 9 | 0.90 | 1.00 |
| 10 | 1.00 | 1.00 |
| 11 | 1.25 | 0.75 |
| 12 | 1.50 | 0.50 |

**Tour Targets**

| Dist (yd) | Proximity (ft) | Range (yd) |
|-----------|----------------|------------|
| 20 | 6.4 | 2.0 |
| 30 | 7.2 | 2.0 |
| 40 | 10.4 | 3.0 |
| 50 | 13.2 | 3.0 |
| 60 | 13.2 | 4.0 |
| 70 | 13.2 | 4.0 |
| 80 | 14.2 | 4.0 |
| 90 | 14.2 | 4.0 |
| 100 | 16.5 | 5.0 |
| 110 | 16.5 | 5.0 |
| 120 | 16.5 | 5.0 |
| 130 | 19.0 | 5.0 |
| 140 | 19.0 | 5.0 |
| 150 | 23.0 | 6.0 |
| 160 | 23.0 | 6.0 |
| 170 | 23.0 | 6.0 |
| 180 | 28.6 | 6.0 |
| 190 | 28.6 | 6.0 |
| 200 | 28.6 | 7.0 |
| 210 | 34.4 | 7.0 |
| 220 | 34.4 | 7.0 |
| 230 | 43.2 | 8.0 |
| 240 | 43.2 | 8.0 |
| 250 | 48.0 | 8.0 |
"""

    header_cols = st.columns([0.7, 1.8, 0.8, 1.2, 2.2, 1.5, 1.6])
    header_cols[0].markdown("**Include**")
    header_cols[1].markdown("**Club**")
    header_cols[2].markdown("**Shots**")
    header_cols[3].markdown("**Skill**")
    header_cols[4].markdown("**Target Types**")
    header_cols[5].markdown("**Distance (yd)**")
    with header_cols[6]:
        lbl_col, btn_col = st.columns([2, 1])
        lbl_col.markdown("**Level (1–12)**")
        with btn_col.popover("ℹ️", use_container_width=False):
            st.markdown(level_info)

    for idx, cfg in enumerate(configs):
        row_cols = st.columns([0.7, 1.8, 0.8, 1.2, 2.2, 1.5, 1.6])

        cfg["include"] = row_cols[0].checkbox(
            "", value=cfg["include"], key=f"inc_{idx}", label_visibility="collapsed"
        )
        row_cols[1].markdown(
            f"<div style='padding-top:8px'><b>{cfg['club']}</b></div>", unsafe_allow_html=True
        )

        try:
            shot_count = len(df.filter(pl.col("club") == cfg["club"]))
        except Exception:
            shot_count = len(df[df["club"] == cfg["club"]])
        row_cols[2].markdown(
            f"<div style='padding-top:8px'>{shot_count}</div>", unsafe_allow_html=True
        )

        prev_section = cfg["skill"]
        cfg["skill"] = row_cols[3].selectbox(
            "", SKILLS, index=SKILLS.index(cfg["skill"]),
            key=f"sec_{idx}", label_visibility="collapsed"
        )
        if cfg["skill"] != prev_section:
            cfg["selected_target_types"] = DEFAULT_SELECTED.get(cfg["skill"], ["Proximity"])

        available_types = SKILL_TARGET_TYPES.get(cfg["skill"], ["Proximity", "Distance Control"])
        valid_selected = [t for t in cfg.get("selected_target_types", []) if t in available_types]
        if not valid_selected:
            valid_selected = [available_types[0]]

        cfg["selected_target_types"] = row_cols[4].multiselect(
            "", available_types, default=valid_selected,
            key=f"tt_{idx}", label_visibility="collapsed"
        )

        dist_val = row_cols[5].number_input(
            "", min_value=0, max_value=400, value=cfg["distance_yd"] or 0,
            step=5, key=f"dist_{idx}", label_visibility="collapsed"
        )
        cfg["distance_yd"] = dist_val if dist_val > 0 else None

        cfg["level"] = row_cols[6].slider(
            "", 1, 12, value=cfg["level"], key=f"lvl_{idx}", label_visibility="collapsed"
        )

    st.session_state.club_configs = configs


# ── SECTION 4: Generate & Download Report ────────────────────────────────
st.markdown('<p class="section-header">4 · Generate & Download Report</p>', unsafe_allow_html=True)

if df is not None:
    def expand_configs(configs):
        rows = []
        for cfg in configs:
            if not cfg.get("include", True):
                continue
            for ttype in cfg.get("selected_target_types", ["Proximity"]):
                rows.append({
                    "club": cfg["club"],
                    "level": cfg["level"],
                    "target_type": ttype,
                    "distance_yd": cfg["distance_yd"],
                    "section": cfg["skill"],
                })
        return rows

    if st.button("🏌️ Generate Report", type="primary"):
        if not player_name:
            st.warning("Please enter a player name.")
        else:
            active_configs = expand_configs(st.session_state.club_configs)
            if not active_configs:
                st.warning("No clubs selected. Check 'Include' for at least one club.")
            else:
                with st.spinner("Building report…"):
                    session_info = {
                        "player": player_name,
                        "coach": coach_name,
                        "date": session_date.strftime("%B %d, %Y"),
                        "week": week_label,
                    }
                    try:
                        logo_path = "csog_logo.png" if os.path.exists("csog_logo.png") else None
                        excel_bytes = build_excel_report(df, session_info, active_configs, logo_path=logo_path)
                        st.session_state.excel_bytes = excel_bytes
                        st.session_state.report_filename = (
                            f"{player_name.replace(' ', '_')}_{session_date.strftime('%Y%m%d')}.xlsx"
                        )
                        st.success("✅ Report generated!")
                    except Exception as e:
                        st.error(f"❌ Error generating report: {e}")
                        raise e

    if "excel_bytes" in st.session_state:
        st.markdown("**Save report:**")
        dl_col, drive_col = st.columns([1, 2])

        with dl_col:
            st.download_button(
                label="⬇️ Download to Computer",
                data=st.session_state.excel_bytes,
                file_name=st.session_state.report_filename,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )

        with drive_col:
            if drive_secrets_configured() and st.user.is_logged_in:
                service = get_drive_service()
                if service:
                    subfolders = list_output_subfolders(service)
                    if subfolders:
                        folder_names = [f[0] for f in subfolders]
                        folder_ids   = [f[1] for f in subfolders]
                        selected_idx = st.selectbox(
                            "Upload to Drive folder",
                            range(len(folder_names)),
                            format_func=lambda i: folder_names[i],
                            key="output_folder_select",
                        )
                        if st.button("☁️ Upload to Drive", use_container_width=True):
                            with st.spinner("Uploading…"):
                                try:
                                    link = upload_to_drive(
                                        service,
                                        st.session_state.excel_bytes,
                                        st.session_state.report_filename,
                                        folder_ids[selected_idx],
                                    )
                                    st.success(f"✅ Uploaded! [Open in Drive]({link})")
                                except Exception as e:
                                    st.error(f"Upload failed: {e}")
                    else:
                        st.info("Could not access the configured output folder.")
                else:
                    st.error("Could not connect to Google Drive.")
            elif drive_secrets_configured() and not st.user.is_logged_in:
                st.info("Sign in with Google to enable Drive upload.")
            else:
                st.info("☁️ Google Drive not configured. See README for setup instructions.")
else:
    st.info("Load a file above to get started.")
