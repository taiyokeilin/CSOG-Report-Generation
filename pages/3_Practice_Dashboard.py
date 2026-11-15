import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd
from datetime import date, timedelta

from calculations import compute_club_stats

st.set_page_config(page_title="Practice Dashboard", layout="wide")

from auth import require_login, filter_players
access = require_login()

st.markdown("""
<style>
    .main-header {font-size: 2rem; font-weight: 700; color: #1F4E79; margin-bottom: 0;}
    .sub-header {font-size: 1rem; color: #555; margin-bottom: 1rem;}
    .section-header {font-size: 1.2rem; font-weight: 600; color: #2E75B6;
                     border-bottom: 2px solid #BDD7EE; padding-bottom: 4px; margin-top: 1.5rem;}
</style>
""", unsafe_allow_html=True)

st.markdown('<p class="main-header">Practice Dashboard</p>', unsafe_allow_html=True)
st.markdown('<p class="sub-header">Select a player and date range (from the menu on the left) to view their development</p>', unsafe_allow_html=True)


# ── Supabase connection ───────────────────────────────────────────────────────
from db import get_supabase

PAGE = 1000  # Supabase returns at most 1,000 rows per request, so we page through


def _fetch_all(make_query):
    """Run a query repeatedly with .range() until every row has been returned."""
    rows, start = [], 0
    while True:
        batch = make_query().range(start, start + PAGE - 1).execute().data or []
        rows.extend(batch)
        if len(batch) < PAGE:
            return rows
        start += PAGE


@st.cache_data(ttl=300)
def load_coach_names():
    rows = get_supabase().table("coaches").select("coach_id, first_name, last_name").execute().data or []
    return {c["coach_id"]: f"{c['first_name']} {c['last_name']}" for c in rows}


@st.cache_data(ttl=300)
def load_players():
    sb = get_supabase()
    res = (sb.table("players").select("player_id, first_name, last_name, coach_id, handedness")
           .eq("active", True).execute())
    return res.data or []


SHOT_FIELDS = (
    "shot_id, session_id, club, shot_num_session, shot_num_club, "
    "ball_speed_mph, launch_angle_deg, side_angle_deg, "
    "backspin_rpm, side_spin_rpm, tilt_angle_deg, total_spin_rpm, "
    "carry_yd, total_yd, offline_yd, descent_angle_deg, peak_height_ft, to_pin_ft, "
    "club_speed_mph, smash_factor, angle_of_attack_deg, "
    "club_path_deg, face_angle_deg, face_to_path_deg, "
    "dynamic_lie_deg, dynamic_loft_deg, closure_rate_dps, "
    "face_impact_horizontal_mm, face_impact_vertical_mm, face_impact_from_center_mm"
)


@st.cache_data(ttl=60)
def load_shots(player_id: str, date_from: str, date_to: str) -> pd.DataFrame:
    sb = get_supabase()

    # 1) Sessions for this player in the date range
    sessions = _fetch_all(lambda: (
        sb.table("sessions")
        .select("session_id, session_date, launch_monitor_type")
        .eq("player_id", player_id)
        .gte("session_date", date_from)
        .lte("session_date", date_to)
        .order("session_id")
    ))
    if not sessions:
        return pd.DataFrame()
    sessions_df = pd.DataFrame(sessions)

    # 2) Their shots, a handful of sessions at a time
    ids = sessions_df["session_id"].tolist()
    shots = []
    for i in range(0, len(ids), 40):
        chunk = ids[i:i + 40]
        shots.extend(_fetch_all(lambda: (
            sb.table("shots").select(SHOT_FIELDS)
            .in_("session_id", chunk).order("shot_id")
        )))
    if not shots:
        return pd.DataFrame()

    merged = pd.DataFrame(shots).merge(sessions_df, on="session_id", how="inner")
    merged["session_date"] = pd.to_datetime(merged["session_date"])
    # Columns that are empty for a whole player arrive as None; make them numeric (NaN)
    for col in SHOT_FIELDS.replace("\n", "").split(", "):
        if col not in ("shot_id", "session_id", "club"):
            merged[col] = pd.to_numeric(merged[col], errors="coerce")
    return merged


# ── Metric display names ──────────────────────────────────────────────────────
METRICS = {
    "Ball Speed (mph)":             "ball_speed_mph",
    "Club Speed (mph)":             "club_speed_mph",
    "Smash Factor":                 "smash_factor",
    "Launch Angle (°)":             "launch_angle_deg",
    "Side Angle (°)":               "side_angle_deg",
    "Backspin (rpm)":               "backspin_rpm",
    "Side Spin (rpm)":              "side_spin_rpm",
    "Tilt Angle (°)":               "tilt_angle_deg",
    "Total Spin (rpm)":             "total_spin_rpm",
    "Carry (yd)":                   "carry_yd",
    "Total Distance (yd)":          "total_yd",
    "Offline (yd)":                 "offline_yd",
    "Descent Angle (°)":            "descent_angle_deg",
    "Peak Height (ft)":             "peak_height_ft",
    "Proximity to Pin (ft)":        "to_pin_ft",
    "Angle of Attack (°)":          "angle_of_attack_deg",
    "Club Path (°)":                "club_path_deg",
    "Face Angle (°)":               "face_angle_deg",
    "Face to Path (°)":             "face_to_path_deg",
    "Dynamic Lie (°)":              "dynamic_lie_deg",
    "Dynamic Loft (°)":             "dynamic_loft_deg",
    "Closure Rate (dps)":           "closure_rate_dps",
    "Face Impact Horizontal (mm)":  "face_impact_horizontal_mm",
    "Face Impact Vertical (mm)":    "face_impact_vertical_mm",
    "Face Impact from Center (mm)": "face_impact_from_center_mm",
}


# ── Radar: share of shots that hit the target ──────────────────
RADAR_AXES = [  # (label, skill tag on the player's targets, target type)
    ("Wedge Proximity",           "Wedge Play", "Proximity"),
    ("Wedge Distance Control",    "Wedge Play", "Distance Control"),
    ("Approach Proximity",        "Approach",   "Proximity"),
    ("Approach Distance Control", "Approach",   "Distance Control"),
    ("Driving Distance",          "Driving",    "Distance"),
    ("Driving Dispersion",        "Driving",    "Dispersion"),
]


@st.cache_data(ttl=60)
def targets_as_of(player_id: str, day: str):
    """The player's targets as they stood on that day (not today's targets)."""
    return get_supabase().rpc("player_targets_as_of", {"p_player_id": player_id, "p_date": day}).execute().data or []


def goal_rows(shots: pd.DataFrame, player_id: str) -> pd.DataFrame:
    """One row per session day, club and target: attempts, successes, and the goal rate.
    Each day uses the targets that were saved as of that day (not today's targets)."""
    out = []
    if shots.empty:
        return pd.DataFrame(columns=["date", "club", "skill", "target_type", "attempts", "successes",
                                     "actual_pct", "target_pct"])
    for (day, club), group in shots.groupby([shots["session_date"].dt.strftime("%Y-%m-%d"), "club"]):
        for t in targets_as_of(player_id, day):
            if t["club"] != club:
                continue
            stats = compute_club_stats(group, club_name=club, target_type=t["target_type"],
                                       distance_yd=t.get("distance_yd"), level=int(t["level"]),
                                       section=t["skill"])
            if stats["successes"] is None:
                continue
            out.append({
                "date": pd.Timestamp(day), "club": club, "skill": t["skill"], "target_type": t["target_type"],
                "attempts": stats["attempts"], "successes": stats["successes"],
                "actual_pct": 100 * stats["successes"] / stats["attempts"],
                "target_pct": None if stats["target_pct"] is None else 100 * stats["target_pct"],
            })
    return pd.DataFrame(out, columns=["date", "club", "skill", "target_type", "attempts", "successes",
                                      "actual_pct", "target_pct"])


