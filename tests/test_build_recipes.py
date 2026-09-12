import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_recipes.py"
SPEC = importlib.util.spec_from_file_location("build_recipes", SCRIPT)
BUILD_RECIPES = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILD_RECIPES)
IS_PRODUCTION_GEAR = getattr(BUILD_RECIPES, "is_production_gear", lambda item: False)
ADD_MISSING_GEAR = getattr(BUILD_RECIPES, "add_missing_production_gear", lambda *args: 0)


class ProductionGearTests(unittest.TestCase):
    def item(self, name, *, category="armors", tier="6", market="true", recipe=True):
        item = {
            "@uniquename": name,
            "@shopcategory": category,
            "@tier": tier,
            "@showinmarketplace": market,
        }
        if recipe:
            item["craftingrequirements"] = {
                "craftresource": {"@uniquename": "T6_LEATHER", "@count": "16"}
            }
        return item

    def test_accepts_new_marketplace_combat_gear(self):
        item = self.item("T6_ARMOR_LEATHER_DRAGON")
        self.assertTrue(IS_PRODUCTION_GEAR(item))

    def test_rejects_non_product_gear(self):
        cases = [
            self.item("T8_ARMOR_CLOTH_PROTOTYPE"),
            self.item("T6_ARMOR_GATHERER_HIDE", category="gathering"),
            self.item("T6_ARMOR_LEATHER_HIDDEN", market="false"),
            self.item("T3_ARMOR_LEATHER_DRAGON", tier="3"),
            self.item("T6_ARMOR_LEATHER_NO_RECIPE", recipe=False),
        ]
        for item in cases:
            with self.subTest(item=item["@uniquename"]):
                self.assertFalse(IS_PRODUCTION_GEAR(item))

    def test_adds_base_and_enchanted_recipe_keys(self):
        dragon = self.item("T6_ARMOR_LEATHER_DRAGON")
        dragon["enchantments"] = {
            "enchantment": {
                "@enchantmentlevel": "1",
                "craftingrequirements": {
                    "craftresource": {
                        "@uniquename": "T6_LEATHER_LEVEL1",
                        "@enchantmentlevel": "1",
                        "@count": "16",
                    }
                },
            }
        }
        recipes, batches = {}, {}
        added = ADD_MISSING_GEAR(recipes, batches, {"items": {"equipmentitem": [dragon]}})
        self.assertEqual(added, 2)
        self.assertEqual(recipes["T6_ARMOR_LEATHER_DRAGON"], [["T6_LEATHER", 16]])
        self.assertEqual(recipes["T6_ARMOR_LEATHER_DRAGON@1"], [["T6_LEATHER_LEVEL1@1", 16]])


if __name__ == "__main__":
    unittest.main()
