"""Reusable campus asset generation pipeline.

The package deliberately keeps discovery, normalization, validation and asset
writing separate.  Network harvesters can be added later without changing the
runtime Flask application.
"""

from .profile import load_profile, validate_profile
from .normalize import normalize_candidates
from .validate import validate_pois

__all__ = ["load_profile", "validate_profile", "normalize_candidates", "validate_pois"]
