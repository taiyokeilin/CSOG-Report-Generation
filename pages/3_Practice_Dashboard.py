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
st.markdown('<p class="sub-header">Select a player and date range to view their development</p>', unsafe_allow_html=True)


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
    date_from = st.date_input("From", value=date.today() - timedelta(days=365))
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
    selected_metric_name = st.selectbox("Display metric", list(METRICS.keys()), key="box_metric")
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
    impact_clubs = st.multiselect("Club(s)", sorted(impact_df["club"].unique()),
                                  default=[sorted(impact_df["club"].unique())[0]], key="impact_club")
    impact_plot_df = impact_df[impact_df["club"].isin(impact_clubs)].copy()

    import numpy as np
    fig2 = go.Figure()

    # Club face outline — circle with 20mm radius
    theta = np.linspace(0, 2 * np.pi, 200)
    fig2.add_trace(go.Scatter(
        x=20 * np.cos(theta), y=20 * np.sin(theta),
        mode="lines", line=dict(color="#AAAAAA", width=2),
        fill="toself", fillcolor="rgba(240,240,240,0.3)",
        showlegend=False, hoverinfo="skip",
    ))
    # Crosshairs
    fig2.add_shape(type="line", x0=0, x1=0, y0=-20, y1=20,
                   line=dict(color="#CCCCCC", width=1, dash="dash"))
    fig2.add_shape(type="line", x0=-20, x1=20, y0=0, y1=0,
                   line=dict(color="#CCCCCC", width=1, dash="dash"))

    colors = px.colors.qualitative.Plotly
    for i, club in enumerate(impact_clubs):
        club_df = impact_plot_df[impact_plot_df["club"] == club]
        color = colors[i % len(colors)]
        avg_h = club_df["face_impact_horizontal_mm"].mean()
        avg_v = club_df["face_impact_vertical_mm"].mean()

        # Individual shots — note: toe positive, heel negative
        fig2.add_trace(go.Scatter(
            x=club_df["face_impact_horizontal_mm"],
            y=club_df["face_impact_vertical_mm"],
            mode="markers", name=club,
            marker=dict(size=8, color=color, opacity=0.6, line=dict(width=1, color="white")),
            hovertemplate=f"<b>{club}</b><br>Horizontal: %{{x:.1f}} mm<br>Vertical: %{{y:.1f}} mm<extra></extra>",
        ))
        # Average dot
        fig2.add_trace(go.Scatter(
            x=[avg_h], y=[avg_v],
            mode="markers", name=f"{club} avg",
            marker=dict(size=16, color=color, symbol="circle",
                        line=dict(width=2, color="white")),
            hovertemplate=f"<b>{club} avg</b><br>Horizontal: {avg_h:.1f} mm<br>Vertical: {avg_v:.1f} mm<extra></extra>",
        ))

    fig2.update_layout(
        title=f"Face Impact — {', '.join(impact_clubs)}",
        xaxis_title="← Heel  |  Toe →  (mm)",
        yaxis_title="← Low  |  High →  (mm)",
        xaxis=dict(range=[-28, 28], showgrid=False, zeroline=False),
        yaxis=dict(range=[-28, 28], showgrid=False, zeroline=False, scaleanchor="x"),
        plot_bgcolor="white", paper_bgcolor="white",
        height=500, legend_title="Club",
    )
    st.plotly_chart(fig2, use_container_width=False)


# ── PLOT 3: Carry Dispersion ──────────────────────────────────────────────────
st.markdown('<p class="section-header">📍 Carry Dispersion</p>', unsafe_allow_html=True)

disp_df = df[["club", "carry_yd", "offline_yd", "session_date"]].dropna()

if disp_df.empty:
    st.info("No carry/offline data available.")
