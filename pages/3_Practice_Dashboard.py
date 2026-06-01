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
st.markdown('<p class="sub-header">Select a player and date range (from the menu on the left) to view their development</p>', unsafe_allow_html=True)


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


# ── SIDEBAR FILTERS ──────────────────────────────────────────────────────────
players = load_players()
if not players:
    st.warning("No players found in database.")
    st.stop()

players_sorted = sorted(players, key=lambda p: p["last_name"].lower())
player_options = {f"{p['first_name']} {p['last_name']}": p["player_id"] for p in players_sorted}

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

    all_clubs = sorted(df["club"].dropna().unique().tolist())
    selected_clubs = st.multiselect("Clubs", all_clubs, default=all_clubs)
    df = df[df["club"].isin(selected_clubs)]

    if df.empty:
        st.info("No shots for the selected clubs.")
        st.stop()

    # Outlier detection
    if "ball_speed_mph" in df.columns and df["ball_speed_mph"].notna().any():
        medians = df.groupby(["club", df["session_date"].dt.date])["ball_speed_mph"].transform("median")
        df["_is_outlier"] = (
            df["ball_speed_mph"].notna() &
            ((df["ball_speed_mph"] < medians * 0.85) | (df["ball_speed_mph"] > medians * 1.15))
        )
    else:
        df["_is_outlier"] = False

    n_outliers = df["_is_outlier"].sum()
    exclude_outliers = st.checkbox(
        f"Exclude outliers ({n_outliers} shots ±15% from median ball speed)",
        value=False,
        key="exclude_outliers",
    )
    if exclude_outliers:
        df = df[~df["_is_outlier"]].copy()

    st.caption(f"**{len(df):,} shots** · {df['session_date'].dt.date.nunique()} sessions · {df['club'].nunique()} clubs" +
               (f" · {n_outliers} excluded" if exclude_outliers else ""))



# ── PLOT 1: Box Plot ──────────────────────────────────────────────────────────
st.markdown('<p class="section-header">📦 Metric by Date</p>', unsafe_allow_html=True)

p1c1, p1c2, p1c3 = st.columns([2, 2, 1])
with p1c1:
    selected_metric_name = st.selectbox("Display Metric", list(METRICS.keys()), key="box_metric")
    selected_metric_col  = METRICS[selected_metric_name]
with p1c2:
    box_clubs_available = sorted(df["club"].dropna().unique().tolist())
    box_clubs = st.multiselect("Club(s)", box_clubs_available, default=box_clubs_available, key="box_clubs")
with p1c3:
    color_by_club = st.checkbox("Color by club", value=True, key="box_color")

plot_df = df[df["club"].isin(box_clubs)][["session_date", "club", selected_metric_col]].dropna(subset=[selected_metric_col]).copy()
plot_df["date_str"] = plot_df["session_date"].dt.strftime("%b %d")

if plot_df.empty:
    st.info(f"No data available for {selected_metric_name}.")
else:
    # Need full df (pre-exclusion) for outlier highlighting when not excluded
    plot_df_full = df[df["club"].isin(box_clubs)][["session_date", "club", selected_metric_col, "_is_outlier"]].dropna(subset=[selected_metric_col]).copy()
    plot_df_full["date_str"] = plot_df_full["session_date"].dt.strftime("%b %d")

    fig1 = px.box(
        plot_df, x="date_str", y=selected_metric_col,
        color="club" if color_by_club else None,
        points="all",
        labels={"date_str": "Date", selected_metric_col: selected_metric_name, "club": "Club"},
        title=f"{selected_metric_name} by Date — {selected_player_name}",
    )
    # Highlight outliers if not already excluded
    if not exclude_outliers:
        outlier_df = plot_df_full[plot_df_full["_is_outlier"]]
        if not outlier_df.empty:
            fig1.add_trace(go.Scatter(
                x=outlier_df["date_str"], y=outlier_df[selected_metric_col],
                mode="markers", name="Outlier",
                marker=dict(size=10, color="red", symbol="x", line=dict(width=2, color="darkred")),
                hovertemplate="<b>Outlier</b><br>Date: %{x}<br>Value: %{y}<extra></extra>",
            ))
    fig1.update_layout(
        height=500, plot_bgcolor="white", paper_bgcolor="white",
        xaxis=dict(showgrid=False), yaxis=dict(gridcolor="#EEEEEE"),
        legend_title="Club",
    )
    st.plotly_chart(fig1, use_container_width=True)




