"""用户手工补点的作用域：只在任务私有层，且必须能被预览、审核和回滚。"""

import json
import tempfile
import unittest
from pathlib import Path

from tools.campus_generator.review import apply_review, load_review
from tools.campus_generator.user_pois import (
    MAX_USER_POIS,
    USER_POI_FILE,
    add_user_added,
    load_user_added,
    remove_user_added,
)


PROFILE = {
    "school": {"id": "demo", "name": "示例大学", "enName": "DEMO"},
    "campus": {
        "name": "示例校区", "slug": "demo_main", "center": [121.0, 31.0],
        "trustRadiusM": 1200,
    },
}


def _prepare(root: Path, candidates=None) -> None:
    (root / "campus_profile.json").write_text(
        json.dumps(PROFILE, ensure_ascii=False), encoding="utf-8")
    (root / "normalized_pois.json").write_text(
        json.dumps(candidates or [], ensure_ascii=False), encoding="utf-8")
    (root / "approved_pois.json").write_text("[]", encoding="utf-8")
    (root / "build_report.json").write_text(
        json.dumps({"theme": "walk"}, ensure_ascii=False), encoding="utf-8")


def _payload(**overrides) -> dict:
    payload = {
        "name": "思源湖西岸草坪", "lng": 121.0005, "lat": 31.0005,
        "category": "scene", "subCategory": "草坪",
        "scenes": ["walk", "date"], "text": "树下有长椅",
    }
    payload.update(overrides)
    return payload


class UserAddedPoiTests(unittest.TestCase):
    def test_add_keeps_point_in_private_layer_and_auto_selects_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _prepare(root)

            result = add_user_added(root, _payload(), PROFILE, theme="walk")

            stored = json.loads((root / USER_POI_FILE).read_text(encoding="utf-8"))
            self.assertEqual(len(stored), 1)
            self.assertEqual(stored[0]["id"], result["item"]["id"])
            self.assertEqual(stored[0]["source"], "manual")
            self.assertTrue(stored[0]["evidence"]["userAdded"])
            self.assertLess(stored[0]["confidence"], 0.6)
            # 正式数据与采集候选都不能被用户补点污染。
            self.assertEqual(
                json.loads((root / "normalized_pois.json").read_text()), [])
            self.assertEqual(
                json.loads((root / "approved_pois.json").read_text()), [])
            selection = json.loads(
                (root / "preview_selection.json").read_text(encoding="utf-8"))
            self.assertIn(result["item"]["id"], selection["selectedIds"])

    def test_near_duplicate_and_outside_campus_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _prepare(root)
            add_user_added(root, _payload(), PROFILE)

            with self.assertRaisesRegex(ValueError, "已在本次任务|距离已有地点"):
                add_user_added(root, _payload(), PROFILE)
            with self.assertRaisesRegex(ValueError, "不在校区范围内"):
                add_user_added(root, _payload(name="校外点", lng=121.5, lat=31.5), PROFILE)

    def test_limits_and_required_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _prepare(root)
            with self.assertRaisesRegex(ValueError, "地点名称"):
                add_user_added(root, _payload(name="  "), PROFILE)
            with self.assertRaisesRegex(ValueError, "经度"):
                add_user_added(root, _payload(lng="abc"), PROFILE)
            with self.assertRaisesRegex(ValueError, "未知场景"):
                add_user_added(root, _payload(scenes=["party"]), PROFILE)

            for index in range(MAX_USER_POIS):
                add_user_added(
                    root,
                    _payload(name=f"补点{index}", lng=121.0 + index * 0.0004,
                             lat=31.0 + index * 0.0004),
                    PROFILE,
                )
            with self.assertRaisesRegex(ValueError, "最多手动添加"):
                add_user_added(root, _payload(name="超限", lng=121.01, lat=31.01), PROFILE)

    def test_review_bundle_surfaces_user_points_and_admin_can_promote_them(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _prepare(root)
            added = add_user_added(root, _payload(), PROFILE, theme="walk")

            bundle = load_review(root)
            self.assertEqual([item["id"] for item in bundle["userAdded"]],
                             [added["item"]["id"]])
            self.assertIn(added["item"]["id"],
                          [item["id"] for item in bundle["candidates"]])
            self.assertEqual(bundle["userAdded"][0]["reviewStatus"], "pending")

            applied = apply_review(root, [
                {"id": added["item"]["id"], "action": "approve"},
            ])

            self.assertEqual(
                [item["id"] for item in applied["approved"]], [added["item"]["id"]])
            self.assertEqual(load_user_added(root), [])
            self.assertEqual(applied["userAdded"], [])

    def test_remove_only_touches_user_layer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _prepare(root)
            added = add_user_added(root, _payload(), PROFILE)
            keep = add_user_added(
                root, _payload(name="另一个点", lng=121.002, lat=31.002), PROFILE)

            result = remove_user_added(root, added["item"]["id"])

            self.assertEqual([item["id"] for item in load_user_added(root)],
                             [keep["item"]["id"]])
            self.assertNotIn(added["item"]["id"], result["selection"]["selectedIds"])
            with self.assertRaises(LookupError):
                remove_user_added(root, "not-a-user-point")


if __name__ == "__main__":
    unittest.main()
