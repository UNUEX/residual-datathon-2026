"""Run: python -m unittest discover -s examples/research_brief -v"""
import unittest
from reproduce import DataError, calculate


class BriefTests(unittest.TestCase):
    def setUp(self):
        self.housing = [dict(NOMER="001", OB_PL="10", J_PL="6"),
                        dict(NOMER="002", OB_PL="100", J_PL="110")]
        self.roster = [dict(NOMER="001", NOMP="01", KOL_CHL="1"),
                       dict(NOMER="002", NOMP="01", KOL_CHL="2"),
                       dict(NOMER="002", NOMP="02", KOL_CHL="2")]

    def test_median_of_ratios_and_separate_sensitivity(self):
        result = calculate(self.housing, self.roster)
        self.assertEqual(result["primary"][0]["median_area_per_person_m2"], 30)
        self.assertNotEqual(result["primary"][0]["median_area_per_person_m2"], 55 / 1.5)
        self.assertEqual(result["primary"][0]["households"], 2)
        self.assertEqual(result["sensitivity"][0]["median_area_per_person_m2"], 10)
        self.assertEqual(result["coverage"]["area_order_flagged_households"], 1)
        self.assertFalse(result["ai_planner_connected"])

    def test_duplicate_person_rejected(self):
        with self.assertRaisesRegex(DataError, "Duplicate person key"):
            calculate(self.housing, self.roster + [self.roster[0]])

    def test_nonfinite_area_and_inconsistent_size_rejected(self):
        with self.assertRaisesRegex(DataError, "finite"):
            calculate([dict(NOMER="001", OB_PL="nan", J_PL="6")], self.roster)
        wrong_roster = [dict(row) for row in self.roster]
        wrong_roster[0]["KOL_CHL"] = "2"
        with self.assertRaisesRegex(DataError, "differs from roster count"):
            calculate(self.housing, wrong_roster)


if __name__ == "__main__":
    unittest.main()