def _darken_hex(hex_color: str, factor: float = 0.85) -> str:
    """Return a slightly darker version of a hex color."""
    hex_color = hex_color.lstrip("#")
    r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
    return "#{:02x}{:02x}{:02x}".format(int(r*factor), int(g*factor), int(b*factor))

# ── PLOT 2: Carry Dispersion ──────────────────────────────────────────────────
st.markdown('<p class="section-header">📍 Carry Dispersion</p>', unsafe_allow_html=True)

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
                        marker=dict(size=8, color=date_colors[j % len(date_colors)],
                                    opacity=0.75, line=dict(width=1, color="white")),
                    ))
            else:
                fig3.add_trace(go.Scatter(
                    x=normal_data["offline_yd"], y=normal_data["carry_yd"], mode="markers",
                    name=club, showlegend=False,
                    text=normal_data["hover_text"],
                    hovertemplate="%{text}<extra></extra>",
                    marker=dict(size=8, color=base_color, opacity=0.75,
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


# ── PLOTS 3 & 4: Face Impact + Club Path vs Face Angle (side by side) ──────
st.markdown('<p class="section-header">🎯 Strike Location &nbsp;&nbsp;|&nbsp;&nbsp; 📦 Delivery</p>', unsafe_allow_html=True)

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

            # Individual shots — hidden from legend
            fig2.add_trace(go.Scatter(
                x=club_df["face_impact_horizontal_mm"],
                y=club_df["face_impact_vertical_mm"],
                mode="markers", name=club, showlegend=False,
                marker=dict(size=8, color=color, opacity=0.6, line=dict(width=1, color="white")),
                hovertemplate=f"<b>{club}</b><br>Horizontal: %{{x:.1f}} mm<br>Vertical: %{{y:.1f}} mm<extra></extra>",
            ))
            # Average dot — in legend with club name, avg size/color
            fig2.add_trace(go.Scatter(
                x=[avg_h], y=[avg_v],
                mode="markers", name=club, showlegend=True,
                marker=dict(size=16, color=color, symbol="circle",
                            line=dict(width=2, color="white")),
                hovertemplate=f"<b>{club} avg</b><br>Horizontal: {avg_h:.1f} mm<br>Vertical: {avg_v:.1f} mm<extra></extra>",
            ))

        fig2.update_layout(
            title=f"Strike Location — {', '.join(impact_clubs)}",
            xaxis_title="← Heel  |  Toe →  (mm)",
            yaxis_title="← Low  |  High →  (mm)",
            xaxis=dict(range=[-28, 28], showgrid=False, zeroline=False),
            yaxis=dict(range=[-28, 28], showgrid=False, zeroline=False, scaleanchor="x"),
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
            fig4.add_trace(go.Scatter(
                x=cdf["club_path_deg"], y=cdf["face_angle_deg"],
                mode="markers", name=club,
                text=cdf["hover"], hovertemplate="%{text}<extra></extra>",
                marker=dict(size=10, symbol=shape,
                            color=cdf["angle_of_attack_deg"],
                            colorscale="RdYlGn", cmin=aoa_min, cmax=aoa_max,
                            showscale=(i == 0),
                            colorbar=dict(title="Angle of Attack (°)",
                                          x=1.02, y=1, len=0.5, yanchor="top",
                                          titlefont=dict(size=13)) if i == 0 else None,
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
                title="Club",
                x=1.02,
                y=0.5,
                yanchor="top",
                titlefont=dict(size=13)
            ),
            coloraxis_colorbar=dict(x=1.02, len=0.5, yanchor="top", y=1),
        )
        st.plotly_chart(fig4, use_container_width=True)
