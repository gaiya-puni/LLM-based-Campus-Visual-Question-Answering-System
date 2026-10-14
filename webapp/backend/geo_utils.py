"""Shared lightweight geographic helpers used by backend ranking and planning.

The calculation intentionally keeps the project's existing local approximation:
it is fast enough for campus-scale ranking and preserves the previous results.
"""

import math


def distance_meters(lng1: float, lat1: float, lng2: float, lat2: float) -> float:
    """Return the approximate distance between two longitude/latitude pairs."""
    mean_lat = math.radians((lat1 + lat2) / 2)
    dx = (lng1 - lng2) * 111320 * math.cos(mean_lat)
    dy = (lat1 - lat2) * 110540
    return (dx * dx + dy * dy) ** 0.5
