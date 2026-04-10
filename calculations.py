import math
from data.tour_targets import get_tour_target, get_level_multipliers


def feet_to_feet_inches_str(feet_val: float) -> str:
    """Convert decimal feet to '14'2"' style string."""
    if feet_val is None or math.isnan(feet_val):
        return ""
    total_inches = feet_val * 12
    ft = int(total_inches // 12)
    inches = round(total_inches % 12)
    if inches == 12:
        ft += 1
        inches = 0
    return f"{ft}'{inches}\""


def compute_proximity_ft(total_yd: float, offline_yd: float, target_yd: float) -> float | None:
    """Euclidean distance to pin in feet."""
    if any(v is None for v in [total_yd, offline_yd, target_yd]):
        return None
    return math.sqrt((target_yd - total_yd) ** 2 + offline_yd ** 2) * 3


def get_target_proximity_ft(distance_yd: int, level: int) -> float | None:
    """Scaled target proximity in feet."""
    prox_ft, _, _ = get_tour_target(distance_yd)
    _, prox_mult = get_level_multipliers(level)
    if prox_ft is None or prox_mult is None:
        return None
    return prox_ft * prox_mult


def get_target_range_yd(distance_yd: int, level: int) -> float | None:
    """Scaled distance control range in yards."""
    _, range_yd, _ = get_tour_target(distance_yd)
    _, prox_mult = get_level_multipliers(level)
    if range_yd is None or prox_mult is None:
        return None
    return range_yd * prox_mult


def get_target_rate(distance_yd: int, level: int) -> float | None:
    """Scaled success rate target (capped at 1.0)."""
    _, _, tour_rate = get_tour_target(distance_yd)
    rate_mult, _ = get_level_multipliers(level)
    if tour_rate is None or rate_mult is None:
        return None
    return min(1.0, tour_rate * rate_mult)


def compute_club_stats(
    df,
    club_name: str,
    target_type: str,
    distance_yd: int | None,
    level: int,
    section: str = "",
) -> dict:
    """
    Compute all stats for a single club row.
    Works with polars or pandas DataFrames.
    """
    import pandas as _pd
    if isinstance(df, _pd.DataFrame):
        rows = df[df["club"] == club_name].to_dict("records")
    else:
        try:
            import polars as pl
            rows = df.filter(pl.col("club") == club_name).to_dicts()
        except Exception:
            rows = df[df["club"] == club_name].to_dict("records")
    attempts = len(rows)

    if attempts == 0 or distance_yd is None:
        return {
            "attempts": 0,
            "successes": None,
            "actual_pct": None,
            "target_pct": None,
            "goal_status": None,
            "target_raw": None,
            "actual_raw": None,
        }

    # Default target_pct (overridden by Distance/Dispersion branches)
    target_pct = get_target_rate(distance_yd, level)

    if target_type == "Proximity":
        target_prox = get_target_proximity_ft(distance_yd, level)
        proximities = [
            compute_proximity_ft(row.get("total_yd"), row.get("offline_yd"), distance_yd)
            for row in rows
        ]
        successes = sum(1 for p in proximities if p is not None and target_prox is not None and p <= target_prox)
        valid_prox = [p for p in proximities if p is not None]
        avg_prox = sum(valid_prox) / len(valid_prox) if valid_prox else None

        target_raw = feet_to_feet_inches_str(target_prox) if target_prox else None
        actual_raw = feet_to_feet_inches_str(avg_prox) if avg_prox else None

    elif target_type == "Distance Control":
        target_range = get_target_range_yd(distance_yd, level)
        totals = [r["total_yd"] for r in rows if r.get("total_yd") is not None]
        if target_range is not None and distance_yd is not None:
            successes = sum(1 for t in totals if abs(t - distance_yd) <= target_range)
        else:
            successes = None
        avg_total = sum(totals) / len(totals) if totals else None
        target_raw = f"+/- {target_range:.0f} yds" if target_range is not None else None
        actual_raw = f"{round(avg_total)} yds" if avg_total is not None else None

    elif target_type == "Distance":
        # Driving: target = inputted distance, actual = avg carry, success = carry >= distance
        rate_mult, _ = get_level_multipliers(level)
        target_pct = min(1.0, 0.65 * rate_mult) if rate_mult else None
        carries = [r["carry_yd"] for r in rows if r.get("carry_yd") is not None]
        successes = sum(1 for c in carries if c >= distance_yd) if carries else None
        avg_carry = sum(carries) / len(carries) if carries else None
        target_raw = f"{distance_yd} yds"
        actual_raw = f"{round(avg_carry)} yds" if avg_carry is not None else None

    elif target_type == "Dispersion":
        # Driving: target = 30/2 * prox_mult, actual = avg abs(offline), success = offline <= target
        _, prox_mult = get_level_multipliers(level)
        target_disp = (30 / 2) * prox_mult if prox_mult else None
        # Use carry offline if available, else total offline
        laterals = []
        for r in rows:
            val = r.get("offline_yd")
            if val is not None:
                laterals.append(abs(val))
        rate_mult_d, _ = get_level_multipliers(level)
        target_pct = min(1.0, 0.65 * rate_mult_d) if rate_mult_d else None
        successes = sum(1 for l in laterals if l <= target_disp) if (laterals and target_disp is not None) else None
        avg_lateral = sum(laterals) / len(laterals) if laterals else None
        target_raw = f"+/- {target_disp:.1f} yds" if target_disp is not None else None
        actual_raw = f"{avg_lateral:.1f} yds" if avg_lateral is not None else None

    else:
        target_range = get_target_range_yd(distance_yd, level)
        totals = [r["total_yd"] for r in rows if r.get("total_yd") is not None]
        successes = None
        avg_total = sum(totals) / len(totals) if totals else None
        target_raw = None
        actual_raw = f"{round(avg_total)} yds" if avg_total is not None else None

    actual_pct = successes / attempts if successes is not None and attempts > 0 else None

    if actual_pct is None or target_pct is None:
        goal_status = None
    elif actual_pct >= target_pct:
        goal_status = "Goal Met"
    elif actual_pct >= target_pct * 0.7:
        goal_status = "Approaching Goal"
    else:
        goal_status = "Goal in Progress"

    return {
        "attempts": attempts,
        "successes": successes,
        "actual_pct": actual_pct,
        "target_pct": target_pct,
        "goal_status": goal_status,
        "target_raw": target_raw,
        "actual_raw": actual_raw,
    }


def compute_overall(report_rows: list[dict]) -> dict:
    """Sum across all report rows for the Overall section."""
    total_attempts = sum(r.get("attempts") or 0 for r in report_rows)
    total_successes = sum(r.get("successes") or 0 for r in report_rows)
    goals_total = sum(1 for r in report_rows if r.get("goal_status") is not None)
    goals_met = sum(1 for r in report_rows if r.get("goal_status") == "Goal Met")
    success_pct = total_successes / total_attempts if total_attempts > 0 else None
    goal_pct = goals_met / goals_total if goals_total > 0 else None
    return {
        "total_attempts": total_attempts,
        "total_successes": total_successes,
        "success_pct": success_pct,
        "goals_total": goals_total,
        "goals_met": goals_met,
        "goal_pct": goal_pct,
    }