def radar_scores(shots: pd.DataFrame, player_id: str) -> dict:
    """label -> (successes, attempts) across all shots in the range."""
    rows = goal_rows(shots, player_id)
    totals = {}
    for label, skill, ttype in RADAR_AXES:
        sub = rows[(rows["skill"] == skill) & (rows["target_type"] == ttype)]
        totals[label] = (int(sub["successes"].sum()), int(sub["attempts"].sum()))
    return totals


def radar_figure(scores: dict) -> go.Figure:
    labels, values, hover = [], [], []
    for label, _, _ in RADAR_AXES:
        hit, n = scores[label]
        if n > 0:
            labels.append(label)
            values.append(round(100 * hit / n, 1))
            hover.append(f"{label}<br>{100 * hit / n:.0f}% on target ({hit} of {n} shots)")
        else:
            labels.append(f"{label}<br><i>no data</i>")
            values.append(None)
            hover.append(f"{label}<br>no data")
    complete = all(v is not None for v in values)
    closed = lambda xs: xs + xs[:1]
    fig = go.Figure(go.Scatterpolar(
        r=closed(values), theta=closed(labels), mode="lines+markers",
        fill="toself" if complete else "none", connectgaps=False,
        line=dict(color="#2E75B6", width=2), marker=dict(size=8, color="#1F4E79"),
        fillcolor="rgba(46,117,182,0.25)", text=closed(hover), hoverinfo="text",
    ))
    fig.update_layout(
        polar=dict(radialaxis=dict(range=[0, 100], tickvals=[25, 50, 75, 100], ticksuffix="%",
                                   tickfont=dict(size=9), gridcolor="#D9E2EC"),
                   angularaxis=dict(tickfont=dict(size=9), rotation=90, direction="clockwise"), bgcolor="rgba(0,0,0,0)"),
        showlegend=False, margin=dict(l=95, r=95, t=25, b=25), height=210,
    )
    return fig


# ── SIDEBAR FILTERS ──────────────────────────────────────────────────────────
players = filter_players(access, load_players())
if not players:
    st.warning("No players found in database.")
    st.stop()

players_sorted = sorted(players, key=lambda p: p["last_name"].lower())
player_options = {f"{p['last_name']}, {p['first_name']}": p["player_id"] for p in players_sorted}

with st.sidebar:
    st.markdown("### Filters")
    selected_player_name = st.selectbox("Player", list(player_options.keys()))
    selected_player_id   = player_options[selected_player_name]
    date_from = st.date_input("From", value=date.today() - timedelta(days=365))
    date_to   = st.date_input("To",   value=date.today())

    df = load_shots(selected_player_id, str(date_from), str(date_to))

    if df.empty:
        st.info("No shots found for this player in the selected date range.")
        st.stop()

    # Outlier flags are worked out per club and day, so they don't depend on the club filter
    if "ball_speed_mph" in df.columns and df["ball_speed_mph"].notna().any():
        medians = df.groupby(["club", df["session_date"].dt.date])["ball_speed_mph"].transform("median")
        df["_is_outlier"] = (
            df["ball_speed_mph"].notna() &
            ((df["ball_speed_mph"] < medians * 0.85) | (df["ball_speed_mph"] > medians * 1.15))
        )
    else:
        df["_is_outlier"] = False

    all_clubs = sorted(df["club"].dropna().unique().tolist())
    selected_clubs = st.multiselect("Clubs", all_clubs, default=all_clubs)
    radar_source = df                      # date range + outlier setting apply; the club filter does not
    df = df[df["club"].isin(selected_clubs)]

    if df.empty:
        st.info("No shots for the selected clubs.")
        st.stop()

    n_outliers = int(df["_is_outlier"].sum())
    exclude_outliers = st.checkbox(
        f"Exclude outliers ({n_outliers} shots ±15% from median ball speed)",
        value=False,
        key="exclude_outliers",
    )
    if exclude_outliers:
        df = df[~df["_is_outlier"]].copy()
        radar_source = radar_source[~radar_source["_is_outlier"]]




# ── PLAYER HEADER + RADAR ───────────────────────────────────────
st.markdown("""
<style>
    .player-name {font-size: 4.5rem; font-weight: 700; color: #1F4E79; line-height: 1.05; margin: 0.5rem 0 0.3rem 0;}
    .player-coach {font-size: 1.2rem; color: #555; margin: 0 0 1.2rem 0;}
    .stat-line {font-size: 1.3rem; font-weight: 600; color: #2E75B6; margin: 0 0 0.8rem 0;}
</style>
""", unsafe_allow_html=True)

last_name, first_name = [x.strip() for x in selected_player_name.split(",", 1)]
n_sessions = df["session_date"].dt.date.nunique()
head_col, radar_col = st.columns([5, 2])
player_rec = next(p for p in players_sorted if p["player_id"] == selected_player_id)
coach_name = load_coach_names().get(player_rec.get("coach_id"), "")
with head_col:
    st.markdown(f'<div class="player-name">{first_name} {last_name}</div>', unsafe_allow_html=True)
    if coach_name:
        st.markdown(f'<div class="player-coach">Coach: {coach_name}</div>', unsafe_allow_html=True)
    n_clubs = df["club"].nunique()
    plural = lambda n, word: f"{n:,} {word}" + ("" if n == 1 else "s")
    st.markdown(f'<div class="stat-line">{plural(len(df), "shot")} &bull; {plural(n_sessions, "session")} &bull; '
                f'{plural(n_clubs, "club")}</div>', unsafe_allow_html=True)
    if exclude_outliers:
        st.caption(f"{n_outliers} outlier shots excluded")
with radar_col:
    scores = radar_scores(radar_source, selected_player_id)
    st.plotly_chart(radar_figure(scores), use_container_width=True)
    st.caption("Share of shots on target, using the targets saved on each shot's day. "
               "Follows the date range and outlier setting, not the club filter.")


# ── Club face outlines for the strike location plot ──
import re as _re

