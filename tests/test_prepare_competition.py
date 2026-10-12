import json
from pathlib import Path
import tempfile
import unittest

from src.prepare_competition import (COLUMNS, DOC_PATHS, GROUP, HOUSING_PATH,
    PER_PERSON, ROSTER_PATH, PreparationError, prepare, prepare_archive)


H = "NOMER;OB_PL;J_PL\n001;60;30\n1;90;45\n003;100;50\n"
R = "NOMER;NOMP;KOL_CHL;GOD\n001;01;2;2024\n001;02;2;2024\n1;01;1;2024\n003;01;1;2024\n999;01;1;2024\n"


class CompetitionPreparationTests(unittest.TestCase):
    def test_exact_join_preserves_leading_zero_keys_and_household_grain(self):
        rows, audit = prepare(H, R)
        self.assertEqual([row[PER_PERSON] for row in rows], [30, 90, 100])
        self.assertEqual(audit["matched_housing_households"], 3)
        self.assertEqual(audit["unused_roster_households"], 1)
        self.assertEqual(audit["roster_rows"], 5)
        self.assertEqual(audit["eligible_households"], 3)
        self.assertTrue(all(set(row) == set(COLUMNS) for row in rows))
        self.assertFalse(any("NOMER" in row or "NOMP" in row for row in rows))

    def test_duplicate_or_blank_join_keys_abort_instead_of_multiplying_rows(self):
        for housing, roster in [(H + "001;60;30\n", R), (H, R + "001;01;2;2024\n"),
                                (H.replace("001;60", ";60"), R),
                                (H, R.replace("001;01", "001;"))]:
            with self.subTest(housing=housing, roster=roster), self.assertRaises(PreparationError):
                prepare(housing, roster)

    def test_bad_year_aborts_and_unconfirmed_size_is_not_assumed(self):
        with self.assertRaises(PreparationError):
            prepare(H, R.replace("2024", "2023", 1))
        for declared in ("", "0", "1", "2.5", "NaN", "3"):
            rows, audit = prepare(H, R.replace("001;01;2;2024", f"001;01;{declared};2024"))
            self.assertEqual(len(rows), 2)
            self.assertEqual(audit["exclusions_mutually_exclusive"]["roster_size_unconfirmed"], 1)

    def test_exclusions_are_explicit_disjoint_and_reconcile(self):
        housing = H + "004;40;20\n005;40;50\n006;0;0\n007;10.5;5\n008;nan;5\n"
        roster = R + "005;01;1;2024\n006;01;1;2024\n007;01;1;2024\n008;01;1;2024\n"
        rows, audit = prepare(housing, roster)
        self.assertEqual(len(rows), 3)
        self.assertEqual(audit["exclusions_mutually_exclusive"], {
            "missing_roster": 1, "roster_size_unconfirmed": 0, "invalid_area": 3,
            "living_area_exceeds_total": 1, "nonfinite_derived": 0})
        self.assertEqual(audit["housing_rows"], len(rows) + audit["excluded_households"])

    def test_group_boundaries_use_members_not_person_weighted_rows(self):
        housing = "NOMER;OB_PL;J_PL\n"
        roster = "NOMER;NOMP;KOL_CHL;GOD\n"
        for size in range(1, 7):
            housing += f"H{size};60;30\n"
            for person in range(1, size + 1):
                roster += f"H{size};{person};{size};2024\n"
        rows, audit = prepare(housing, roster)
        self.assertEqual([row[GROUP] for row in rows],
                         ["1–2 человека"] * 2 + ["3–4 человека"] * 2 + ["5+ человек"] * 2)
        self.assertEqual(audit["group_counts"], {"1–2 человека": 2, "3–4 человека": 2, "5+ человек": 2})

    def test_saved_audit_hashes_actual_bytes_and_omits_identifiers_and_host_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, out = root / "archive", root / "private"
            for relative, content in [(HOUSING_PATH, H), (ROSTER_PATH, R),
                                       *[(path, "fixture documentation") for path in DOC_PATHS]]:
                path = archive / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            result = prepare_archive(archive, out)
            audit_text = (out / "preparation_audit.json").read_text(encoding="utf-8")
            self.assertEqual(json.loads(audit_text), result)
            self.assertNotIn(str(root), audit_text)
            self.assertNotIn('"001"', audit_text)
            self.assertEqual(result["reference_aggregates"][0]["median_area_per_person_m2"], 90)
            self.assertTrue(result["synthetic_data"])
            self.assertFalse(result["population_inference"])
            old_hash = result["prepared_file"]["sha256"]
            (archive / HOUSING_PATH).write_text(H.replace("1;90;45", "1;120;45"), encoding="utf-8")
            changed = prepare_archive(archive, out)
            self.assertNotEqual(old_hash, changed["prepared_file"]["sha256"])
            self.assertEqual(changed["reference_aggregates"][0]["median_area_per_person_m2"], 100)


if __name__ == "__main__":
    unittest.main()
