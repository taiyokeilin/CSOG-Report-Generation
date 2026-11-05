"""PDF reports: the one-page practice report card and the progress-over-time report.

Built with ReportLab (pure Python) and matplotlib for the trend charts.
Text sticks to characters that the built-in PDF fonts support (no arrows or
special symbols), so nothing renders as a blank box.
"""
import io
import math
import os
from datetime import date, datetime, timedelta

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    CondPageBreak, Image, KeepInFrame, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

from calculations import compute_club_stats, compute_overall

# ── Palette ───────────────────────────────────────────────────────────────────
NAVY = colors.HexColor("#1F4E79")
BLUE = colors.HexColor("#2E75B6")
RULE = colors.HexColor("#BDD7EE")
GREY_BG = colors.HexColor("#F2F2F2")
GRID = colors.HexColor("#AAAAAA")
INK = colors.HexColor("#222222")
MUTED = colors.HexColor("#666666")

GREEN_BG, GREEN_FG = colors.HexColor("#E2EFDA"), colors.HexColor("#375623")
AMBER_BG, AMBER_FG = colors.HexColor("#FFEB9C"), colors.HexColor("#7D6608")
RED_BG, RED_FG = colors.HexColor("#FFC7CE"), colors.HexColor("#9C0006")

# Chart series colors (validated categorical slots 1 and 2)
C_SERIES1 = "#2a78d6"
C_SERIES2 = "#eb6834"
C_TEXT = "#52514e"
C_GRID = "#e6e6e4"

PAGE_W, PAGE_H = letter
MARGIN = 36
CONTENT_W = PAGE_W - 2 * MARGIN

SECTION_ORDER = ["Putting", "Wedge Play", "Approach", "Driving", "Other"]


# ── Small helpers ─────────────────────────────────────────────────────────────
def _style(name="x", size=8, bold=False, color=INK, align=TA_LEFT, leading=None, italic=False):
    font = "Helvetica"
    if bold and italic:
        font = "Helvetica-BoldOblique"
    elif bold:
        font = "Helvetica-Bold"
    elif italic:
        font = "Helvetica-Oblique"
    return ParagraphStyle(name, fontName=font, fontSize=size, textColor=color,
                          alignment=align, leading=leading or size * 1.25)


def _pct(x):
    return "" if x is None else f"{round(x * 100)}%"


def _ratio_colors(actual, target):
    """Same green / amber / red thresholds as the Excel report."""
    if actual is None or not target:
        return None, None
    r = actual / target
    if r >= 1.0:
        return GREEN_BG, GREEN_FG
    if r >= 0.7:
        return AMBER_BG, AMBER_FG
    return RED_BG, RED_FG


def _status_colors(status):
    return {"Goal Met": (GREEN_BG, GREEN_FG),
            "Approaching Goal": (AMBER_BG, AMBER_FG),
            "Goal in Progress": (RED_BG, RED_FG)}.get(status, (None, None))


def _logo(logo_path, size):
    if not (logo_path and os.path.exists(logo_path)):
        return ""
    try:
        w, h = ImageReader(logo_path).getSize()
        scale = size / max(w, h)
        return Image(logo_path, width=w * scale, height=h * scale)
    except Exception:
        return ""


def _footer(label):
    def draw(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(MUTED)
        canvas.drawString(MARGIN, 22, label)
        canvas.drawRightString(PAGE_W - MARGIN, 22, f"Page {doc.page}")
        canvas.restoreState()
    return draw


def _section_bar(text, width=CONTENT_W, size=10):
    t = Table([[Paragraph(text, _style("sec", size, bold=True, color=NAVY))]], colWidths=[width])
    t.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, -1), 1.2, RULE),
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
    ]))
    return t


def _header_block(title, info_rows, logo_path):
    """Logo on the left, title and a small label/value grid on the right."""
    info = Table(
        [[Paragraph(f"<b>{a}</b>", _style("l", 9, align=TA_LEFT)), Paragraph(str(b or ""), _style("v", 9)),
          Paragraph(f"<b>{c}</b>", _style("l", 9)), Paragraph(str(d or ""), _style("v", 9))]
         for a, b, c, d in info_rows],
        colWidths=[44, 170, 50, 170],
    )
    info.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 1),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    right = [Paragraph(title, _style("t", 20, bold=True, color=NAVY, leading=24)), Spacer(1, 4), info]
    block = Table([[_logo(logo_path, 62), right]], colWidths=[76, CONTENT_W - 76])
    block.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    return block


