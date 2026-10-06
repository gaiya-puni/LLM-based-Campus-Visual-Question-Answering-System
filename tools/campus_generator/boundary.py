"""Trusted campus-boundary validation and point membership helpers.

Coordinates follow the project's existing ``[lng, lat]`` convention.  The
MVP deliberately accepts one GeoJSON Polygon outer ring only: holes and
MultiPolygon data need an explicit review policy before they can safely enter
the asset pipeline.
"""

from __future__ import annotations

import hashlib
import json
import math


EARTH_RADIUS_M = 6371008.8
_EPSILON = 1e-12
SUPPORTED_COORDINATE_SYSTEMS = {"GCJ-02", "WGS84", "BD-09"}


def _finite_pair(value, label: str) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"{label} must be [lng, lat]")
    try:
        lng, lat = float(value[0]), float(value[1])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must contain numbers") from exc
    if not math.isfinite(lng) or not math.isfinite(lat):
        raise ValueError(f"{label} must contain finite numbers")
    if not -180 <= lng <= 180 or not -90 <= lat <= 90:
        raise ValueError(f"{label} is outside coordinate bounds")
    return lng, lat


def _cross(a: tuple[float, float], b: tuple[float, float],
           c: tuple[float, float]) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _point_on_segment(point: tuple[float, float], start: tuple[float, float],
                      end: tuple[float, float]) -> bool:
    scale = max(1.0, abs(end[0] - start[0]), abs(end[1] - start[1]))
    if abs(_cross(start, end, point)) > _EPSILON * scale:
        return False
    return (min(start[0], end[0]) - _EPSILON <= point[0] <=
            max(start[0], end[0]) + _EPSILON and
            min(start[1], end[1]) - _EPSILON <= point[1] <=
            max(start[1], end[1]) + _EPSILON)


def _segments_intersect(a: tuple[float, float], b: tuple[float, float],
                        c: tuple[float, float], d: tuple[float, float]) -> bool:
    ab_c, ab_d = _cross(a, b, c), _cross(a, b, d)
    cd_a, cd_b = _cross(c, d, a), _cross(c, d, b)
    if ((ab_c > _EPSILON and ab_d < -_EPSILON) or
            (ab_c < -_EPSILON and ab_d > _EPSILON)) and (
            (cd_a > _EPSILON and cd_b < -_EPSILON) or
            (cd_a < -_EPSILON and cd_b > _EPSILON)):
        return True
    return ((abs(ab_c) <= _EPSILON and _point_on_segment(c, a, b)) or
            (abs(ab_d) <= _EPSILON and _point_on_segment(d, a, b)) or
            (abs(cd_a) <= _EPSILON and _point_on_segment(a, c, d)) or
            (abs(cd_b) <= _EPSILON and _point_on_segment(b, c, d)))


def _ring(boundary: dict) -> list[tuple[float, float]]:
    coordinates = boundary["coordinates"]
    return [_finite_pair(value, f"campus.boundary.coordinates[0][{index}]")
            for index, value in enumerate(coordinates[0])]


