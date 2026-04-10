# Tour Targets lookup table: {carry_yardage: (proximity_ft, range_yd, target_rate)}
TOUR_TARGETS = {
    20: (6.4, 2.0, 0.95),
    30: (7.2, 2.0, 0.95),
    40: (10.4, 3.0, 0.93),
    50: (13.2, 3.0, 0.92),
    60: (13.2, 4.0, 0.92),
    70: (13.2, 4.0, 0.92),
    80: (14.2, 4.0, 0.90),
    90: (14.2, 4.0, 0.88),
    100: (16.5, 5.0, 0.86),
    110: (16.5, 5.0, 0.86),
    120: (16.5, 5.0, 0.84),
    130: (19.0, 5.0, 0.80),
    140: (19.0, 5.0, 0.78),
    150: (23.0, 6.0, 0.75),
    160: (23.0, 6.0, 0.70),
    170: (23.0, 6.0, 0.68),
    180: (28.6, 6.0, 0.62),
    190: (28.6, 6.0, 0.58),
    200: (28.6, 7.0, 0.55),
    210: (34.4, 7.0, 0.50),
    220: (34.4, 7.0, 0.46),
    230: (43.2, 8.0, 0.42),
    240: (43.2, 8.0, 0.38),
    250: (48.0, 8.0, 0.35),
    260: (90.0, None, None),
    270: (90.0, None, None),
    280: (90.0, None, None),
    290: (90.0, None, None),
    300: (90.0, None, None),
}

# Levels multipliers: {level: (rate_multiplier, proximity_multiplier)}
LEVELS_MULTIPLIERS = {
    1: (0.10, 3.00),
    2: (0.20, 2.50),
    3: (0.30, 2.25),
    4: (0.40, 2.00),
    5: (0.50, 1.75),
    6: (0.60, 1.50),
    7: (0.70, 1.25),
    8: (0.80, 1.00),
    9: (0.90, 1.00),
    10: (1.00, 1.00),
    11: (1.25, 0.75),
    12: (1.50, 0.50),
}

def get_tour_target(distance_yd: int) -> tuple:
    """Return (proximity_ft, range_yd, target_rate) for the 10-yd band containing distance_yd.
    e.g. 63 -> 60 band (60-69), 75 -> 70 band (70-79).
    """
    banded = (int(distance_yd) // 10) * 10
    banded = max(20, min(300, banded))
    return TOUR_TARGETS.get(banded, (None, None, None))

def get_level_multipliers(level: int) -> tuple:
    """Return (rate_multiplier, proximity_multiplier) for a player level."""
    return LEVELS_MULTIPLIERS.get(level, (None, None))