# ═══════════════════════════════════════════════════════════════════════════════
# 1. One-page practice report card
# ═══════════════════════════════════════════════════════════════════════════════
def build_session_card_pdf(df, session_info: dict, club_configs: list, logo_path: str = None) -> bytes:
    """club_configs rows: {club, level, target_type, distance_yd, section}. Always one page."""
    sections_dict: dict = {}
    for cfg in club_configs:
        stats = compute_club_stats(df, club_name=cfg["club"], target_type=cfg["target_type"],
                                   distance_yd=cfg["distance_yd"], level=cfg["level"],
                                   section=cfg.get("section", ""))
        sections_dict.setdefault(cfg.get("section", "Other"), []).append({**cfg, "stats": stats})

    ordered = [s for s in SECTION_ORDER if s in sections_dict] + \
              [s for s in sections_dict if s not in SECTION_ORDER]
    overall = compute_overall([r["stats"] for s in ordered for r in sections_dict[s]])

    story = [
        _header_block("Player Practice Report Card", [
            ("Player:", session_info.get("player"), "Coach:", session_info.get("coach")),
            ("Date:", session_info.get("date"), "Week:", session_info.get("week")),
        ], logo_path),
        Spacer(1, 10),
    ]

    n_rows = sum(len(v) for v in sections_dict.values())
    pad = 6 if n_rows <= 12 else (4.5 if n_rows <= 18 else 3)      # roomier when there is space
    notes_h = 130 if n_rows <= 12 else (100 if n_rows <= 18 else 84)
    col_w = [76, 30, 66, 34, 52, 52, 34, 30, 32, 36, 32, 66]   # = 540
    headers = ["Club", "Level", "Target Type", "Dist (yd)", "Target", "Actual", "Target %",
               "Att.", "Succ.", "Actual %", "Goal %", "Goal Status"]
    hstyle = _style("h", 7, bold=True, italic=True, align=TA_CENTER, leading=8.5)
    cstyle = _style("c", 7.5, align=TA_CENTER)
    clubstyle = _style("cl", 7.5, bold=True)

    for sec in ordered:
        rows = sections_dict[sec]
        data = [[Paragraph(h, hstyle) for h in headers]]
        cmds = [
            ("BACKGROUND", (0, 0), (-1, 0), GREY_BG),
            ("GRID", (0, 0), (-1, -1), 0.5, GRID),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), pad), ("BOTTOMPADDING", (0, 0), (-1, -1), pad),
            ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ]
        for i, r in enumerate(rows, start=1):
            s = r["stats"]
            goal = s["actual_pct"] / s["target_pct"] if (s["actual_pct"] is not None and s["target_pct"]) else None
            data.append([
                Paragraph(r["club"], clubstyle),
                Paragraph(str(r["level"]), cstyle),
                Paragraph(r["target_type"], cstyle),
                Paragraph("" if r["distance_yd"] is None else str(r["distance_yd"]), cstyle),
                Paragraph(s["target_raw"] or "", cstyle),
                Paragraph(s["actual_raw"] or "", cstyle),
                Paragraph(_pct(s["target_pct"]), cstyle),
                Paragraph(str(s["attempts"] or ""), cstyle),
                Paragraph("" if s["successes"] is None else str(s["successes"]), cstyle),
                Paragraph(_pct(s["actual_pct"]), cstyle),
                Paragraph(_pct(goal), cstyle),
                Paragraph(s["goal_status"] or "", cstyle),
            ])
            bg, fg = _ratio_colors(s["actual_pct"], s["target_pct"])
            if bg is not None:
                for col in (9, 10):
                    cmds += [("BACKGROUND", (col, i), (col, i), bg)]
                data[i][9] = Paragraph(f'<font color="{fg.hexval()}">{_pct(s["actual_pct"])}</font>', cstyle)
                data[i][10] = Paragraph(f'<font color="{fg.hexval()}">{_pct(goal)}</font>', cstyle)
            sbg, sfg = _status_colors(s["goal_status"])
            if sbg is not None:
                cmds.append(("BACKGROUND", (11, i), (11, i), sbg))
                data[i][11] = Paragraph(f'<font color="{sfg.hexval()}">{s["goal_status"]}</font>', cstyle)
        t = Table(data, colWidths=col_w, repeatRows=1)
        t.setStyle(TableStyle(cmds))
        story += [_section_bar(sec), Spacer(1, 3), t, Spacer(1, 8 if pad < 6 else 12)]

    # Overall
    ov = overall
    ov_labels = ["Attempts", "Successes", "Success %", "Goals", "Goals Met", "Goal %"]
    ov_vals = [str(ov["total_attempts"]), str(ov["total_successes"]), _pct(ov["success_pct"]),
               str(ov["goals_total"]), str(ov["goals_met"]), _pct(ov["goal_pct"])]
    big = _style("big", 11, align=TA_CENTER)
    ov_data = [[Paragraph(l, hstyle) for l in ov_labels], [Paragraph(v, big) for v in ov_vals]]
    ov_cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), GREY_BG), ("GRID", (0, 0), (-1, -1), 0.5, GRID),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    for col, val in ((5, ov["goal_pct"]),):
        bg, fg = _ratio_colors(val, 1.0)
        if bg is not None:
            ov_cmds.append(("BACKGROUND", (col, 1), (col, 1), bg))
            ov_data[1][col] = Paragraph(f'<font color="{fg.hexval()}">{ov_vals[col]}</font>', big)
    ov_t = Table(ov_data, colWidths=[CONTENT_W / 6] * 6)
    ov_t.setStyle(TableStyle(ov_cmds))
    story += [_section_bar("Overall"), Spacer(1, 3), ov_t, Spacer(1, 8)]

    # Notes box
    notes = Table([[""]], colWidths=[CONTENT_W], rowHeights=[notes_h])
    notes.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.5, GRID), ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FAFAFA"))]))
    story += [_section_bar("Additional Notes"), Spacer(1, 3), notes, Spacer(1, 6)]
    story.append(Paragraph(
        "Goal Met: Goal % of 100% or more. Approaching Goal: 70% to 99%. Goal in Progress: below 70%. "
        "Goal % = Actual % divided by Target %.", _style("leg", 6.5, color=MUTED)))

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, leftMargin=MARGIN, rightMargin=MARGIN,
                            topMargin=MARGIN, bottomMargin=34,
                            title="Player Practice Report Card", author="CSOG")
    frame_h = PAGE_H - MARGIN - 34 - 12
    doc.build([KeepInFrame(CONTENT_W, frame_h, story, mode="shrink")],
              onFirstPage=_footer(f"Generated {date.today().strftime('%b %d, %Y')}"))
    return buf.getvalue()


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Progress over time
# ═══════════════════════════════════════════════════════════════════════════════
def _smash(df):
    s = pd.to_numeric(df.get("smash_factor"), errors="coerce")
    if s.isna().all() and "ball_speed_mph" in df and "club_speed_mph" in df:
        s = df["ball_speed_mph"] / df["club_speed_mph"]
    return s