else:
    p3c1, p3c2, p3c3 = st.columns([2, 1, 1])
    with p3c1:
        disp_clubs = st.multiselect("Club(s)", sorted(disp_df["club"].unique()),
                                    default=[sorted(disp_df["club"].unique())[0]], key="disp_club")
    with p3c2:
        color_by_date = st.checkbox("Color by date", value=False, key="disp_color")
    with p3c3:
        intended_carry = st.number_input("Intended carry (yd)", min_value=0, max_value=400,
                                         value=0, step=5, key="disp_target")

    disp_plot_df = disp_df[disp_df["club"].isin(disp_clubs)].copy()
    disp_plot_df["date_str"] = disp_plot_df["session_date"].dt.strftime("%b %d, %Y")

    # Symmetric x-axis around 0
    max_offline = disp_plot_df["offline_yd"].abs().max()
    x_range = [-max_offline * 1.2 - 1, max_offline * 1.2 + 1]

    if color_by_date:
        fig3 = px.scatter(
            disp_plot_df, x="offline_yd", y="carry_yd", color="date_str",
            symbol="club" if len(disp_clubs) > 1 else None,
            labels={"offline_yd": "Offline (yd)", "carry_yd": "Carry (yd)", "date_str": "Date", "club": "Club"},
            title=f"Carry Dispersion — {', '.join(disp_clubs)}",
        )
    else:
        fig3 = px.scatter(
            disp_plot_df, x="offline_yd", y="carry_yd",
            color="club" if len(disp_clubs) > 1 else None,
            labels={"offline_yd": "Offline (yd)", "carry_yd": "Carry (yd)", "club": "Club"},
            title=f"Carry Dispersion — {', '.join(disp_clubs)}",
        )
        if len(disp_clubs) == 1:
            fig3.update_traces(marker=dict(size=8, color="#2E75B6", opacity=0.7,
                                           line=dict(width=1, color="white")))

    # Per-club averages
    colors = px.colors.qualitative.Plotly
    for i, club in enumerate(disp_clubs):
        club_data = disp_plot_df[disp_plot_df["club"] == club]
        avg_carry   = club_data["carry_yd"].mean()
        avg_offline = club_data["offline_yd"].mean()
        color = colors[i % len(colors)] if len(disp_clubs) > 1 else "#2E75B6"
        fig3.add_trace(go.Scatter(
            x=[avg_offline], y=[avg_carry], mode="markers",
            name=f"{club} avg",
            marker=dict(size=16, color=color, symbol="circle",
                        line=dict(width=2, color="white")),
            hovertemplate=f"<b>{club} avg</b><br>Offline: {avg_offline:.1f} yd<br>Carry: {avg_carry:.1f} yd<extra></extra>",
        ))

    # Intended carry line
    if intended_carry > 0:
        fig3.add_hline(y=intended_carry, line_color="#333333", line_width=2,
                       annotation_text=f"Target: {intended_carry} yd",
                       annotation_position="right",
                       annotation_font=dict(color="#333333", size=12))

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

# ── PLOT 4: Club Path vs Face Angle ──────────────────────────────────────────
st.markdown('<p class="section-header">🔄 Club Path vs Face Angle</p>', unsafe_allow_html=True)

path_cols = ["club", "club_path_deg", "face_angle_deg", "smash_factor",
             "club_speed_mph", "ball_speed_mph", "dynamic_loft_deg",
             "launch_angle_deg", "total_spin_rpm", "carry_yd", "offline_yd", "total_yd"]
path_df = df[path_cols].dropna(subset=["club_path_deg", "face_angle_deg"]).copy()

if path_df.empty:
    st.info("No club path / face angle data available.")
