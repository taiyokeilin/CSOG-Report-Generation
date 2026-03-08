import io
import math
from datetime import date
from openpyxl import Workbook
from openpyxl.styles import (
    Font, PatternFill, Alignment, Border, Side, numbers
)
from openpyxl.utils import get_column_letter

from calculations import (
    compute_club_stats, compute_overall,
    feet_to_feet_inches_str, compute_proximity_ft,
    get_target_proximity_ft, get_target_range_yd, get_target_rate,
)
from data.tour_targets import get_tour_target, get_level_multipliers

# ── Palette ──────────────────────────────────────────────────────────────────
C_HEADER_BG   = "1F4E79"   # dark blue
C_HEADER_FG   = "FFFFFF"
C_SECTION_BG  = "2E75B6"   # mid blue
C_SECTION_FG  = "FFFFFF"
C_COL_HDR_BG  = "BDD7EE"   # light blue
C_DIST_BG     = "FFFF00"   # yellow – editable distance
C_ALT_ROW     = "EBF3FB"   # alternating row tint
C_GREEN       = "E2EFDA"
C_AMBER       = "FFEB9C"
C_RED_BG      = "FFC7CE"
C_RED_FG      = "9C0006"
C_GREEN_FG    = "375623"
C_AMBER_FG    = "7D6608"
C_RAW_FG      = "000000"

THIN = Side(style="thin", color="AAAAAA")
MED  = Side(style="medium", color="555555")

def _border(left=None, right=None, top=None, bottom=None):
    return Border(left=left or Side(), right=right or Side(),
                  top=top or Side(), bottom=bottom or Side())

def _fill(hex_color):
    return PatternFill("solid", fgColor=hex_color)

def _font(bold=False, color="000000", size=11, name="Arial"):
    return Font(bold=bold, color=color, size=size, name=name)

def _center():
    return Alignment(horizontal="center", vertical="center", wrap_text=True)

def _left():
    return Alignment(horizontal="left", vertical="center", wrap_text=True)


def _set_row(ws, row, values: list, bold=False, bg=None, fg="000000",
             border=None, align="left", size=11):
    for col_idx, val in enumerate(values, 1):
        cell = ws.cell(row=row, column=col_idx, value=val)
        cell.font = _font(bold=bold, color=fg, size=size)
        if bg:
            cell.fill = _fill(bg)
        if border:
            cell.border = border
        cell.alignment = _center() if align == "center" else _left()


def _merge_title(ws, row, text, start_col, end_col, bg, fg, size=12, bold=True):
    ws.merge_cells(
        start_row=row, start_column=start_col,
        end_row=row, end_column=end_col
    )
    cell = ws.cell(row=row, column=start_col, value=text)
    cell.font = _font(bold=bold, color=fg, size=size)
    cell.fill = _fill(bg)
    cell.alignment = _center()
    return cell


def _col_header_row(ws, row, headers: list, ncols: int):
    for col_idx, h in enumerate(headers, 1):
        cell = ws.cell(row=row, column=col_idx, value=h)
        cell.font = _font(bold=True, size=10)
        cell.fill = _fill(C_COL_HDR_BG)
        cell.alignment = _center()
        cell.border = _border(bottom=Side(style="thin"), top=Side(style="thin"))
    for col_idx in range(len(headers) + 1, ncols + 1):
        cell = ws.cell(row=row, column=col_idx)
        cell.fill = _fill(C_COL_HDR_BG)


def _goal_status_style(cell, status):
    if status == "Goal Met":
        cell.fill = _fill(C_GREEN)
        cell.font = _font(color=C_GREEN_FG, size=10)
    elif status == "Approaching Goal":
        cell.fill = _fill(C_AMBER)
        cell.font = _font(color=C_AMBER_FG, size=10)
    elif status == "Goal in Progress":
        cell.fill = _fill(C_RED_BG)
        cell.font = _font(color=C_RED_FG, size=10)
    else:
        cell.font = _font(size=10)