def _style_axes(ax):
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color("#bdbdb8")
    ax.tick_params(colors=C_TEXT, labelsize=7, length=2)
    ax.grid(axis="y", color=C_GRID, linewidth=0.6)
    ax.set_axisbelow(True)


def _date_axis(ax, dates):
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    if len(dates) <= 8:
        ax.set_xticks(dates)
    else:
        ax.xaxis.set_major_locator(mdates.AutoDateLocator(maxticks=8))
    ax.margins(x=0.06)


def _fig_image(fig, width_pt):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=200, facecolor="white")
    plt.close(fig)
    buf.seek(0)
    w_in, h_in = fig.get_size_inches()
    return Image(buf, width=width_pt, height=width_pt * h_in / w_in)


def _goal_chart(sessions_summary):
    pts = [s for s in sessions_summary if s["success_pct"] is not None]
    if len(pts) < 2:
        return None
    x = [s["date"] for s in pts]
    fig, ax = plt.subplots(figsize=(7.5, 2.5))
    ax.plot(x, [s["success_pct"] * 100 for s in pts], color=C_SERIES1, linewidth=1.8, marker="o",
            markersize=5.5, markeredgecolor="white", markeredgewidth=1, label="Success %")
    gp = [s for s in pts if s["goal_pct"] is not None]
    if len(gp) >= 2:
        ax.plot([s["date"] for s in gp], [s["goal_pct"] * 100 for s in gp], color=C_SERIES2, linewidth=1.8,
                marker="o", markersize=5.5, markeredgecolor="white", markeredgewidth=1, label="Goals met %")
        ax.annotate(f"{gp[-1]['goal_pct'] * 100:.0f}%", (gp[-1]["date"], gp[-1]["goal_pct"] * 100),
                    textcoords="offset points", xytext=(7, -3), fontsize=8, color=C_TEXT)
    ax.annotate(f"{pts[-1]['success_pct'] * 100:.0f}%", (pts[-1]["date"], pts[-1]["success_pct"] * 100),
                textcoords="offset points", xytext=(7, -3), fontsize=8, color=C_TEXT)
    ax.set_ylim(0, 105)
    ax.set_yticks([0, 25, 50, 75, 100])
    ax.set_yticklabels([f"{v}%" for v in (0, 25, 50, 75, 100)])
    _style_axes(ax)
    _date_axis(ax, x)
    ax.legend(loc="upper left", frameon=False, fontsize=8, ncol=2, labelcolor=C_TEXT)
    fig.tight_layout(pad=0.6)
    return _fig_image(fig, CONTENT_W)