else:
    # Compute smash factor: use stored value, fall back to ball_speed / club_speed
    path_df["smash"] = path_df["smash_factor"]
    mask = path_df["smash"].isna() & path_df["ball_speed_mph"].notna() & path_df["club_speed_mph"].notna()
    path_df.loc[mask, "smash"] = path_df.loc[mask, "ball_speed_mph"] / path_df.loc[mask, "club_speed_mph"]

    path_clubs = st.multiselect(
        "Club(s)", sorted(path_df["club"].unique()),
        default=[sorted(path_df["club"].unique())[0]], key="path_club"
    )
    path_plot_df = path_df[path_df["club"].isin(path_clubs)].copy()

    # Build custom hover text
    def fmt(val, decimals=1, suffix=""):
        return f"{val:.{decimals}f}{suffix}" if pd.notna(val) else "—"

    path_plot_df["hover"] = path_plot_df.apply(lambda r: (
        f"<b>{r['club']}</b><br>"
        f"Club Path: {fmt(r['club_path_deg'])}°<br>"
        f"Face Angle: {fmt(r['face_angle_deg'])}°<br>"
        f"Smash Factor: {fmt(r['smash'], 3)}<br>"
        f"Club Speed: {fmt(r['club_speed_mph'])} mph<br>"
        f"Dynamic Loft: {fmt(r['dynamic_loft_deg'])}°<br>"
        f"Launch Angle: {fmt(r['launch_angle_deg'])}°<br>"
        f"Spin Rate: {fmt(r['total_spin_rpm'], 0)} rpm<br>"
        f"Carry: {fmt(r['carry_yd'])} yd<br>"
        f"Offline: {fmt(r['offline_yd'])} yd<br>"
        f"Total: {fmt(r['total_yd'])} yd"
    ), axis=1)

    fig4 = go.Figure()

    colors_seq = px.colors.qualitative.Plotly
    for i, club in enumerate(path_clubs):
        cdf = path_plot_df[path_plot_df["club"] == club].copy()
        smash_vals = cdf["smash"].dropna()
        smash_min = smash_vals.min() if not smash_vals.empty else 1.0
        smash_max = smash_vals.max() if not smash_vals.empty else 1.5

        fig4.add_trace(go.Scatter(
            x=cdf["club_path_deg"],
            y=cdf["face_angle_deg"],
            mode="markers",
            name=club,
            text=cdf["hover"],
            hovertemplate="%{text}<extra></extra>",
            marker=dict(
                size=10,
                color=cdf["smash"],
                colorscale="RdYlGn",
                cmin=smash_min,
                cmax=smash_max,
                showscale=(i == 0),
                colorbar=dict(title="Smash Factor", x=1.02) if i == 0 else None,
                line=dict(width=1, color="white"),
                opacity=0.85,
            ),
        ))

    # Reference lines at 0
    fig4.add_vline(x=0, line_color="#CCCCCC", line_width=1, line_dash="dash")
    fig4.add_hline(y=0, line_color="#CCCCCC", line_width=1, line_dash="dash")
    # 45-degree line (face = path = perfectly square)
    axis_range = max(
        abs(path_plot_df["club_path_deg"]).max(),
        abs(path_plot_df["face_angle_deg"]).max()
    ) * 1.2 + 1
    fig4.add_trace(go.Scatter(
        x=[-axis_range, axis_range], y=[-axis_range, axis_range],
        mode="lines", line=dict(color="#DDDDDD", width=1, dash="dot"),
        name="Face = Path", showlegend=True, hoverinfo="skip",
    ))

    fig4.update_layout(
        title=f"Club Path vs Face Angle — {', '.join(path_clubs)}",
        xaxis_title="Club Path (°)<br><sub>← Out-to-In  |  In-to-Out →</sub>",
        yaxis_title="Face Angle (°)<br><sub>← Closed  |  Open →</sub>",
        plot_bgcolor="white", paper_bgcolor="white",
        xaxis=dict(showgrid=False, zeroline=False,
                   range=[-axis_range, axis_range]),
        yaxis=dict(gridcolor="#EEEEEE", zeroline=False,
                   range=[-axis_range, axis_range]),
        height=550, legend_title="Club",
        legend=dict(x=1.12),
        coloraxis_colorbar=dict(x=1.02, len=0.5, yanchor="top", y=1),
    )
    st.plotly_chart(fig4, use_container_width=True)