# Club face outlines in mm, as a right-handed golfer sees them (toe on the left, heel on the right).
# Origin (0, 0) is the strike-plot centre. Irons and wedges are 78 mm long, centred left to right, and
# positioned by eye (iron 20 mm up, wedge 30 mm up and 56 mm tall); hybrids and fairway woods have it 40% of the way up, drivers at the vertical middle.
# Traced from reference photos and simplified.
FACE_OUTLINES = {
    "iron": (
        [-21.7, -23.9, -25.9, -27.8, -29.5, -31.1, -32.4, -33.6, -34.7, -35.7, -36.6, -37.3, -38, -38.4, -38.7, -38.9, -39, -39, -38.8, -38.4, -37.9, -37.2, -36.5, -35.5, -34.2, -32.8, -31.2, -29.5, -27.6, -25.6, -23.7, -21.7, -19.8, -17.6, -15.5, -13.1, -10.8, -8.5, -6.2, -3.9, -1.4, 0.9, 3.2, 5.7, 8, 10.3, 12.8, 15.1, 17.4, 19.8, 22.1, 24.3, 26.7, 29, 31.2, 33.5, 35.6, 37.4, 38.4, 38.8, 39, 39, 39, 39, 39, 39, 39, 38.9, 38.6, 37.8, 36.3, 34.5, 32.6, 30.7, 28.8, 26.9, 24.9, 22.9, 20.9, 18.9, 17, 15.2, 13.4, 11.5, 9.6, 7.6, 5.7, 3.8, 2, 0, -1.8, -3.8, -5.7, -7.6, -9.6, -11.4, -13.4, -15.4, -17.4, -19.6],
        [36.7, 36.3, 35.6, 34.6, 33.3, 31.8, 30.2, 28.5, 26.7, 25, 23.2, 21.2, 19.3, 17.1, 15, 12.6, 10.3, 7.9, 5.7, 3.6, 1.4, -0.6, -2.5, -4.3, -6, -7.7, -9.1, -10.5, -11.6, -12.4, -13.2, -13.7, -14.1, -14.2, -14.3, -14.4, -14.6, -14.7, -14.7, -14.8, -14.8, -14.8, -14.8, -14.8, -14.8, -14.8, -14.8, -14.8, -14.7, -14.7, -14.5, -14.4, -14.2, -14, -13.8, -13.6, -13.3, -12.5, -11, -9.1, -6.8, -4.4, -2, 0.3, 2.7, 5.1, 7.4, 9.7, 11.9, 13.6, 14.9, 15.9, 16.8, 17.7, 18.4, 19.3, 20.1, 20.9, 21.8, 22.6, 23.5, 24.3, 25.1, 25.9, 26.5, 27.3, 28, 28.9, 29.6, 30.3, 31, 31.7, 32.4, 33.1, 33.8, 34.6, 35.2, 35.9, 36.4, 36.7]),
    "wedge": (
        [-19.5, -21.8, -24, -26.1, -28.1, -29.9, -31.5, -32.9, -34.1, -35.2, -36, -36.8, -37.5, -38, -38.4, -38.7, -38.9, -39, -38.9, -38.8, -38.5, -38.1, -37.7, -37.1, -36.3, -35.5, -34.4, -33.2, -31.8, -30.2, -28.4, -26.5, -24.5, -22.4, -20.2, -17.9, -15.6, -13.3, -10.9, -8.5, -6.1, -3.7, -1.3, 1.2, 3.6, 6, 8.5, 10.9, 13.3, 15.7, 18, 20.4, 22.7, 25, 27.3, 29.6, 31.9, 34.1, 36.1, 37.8, 38.6, 39, 39, 39, 39, 39, 39, 39, 39, 38.8, 38.2, 37.1, 35.5, 33.8, 32.1, 30.4, 28.6, 26.8, 25.1, 23.3, 21.4, 19.6, 17.7, 15.9, 14, 12, 10, 8, 6, 4, 2, -0.1, -2.2, -4.2, -6.2, -8.3, -10.4, -12.6, -14.9, -17.2],
        [42.4, 42.1, 41.6, 40.7, 39.7, 38.4, 36.9, 35.2, 33.5, 31.7, 29.8, 27.9, 25.8, 23.8, 21.7, 19.6, 17.5, 15.5, 13.3, 11.1, 8.9, 6.8, 4.7, 2.6, 0.6, -1.3, -3.1, -4.8, -6.4, -7.9, -9.1, -10.1, -11, -11.6, -12.1, -12.5, -12.7, -12.9, -13.1, -13.3, -13.4, -13.5, -13.5, -13.5, -13.5, -13.5, -13.5, -13.4, -13.3, -13.2, -13, -12.8, -12.6, -12.4, -12.2, -11.9, -11.6, -11.2, -10.5, -9.5, -7.9, -5.8, -3.5, -1.3, 1.1, 3.3, 5.7, 8, 10.2, 12.5, 14.4, 16, 17.3, 18.6, 19.9, 21.1, 22.4, 23.7, 24.8, 26.1, 27.3, 28.4, 29.5, 30.5, 31.6, 32.6, 33.6, 34.5, 35.4, 36.3, 37.1, 37.9, 38.7, 39.4, 40, 40.7, 41.3, 41.8, 42.3, 42.5]),
    "hybrid": (
        [-25, -27.4, -29.8, -32.2, -34.6, -37, -39.3, -41.6, -43.7, -45.6, -47.2, -48.5, -49.4, -50.1, -50.7, -51.1, -51.3, -51.3, -51.2, -50.9, -50.2, -49, -47.5, -45.7, -43.9, -41.9, -39.8, -37.7, -35.6, -33.3, -31.1, -28.8, -26.5, -24.3, -22, -19.8, -17.4, -14.9, -12.4, -10, -7.5, -5.1, -2.6, -0.1, 2.3, 4.8, 7.3, 9.7, 12.2, 14.6, 17, 19.3, 21.5, 23.8, 26.1, 28.4, 30.7, 32.9, 35.2, 37.3, 39.1, 40.1, 40.6, 40.7, 40.7, 40.7, 40.7, 40.7, 40.7, 40.7, 40.6, 40.3, 39.4, 37.8, 35.7, 33.5, 31.2, 29, 26.8, 24.5, 22.3, 20, 17.7, 15.5, 13.2, 10.9, 8.6, 6.3, 4, 1.6, -0.7, -3.1, -5.5, -7.9, -10.3, -12.7, -15.2, -17.6, -20.1, -22.5],
        [23.9, 23.8, 23.7, 23.5, 23.4, 23.2, 22.9, 22.4, 21.6, 20.5, 18.9, 17, 15, 12.8, 10.7, 8.5, 6.2, 3.8, 1.4, -0.9, -2.9, -4.8, -6.5, -8, -9.4, -10.5, -11.5, -12.4, -13.1, -13.7, -14.2, -14.6, -15, -15.4, -15.7, -15.9, -16, -16, -16, -16, -16, -16, -16, -16, -16, -16, -16, -16, -16, -16, -15.9, -15.6, -15.3, -15, -14.7, -14.3, -13.9, -13.4, -12.9, -12.2, -11.3, -9.7, -7.5, -5.2, -2.7, -0.2, 2.2, 4.7, 7.1, 9.6, 12, 14, 15.3, 15.9, 16.2, 16.6, 17.1, 17.7, 18.2, 18.8, 19.2, 19.7, 20.2, 20.6, 21.1, 21.5, 21.9, 22.3, 22.6, 22.9, 23.2, 23.4, 23.6, 23.7, 23.8, 23.9, 24, 24, 24, 24]),
    "wood": (
        [-11, -13.5, -16, -18.5, -21, -23.5, -25.9, -28.4, -30.8, -33.2, -35.5, -37.9, -40.2, -42.5, -44.7, -46.8, -48.8, -50.7, -52.2, -52.9, -52.7, -51.9, -50.9, -49.8, -48.5, -47, -45.6, -44.2, -42.7, -41.1, -39.6, -37.8, -35.9, -33.9, -32, -30.3, -28.5, -26.7, -24.7, -22.5, -20.3, -18, -15.7, -13.3, -11, -8.7, -6.3, -3.8, -1.3, 1.3, 3.8, 6.2, 8.6, 10.9, 13.2, 15.6, 17.9, 20.2, 22.3, 24.3, 26.4, 28.6, 30.7, 32.9, 34.9, 36.8, 38.6, 40.2, 41.7, 43, 43.7, 44.1, 44.1, 44.1, 44.1, 44.1, 44, 43.6, 42.5, 40.7, 38.4, 36, 33.5, 31.1, 28.7, 26.2, 23.8, 21.4, 19, 16.6, 14.1, 11.7, 9.2, 6.7, 4.3, 1.7, -0.8, -3.3, -5.9, -8.4],
        [25.7, 25.6, 25.4, 25.2, 24.9, 24.6, 24.3, 23.9, 23.5, 23, 22.4, 21.8, 21.1, 20.4, 19.7, 18.8, 17.8, 16.7, 15.4, 13.7, 11.7, 9.7, 7.6, 5.6, 3.8, 2.1, 0.4, -1.2, -2.9, -4.4, -5.8, -7, -8.1, -9.3, -10.4, -11.4, -12.2, -12.8, -13.3, -13.9, -14.6, -15.2, -15.6, -16.1, -16.5, -16.7, -16.8, -16.7, -16.7, -16.7, -16.7, -16.8, -16.7, -16.4, -15.9, -15.4, -14.9, -14.4, -13.6, -12.7, -11.9, -11.2, -10.3, -9.3, -8, -6.5, -4.9, -3.1, -1.1, 0.9, 3.2, 5.6, 8.2, 10.8, 13.4, 16, 18.4, 20.3, 21.3, 21.2, 20.9, 20.8, 20.9, 21.2, 21.6, 22, 22.5, 22.9, 23.3, 23.7, 24.1, 24.4, 24.7, 25, 25.2, 25.4, 25.6, 25.7, 25.8, 25.8]),
    "driver": (
        [-16, -18.9, -21.7, -24.6, -27.4, -30.2, -32.9, -35.6, -38.3, -41, -43.7, -46.3, -48.9, -51.5, -54, -56, -57.5, -58.4, -59, -59.4, -59.3, -58.7, -57.8, -56.6, -55.3, -53.8, -52.3, -50.7, -49.1, -47.3, -45.3, -43.2, -41, -38.6, -36.2, -33.8, -31.4, -29, -26.5, -23.9, -21.3, -18.7, -16, -13.2, -10.4, -7.6, -4.8, -1.9, 0.9, 3.7, 6.5, 9.3, 12, 14.7, 17.3, 19.8, 22.3, 24.8, 27.3, 29.8, 32.2, 34.6, 36.9, 39.1, 41.2, 43.2, 45, 46.7, 48.4, 49.9, 51.3, 52.5, 53.1, 52.5, 50.8, 48.7, 46.4, 44.1, 41.8, 39.5, 37.2, 34.9, 32.6, 30.2, 27.8, 25.3, 22.8, 20.2, 17.5, 14.8, 12.2, 9.4, 6.7, 4, 1.2, -1.6, -4.5, -7.3, -10.2, -13.1],
        [28.2, 28.1, 28, 27.8, 27.6, 27.3, 26.9, 26.4, 25.9, 25.3, 24.7, 24, 23.3, 22.5, 21.6, 20.2, 18.3, 16, 13.7, 11.4, 9.2, 6.8, 4.3, 1.9, -0.3, -2.4, -4.7, -6.9, -9.2, -11.3, -13.3, -15.2, -16.9, -18.3, -19.5, -20.7, -21.9, -23.1, -24.2, -25.1, -25.8, -26.4, -26.9, -27.3, -27.7, -27.9, -28, -28.1, -28.1, -28, -27.8, -27.5, -27, -26.5, -25.8, -25, -24.2, -23.3, -22.3, -21.2, -20, -18.7, -17.2, -15.5, -13.6, -11.6, -9.6, -7.5, -5.4, -3.2, -1, 1.2, 3.4, 5.3, 6.9, 8.4, 9.9, 11.4, 12.9, 14.4, 15.9, 17.4, 18.9, 20.3, 21.5, 22.5, 23.3, 24.1, 24.7, 25.3, 25.9, 26.4, 26.8, 27.2, 27.5, 27.8, 28, 28.1, 28.2, 28.3]),
}