def _club_chart(club, rows):
    """rows: list of (date, mean_carry, sd_carry, sd_offline) for sessions with enough shots."""
    x = [r[0] for r in rows]
    carry = [r[1] for r in rows]
    sd = [0 if (r[2] is None or math.isnan(r[2])) else r[2] for r in rows]
    spread = [r[3] for r in rows]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(7.5, 1.9))
    a1.fill_between(x, [c - s for c, s in zip(carry, sd)], [c + s for c, s in zip(carry, sd)],
                    color=C_SERIES1, alpha=0.15, linewidth=0)
    a1.plot(x, carry, color=C_SERIES1, linewidth=1.8, marker="o", markersize=5, markeredgecolor="white", markeredgewidth=1)
    a1.annotate(f"{carry[-1]:.0f}", (x[-1], carry[-1]), textcoords="offset points", xytext=(6, -3), fontsize=7.5, color=C_TEXT)
    a1.set_title("Carry, yd (average, shaded = +/- 1 SD)", fontsize=7.5, color=C_TEXT, loc="left")
    a2.plot(x, spread, color=C_SERIES2, linewidth=1.8, marker="o", markersize=5, markeredgecolor="white", markeredgewidth=1)
    a2.annotate(f"{spread[-1]:.1f}", (x[-1], spread[-1]), textcoords="offset points", xytext=(6, -3), fontsize=7.5, color=C_TEXT)
    a2.set_title("Side-to-side spread, yd (SD, lower is tighter)", fontsize=7.5, color=C_TEXT, loc="left")
    a2.set_ylim(bottom=0)
    for ax in (a1, a2):
        _style_axes(ax)
        _date_axis(ax, x)
    fig.tight_layout(pad=0.6)
    return _fig_image(fig, CONTENT_W)


def _fmt(v, d=0, suffix=""):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "-"
    return f"{v:.{d}f}{suffix}"


def _table_style(extra=None):
    base = [
        ("BACKGROUND", (0, 0), (-1, 0), GREY_BG), ("GRID", (0, 0), (-1, -1), 0.5, GRID),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ]
    return TableStyle(base + (extra or []))


