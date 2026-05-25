import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd
from datetime import date, timedelta

st.set_page_config(page_title="Practice Dashboard", page_icon="⛳", layout="wide")

st.markdown("""
<style>
    .main-header {font-size: 2rem; font-weight: 700; color: #1F4E79; margin-bottom: 0;}
    .sub-header {font-size: 1rem; color: #555; margin-bottom: 1rem;}
    .section-header {font-size: 1.2rem; font-weight: 600; color: #2E75B6;
                     border-bottom: 2px solid #BDD7EE; padding-bottom: 4px; margin-top: 1.5rem;}
</style>
""", unsafe_allow_html=True)

st.markdown('<p class="main-header">⛳ Practice Dashboard</p>', unsafe_allow_html=True)
st.markdown('<p class="sub-header">Query and visualize shot data from Supabase</p>', unsafe_allow_html=True)


# ── Supabase connection ───────────────────────────────────────────────────────
@st.cache_resource
def get_supabase():
    from supabase import create_client
    return create_client(st.secrets["supabase"]["url"], st.secrets["supabase"]["key"])


@st.cache_data(ttl=300)
def load_players():
    sb = get_supabase()
    res = sb.table("players").select("player_id, first_name, last_name").execute()
    return res.data or []


@st.cache_data(ttl=60)
def load_shots(player_id: str, date_from: str, date_to: str) -> pd.DataFrame:
    sb = get_supabase()
    res = (
        sb.table("shots")
        .select(
            "shot_id, session_id, club, shot_num_session, shot_num_club, "
            "ball_speed_mph, launch_angle_deg, side_angle_deg, "
            "backspin_rpm, side_spin_rpm, tilt_angle_deg, total_spin_rpm, "
            "carry_yd, total_yd, offline_yd, descent_angle_deg, peak_height_ft, to_pin_ft, "
            "club_speed_mph, smash_factor, angle_of_attack_deg, "
            "club_path_deg, face_angle_deg, face_to_path_deg, "
            "dynamic_lie_deg, dynamic_loft_deg, closure_rate_dps, "
            "face_impact_horizontal_mm, face_impact_vertical_mm, face_impact_from_center_mm"
        )
        .eq("player_id", player_id)
        .execute()
    )
    shots_df = pd.DataFrame(res.data or [])
    if shots_df.empty:
        return shots_df

    session_ids = shots_df["session_id"].unique().tolist()
    sess_res = (
        sb.table("sessions")
        .select("session_id, session_date, launch_monitor_type")
        .in_("session_id", session_ids)
        .gte("session_date", date_from)
        .lte("session_date", date_to)
        .execute()
    )
    sessions_df = pd.DataFrame(sess_res.data or [])
    if sessions_df.empty:
        return pd.DataFrame()

    merged = shots_df.merge(sessions_df, on="session_id", how="inner")
    merged["session_date"] = pd.to_datetime(merged["session_date"])
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


# ── FILTERS ───────────────────────────────────────────────────────────────────
st.markdown('<p class="section-header">Filters</p>', unsafe_allow_html=True)

players = load_players()
if not players:
    st.warning("No players found in database.")
    st.stop()

player_options = {f"{p['first_name']} {p['last_name']}": p["player_id"] for p in players}

f1, f2, f3 = st.columns([2, 2, 2])
with f1:
    selected_player_name = st.selectbox("Player", list(player_options.keys()))
    selected_player_id   = player_options[selected_player_name]
with f2:
    date_from = st.date_input("From", value=date.today() - timedelta(days=90))
with f3:
    date_to = st.date_input("To", value=date.today())

df = load_shots(selected_player_id, str(date_from), str(date_to))

if df.empty:
    st.info("No shots found for this player in the selected date range.")
    st.stop()

all_clubs = sorted(df["club"].dropna().unique().tolist())
selected_clubs = st.multiselect("Clubs", all_clubs, default=all_clubs)
df = df[df["club"].isin(selected_clubs)]

if df.empty:
    st.info("No shots for the selected clubs.")
    st.stop()

st.caption(f"**{len(df):,} shots** · {df['session_date'].dt.date.nunique()} sessions · {df['club'].nunique()} clubs")


# ── PLOT 1: Box Plot ──────────────────────────────────────────────────────────
st.markdown('<p class="section-header">📦 Metric by Date</p>', unsafe_allow_html=True)

p1c1, p1c2 = st.columns([2, 1])
with p1c1:
    selected_metric_name = st.selectbox("Y-axis metric", list(METRICS.keys()), key="box_metric")
    selected_metric_col  = METRICS[selected_metric_name]
with p1c2:
    color_by_club = st.checkbox("Color by club", value=True, key="box_color")

plot_df = df[["session_date", "club", selected_metric_col]].dropna(subset=[selected_metric_col]).copy()
plot_df["date_str"] = plot_df["session_date"].dt.strftime("%b %d")

if plot_df.empty:
    st.info(f"No data available for {selected_metric_name}.")
