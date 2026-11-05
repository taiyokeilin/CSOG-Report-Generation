import re
import os
from datetime import date, datetime

import pandas as pd
import streamlit as st

from db import get_supabase
from report_builder import build_excel_report
from drive_upload import (
    drive_secrets_configured, get_drive_service,
    list_output_subfolders, upload_to_drive,
)

st.set_page_config(page_title="Report Generator", page_icon="⛳", layout="wide")

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
    st.markdown('<p class="main-header">⛳ Report Generator</p>', unsafe_allow_html=True)
    st.markdown('<p class="sub-header">Pick a player and session → review club targets → download the report</p>',
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
SHOT_FIELDS = [
    "shot_num_session", "shot_num_club", "club",
    "ball_speed_mph", "launch_angle_deg", "side_angle_deg",
    "backspin_rpm", "side_spin_rpm", "tilt_angle_deg", "total_spin_rpm",
    "carry_yd", "total_yd", "offline_yd", "descent_angle_deg",
    "peak_height_ft", "to_pin_ft", "club_speed_mph", "smash_factor",
    "angle_of_attack_deg", "club_path_deg", "face_angle_deg", "face_to_path_deg",
    "dynamic_lie_deg", "dynamic_loft_deg", "closure_rate_dps",
    "face_impact_horizontal_mm", "face_impact_vertical_mm", "face_impact_from_center_mm",
    "shot_date",
]
MONITOR_LABELS = {
    "trackman": "TrackMan", "trackman_normalized": "TrackMan (Normalized)",
    "foresight": "Foresight", "flightscope": "FlightScope",
}
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


# ── Data loading ──────────────────────────────────────────────────────────────
@st.cache_data(ttl=60)
def load_players():
    rows = (get_supabase().table("players")
            .select("player_id, first_name, last_name, coach_id")
            .eq("active", True).execute().data or [])
    return sorted(rows, key=lambda p: (p["last_name"].lower(), p["first_name"].lower()))


@st.cache_data(ttl=60)
def load_coach_names():
    rows = get_supabase().table("coaches").select("coach_id, first_name, last_name").execute().data or []
    return {c["coach_id"]: f"{c['first_name']} {c['last_name']}" for c in rows}


@st.cache_data(ttl=30)
def load_sessions(player_id: str):
    rows = (get_supabase().table("sessions")
            .select("session_id, session_date, week, launch_monitor_type")
            .eq("player_id", player_id).order("session_date", desc=True).execute().data or [])
    return rows


def load_session_shots(session_id: str) -> pd.DataFrame:
    rows, start = [], 0
    while True:
        batch = (get_supabase().table("shots").select(", ".join(SHOT_FIELDS))
                 .eq("session_id", session_id).order("shot_num_session")
                 .range(start, start + PAGE - 1).execute().data or [])
        rows.extend(batch)
        if len(batch) < PAGE:
            break
        start += PAGE
    df = pd.DataFrame(rows, columns=SHOT_FIELDS)
    for col in SHOT_FIELDS:
        if col not in ("club", "shot_date"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.rename(columns={"shot_date": "date"})


def load_targets(player_id: str, as_of: str):
    return get_supabase().rpc(
        "player_targets_as_of", {"p_player_id": player_id, "p_date": as_of}
    ).execute().data or []


# ── Club config helpers ───────────────────────────────────────────────────────
def _infer_distance(club_name):
    m = re.match(r"^(\d+)", (club_name or "").strip())
    if m and 10 <= int(m.group(1)) <= 300:
        return int(m.group(1))
    return None


def _infer_section(club_name):
    name = (club_name or "").lower()
    dist = _infer_distance(club_name)
    if any(x in name for x in ["putter", "putt"]):
        return "Putting"
    if any(x in name for x in ["yard", "chip", "pitch", "lob", "sand", "gap", "sw", "lw", "58", "60", "52", "56"]):
        if dist and dist <= 130:
            return "Wedge Play"
    if any(x in name for x in ["driver", "driv", "1w"]):
        return "Driving"
    if any(x in name for x in ["wood", "hybrid", "iron"]):
        return "Approach"
    if dist and dist <= 130:
        return "Wedge Play"
    return "Other"


def _default_cfg(club):
    section = _infer_section(club)
    return {
        "club": club, "level": 5, "skill": section, "include": True,
        "selected_target_types": list(DEFAULT_SELECTED.get(section, ["Proximity"])),
        "distance_yd": _infer_distance(club),
    }


def build_configs(clubs_in_session, saved_rows):
    """Saved targets first (in saved order), then defaults for clubs not yet saved."""
    by_club, order = {}, []
    for r in saved_rows:
        c = r["club"]
        if c not in by_club:
            by_club[c] = {"club": c, "level": r["level"], "skill": r["skill"], "include": True,
                          "selected_target_types": [], "distance_yd": r.get("distance_yd")}
            order.append(c)
        by_club[c]["selected_target_types"].append(r["target_type"])
    configs = [by_club[c] for c in order if c in clubs_in_session]
    seen = {c["club"] for c in configs}
    for club in clubs_in_session:
        if club not in seen:
            configs.append(_default_cfg(club))
    return configs, [c for c in clubs_in_session if c in by_club]


# ── SECTION 1: player + session ───────────────────────────────────────────────
st.markdown('<p class="section-header">1 · Player and Session</p>', unsafe_allow_html=True)

players = load_players()
if not players:
    st.info("No players yet. Upload a session on the **Upload and Save Data** page first.")
    st.stop()

plabel = {p["player_id"]: f"{p['last_name']}, {p['first_name']}" for p in players}
c1, c2 = st.columns(2)
player_id = c1.selectbox("Player", [p["player_id"] for p in players], index=None,
                         format_func=lambda i: plabel[i], placeholder="Type to search…")

sessions = load_sessions(player_id) if player_id else []
session_id = None
if player_id:
    if not sessions:
        c2.info("This player has no saved sessions yet.")
    else:
        def _slabel(s):
            d = datetime.strptime(s["session_date"], "%Y-%m-%d").strftime("%b %d, %Y")
            wk = f" · Week {s['week']}" if s.get("week") else ""
            return f"{d} · {MONITOR_LABELS.get(s['launch_monitor_type'], s['launch_monitor_type'])}{wk}"
        slabel = {s["session_id"]: _slabel(s) for s in sessions}
        session_id = c2.selectbox("Session", [s["session_id"] for s in sessions],
                                  format_func=lambda i: slabel[i])

if not session_id:
    st.stop()

session = next(s for s in sessions if s["session_id"] == session_id)
player = next(p for p in players if p["player_id"] == player_id)
session_date = datetime.strptime(session["session_date"], "%Y-%m-%d").date()

df = load_session_shots(session_id)
if df.empty:
    st.warning("This session has no shots.")
    st.stop()
clubs_in_session = list(dict.fromkeys(df["club"].dropna().tolist()))
st.caption(f"{len(df)} shots · {len(clubs_in_session)} clubs")


# ── SECTION 2: club targets ───────────────────────────────────────────────────
st.markdown('<p class="section-header">2 · Club Targets</p>', unsafe_allow_html=True)

saved_rows = load_targets(player_id, session["session_date"])
cfg_key = f"{player_id}|{session_id}"
if st.session_state.get("rg_cfg_key") != cfg_key:
    configs, _ = build_configs(clubs_in_session, saved_rows)
    st.session_state.rg_configs = configs
    st.session_state.rg_cfg_key = cfg_key
    st.session_state.rg_ver = st.session_state.get("rg_ver", 0) + 1
    st.session_state.pop("excel_bytes", None)
configs = st.session_state.rg_configs
ver = st.session_state.rg_ver

if saved_rows:
    latest = max(r["effective_date"] for r in saved_rows)
    st.caption(f"Loaded this player's saved targets (in effect since {latest}). "
               "Clubs without saved targets show suggested defaults.")
else:
    st.caption("No saved targets for this player yet. Showing suggested defaults. "
               "Adjust them, then use **Save as targets** below to keep them.")

LEVEL_INFO = """**Level Multipliers**

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

| Dist Band (yd) | Proximity (ft) | Range (yd) |
|----------------|----------------|------------|
| 20–29 | 6.4 | 2.0 |
| 30–39 | 7.2 | 2.0 |
| 40–49 | 10.4 | 3.0 |
| 50–59 | 13.2 | 3.0 |
| 60–79 | 13.2 | 4.0 |
| 80–99 | 14.2 | 4.0 |
| 100–129 | 16.5 | 5.0 |
| 130–149 | 19.0 | 5.0 |
| 150–179 | 23.0 | 6.0 |
| 180–199 | 28.6 | 6.0 |
| 200–209 | 28.6 | 7.0 |
| 210–229 | 34.4 | 7.0 |
| 230–249 | 43.2 | 8.0 |
| 250–259 | 48.0 | 8.0 |
"""

WIDTHS = [0.6, 0.7, 1.8, 0.8, 1.2, 2.2, 1.5, 1.6]
hdr = st.columns(WIDTHS)
for i, t in enumerate(["Order", "Include", "Club", "Shots", "Skill", "Target Types", "Distance (yd)"]):
    hdr[i].markdown(f"**{t}**")
with hdr[7]:
    lbl, btn = st.columns([2, 1])
    lbl.markdown("**Level (1–12)**")
    with btn.popover("ℹ️"):
        st.markdown(LEVEL_INFO)

shot_counts = df["club"].value_counts().to_dict()

for idx, cfg in enumerate(configs):
    row = st.columns(WIDTHS)
    up, dn = row[0].columns(2)
    if up.button("↑", key=f"up_{ver}_{idx}", disabled=(idx == 0)):
        configs[idx], configs[idx - 1] = configs[idx - 1], configs[idx]
        st.session_state.rg_ver += 1
        st.rerun()
    if dn.button("↓", key=f"dn_{ver}_{idx}", disabled=(idx == len(configs) - 1)):
        configs[idx], configs[idx + 1] = configs[idx + 1], configs[idx]
        st.session_state.rg_ver += 1
        st.rerun()

    cfg["include"] = row[1].checkbox("", value=cfg["include"], key=f"inc_{ver}_{idx}",
                                     label_visibility="collapsed")
    row[2].markdown(f"<div style='padding-top:8px'><b>{cfg['club']}</b></div>", unsafe_allow_html=True)
    row[3].markdown(f"<div style='padding-top:8px'>{shot_counts.get(cfg['club'], 0)}</div>",
                    unsafe_allow_html=True)

    prev = cfg["skill"]
    cfg["skill"] = row[4].selectbox("", SKILLS, index=SKILLS.index(cfg["skill"]),
                                    key=f"sec_{ver}_{idx}", label_visibility="collapsed")
    if cfg["skill"] != prev:
        cfg["selected_target_types"] = list(DEFAULT_SELECTED.get(cfg["skill"], ["Proximity"]))

    avail = SKILL_TARGET_TYPES.get(cfg["skill"], ["Proximity", "Distance Control"])
    valid = [t for t in cfg["selected_target_types"] if t in avail] or [avail[0]]
    cfg["selected_target_types"] = row[5].multiselect(
        "", avail, default=valid, key=f"tt_{ver}_{cfg['skill']}_{idx}", label_visibility="collapsed")

    dist = row[6].number_input("", min_value=0, max_value=400, value=int(cfg["distance_yd"] or 0),
                               step=5, key=f"dist_{ver}_{idx}", label_visibility="collapsed")
    cfg["distance_yd"] = dist if dist > 0 else None

    cfg["level"] = row[7].slider("", 1, 12, value=int(cfg["level"]),
                                 key=f"lvl_{ver}_{idx}", label_visibility="collapsed")

st.session_state.rg_configs = configs


# ── Save targets (versioned) ──────────────────────────────────────────────────
def save_targets(effective: date):
    """Write the current settings as new dated rows. Old rows are never edited."""
    eff = effective.isoformat()
    included = [c for c in configs if c["include"] and c["selected_target_types"]]
    new_keys = {(c["club"], c["skill"], t) for c in included for t in c["selected_target_types"]}

    rows = []
    for order, c in enumerate(configs):
        if not (c["include"] and c["selected_target_types"]):
            continue
        for t in c["selected_target_types"]:
            rows.append({
                "player_id": player_id, "club": c["club"], "skill": c["skill"], "target_type": t,
                "distance_yd": c["distance_yd"], "level": c["level"], "sort_order": order,
                "active": True, "effective_date": eff,
            })
    # Retire previous targets for clubs in this session that were unchecked or changed skill/type
    in_session = set(clubs_in_session)
    for r in saved_rows:
        key = (r["club"], r["skill"], r["target_type"])
        if r["club"] in in_session and key not in new_keys:
            rows.append({
                "player_id": player_id, "club": r["club"], "skill": r["skill"],
                "target_type": r["target_type"], "distance_yd": r.get("distance_yd"),
                "level": r["level"], "sort_order": r.get("sort_order", 0),
                "active": False, "effective_date": eff,
            })
    if not rows:
        return 0
    get_supabase().table("player_targets").upsert(
        rows, on_conflict="player_id,club,skill,target_type,effective_date").execute()
    return len(rows)


with st.expander("💾 Save as this player's targets"):
    st.caption("Saving adds a new dated version of this player's targets. Earlier versions are kept, "
               "so reports for older sessions still use the targets that applied then. "
               "Clubs you've unchecked are retired from this date on.")
    eff_date = st.date_input("Targets take effect on", value=session_date, key=f"eff_{cfg_key}")
    if st.button("Save targets", key="save_targets"):
        try:
            n = save_targets(eff_date)
            st.success(f"Saved {n} target rows effective {eff_date}.")
            st.cache_data.clear()
        except Exception as e:
            st.error(f"Couldn't save targets: {e}")


# ── SECTION 3: generate ───────────────────────────────────────────────────────
st.markdown('<p class="section-header">3 · Generate & Download Report</p>', unsafe_allow_html=True)


def expand_configs(cfgs):
    out = []
    for cfg in cfgs:
        if not cfg["include"]:
            continue
        for t in cfg["selected_target_types"]:
            out.append({"club": cfg["club"], "level": cfg["level"], "target_type": t,
                        "distance_yd": cfg["distance_yd"], "section": cfg["skill"]})
    return out


if st.button("🏌️ Generate Report", type="primary"):
    active = expand_configs(configs)
    if not active:
        st.warning("No clubs selected. Check **Include** for at least one club.")
    else:
        with st.spinner("Building report…"):
            session_info = {
                "player": f"{player['first_name']} {player['last_name']}",
                "coach": load_coach_names().get(player.get("coach_id"), ""),
                "date": session_date.strftime("%B %d, %Y"),
                "week": session.get("week") or "",
            }
            try:
                logo = "csog_logo.png" if os.path.exists("csog_logo.png") else None
                st.session_state.excel_bytes = build_excel_report(df, session_info, active, logo_path=logo)
                st.session_state.report_filename = (
                    f"{player['first_name']}_{player['last_name']}_{session_date.strftime('%Y%m%d')}.xlsx"
                ).replace(" ", "_")
                st.success("✅ Report generated!")
            except Exception as e:
                st.error(f"❌ Error generating report: {e}")
                raise

if "excel_bytes" in st.session_state:
    st.markdown("**Save report:**")
    dl_col, drive_col = st.columns([1, 2])
    with dl_col:
        st.download_button(
            "⬇️ Download to Computer", data=st.session_state.excel_bytes,
            file_name=st.session_state.report_filename,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )
    with drive_col:
        if drive_secrets_configured():
            service = get_drive_service()
            if service:
                subfolders = list_output_subfolders(service)
                if subfolders:
                    names = [f[0] for f in subfolders]
                    ids = [f[1] for f in subfolders]
                    i = st.selectbox("Upload to Drive folder", range(len(names)),
                                     format_func=lambda k: names[k], key="output_folder_select")
                    if st.button("☁️ Upload to Drive", use_container_width=True):
                        with st.spinner("Uploading…"):
                            try:
                                link = upload_to_drive(service, st.session_state.excel_bytes,
                                                       st.session_state.report_filename, ids[i])
                                st.success(f"✅ Uploaded! [Open in Drive]({link})")
                            except Exception as e:
                                st.error(f"Upload failed: {e}")
                else:
                    st.info("Could not access the configured output folder.")
            else:
                st.error("Could not connect to Google Drive.")