def club_category(name: str):
    """driver / wood / hybrid / iron / wedge from a club name, or None when it can't be told."""
    n = (name or "").strip().lower()
    if not n:
        return None
    if "driver" in n or _re.search(r"\b1\s?w\b", n) or "1 wood" in n:
        return "driver"
    if "hybrid" in n or "rescue" in n or _re.search(r"\b\d{1,2}\s?h\b", n) or "hyb" in n:
        return "hybrid"
    if "wood" in n or _re.search(r"\b\d{1,2}\s?w\b", n):
        return "wood"
    if "iron" in n or _re.fullmatch(r"\d\s?i", n):
        return "iron"
    if ("wedge" in n or _re.search(r"\b(pw|gw|aw|sw|lw|uw)\b", n)
            or _re.search(r"\b(4[4-9]|5\d|6[0-4])\b", n)):
        return "wedge"
    return None


def face_outline(category: str, toe_sign: int):
    """Closed outline (x, y in mm) of a club face. toe_sign is +1 when the toe is drawn on the right
    (left-handed view) and -1 when it is drawn on the left (right-handed view)."""
    import numpy as np
    xs, ys = FACE_OUTLINES[category]
    x, y = np.array(xs + xs[:1]), np.array(ys + ys[:1])
    return (-toe_sign) * x, y


def _darken_hex(hex_color: str, factor: float = 0.85) -> str:
    """Return a slightly darker version of a hex color."""
    hex_color = hex_color.lstrip("#")
    r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
    return "#{:02x}{:02x}{:02x}".format(int(r*factor), int(g*factor), int(b*factor))


PAGES = ["GOALS", "DELIVERY/IMPACT", "BALL FLIGHT"]
st.markdown("""<style>
.st-key-page_tabs, .st-key-page_tabs > div, .st-key-page_tabs [data-testid="stButtonGroup"], .st-key-page_tabs [role="radiogroup"] {width: 100% !important; gap: 0 !important;}
.st-key-page_tabs [role="radiogroup"] {display: flex !important; flex-wrap: nowrap !important; max-width: none !important;}
.st-key-page_tabs button {flex: 1 1 0 !important; min-width: 0 !important; min-height: 2.6rem !important; border-radius: 0 !important;
    background: #FFFFFF !important; border: 1px solid #C9CED3 !important; margin-left: -1px !important;}
.st-key-page_tabs button p {font-size: 0.85rem !important; font-weight: 500 !important; letter-spacing: 0.04em; color: #333 !important; white-space: nowrap !important; overflow: visible !important;}
.st-key-page_tabs button[aria-checked="true"], .st-key-page_tabs button[data-selected="true"], .st-key-page_tabs button[kind="segmented_controlActive"] {
    background: #4D8F97 !important; border-color: #4D8F97 !important;}
.st-key-page_tabs button[aria-checked="true"] p, .st-key-page_tabs button[data-selected="true"] p, .st-key-page_tabs button[kind="segmented_controlActive"] p {color: #FFFFFF !important;}
.st-key-page_tabs button:first-child {border-radius: 4px 0 0 4px !important; margin-left: 0 !important;}
.st-key-page_tabs button:last-child {border-radius: 0 4px 4px 0 !important;}
</style>""", unsafe_allow_html=True)


def _keep_page():
    """Clicking the selected button again would clear it; put the previous page back."""
    if st.session_state.get("dash_page") is None:
        st.session_state["dash_page"] = st.session_state.get("dash_last", PAGES[0])


st.session_state.setdefault("dash_page", PAGES[0])
with st.container(key="page_tabs"):
    page = st.segmented_control("Section", PAGES, key="dash_page", on_change=_keep_page,
                                label_visibility="collapsed")
page = page or st.session_state.get("dash_last", PAGES[0])
st.session_state["dash_last"] = page

