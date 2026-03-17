import re
import streamlit as st
import polars as pl
from datetime import date

from parsers import parse_file
from report_builder import build_excel_report
from drive_upload import drive_secrets_configured, get_drive_service, list_drive_folders, upload_to_drive

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

st.markdown('<p class="main-header">⛳ Golf Practice Report Generator</p>', unsafe_allow_html=True)
st.markdown('<p class="sub-header">Upload a launch monitor CSV → configure clubs → download your report</p>', unsafe_allow_html=True)


# ── SECTION 1: Upload & Parse ─────────────────────────────────────────────
st.markdown('<p class="section-header">1 · Upload Data File</p>', unsafe_allow_html=True)

col1, col2 = st.columns([2, 1])
with col1:
    uploaded_file = st.file_uploader(
        "Upload your launch monitor file",
        type=["csv", "xlsx"],
        help="TrackMan and Foresight: CSV. FlightScope: XLSX.",
    )
with col2:
    monitor_type = st.selectbox(
        "Launch Monitor",
        ["TrackMan", "Foresight", "FlightScope"],
        help="Select the device that recorded this session",
    )

df = None

if uploaded_file:
    try:
        file_bytes = uploaded_file.read()
        with st.spinner("Parsing file…"):
            df = parse_file(file_bytes, monitor_type.lower())
        try:
            n_shots = len(df)
            n_clubs = df["club"].n_unique()
        except Exception:
            n_shots = len(df)
            n_clubs = df["club"].nunique()
        st.success(f"✅ Loaded **{n_shots} shots** across **{n_clubs} clubs**")
        with st.expander("Preview raw data", expanded=False):
            try:
                st.dataframe(df.head(30).to_pandas(), use_container_width=True)
            except Exception:
                st.dataframe(df.head(30), use_container_width=True)
    except Exception as e:
        st.error(f"❌ Failed to parse file: {e}")
        df = None


# ── SECTION 2: Session Info ───────────────────────────────────────────────
st.markdown('<p class="section-header">2 · Session Details</p>', unsafe_allow_html=True)

c1, c2, c3, c4 = st.columns(4)
with c1:
    player_name = st.text_input("Player Name", placeholder="e.g. Joe Matzek")
with c2:
    coach_name = st.text_input("Coach Name", placeholder="e.g. David Maslen, PGA")
with c3:
    session_date = st.date_input("Session Date", value=date.today())
with c4:
    week_label = st.text_input("Week / Label", placeholder="e.g. Week 12")


# ── SECTION 3: Club Configuration ────────────────────────────────────────
st.markdown('<p class="section-header">3 · Configure Clubs</p>', unsafe_allow_html=True)

SECTIONS = ["Putting", "Wedge Play", "Approach", "Driving", "Other"]

SECTION_TARGET_TYPES = {
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
        "section": section,
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

    header_cols = st.columns([0.4, 2.2, 1.2, 2.2, 1.5, 1.6, 1.0])
    header_cols[0].markdown("**Include**")
    header_cols[1].markdown("**Club**")
    header_cols[2].markdown("**Section**")
    header_cols[3].markdown("**Target Types**")
    header_cols[4].markdown("**Distance (yd)**")
    header_cols[5].markdown("**Level (1–12)**")
    header_cols[6].markdown("**Shots**")

    for idx, cfg in enumerate(configs):
        row_cols = st.columns([0.4, 2.2, 1.2, 2.2, 1.5, 1.6, 1.0])

        cfg["include"] = row_cols[0].checkbox(
            "", value=cfg["include"], key=f"inc_{idx}", label_visibility="collapsed"
        )
        row_cols[1].markdown(
            f"<div style='padding-top:8px'><b>{cfg['club']}</b></div>", unsafe_allow_html=True
        )

        prev_section = cfg["section"]
        cfg["section"] = row_cols[2].selectbox(
            "", SECTIONS, index=SECTIONS.index(cfg["section"]),
            key=f"sec_{idx}", label_visibility="collapsed"
        )
        if cfg["section"] != prev_section:
            cfg["selected_target_types"] = DEFAULT_SELECTED.get(cfg["section"], ["Proximity"])

        available_types = SECTION_TARGET_TYPES.get(cfg["section"], ["Proximity", "Distance Control"])
        valid_selected = [t for t in cfg.get("selected_target_types", []) if t in available_types]
        if not valid_selected:
            valid_selected = [available_types[0]]

        cfg["selected_target_types"] = row_cols[3].multiselect(
            "", available_types, default=valid_selected,
            key=f"tt_{idx}", label_visibility="collapsed"
        )

        dist_val = row_cols[4].number_input(
            "", min_value=0, max_value=400, value=cfg["distance_yd"] or 0,
            step=5, key=f"dist_{idx}", label_visibility="collapsed"
        )
        cfg["distance_yd"] = dist_val if dist_val > 0 else None

        cfg["level"] = row_cols[5].slider(
            "", 1, 12, value=cfg["level"], key=f"lvl_{idx}", label_visibility="collapsed"
        )

        try:
            shot_count = len(df.filter(pl.col("club") == cfg["club"]))
        except Exception:
            shot_count = len(df[df["club"] == cfg["club"]])
        row_cols[6].markdown(
            f"<div style='padding-top:8px'>{shot_count}</div>", unsafe_allow_html=True
        )

    st.session_state.club_configs = configs


# ── SECTION 4: Generate Report ───────────────────────────────────────────
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
                    "section": cfg["section"],
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
                        excel_bytes = build_excel_report(df, session_info, active_configs)
                        st.session_state.excel_bytes = excel_bytes
                        st.session_state.report_filename = (
                            f"{player_name.replace(' ', '_')}_{session_date.strftime('%Y%m%d')}.xlsx"
                        )
                        st.success("✅ Report generated successfully!")
                    except Exception as e:
                        st.error(f"❌ Error generating report: {e}")
                        raise e

    if "excel_bytes" in st.session_state:
        dl_col, drive_col = st.columns([1, 2])

        with dl_col:
            st.download_button(
                label="⬇️ Download Excel Report",
                data=st.session_state.excel_bytes,
                file_name=st.session_state.report_filename,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )

        with drive_col:
            if drive_secrets_configured():
                with st.expander("☁️ Upload to Google Drive", expanded=False):
                    service = get_drive_service()
                    if service and not isinstance(service, tuple):
                        folders = list_drive_folders(service)
                        if folders:
                            folder_names = ["(Root / My Drive)"] + [f[0] for f in folders]
                            folder_ids   = [None] + [f[1] for f in folders]
                            selected_idx = st.selectbox(
                                "Select Drive folder",
                                range(len(folder_names)),
                                format_func=lambda i: folder_names[i],
                            )
                            selected_folder_id = folder_ids[selected_idx]
                        else:
                            st.info("No folders found — file will upload to root Drive.")
                            selected_folder_id = None

                        if st.button("Upload to Drive", use_container_width=True):
                            with st.spinner("Uploading…"):
                                try:
                                    link = upload_to_drive(
                                        service,
                                        st.session_state.excel_bytes,
                                        st.session_state.report_filename,
                                        selected_folder_id,
                                    )
                                    st.success(f"✅ Uploaded! [Open in Drive]({link})")
                                except Exception as e:
                                    st.error(f"Upload failed: {e}")
                    else:
                        st.error("Could not connect to Google Drive. Check secrets configuration.")
            else:
                st.info(
                    "☁️ **Google Drive upload not configured.**\n\n"
                    "To enable, add a `[google_service_account]` section to your Streamlit secrets. "
                    "See the README for setup instructions.",
                )
else:
    st.info("Upload a file above to get started.")