else:
    fig1 = px.box(
        plot_df, x="date_str", y=selected_metric_col,
        color="club" if color_by_club else None,
        points="all",
        labels={"date_str": "Date", selected_metric_col: selected_metric_name, "club": "Club"},
        title=f"{selected_metric_name} by Date — {selected_player_name}",
    )
    fig1.update_layout(
        height=500, plot_bgcolor="white", paper_bgcolor="white",
        xaxis=dict(showgrid=False), yaxis=dict(gridcolor="#EEEEEE"),
        legend_title="Club",
    )
    st.plotly_chart(fig1, use_container_width=True)


# ── PLOT 2: Face Impact ───────────────────────────────────────────────────────
st.markdown('<p class="section-header">🎯 Face Impact Location</p>', unsafe_allow_html=True)

impact_df = df[["club", "face_impact_horizontal_mm", "face_impact_vertical_mm"]].dropna()

if impact_df.empty:
    st.info("No face impact data available.")
else:
    impact_club = st.selectbox("Club", sorted(impact_df["club"].unique()), key="impact_club")
    impact_plot_df = impact_df[impact_df["club"] == impact_club]

    fig2 = go.Figure()
    fig2.add_shape(type="rect", x0=-22.5, x1=22.5, y0=-17.5, y1=17.5,
                   line=dict(color="#AAAAAA", width=2), fillcolor="rgba(240,240,240,0.3)")
    fig2.add_shape(type="line", x0=0, x1=0, y0=-17.5, y1=17.5,
                   line=dict(color="#CCCCCC", width=1, dash="dash"))
    fig2.add_shape(type="line", x0=-22.5, x1=22.5, y0=0, y1=0,
                   line=dict(color="#CCCCCC", width=1, dash="dash"))
    fig2.add_trace(go.Scatter(
        x=impact_plot_df["face_impact_horizontal_mm"],
        y=impact_plot_df["face_impact_vertical_mm"],
        mode="markers",
        marker=dict(size=8, color="#2E75B6", opacity=0.7, line=dict(width=1, color="white")),
        hovertemplate="Horizontal: %{x:.1f} mm<br>Vertical: %{y:.1f} mm<extra></extra>",
    ))
    fig2.update_layout(
        title=f"Face Impact — {impact_club}",
        xaxis_title="Horizontal (mm)  ←  Toe  |  Heel  →",
        yaxis_title="Vertical (mm)  ←  Low  |  High  →",
        xaxis=dict(range=[-30, 30], showgrid=False, zeroline=False),
        yaxis=dict(range=[-25, 25], showgrid=False, zeroline=False, scaleanchor="x"),
        plot_bgcolor="white", paper_bgcolor="white",
        height=450,
    )
    st.plotly_chart(fig2, use_container_width=False)


# ── PLOT 3: Carry Dispersion ──────────────────────────────────────────────────
st.markdown('<p class="section-header">📍 Carry Dispersion</p>', unsafe_allow_html=True)

disp_df = df[["club", "carry_yd", "offline_yd", "session_date"]].dropna()

if disp_df.empty:
    st.info("No carry/offline data available.")
else:
    p3c1, p3c2 = st.columns([2, 1])
    with p3c1:
        disp_club = st.selectbox("Club", sorted(disp_df["club"].unique()), key="disp_club")
    with p3c2:
        color_by_date = st.checkbox("Color by date", value=False, key="disp_color")

    disp_plot_df = disp_df[disp_df["club"] == disp_club].copy()
    disp_plot_df["date_str"] = disp_plot_df["session_date"].dt.strftime("%b %d, %Y")

    avg_carry   = disp_plot_df["carry_yd"].mean()
    avg_offline = disp_plot_df["offline_yd"].mean()

    if color_by_date:
        fig3 = px.scatter(
            disp_plot_df, x="offline_yd", y="carry_yd", color="date_str",
            labels={"offline_yd": "Offline (yd)", "carry_yd": "Carry (yd)", "date_str": "Date"},
            title=f"Carry Dispersion — {disp_club}",
        )
    else:
        fig3 = px.scatter(
            disp_plot_df, x="offline_yd", y="carry_yd",
            labels={"offline_yd": "Offline (yd)", "carry_yd": "Carry (yd)"},
            title=f"Carry Dispersion — {disp_club}",
        )
        fig3.update_traces(marker=dict(size=8, color="#2E75B6", opacity=0.7,
                                       line=dict(width=1, color="white")))

    fig3.add_hline(y=avg_carry, line_dash="dash", line_color="#888888",
                   annotation_text=f"Avg: {avg_carry:.1f} yd", annotation_position="right")
    fig3.add_vline(x=avg_offline, line_dash="dash", line_color="#888888",
                   annotation_text=f"Avg: {avg_offline:.1f} yd", annotation_position="top")
    fig3.add_vline(x=0, line_color="#DDDDDD", line_width=1)

    fig3.update_layout(
        xaxis_title="Offline (yd)  ←  Left  |  Right  →",
        yaxis_title="Carry (yd)",
        plot_bgcolor="white", paper_bgcolor="white",
        xaxis=dict(showgrid=False, zeroline=False),
        yaxis=dict(gridcolor="#EEEEEE"),
        height=500,
    )
    st.plotly_chart(fig3, use_container_width=True)