def validate_polygon(boundary) -> list[str]:
    """Return schema/topology problems for the supported Polygon subset."""
    problems: list[str] = []
    if not isinstance(boundary, dict):
        return ["campus.boundary must be an object"]
    if boundary.get("type") != "Polygon":
        problems.append("campus.boundary.type must be Polygon")
    coordinates = boundary.get("coordinates")
    if not isinstance(coordinates, list) or len(coordinates) != 1:
        problems.append("campus.boundary.coordinates must contain exactly one outer ring")
        return problems
    values = coordinates[0]
    if not isinstance(values, list) or len(values) < 4:
        problems.append("campus.boundary outer ring must contain at least 4 positions")
        return problems
    if len(values) > 2000:
        problems.append("campus.boundary outer ring exceeds 2000 positions")
        return problems
    ring: list[tuple[float, float]] = []
    for index, value in enumerate(values):
        try:
            ring.append(_finite_pair(value, f"campus.boundary.coordinates[0][{index}]"))
        except ValueError as exc:
            problems.append(str(exc))
    if problems:
        return problems
    if ring[0] != ring[-1]:
        problems.append("campus.boundary outer ring must be closed")
        return problems
    if len(set(ring[:-1])) < 3:
        problems.append("campus.boundary outer ring must contain at least 3 distinct vertices")
        return problems
    twice_area = sum(
        ring[index][0] * ring[index + 1][1] - ring[index + 1][0] * ring[index][1]
        for index in range(len(ring) - 1)
    )
    if abs(twice_area) <= _EPSILON:
        problems.append("campus.boundary outer ring must have non-zero area")
        return problems
    segment_count = len(ring) - 1
    for first in range(segment_count):
        for second in range(first + 1, segment_count):
            if second == first + 1 or (first == 0 and second == segment_count - 1):
                continue
            if _segments_intersect(ring[first], ring[first + 1],
                                   ring[second], ring[second + 1]):
                problems.append("campus.boundary outer ring must not self-intersect")
                return problems
    return problems


def _point_relation_in_ring(point, ring: list[tuple[float, float]]) -> str:
    target = _finite_pair(point, "point")
    for index in range(len(ring) - 1):
        if _point_on_segment(target, ring[index], ring[index + 1]):
            return "boundary"
    inside = False
    x, y = target
    for index in range(len(ring) - 1):
        x1, y1 = ring[index]
        x2, y2 = ring[index + 1]
        if (y1 > y) != (y2 > y):
            crossing_x = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < crossing_x:
                inside = not inside
    return "inside" if inside else "outside"


def point_relation(point, boundary: dict) -> str:
    """Return ``inside``, ``boundary`` or ``outside`` for a valid polygon."""
    problems = validate_polygon(boundary)
    if problems:
        raise ValueError("invalid campus boundary: " + "; ".join(problems))
    return _point_relation_in_ring(point, _ring(boundary))


def point_in_polygon(point, boundary: dict) -> bool:
    """Treat points on the outer ring as accepted campus points."""
    return point_relation(point, boundary) != "outside"


def boundary_hash(boundary: dict) -> str:
    payload = json.dumps(boundary, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_campus_boundary(campus: dict) -> list[str]:
    """Validate optional boundary data plus its required trust metadata."""
    boundary = campus.get("boundary")
    coordinate_system = campus.get("coordinateSystem")
    problems: list[str] = []
    if (coordinate_system is not None and
            (not isinstance(coordinate_system, str) or
             coordinate_system not in SUPPORTED_COORDINATE_SYSTEMS)):
        problems.append(
            "campus.coordinateSystem must be one of GCJ-02, WGS84 or BD-09"
        )
    metadata_present = ("boundarySource" in campus or "boundaryConfidence" in campus)
    if boundary is None:
        if metadata_present:
            problems.append("campus.boundary is required when boundary metadata is present")
        return problems
    polygon_problems = validate_polygon(boundary)
    problems.extend(polygon_problems)
    source = campus.get("boundarySource")
    if not isinstance(source, str) or not source.strip():
        problems.append("campus.boundarySource is required when campus.boundary is present")
    confidence = campus.get("boundaryConfidence")
    try:
        if isinstance(confidence, bool):
            raise ValueError
        confidence_number = float(confidence)
        if not math.isfinite(confidence_number) or not 0 <= confidence_number <= 1:
            raise ValueError
    except (TypeError, ValueError):
        problems.append("campus.boundaryConfidence must be between 0 and 1")
    if coordinate_system != "GCJ-02":
        problems.append(
            "campus.coordinateSystem must be GCJ-02 when campus.boundary is present"
        )
    if not polygon_problems:
        try:
            if _point_relation_in_ring(campus.get("center"), _ring(boundary)) == "outside":
                problems.append("campus.center must be inside campus.boundary")
        except ValueError:
            # The profile validator reports malformed centers separately.
            pass
    return problems


def distance_m(first, second) -> float:
    lng1, lat1 = _finite_pair(first, "first point")
    lng2, lat2 = _finite_pair(second, "second point")
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lng2 - lng1)
    value = (math.sin(d_phi / 2) ** 2 +
             math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2)
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(min(1.0, value)))