# ── Excel formula helpers ──────────────────────────────────────────────────
def _formula_target_proximity(dist_cell: str, level: int) -> str:
    """
    Build an Excel formula that, given a distance cell reference, looks up
    proximity target using embedded lookup table logic.
    We embed a nested IFS over the known distances rather than needing a
    hidden sheet, so editing distance recalculates instantly.
    """
    cases = []
    from data.tour_targets import TOUR_TARGETS, LEVELS_MULTIPLIERS
    _, prox_mult = LEVELS_MULTIPLIERS.get(level, (None, None))
    for yd, (prox_ft, range_yd, rate) in sorted(TOUR_TARGETS.items()):
        rounded_val = f'ROUND({dist_cell}/10,0)*10'
        cases.append(f'ROUND({dist_cell}/10,0)*10={yd},{prox_ft * prox_mult}')
    ifs_body = ",".join(cases)
    return f'=IFERROR(IFS({ifs_body}),"")'


def _formula_target_range(dist_cell: str, level: int) -> str:
    from data.tour_targets import TOUR_TARGETS, LEVELS_MULTIPLIERS
    _, prox_mult = LEVELS_MULTIPLIERS.get(level, (None, None))
    cases = []
    for yd, (prox_ft, range_yd, rate) in sorted(TOUR_TARGETS.items()):
        if range_yd is not None:
            cases.append(f'ROUND({dist_cell}/10,0)*10={yd},{range_yd * prox_mult}')
    ifs_body = ",".join(cases)
    return f'=IFERROR(IFS({ifs_body}),"")'


def _formula_target_rate(dist_cell: str, level: int) -> str:
    from data.tour_targets import TOUR_TARGETS, LEVELS_MULTIPLIERS
    rate_mult, _ = LEVELS_MULTIPLIERS.get(level, (None, None))
    cases = []
    for yd, (prox_ft, range_yd, rate) in sorted(TOUR_TARGETS.items()):
        if rate is not None:
            scaled = min(1.0, rate * rate_mult)
            cases.append(f'ROUND({dist_cell}/10,0)*10={yd},{scaled}')
    ifs_body = ",".join(cases)
    return f'=IFERROR(IFS({ifs_body}),"")'


