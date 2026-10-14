"""Distance refactoring must preserve the legacy formula and entry points."""

import importlib
import math
import subprocess
import sys
from pathlib import Path

import pytest

from geo_utils import distance_meters


@pytest.mark.parametrize('points', [
    (121.406079, 31.227073, 121.406079, 31.227073),
    (121.406079, 31.227073, 121.407079, 31.227073),
    (121.406079, 31.227073, 121.406079, 31.228073),
    (121.406079, 31.227073, 121.453725, 31.03148),
    (121.453725, 31.03148, 121.406079, 31.227073),
    (0.0, 0.0, 0.001, 0.001),
])
def test_distance_matches_legacy_formula_exactly(points):
    lng1, lat1, lng2, lat2 = points
    mean_lat = math.radians((lat1 + lat2) / 2)
    dx = (lng1 - lng2) * 111320 * math.cos(mean_lat)
    dy = (lat1 - lat2) * 110540
    expected = (dx * dx + dy * dy) ** 0.5
    assert distance_meters(*points) == expected


@pytest.mark.parametrize('module_name', ['itinerary', 'retrieval_engine', 'userdata_store'])
def test_existing_private_entry_points_remain_available(module_name):
    module = importlib.import_module(module_name)
    points = (121.406079, 31.227073, 121.407079, 31.228073)
    assert module._distance_meters(*points) == distance_meters(*points)


def test_planning_and_userdata_still_import_without_flask_or_server():
    code = (
        'import sys; import geo_utils, itinerary, userdata_store; '
        'assert "server" not in sys.modules; '
        'assert "flask" not in sys.modules; '
        'assert "sentence_transformers" not in sys.modules'
    )
    subprocess.run(
        [sys.executable, '-c', code],
        cwd=Path(__file__).resolve().parent,
        check=True,
        timeout=15,
    )