class CampusMembershipEvaluator:
    """Validate a campus once, then evaluate many candidate points cheaply."""

    def __init__(self, campus: dict):
        self.center = _finite_pair(campus.get("center"), "campus.center")
        try:
            self.radius = float(campus["trustRadiusM"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("campus.trustRadiusM must be a positive number") from exc
        if not math.isfinite(self.radius) or self.radius <= 0:
            raise ValueError("campus.trustRadiusM must be a positive number")
        problems = validate_campus_boundary(campus)
        if problems:
            raise ValueError("invalid campus spatial policy: " + "; ".join(problems))
        self.boundary = campus.get("boundary")
        self.ring = _ring(self.boundary) if self.boundary is not None else None
        self.boundary_source = campus.get("boundarySource")
        self.boundary_confidence = campus.get("boundaryConfidence")
        self.coordinate_system = campus.get("coordinateSystem")
        # Existing runtime campus records predate explicit CRS metadata, but
        # all of their map coordinates are AMap/GCJ-02. Missing candidate CRS
        # remains compatible; an explicitly different CRS never is.
        self.effective_coordinate_system = self.coordinate_system or "GCJ-02"
        self.boundary_digest = boundary_hash(self.boundary) if self.boundary is not None else None

    def covering_radius_m(self) -> float:
        if self.ring is None:
            return self.radius
        return max(self.radius, *(distance_m(self.center, vertex) for vertex in self.ring[:-1]))

    def evaluate(self, point) -> tuple[bool, float, dict]:
        distance = distance_m(self.center, point)
        if self.ring is not None:
            relation = _point_relation_in_ring(point, self.ring)
            accepted = relation != "outside"
            return accepted, distance, {
                "method": "polygon",
                "relation": relation,
                "boundaryHash": self.boundary_digest,
                "boundarySource": self.boundary_source,
                "boundaryConfidence": float(self.boundary_confidence),
                "coordinateSystem": self.effective_coordinate_system,
            }
        accepted = distance <= self.radius
        return accepted, distance, {
            "method": "radius_fallback",
            "relation": "inside" if accepted else "outside",
            "radiusMeters": self.radius,
            "coordinateSystem": self.effective_coordinate_system,
        }


def covering_radius_m(campus: dict) -> float:
    """Radius needed for a provider's circular query to cover the polygon."""
    return CampusMembershipEvaluator(campus).covering_radius_m()


def campus_membership(campus: dict, point) -> tuple[bool, float, dict]:
    """Evaluate polygon-first membership and return an auditable decision."""
    return CampusMembershipEvaluator(campus).evaluate(point)


def membership_evidence(decision: dict, distance: float) -> dict:
    """Flatten one membership decision into stable review evidence fields."""
    evidence = {
        "distanceMeters": round(float(distance)),
        "campusMembershipMethod": decision["method"],
        "campusMembershipRelation": decision["relation"],
    }
    if decision.get("coordinateSystem"):
        evidence["campusCoordinateSystem"] = decision["coordinateSystem"]
    if decision["method"] == "polygon":
        evidence.update({
            "boundaryHash": decision["boundaryHash"],
            "boundarySource": decision["boundarySource"],
            "boundaryConfidence": decision["boundaryConfidence"],
            "boundaryCoordinateSystem": decision.get("coordinateSystem"),
        })
    else:
        evidence["campusMembershipRadiusMeters"] = decision["radiusMeters"]
    return evidence