def build_progress_pdf(player_name: str, coach_name: str, sessions: list,
                       date_from: date, date_to: date, logo_path: str = None) -> bytes:
    """sessions: list of {"date": date, "df": shots DataFrame, "targets": [target rows]} (any order)."""
    sessions = sorted(sessions, key=lambda s: s["date"])

    # ── Per-session goal results (using the targets in force that day) ────────
    summary = []
    for s in sessions:
        df = s["df"]
        stats_rows = []
        for t in s["targets"]:
            stats_rows.append(compute_club_stats(df, t["club"], t["target_type"], t.get("distance_yd"), t["level"]))
        ov = compute_overall(stats_rows) if stats_rows else None
        has = bool(ov and ov["total_attempts"] > 0)
        summary.append({
            "date": s["date"], "shots": len(df),
            "attempts": ov["total_attempts"] if has else 0,
            "successes": ov["total_successes"] if has else 0,
            "goals_total": ov["goals_total"] if has else 0,
            "goals_met": ov["goals_met"] if has else 0,
            "success_pct": ov["success_pct"] if has else None,
            "goal_pct": ov["goal_pct"] if has else None,
        })
    has_targets = any(x["success_pct"] is not None for x in summary)

    # ── Headline tiles ────────────────────────────────────────────────────────
    total_shots = sum(x["shots"] for x in summary)
    all_clubs = set()
    for s in sessions:
        all_clubs |= set(s["df"]["club"].dropna().unique())
    tile_vals = [("Sessions", str(len(sessions))), ("Shots", f"{total_shots:,}"), ("Clubs", str(len(all_clubs)))]
    if has_targets:
        pts = [x for x in summary if x["success_pct"] is not None]
        first, last = pts[0]["success_pct"], pts[-1]["success_pct"]
        diff = round((last - first) * 100)
        tile_vals += [("Success % (latest)", _pct(last)),
                      ("Change since first", f"{'+' if diff > 0 else ''}{diff} pts")]
    tn = len(tile_vals)
    tile_lbl = _style("tl", 7, bold=True, italic=True, align=TA_CENTER, leading=8.5)
    tile_val = _style("tv", 15, bold=True, color=NAVY, align=TA_CENTER, leading=18)
    tiles = Table([[Paragraph(l, tile_lbl) for l, _ in tile_vals], [Paragraph(v, tile_val) for _, v in tile_vals]],
                  colWidths=[CONTENT_W / tn] * tn)
    tiles.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), GREY_BG), ("BOX", (0, 0), (-1, -1), 0.5, GRID),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, GRID), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))

    span = f"{date_from.strftime('%b %d, %Y')} - {date_to.strftime('%b %d, %Y')}"
    story = [
        _header_block("Player Progress Report", [
            ("Player:", player_name, "Coach:", coach_name),
            ("Period:", span, "Sessions:", str(len(sessions))),
        ], logo_path),
        Spacer(1, 10), tiles, Spacer(1, 10),
    ]

    # ── Goal progress chart ───────────────────────────────────────────────────
    story += [_section_bar("Goal progress by session"), Spacer(1, 4)]
    chart = _goal_chart(summary) if has_targets else None
    if chart is not None:
        story.append(chart)
    else:
        story.append(Paragraph("No saved targets cover these sessions, so goal results can't be shown. "
                               "Save targets for this player on the Report Generator page.",
                               _style("n", 8, color=MUTED)))
    story.append(Spacer(1, 10))

    # ── Weekly summary ────────────────────────────────────────────────────────
    weeks: dict = {}
    for x in summary:
        monday = x["date"] - timedelta(days=x["date"].weekday())
        w = weeks.setdefault(monday, {"sessions": 0, "shots": 0, "att": 0, "succ": 0, "gm": 0, "gt": 0})
        w["sessions"] += 1; w["shots"] += x["shots"]; w["att"] += x["attempts"]; w["succ"] += x["successes"]
        w["gm"] += x["goals_met"]; w["gt"] += x["goals_total"]
    week_keys = sorted(weeks)
    shown = week_keys[-12:]
    h = _style("h", 7, bold=True, italic=True, align=TA_CENTER, leading=8.5)
    c = _style("c", 8, align=TA_CENTER)
    wdata = [[Paragraph(t, h) for t in ["Week of", "Sessions", "Shots", "Success %", "Goals met"]]]
    wcmds = []
    for i, k in enumerate(shown, start=1):
        w = weeks[k]
        sp = (w["succ"] / w["att"]) if w["att"] else None
        wdata.append([Paragraph(k.strftime("%b %d, %Y"), c), Paragraph(str(w["sessions"]), c),
                      Paragraph(str(w["shots"]), c), Paragraph(_pct(sp), c),
                      Paragraph(f"{w['gm']} of {w['gt']}" if w["gt"] else "", c)])
    wt = Table(wdata, colWidths=[CONTENT_W / 5] * 5, repeatRows=1)
    wt.setStyle(_table_style(wcmds))
    note = "" if len(week_keys) <= 12 else f" (latest 12 of {len(week_keys)} weeks)"
    story += [KeepTogether([_section_bar("Week by week" + note), Spacer(1, 4), wt]), Spacer(1, 10)]

    # ── Per-club metrics across all sessions ─────────────────────────────────
    per_session_club = {}   # club -> list of (date, mean carry, sd carry, sd offline, n)
    pooled = []
    for s in sessions:
        d = s["df"].copy()
        d["_smash"] = _smash(d)
        d["_date"] = s["date"]
        pooled.append(d)
        for club, g in d.groupby("club"):
            if len(g) >= 3 and g["carry_yd"].notna().sum() >= 3:
                per_session_club.setdefault(club, []).append(
                    (s["date"], g["carry_yd"].mean(), g["carry_yd"].std(), g["offline_yd"].std(), len(g)))
    allshots = pd.concat(pooled, ignore_index=True) if pooled else pd.DataFrame()
    club_rows = []
    if not allshots.empty:
        for club, g in allshots.groupby("club"):
            sess = per_session_club.get(club, [])
            trend, trend_color = "-", MUTED
            if len(sess) >= 2 and sess[0][3] and not math.isnan(sess[0][3]) and not math.isnan(sess[-1][3]):
                a, b = sess[0][3], sess[-1][3]
                pct = (b - a) / a * 100
                trend = f"{a:.1f} to {b:.1f} yd ({pct:+.0f}%)"
                trend_color = GREEN_FG if pct <= -10 else (RED_FG if pct >= 10 else MUTED)
            club_rows.append({
                "club": club, "sessions": g["_date"].nunique(), "shots": len(g),
                "cs": g["club_speed_mph"].mean(), "bs": g["ball_speed_mph"].mean(), "smash": g["_smash"].mean(),
                "carry": g["carry_yd"].mean(), "carry_sd": g["carry_yd"].std(), "off_sd": g["offline_yd"].std(),
                "trend": trend, "trend_color": trend_color,
            })
    club_rows.sort(key=lambda r: -(r["carry"] if r["carry"] == r["carry"] else 0))

    cw = [84, 34, 34, 42, 42, 36, 42, 42, 42, 142]   # = 540
    hdrs = ["Club", "Sess.", "Shots", "Club speed", "Ball speed", "Smash", "Avg carry", "Carry SD", "Side SD", "Side spread, first to latest session"]
    cdata = [[Paragraph(t, h) for t in hdrs]]
    ccmds = []
    for i, r in enumerate(club_rows, start=1):
        cdata.append([
            Paragraph(r["club"], _style("cb", 8, bold=True)), Paragraph(str(r["sessions"]), c),
            Paragraph(str(r["shots"]), c), Paragraph(_fmt(r["cs"], 1), c), Paragraph(_fmt(r["bs"], 1), c),
            Paragraph(_fmt(r["smash"], 2), c), Paragraph(_fmt(r["carry"], 0), c),
            Paragraph(_fmt(r["carry_sd"], 1), c), Paragraph(_fmt(r["off_sd"], 1), c),
            Paragraph(f'<font color="{r["trend_color"].hexval()}">{r["trend"]}</font>', c),
        ])
    ct = Table(cdata, colWidths=cw, repeatRows=1)
    ct.setStyle(_table_style(ccmds))
    story += [
        _section_bar("Club summary (all sessions in this period)"), Spacer(1, 4), ct, Spacer(1, 4),
        Paragraph("Speeds in mph, distances in yards. SD = standard deviation (the typical size of the miss; "
                  "smaller means more consistent). Side SD measures left-right spread. "
                  "Green means the side spread shrank by 10% or more between the first and latest session.",
                  _style("fn", 6.5, color=MUTED)),
        Spacer(1, 12),
    ]

    # ── Club trend charts ─────────────────────────────────────────────────────
    charted = [r["club"] for r in club_rows if len(per_session_club.get(r["club"], [])) >= 2]
    if charted:
        story.append(CondPageBreak(190))
        story.append(_section_bar("Club trends by session"))
        story.append(Spacer(1, 4))
        for club in charted:
            story.append(KeepTogether([
                Paragraph(club, _style("ct", 9, bold=True, color=NAVY)),
                _club_chart(club, per_session_club[club]),
                Spacer(1, 6),
            ]))
        skipped = [r["club"] for r in club_rows if r["club"] not in charted]
        if skipped:
            story.append(Paragraph("Not charted (fewer than two sessions with 3 or more shots): " + ", ".join(skipped),
                                   _style("sk", 7, color=MUTED)))

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, leftMargin=MARGIN, rightMargin=MARGIN,
                            topMargin=MARGIN, bottomMargin=36,
                            title="Player Progress Report", author="CSOG")
    label = f"{player_name} - Progress report - generated {date.today().strftime('%b %d, %Y')}"
    doc.build(story, onFirstPage=_footer(label), onLaterPages=_footer(label))
    return buf.getvalue()