def build_report_sheet(
    ws,
    session_info: dict,
    sections: list[dict],   # [{section_name, rows: [{club, level, target_type, distance_yd, stats}]}]
    raw_data_sheet_name: str,
    overall: dict,
):
    NCOLS = 14
    COL_CLUB   = 1
    COL_LEVEL  = 2
    COL_TTYPE  = 3
    COL_DIST   = 4   # ← yellow, editable
    COL_TRAW   = 5
    COL_ARAW   = 6
    COL_TPCT   = 7
    COL_ATT    = 8
    COL_SUCC   = 9
    COL_APCT   = 10
    COL_GOAL   = 11
    COL_STATUS = 12
    COL_NOTES  = 13

    # Column widths
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 8
    ws.column_dimensions["C"].width = 18
    ws.column_dimensions["D"].width = 12
    ws.column_dimensions["E"].width = 14
    ws.column_dimensions["F"].width = 14
    ws.column_dimensions["G"].width = 10
    ws.column_dimensions["H"].width = 10
    ws.column_dimensions["I"].width = 10
    ws.column_dimensions["J"].width = 10
    ws.column_dimensions["K"].width = 10
    ws.column_dimensions["L"].width = 16
    ws.column_dimensions["M"].width = 22
    ws.column_dimensions["N"].width = 22

    current_row = 1

    # ── Title ────────────────────────────────────────────────────────────────
    _merge_title(ws, current_row, "Player Practice Report Card",
                 1, NCOLS, C_HEADER_BG, C_HEADER_FG, size=16)
    ws.row_dimensions[current_row].height = 30
    current_row += 1

    # Meta row
    meta = [
        session_info.get("date", ""), "", "", "",
        "Player:", "", "", session_info.get("player", ""), "",
        "Coach:", "", session_info.get("coach", ""), "", ""
    ]
    for col_idx, val in enumerate(meta, 1):
        cell = ws.cell(row=current_row, column=col_idx, value=val)
        cell.font = _font(bold=(val in ("Player:", "Coach:")), size=11)
        cell.fill = _fill("D6E4F0")
        cell.alignment = _left()
    ws.row_dimensions[current_row].height = 20
    current_row += 1

    week_row = current_row
    ws.cell(row=week_row, column=5, value="Week:").font = _font(bold=True)
    ws.cell(row=week_row, column=8, value=session_info.get("week", "")).font = _font()
    ws.cell(row=week_row, column=5).fill = _fill("D6E4F0")
    for c in range(1, NCOLS + 1):
        ws.cell(row=week_row, column=c).fill = _fill("D6E4F0")
    current_row += 2

    # ── Distance note ────────────────────────────────────────────────────────
    note_cell = ws.cell(
        row=current_row, column=1,
        value="💡 Yellow cells (Distance) are editable — changing them recalculates Target and Actual columns automatically."
    )
    note_cell.font = _font(size=9, color="555555", bold=False)
    note_cell.fill = _fill("FFFDE7")
    ws.merge_cells(start_row=current_row, start_column=1, end_row=current_row, end_column=NCOLS)
    current_row += 2

    # ── Sections ─────────────────────────────────────────────────────────────
    all_stat_rows = []

    for section in sections:
        sec_name = section["section_name"]
        rows = section["rows"]

        # Section header
        _merge_title(ws, current_row, sec_name, 1, NCOLS, C_SECTION_BG, C_SECTION_FG, size=13)
        ws.row_dimensions[current_row].height = 22
        current_row += 1

        # Column headers
        col_headers = [
            "Club", "Level", "Target Type", "Distance (yd)",
            "Target (raw)", "Actual (raw)", "Target %",
            "Attempts", "Successes", "Actual %", "Goal %",
            "Goal Status", "Notes", ""
        ]
        _col_header_row(ws, current_row, col_headers, NCOLS)
        ws.row_dimensions[current_row].height = 30
        current_row += 1

        for i, row_data in enumerate(rows):
            club      = row_data["club"]
            level     = row_data["level"]
            ttype     = row_data["target_type"]
            dist      = row_data["distance_yd"]
            stats     = row_data["stats"]
            bg = C_ALT_ROW if i % 2 == 0 else "FFFFFF"

            dist_cell_ref = f"{get_column_letter(COL_DIST)}{current_row}"
            raw_sheet     = f"'{raw_data_sheet_name}'"

            # ── Populate cells ───────────────────────────────────────────
            cells_vals = {
                COL_CLUB:   club,
                COL_LEVEL:  level,
                COL_TTYPE:  ttype,
                COL_DIST:   dist,
                COL_ATT:    stats["attempts"] if stats["attempts"] else "",
                COL_SUCC:   stats["successes"] if stats["successes"] is not None else "",
                COL_NOTES:  "",
            }

            # Static computed values (Python-calculated, will match formula)
            cells_vals[COL_TRAW]  = stats["target_raw"] or ""
            cells_vals[COL_ARAW]  = stats["actual_raw"] or ""
            cells_vals[COL_TPCT]  = stats["target_pct"]
            cells_vals[COL_APCT]  = stats["actual_pct"]
            cells_vals[COL_GOAL]  = (
                f'=IFERROR({get_column_letter(COL_APCT)}{current_row}/'
                f'{get_column_letter(COL_TPCT)}{current_row},"")'
            )
            cells_vals[COL_STATUS] = stats["goal_status"] or ""

            # ── Live formulas for distance-dependent cols ────────────────
            # Target (raw) formula
            if dist is not None:
                if ttype == "Proximity":
                    cells_vals[COL_TRAW] = _formula_target_proximity(dist_cell_ref, level)
                else:
                    cells_vals[COL_TRAW] = (
                        f'=IFERROR(CONCATENATE("+/- ",'
                        f'TEXT({_formula_target_range(dist_cell_ref, level)[1:]},"0")," yds"),"")'
                    )

                # Target % formula
                cells_vals[COL_TPCT] = _formula_target_rate(dist_cell_ref, level)

            # Write all cells
            for col_idx, val in cells_vals.items():
                cell = ws.cell(row=current_row, column=col_idx, value=val)
                cell.fill = _fill(bg)
                cell.alignment = _center()
                cell.font = _font(size=10)
                cell.border = _border(bottom=Side(style="thin", color="CCCCCC"))

            # Special styling
            dist_cell = ws.cell(row=current_row, column=COL_DIST)
            dist_cell.fill = _fill(C_DIST_BG)
            dist_cell.font = _font(bold=True, color="000080", size=10)

            tpct_cell = ws.cell(row=current_row, column=COL_TPCT)
            tpct_cell.number_format = "0.0%"

            apct_cell = ws.cell(row=current_row, column=COL_APCT)
            apct_cell.number_format = "0.0%"

            goal_cell = ws.cell(row=current_row, column=COL_GOAL)
            goal_cell.number_format = "0.0%"

            status_cell = ws.cell(row=current_row, column=COL_STATUS)
            _goal_status_style(status_cell, stats.get("goal_status"))
            status_cell.alignment = _center()

            all_stat_rows.append(current_row)
            current_row += 1

        current_row += 1  # gap between sections

    # ── Overall summary ───────────────────────────────────────────────────
    _merge_title(ws, current_row, "Overall", 1, NCOLS, C_HEADER_BG, C_HEADER_FG, size=13)
    ws.row_dimensions[current_row].height = 22
    current_row += 1

    _col_header_row(ws, current_row, [
        "Attempts", "Successes", "Success %", "Goals", "Goals Met", "", "Goal %", "", "", "", "", "", "", ""
    ], NCOLS)
    ws.row_dimensions[current_row].height = 22
    current_row += 1

    ov = overall
    ov_vals = [
        ov["total_attempts"], ov["total_successes"],
        f'{ov["success_pct"]:.1%}' if ov["success_pct"] is not None else "",
        ov["goals_total"], ov["goals_met"], "",
        f'{ov["goal_pct"]:.1%}' if ov["goal_pct"] is not None else "",
    ]
    for col_idx, val in enumerate(ov_vals, 1):
        cell = ws.cell(row=current_row, column=col_idx, value=val)
        cell.font = _font(bold=True, size=11)
        cell.fill = _fill("D6E4F0")
        cell.alignment = _center()
    current_row += 2

    # Notes
    ws.cell(row=current_row, column=1, value="Additional Notes").font = _font(bold=True, size=11)
    current_row += 1
    ws.merge_cells(
        start_row=current_row, start_column=1,
        end_row=current_row + 4, end_column=NCOLS
    )
    notes_cell = ws.cell(row=current_row, column=1)
    notes_cell.fill = _fill("F5F5F5")
    notes_cell.alignment = _left()
    ws.row_dimensions[current_row].height = 80


