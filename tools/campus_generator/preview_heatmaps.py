"""Preview-only heatmap policy; never installed in the web server process."""

from __future__ import annotations

import math
import sys
from functools import partial
from pathlib import Path


ALGORITHM_VERSION = "campus-preview-11-density-and-tighter-kernel"


def fuse_preview_fields(geometry, similarities, plant_priors=None, season=None,
                        scene=None, class_weights=None):
    import scene_heatmaps as core

    return _fuse_preview_fields(core.fuse_fields, geometry, similarities, plant_priors,
                                season, scene, class_weights)


def _density_fields(geometry):
    """Preview-only point density: every approved POI contributes equally.

    Class fields are already min-max normalized by ``prepare_geometry``; weighting
    each class by its point count turns the sum into a point-density field.  This
    is the honest answer for mixed requests ("建筑 + 花草树木 + 地标"), where no
    single scene corpus applies.
    """
    import numpy as np
    import scene_heatmaps as core

    mask = geometry["mask"]
    fields = geometry.get("fields") or {}
    groups = geometry.get("groups") or {}
    total = sum(len(indexes) for indexes in groups.values()) or 1
    fused = np.zeros(len(mask))
    contributions = {}
    for name, indexes in groups.items():
        part = fields[name] * (len(indexes) / total)
        contributions[name] = part
        fused = fused + part
    return core.normalize(fused, mask), contributions, {name: 1.0 for name in fields}


def _fuse_preview_fields(original_fuse, geometry, similarities, plant_priors=None,
                         season=None, scene=None, class_weights=None):
    if scene == "density":
        return _density_fields(geometry)

    config = geometry["config"]
    minimum = config.get("minimumCategoryActivation", 0.2)
    if (isinstance(minimum, bool) or not isinstance(minimum, (int, float))
            or not 0 < minimum <= 1):
        raise ValueError("minimumCategoryActivation must be in (0, 1]")
    if config["negativeContribution"] == "clip":
        # Preview activation = log(class_count / rank) + minimum. Even the
        # last class stays positive. Semantic similarity and scene priors still
        # determine contribution; missing/zero similarity stays zero.
        config = dict(config, positiveRatio=math.exp(minimum))
        geometry = dict(geometry, config=config)
    return original_fuse(geometry, similarities, plant_priors, season, scene,
                         class_weights)


def main():
    # Use the task's isolated sources and configuration, not the formal cache.
    if "--base-dir" not in sys.argv:
        raise ValueError("preview builder requires --base-dir")
    workspace = Path(sys.argv[sys.argv.index("--base-dir") + 1]).resolve()
    sys.path.insert(0, str(workspace))
    import scene_heatmaps as core
    import build_scene_heatmaps as builder

    original_sources = core.source_paths
    core.ALGORITHM_VERSION = ALGORITHM_VERSION
    # build_map resolves fuse_fields in the core module at runtime. Install the
    # policy only in this short-lived subprocess; the Flask server is unchanged.
    core.fuse_fields = partial(_fuse_preview_fields, core.fuse_fields)
    core.source_paths = lambda base: [*original_sources(base), Path(__file__)]
    builder.ALGORITHM_VERSION = ALGORITHM_VERSION
    builder.main()


if __name__ == "__main__":
    main()
