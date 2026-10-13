"""Reusable campus asset generation pipeline.

The package deliberately keeps discovery, normalization, validation and asset
writing separate.  Network harvesters can be added later without changing the
runtime Flask application.
"""

from .profile import load_profile, validate_profile
from .normalize import normalize_candidates
from .quality import assess_candidate_quality
from .validate import validate_pois
from .boundary import point_in_polygon, validate_campus_boundary

__all__ = [
    "load_profile", "validate_profile", "normalize_candidates", "validate_pois",
    "assess_candidate_quality",
    "point_in_polygon", "validate_campus_boundary",
]
