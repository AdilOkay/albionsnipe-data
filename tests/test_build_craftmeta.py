import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_craftmeta.py"
SPEC = importlib.util.spec_from_file_location("build_craftmeta", SCRIPT)
BUILD_CRAFTMETA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILD_CRAFTMETA)
RECIPE_KEYS = getattr(BUILD_CRAFTMETA, "recipe_keys", lambda payload: [])


class RecipeKeyTests(unittest.TestCase):
    def test_new_patch_recipe_does_not_wait_for_baseline(self):
        payload = {
            "items": {
                "T6_ARMOR_LEATHER_SET1": [["T6_LEATHER", 16]],
                "T6_ARMOR_LEATHER_DRAGON": [["T6_LEATHER", 16]],
            }
        }
        self.assertEqual(
            RECIPE_KEYS(payload),
            ["T6_ARMOR_LEATHER_SET1", "T6_ARMOR_LEATHER_DRAGON"],
        )


if __name__ == "__main__":
    unittest.main()