def build_raw_data_sheet(ws, df):
    ws.title = "Raw Data"
    headers = list(df.columns)
    for col_idx, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_idx, value=h)
        cell.font = _font(bold=True, color=C_HEADER_FG, size=10)
        cell.fill = _fill(C_HEADER_BG)
        cell.alignment = _center()
        ws.column_dimensions[get_column_letter(col_idx)].width = max(14, len(h) + 2)

    try:
        rows_iter = df.to_dicts()  # polars
    except AttributeError:
        rows_iter = df.to_dict("records")  # pandas

    for row_idx, row in enumerate(rows_iter, 2):
        for col_idx, h in enumerate(headers, 1):
            val = row[h]
            if val is not None and isinstance(val, float) and math.isnan(val):
                val = None
            cell = ws.cell(row=row_idx, column=col_idx, value=val)
            cell.font = _font(size=9)
            if row_idx % 2 == 0:
                cell.fill = _fill(C_ALT_ROW)


def build_excel_report(
    df,
    session_info: dict,
    club_configs: list[dict],
) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)  # remove default sheet

    # ── Raw Data sheet ──────────────────────────────────────────────────
    ws_raw = wb.create_sheet("Raw Data")
    build_raw_data_sheet(ws_raw, df)

    # ── Build section structure ─────────────────────────────────────────
    section_order = ["Putting", "Wedge Play", "Approach", "Driving", "Other"]
    sections_dict: dict[str, list] = {}

    for cfg in club_configs:
        sec = cfg.get("section", "Other")
        if sec not in sections_dict:
            sections_dict[sec] = []
        stats = compute_club_stats(
            df,
            club_name=cfg["club"],
            target_type=cfg["target_type"],
            distance_yd=cfg["distance_yd"],
            level=cfg["level"],
        )
        sections_dict[sec].append({**cfg, "stats": stats})

    sections = []
    for sec in section_order:
        if sec in sections_dict:
            sections.append({"section_name": sec, "rows": sections_dict[sec]})
    for sec in sections_dict:
        if sec not in section_order:
            sections.append({"section_name": sec, "rows": sections_dict[sec]})

    all_stats = [row["stats"] for sec in sections for row in sec["rows"]]
    overall = compute_overall(all_stats)

    # ── Report sheet ────────────────────────────────────────────────────
    ws_report = wb.create_sheet("Report")
    ws_report.sheet_view.showGridLines = False
    build_report_sheet(ws_report, session_info, sections, "Raw Data", overall)

    # Move Report to front
    wb.move_sheet("Report", offset=-wb.index(wb["Report"]))

    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    return out.read()