CLUB_PALETTE = px.colors.qualitative.Plotly
club_color = {c: CLUB_PALETTE[i % len(CLUB_PALETTE)] for i, c in enumerate(sorted(df["club"].dropna().unique()))}
display_name = f"{first_name} {last_name}"


# ── Metric by date: median with 20th and 80th percentile lines ──
def render_metric_by_date(page_key: str):
    st.markdown('<p class="section-header">Metric by Date</p>', unsafe_allow_html=True)
    c1, c2 = st.columns([2, 3])
    with c1:
        metric_name = st.selectbox("Display Metric", list(METRICS.keys()), key=f"mbd_metric_{page_key}")
        metric_col = METRICS[metric_name]
    with c2:
        available = sorted(df["club"].dropna().unique().tolist())
        clubs = st.multiselect("Club(s)", available, default=available, key=f"mbd_clubs_{page_key}")

    sub = df[df["club"].isin(clubs)][["session_date", "club", metric_col]].dropna(subset=[metric_col]).copy()
    if sub.empty:
        st.info(f"No data available for {metric_name}.")
        return
    sub["day"] = sub["session_date"].dt.normalize()
    agg = (sub.groupby(["club", "day"])[metric_col]
              .agg(median="median", p20=lambda s: s.quantile(0.2), p80=lambda s: s.quantile(0.8), n="size")
              .reset_index().sort_values("day"))

    fig = go.Figure()
    for club in sorted(agg["club"].unique()):
        g = agg[agg["club"] == club]
        color = club_color.get(club, "#2E75B6")
        fig.add_trace(go.Scatter(
            x=g["day"], y=g["median"], mode="lines+markers", name=club, legendgroup=club,
            line=dict(color=color, width=3), marker=dict(size=7),
            customdata=g[["n", "p20", "p80"]].to_numpy(),
            hovertemplate=(f"<b>{club}</b><br>%{{x|%b %d, %Y}}<br>Median: %{{y:.1f}}<br>"
                           "20th pct: %{customdata[1]:.1f}<br>80th pct: %{customdata[2]:.1f}"
                           "<br>%{customdata[0]} shots<extra></extra>")))
        for col, label in (("p20", "20th pct"), ("p80", "80th pct")):
            fig.add_trace(go.Scatter(
                x=g["day"], y=g[col], mode="lines", name=f"{club} {label}", legendgroup=club, showlegend=False,
                line=dict(color=color, width=1.3, dash="dash"),
                hovertemplate=f"<b>{club}</b> {label}<br>%{{x|%b %d, %Y}}: %{{y:.1f}}<extra></extra>"))
    fig.update_layout(
        title=f"{metric_name} by Date - {display_name}",
        height=480, plot_bgcolor="white", paper_bgcolor="white", legend_title="Club",
        xaxis=dict(showgrid=False, tickformat="%b %d", title="Date"),
        yaxis=dict(gridcolor="#EEEEEE", title=metric_name),
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption("Solid line: median for the day. Dashed lines: 20th and 80th percentile.")


# ── Goals: share of shots on target, by session, with each club's goal ──
GOAL_ORDER = [("Wedge Play", "Proximity"), ("Wedge Play", "Distance Control"),
              ("Approach", "Proximity"), ("Approach", "Distance Control"),
              ("Driving", "Distance"), ("Driving", "Dispersion"),
              ("Putting", "Proximity"), ("Putting", "Distance Control"),
              ("Other", "Proximity"), ("Other", "Distance Control"),
              ("Other", "Distance"), ("Other", "Dispersion")]


def render_goals():
    st.markdown('<p class="section-header">Targets and Goals</p>', unsafe_allow_html=True)
    rows = goal_rows(df, selected_player_id)
    if rows.empty:
        st.info("No saved targets match this player's shots in the selected range. "
                "Save targets for this player on the Report Generator page.")
        return
    st.caption("Share of shots that hit the target in each session. Solid lines are actual results; "
               "dashed lines are the goal for each club. Each session uses the targets saved on that day.")
    groups = [g for g in GOAL_ORDER if ((rows["skill"] == g[0]) & (rows["target_type"] == g[1])).any()]
    for i in range(0, len(groups), 2):
        cols = st.columns(2)
        for col, (skill, ttype) in zip(cols, groups[i:i + 2]):
            sub = rows[(rows["skill"] == skill) & (rows["target_type"] == ttype)].sort_values("date")
            fig = go.Figure()
            for club in sorted(sub["club"].unique()):
                g = sub[sub["club"] == club]
                color = club_color.get(club, "#2E75B6")
                fig.add_trace(go.Scatter(
                    x=g["date"], y=g["actual_pct"], mode="lines+markers", name=club, legendgroup=club,
                    line=dict(color=color, width=3), marker=dict(size=7),
                    customdata=g[["successes", "attempts", "target_pct"]].to_numpy(),
                    hovertemplate=(f"<b>{club}</b><br>%{{x|%b %d, %Y}}<br>%{{y:.0f}}% on target "
                                   "(%{customdata[0]} of %{customdata[1]} shots)<br>"
                                   "Goal: %{customdata[2]:.0f}%<extra></extra>")))
                if g["target_pct"].notna().any():
                    fig.add_trace(go.Scatter(
                        x=g["date"], y=g["target_pct"], mode="lines", name=f"{club} goal", legendgroup=club,
                        showlegend=False, line=dict(color=color, width=1.5, dash="dash"), hoverinfo="skip"))
            fig.update_layout(
                title=f"{skill}: {ttype}", height=340, plot_bgcolor="white", paper_bgcolor="white",
                margin=dict(l=50, r=20, t=50, b=40), legend_title="Club",
                xaxis=dict(showgrid=False, tickformat="%b %d"),
                yaxis=dict(gridcolor="#EEEEEE", range=[0, 105], ticksuffix="%", title="On target"))
            col.plotly_chart(fig, use_container_width=True)


# ── Existing plots, now grouped by page ──
def render_ball_flight():
    # ── PLOT 2: Carry Dispersion ──────────────────────────────────────────────────
    st.markdown('<p class="section-header">Carry Dispersion</p>', unsafe_allow_html=True)

    disp_df = df[["club", "carry_yd", "offline_yd", "session_date"]].dropna()

    if disp_df.empty:
        st.info("No carry/offline data available.")
    else:
        p3c1, p3c2 = st.columns([2, 1])
        with p3c1:
            disp_clubs = st.multiselect("Club(s)", sorted(disp_df["club"].unique()),
                                        default=[sorted(disp_df["club"].unique())[0]], key="disp_club")
        with p3c2:
            color_by_date = st.checkbox("Color by date", value=False, key="disp_color")

        p3d1, p3d2, p3d3 = st.columns([1, 1, 1])
        with p3d1:
            show_ellipse = st.checkbox("Show each club's dispersion", value=False, key="disp_ellipse")
            hide_shots   = st.checkbox("Remove individual shots", value=False, key="disp_hide_shots")
        with p3d2:
            single_club = len(disp_clubs) == 1
            show_proximity = st.checkbox(
                "Show proximity circles" + ("" if single_club else " (single club only)"),
                value=False, key="disp_prox",
                disabled=not single_club,
            )
            if not single_club:
                st.caption("Select one club to enable proximity circles.")
        with p3d3:
            if show_proximity or show_ellipse:
                intended_carry = st.number_input("Intended carry (yd)", min_value=0, max_value=400,
                                                 value=0, step=5, key="disp_target")
                disp_level = st.slider("Level", 1, 12, value=5, key="disp_level")
            else:
                intended_carry = 0
                disp_level = 5

        disp_plot_df = disp_df[disp_df["club"].isin(disp_clubs)].copy()
        disp_plot_df["date_str"] = disp_plot_df["session_date"].dt.strftime("%b %d, %Y")

        # Proximity to hole if intended carry set
        if intended_carry > 0:
            import math as _math
            def _prox(row):
                if pd.isna(row["carry_yd"]) or pd.isna(row["offline_yd"]):
                    return None
                return _math.sqrt(row["offline_yd"]**2 + (intended_carry - row["carry_yd"])**2) * 3
            disp_plot_df["proximity_ft"] = disp_plot_df.apply(_prox, axis=1)
            def _fmt_prox(ft):
                if ft is None or pd.isna(ft): return "—"
                total_in = ft * 12
                feet = int(total_in // 12)
                inches = round(total_in % 12)
                if inches == 12: feet += 1; inches = 0
                return str(feet) + "\'" + str(inches) + "\""
            disp_plot_df["prox_str"] = disp_plot_df["proximity_ft"].apply(_fmt_prox)
        else:
            disp_plot_df["prox_str"] = None

        # Symmetric x-axis around 0
        max_offline = disp_plot_df["offline_yd"].abs().max()
        x_range = [-max_offline * 1.2 - 1, max_offline * 1.2 + 1]

        has_prox = intended_carry > 0 and "prox_str" in disp_plot_df.columns

        def _build_disp_hover(row):
            s = (f"<b>{row['club']}</b><br>"
                 f"Carry: {row['carry_yd']:.1f} yd<br>"
                 f"Offline: {row['offline_yd']:.1f} yd")
            if has_prox and row.get("prox_str"):
                s += f"<br>Proximity: {row['prox_str']}"
            return s

        disp_plot_df["hover_text"] = disp_plot_df.apply(_build_disp_hover, axis=1)

        # Build figure manually for coordinated per-club colors
        BASE_COLORS = px.colors.qualitative.Plotly
        fig3 = go.Figure()
        fig3.update_layout(title=f"Carry Dispersion — {', '.join(disp_clubs)}")

        for i, club in enumerate(disp_clubs):
            club_data = disp_plot_df[disp_plot_df["club"] == club]
            normal_data  = club_data[~club_data["_is_outlier"]] if "_is_outlier" in club_data.columns else club_data
            outlier_data = club_data[club_data["_is_outlier"]]  if "_is_outlier" in club_data.columns else club_data.iloc[0:0]

            base_color = BASE_COLORS[i % len(BASE_COLORS)]
            dark_color = _darken_hex(base_color, 0.82)

            # Individual points
            if not hide_shots:
                if color_by_date:
                    dates = sorted(normal_data["date_str"].unique())
                    date_colors = px.colors.qualitative.Pastel
                    for j, d in enumerate(dates):
                        dd = normal_data[normal_data["date_str"] == d]
                        fig3.add_trace(go.Scatter(
                            x=dd["offline_yd"], y=dd["carry_yd"], mode="markers",
                            name=d, showlegend=False,
                            text=dd["hover_text"],
                            hovertemplate="%{text}<extra></extra>",
                            marker=dict(size=10, color=date_colors[j % len(date_colors)],
                                        opacity=0.75, line=dict(width=1, color="white")),
                        ))
                else:
                    fig3.add_trace(go.Scatter(
                        x=normal_data["offline_yd"], y=normal_data["carry_yd"], mode="markers",
                        name=club, showlegend=False,
                        text=normal_data["hover_text"],
                        hovertemplate="%{text}<extra></extra>",
                        marker=dict(size=10, color=base_color, opacity=0.75,
                                    line=dict(width=1, color="white")),
                    ))

                # Outlier overlay
                if not exclude_outliers and not outlier_data.empty:
                    fig3.add_trace(go.Scatter(
                        x=outlier_data["offline_yd"], y=outlier_data["carry_yd"],
                        mode="markers", name=f"{club} outlier", showlegend=False,
                        marker=dict(size=10, color="red", symbol="x", line=dict(width=2, color="darkred")),
                        hovertemplate="<b>Outlier</b><br>Offline: %{x:.1f} yd<br>Carry: %{y:.1f} yd<extra></extra>",
                    ))

            # Average dot — in legend with just club name
            avg_carry   = club_data["carry_yd"].mean()
            avg_offline = club_data["offline_yd"].mean()
            fig3.add_trace(go.Scatter(
                x=[avg_offline], y=[avg_carry], mode="markers",
                name=club, showlegend=True,
                marker=dict(size=14, color=dark_color, opacity=1.0, symbol="circle",
                            line=dict(width=2, color="black")),
                hovertemplate=f"<b>{club} avg</b><br>Offline: {avg_offline:.1f} yd<br>Carry: {avg_carry:.1f} yd<extra></extra>",
            ))

        # Intended carry line
        if intended_carry > 0:
            fig3.add_hline(y=intended_carry, line_color="#333333", line_width=2,
                           annotation_text=f"Target: {intended_carry} yd",
                           annotation_position="right",
                           annotation_font=dict(color="#333333", size=12))

        import numpy as np
        import math
        from scipy import stats

        # 95% ellipse — one per club
        if show_ellipse:
            theta = np.linspace(0, 2 * np.pi, 200)
            for i, club in enumerate(disp_clubs):
                club_data = disp_plot_df[disp_plot_df["club"] == club]
                valid = club_data[["carry_yd", "offline_yd"]].dropna()
                if len(valid) < 3:
                    continue
                carry_vals   = valid["carry_yd"].values
                offline_vals = valid["offline_yd"].values
                avg_carry_e  = carry_vals.mean()
                avg_offline_e = offline_vals.mean()
                cov = np.cov(offline_vals, carry_vals)
                chi2_95 = stats.chi2.ppf(0.95, df=2)
                eigvals, eigvecs = np.linalg.eigh(cov)
                order = np.argsort(eigvals)[::-1]
                eigvals, eigvecs = eigvals[order], eigvecs[:, order]
                angle = np.arctan2(eigvecs[1, 0], eigvecs[0, 0])
                a = np.sqrt(chi2_95 * max(eigvals[0], 0))
                b = np.sqrt(chi2_95 * max(eigvals[1], 0))
                ellipse_x = (a * np.cos(theta) * np.cos(angle)
                             - b * np.sin(theta) * np.sin(angle) + avg_offline_e)
                ellipse_y = (a * np.cos(theta) * np.sin(angle)
                             + b * np.sin(theta) * np.cos(angle) + avg_carry_e)
                ellipse_color = _darken_hex(BASE_COLORS[i % len(BASE_COLORS)], 0.7)
                fig3.add_trace(go.Scatter(
                    x=ellipse_x, y=ellipse_y,
                    mode="lines", name=f"{club} 95% ellipse", showlegend=False,
                    line=dict(color=ellipse_color, width=2),
                    hoverinfo="skip",
                ))

        # Proximity circles — single club only
        if show_proximity and single_club and intended_carry > 0:
            import sys, os
            sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
            from data.tour_targets import get_tour_target, get_level_multipliers

            prox_ft, _, _ = get_tour_target(intended_carry)
            _, prox_mult = get_level_multipliers(disp_level)
            if prox_ft is not None and prox_mult is not None:
                target_prox_ft = prox_ft * prox_mult
                target_prox_yd = target_prox_ft / 3
                theta = np.linspace(0, 2 * np.pi, 200)

                # Target circle — centered on intended carry (blue dashed)
                fig3.add_trace(go.Scatter(
                    x=target_prox_yd * np.cos(theta),
                    y=intended_carry + target_prox_yd * np.sin(theta),
                    mode="lines", name=f"Target ({target_prox_ft:.1f}ft)",
                    line=dict(color="#2E75B6", width=2, dash="dash"),
                    hoverinfo="skip",
                ))

                valid = disp_plot_df[disp_plot_df["club"] == disp_clubs[0]][["carry_yd","offline_yd"]].dropna()
                if not valid.empty:
                    carry_vals   = valid["carry_yd"].values
                    offline_vals = valid["offline_yd"].values
                    proximities  = [math.sqrt(o**2 + (intended_carry - c)**2)
                                    for c, o in zip(carry_vals, offline_vals)]
                    avg_prox_yd  = sum(proximities) / len(proximities)
                    avg_prox_ft  = avg_prox_yd * 3
                    # Actual avg proximity circle — centered on intended carry (orange dotted)
                    fig3.add_trace(go.Scatter(
                        x=avg_prox_yd * np.cos(theta),
                        y=intended_carry + avg_prox_yd * np.sin(theta),
                        mode="lines", name=f"Actual avg ({avg_prox_ft:.1f}ft)",
                        line=dict(color="#E07B39", width=2, dash="dot"),
                        hoverinfo="skip",
                    ))

        # Always center x at 0
        fig3.add_vline(x=0, line_color="#AAAAAA", line_width=1.5)

        fig3.update_layout(
            xaxis_title="← Left  |  Right →  (yd)",
            yaxis_title="Carry (yd)",
            plot_bgcolor="white", paper_bgcolor="white",
            xaxis=dict(showgrid=False, zeroline=False, range=x_range),
            yaxis=dict(gridcolor="#EEEEEE"),
            height=500, legend_title="Club",
        )
        st.plotly_chart(fig3, use_container_width=True)




def render_delivery_impact():
    # ── PLOTS 3 & 4: Face Impact + Club Path vs Face Angle (side by side) ──────
    st.markdown('<p class="section-header">Strike Location &nbsp;&nbsp;|&nbsp;&nbsp; Delivery</p>', unsafe_allow_html=True)

    # Shared club filter
    _all_clubs_34 = sorted(df["club"].dropna().unique().tolist())
    impact_clubs = st.multiselect("Club(s)", _all_clubs_34,
                                   default=[_all_clubs_34[0]] if _all_clubs_34 else [],
                                   key="impact_club")

    _col_impact, _col_path = st.columns(2)

    with _col_impact:
        impact_df = df[df["club"].isin(impact_clubs)][["club", "face_impact_horizontal_mm", "face_impact_vertical_mm"]].dropna()

        if impact_df.empty:
            st.info("No strike location data available.")
        else:
            impact_plot_df = impact_df.copy()

            # Handedness decides which side the toe is drawn on: left-handed has the toe on the
            # right (+x); right-handed is mirrored so the heel is positive and the toe negative.
            handed = (player_rec.get("handedness") or "right").lower()
            toe_sign = 1 if handed == "left" else -1
            impact_plot_df["face_impact_horizontal_mm"] = toe_sign * impact_plot_df["face_impact_horizontal_mm"]
            if not player_rec.get("handedness"):
                st.caption("Handedness isn't set for this player, so the plot is shown as right-handed. "
                           "Set it on the Players page.")

            import numpy as np
            fig2 = go.Figure()

            # Club face outline: shaped like the club when every selected club is the same kind
            cats = {club_category(c) for c in impact_clubs}
            if len(cats) == 1 and None not in cats:
                outline_x, outline_y = face_outline(next(iter(cats)), toe_sign)
            else:
                theta = np.linspace(0, 2 * np.pi, 200)
                outline_x, outline_y = 20 * np.cos(theta), 20 * np.sin(theta)
            fig2.add_trace(go.Scatter(
                x=outline_x, y=outline_y,
                mode="lines", line=dict(color="#AAAAAA", width=2),
                fill="toself", fillcolor="rgba(240,240,240,0.3)",
                showlegend=False, hoverinfo="skip",
            ))
            # Crosshairs
            data_x, data_y = impact_plot_df["face_impact_horizontal_mm"], impact_plot_df["face_impact_vertical_mm"]
            x_lo, x_hi = min(outline_x.min(), data_x.min()) - 6, max(outline_x.max(), data_x.max()) + 6
            y_lo, y_hi = min(outline_y.min(), data_y.min()) - 6, max(outline_y.max(), data_y.max()) + 6
            fig2.add_shape(type="line", x0=0, x1=0, y0=y_lo, y1=y_hi,
                           line=dict(color="#CCCCCC", width=1, dash="dash"))
            fig2.add_shape(type="line", x0=x_lo, x1=x_hi, y0=0, y1=0,
                           line=dict(color="#CCCCCC", width=1, dash="dash"))

            colors = px.colors.qualitative.Plotly
            for i, club in enumerate(impact_clubs):
                club_df = impact_plot_df[impact_plot_df["club"] == club]
                color = colors[i % len(colors)]
                avg_h = club_df["face_impact_horizontal_mm"].mean()
                avg_v = club_df["face_impact_vertical_mm"].mean()

                # Individual shots — hidden from legend
                fig2.add_trace(go.Scatter(
                    x=club_df["face_impact_horizontal_mm"],
                    y=club_df["face_impact_vertical_mm"],
                    mode="markers", name=club, showlegend=False,
                    marker=dict(size=11, color=color, opacity=0.5, line=dict(width=1, color="white")),
                    hovertemplate=f"<b>{club}</b><br>Horizontal: %{{x:.1f}} mm<br>Vertical: %{{y:.1f}} mm<extra></extra>",
                ))
                # Average dot — in legend with club name, avg size/color
                fig2.add_trace(go.Scatter(
                    x=[avg_h], y=[avg_v],
                    mode="markers", name=club, showlegend=True,
                    marker=dict(size=18, color=color, symbol="circle",
                                line=dict(width=2, color="black")),
                    hovertemplate=f"<b>{club} avg</b><br>Horizontal: {avg_h:.1f} mm<br>Vertical: {avg_v:.1f} mm<extra></extra>",
                ))

            fig2.update_layout(
                title=f"Strike Location — {', '.join(impact_clubs)}",
                xaxis_title=("← Heel  |  Toe →  (mm)" if toe_sign == 1 else "← Toe  |  Heel →  (mm)"),
                yaxis_title="← Low  |  High →  (mm)",
                xaxis=dict(range=[x_lo, x_hi], showgrid=False, zeroline=False, constrain="domain"),
                yaxis=dict(range=[y_lo, y_hi], showgrid=False, zeroline=False, scaleanchor="x", constrain="domain"),
                plot_bgcolor="white", paper_bgcolor="white",
                height=500, legend_title="Club",
            )
            st.plotly_chart(fig2, use_container_width=False)


    with _col_path:
        has_path = df["club_path_deg"].notna().any()
        has_face = df["face_angle_deg"].notna().any() if "face_angle_deg" in df.columns else False

        if not has_path:
            st.info("No club path data available.")
        elif not has_face:
            # ── Histogram: club path distribution (bin=1°) ───────────────────────
            density_df = df[["club", "club_path_deg"]].dropna(subset=["club_path_deg"])
            density_plot_df = density_df[density_df["club"].isin(impact_clubs)]
            fig4 = go.Figure()
            BASE_COLORS_D = px.colors.qualitative.Plotly

            for i, club in enumerate(impact_clubs):
                cdf = density_plot_df[density_plot_df["club"] == club]["club_path_deg"].dropna()
                if cdf.empty:
                    continue
                color = BASE_COLORS_D[i % len(BASE_COLORS_D)]
                fig4.add_trace(go.Histogram(
                    x=cdf, name=club,
                    xbins=dict(start=cdf.min() - 0.5, end=cdf.max() + 0.5, size=1),
                    marker_color=color, opacity=0.6,
                ))
                fig4.add_vline(x=float(cdf.mean()), line_color=color, line_width=2,
                               line_dash="dash",
                               annotation_text=f"{cdf.mean():.1f}°",
                               annotation_position="top")

            fig4.add_vline(x=0, line_color="#AAAAAA", line_width=1.5)
            fig4.update_layout(
                barmode="overlay",
                title=f"Club Path Distribution — {', '.join(impact_clubs)}",
                xaxis_title="Club Path (°)<br><sub>← Out-to-In  |  In-to-Out →</sub>",
                yaxis_title="Count",
                plot_bgcolor="white", paper_bgcolor="white",
                xaxis=dict(showgrid=False, zeroline=False),
                yaxis=dict(gridcolor="#EEEEEE", zeroline=False),
                height=550, legend_title="Club",
            )
            st.plotly_chart(fig4, use_container_width=True)

        else:
            # ── Scatter: club path vs face angle ─────────────────────────────────
            path_cols = ["club", "club_path_deg", "face_angle_deg", "smash_factor",
                         "club_speed_mph", "ball_speed_mph", "angle_of_attack_deg", "dynamic_loft_deg",
                         "launch_angle_deg", "total_spin_rpm", "carry_yd", "offline_yd", "total_yd",
                         "face_impact_horizontal_mm", "face_impact_vertical_mm", "_is_outlier"]
            path_df = df[[c for c in path_cols if c in df.columns]].dropna(subset=["club_path_deg", "face_angle_deg"]).copy()

            path_df["smash"] = path_df["smash_factor"]
            mask = path_df["smash"].isna() & path_df["ball_speed_mph"].notna() & path_df["club_speed_mph"].notna()
            path_df.loc[mask, "smash"] = path_df.loc[mask, "ball_speed_mph"] / path_df.loc[mask, "club_speed_mph"]

            path_clubs = impact_clubs
            path_plot_df = path_df[path_df["club"].isin(path_clubs)].copy()

            def fmt(val, decimals=1, suffix=""):
                return f"{val:.{decimals}f}{suffix}" if pd.notna(val) else "—"

            path_plot_df["hover"] = path_plot_df.apply(lambda r: (
                f"<b>{r['club']}</b><br>"
                f"Club Speed: {fmt(r['club_speed_mph'], 0)} mph<br>"
                f"Angle of Attack: {fmt(r['angle_of_attack_deg'])}°<br>"
                f"Club Path: {fmt(r['club_path_deg'])}°<br>"
                f"Face Angle: {fmt(r['face_angle_deg'])}°<br>"
                f"Dynamic Loft: {fmt(r['dynamic_loft_deg'], 0)}°<br>"
                f"Impact H: {fmt(r['face_impact_horizontal_mm'], 0)} mm<br>"
                f"Impact V: {fmt(r['face_impact_vertical_mm'], 0)} mm<br>"
                f"Ball Speed: {fmt(r['ball_speed_mph'], 0)} mph<br>"
                f"Smash Factor: {fmt(r['smash'], 2)}<br>"
                f"Launch Angle: {fmt(r['launch_angle_deg'], 0)}°<br>"
                f"Spin Rate: {fmt(r['total_spin_rpm'], 0)} rpm<br>"
                f"Carry: {fmt(r['carry_yd'], 0)} yd<br>"
                f"Offline: {fmt(r['offline_yd'], 0)} yd<br>"
                f"Total: {fmt(r['total_yd'], 0)} yd"
            ), axis=1)

            fig4 = go.Figure()
            SHAPES = ["circle", "square", "diamond", "triangle-up", "cross",
                      "star", "hexagon", "pentagon", "triangle-down", "x"]
            all_aoa = path_plot_df["angle_of_attack_deg"].dropna()
            aoa_min = all_aoa.min() if not all_aoa.empty else -10.0
            aoa_max = all_aoa.max() if not all_aoa.empty else 5.0

            for i, club in enumerate(path_clubs):
                cdf = path_plot_df[path_plot_df["club"] == club].copy()
                shape = SHAPES[i % len(SHAPES)]
                colorbar_cfg = dict(title=dict(text="Angle of Attack (°)", font=dict(size=13)),
                                    x=1.02, y=1, len=0.5, yanchor="top") if i == 0 else {}
                fig4.add_trace(go.Scatter(
                    x=cdf["club_path_deg"], y=cdf["face_angle_deg"],
                    mode="markers", name=club,
                    text=cdf["hover"], hovertemplate="%{text}<extra></extra>",
                    marker=dict(size=14, symbol=shape,
                                color=cdf["angle_of_attack_deg"],
                                colorscale="RdYlGn", cmin=aoa_min, cmax=aoa_max,
                                showscale=(i == 0),
                                **{"colorbar": colorbar_cfg} if colorbar_cfg else {},
                                line=dict(width=1, color="white"), opacity=0.85)
                ))

            if not exclude_outliers and "_is_outlier" in path_plot_df.columns:
                path_out = path_plot_df[path_plot_df["_is_outlier"]]
                if not path_out.empty:
                    fig4.add_trace(go.Scatter(
                        x=path_out["club_path_deg"], y=path_out["face_angle_deg"],
                        mode="markers", name="Outlier",
                        marker=dict(size=12, color="red", symbol="x", line=dict(width=2, color="darkred")),
                        hovertemplate="<b>Outlier</b><br>Path: %{x:.1f}°<br>Face: %{y:.1f}°<extra></extra>",
                    ))

            fig4.add_vline(x=0, line_color="#CCCCCC", line_width=1, line_dash="dash")
            fig4.add_hline(y=0, line_color="#CCCCCC", line_width=1, line_dash="dash")
            axis_range = max(abs(path_plot_df["club_path_deg"]).max(),
                             abs(path_plot_df["face_angle_deg"]).max()) * 1.2 + 1
            fig4.update_layout(
                title=f"Club Path vs Face Angle — {', '.join(path_clubs)}",
                xaxis_title="Club Path (°)<br><sub>← Out-to-In  |  In-to-Out →</sub>",
                yaxis_title="Face Angle (°)<br><sub>← Closed  |  Open →</sub>",
                plot_bgcolor="white", paper_bgcolor="white",
                xaxis=dict(showgrid=False, zeroline=False, range=[-axis_range, axis_range]),
                yaxis=dict(gridcolor="#EEEEEE", zeroline=False, range=[-axis_range, axis_range]),
                height=550,
                legend=dict(
                    title=dict(text="Club", font=dict(size=13)),
                    x=1.02,
                    y=0.5,
                    yanchor="top"
                ),
                coloraxis_colorbar=dict(x=1.02, len=0.5, yanchor="top", y=1),
            )
            st.plotly_chart(fig4, use_container_width=True)



if page == "GOALS":
    render_goals()
elif page == "DELIVERY/IMPACT":
    render_delivery_impact()
else:
    render_ball_flight()
render_metric_by_date(page.split("/")[0].lower().replace(" ", "_"))
