import unittest
import pandas as pd
from src.eda import clean_scale, make_target, household_roster


class SurveySemanticsTests(unittest.TestCase):
    def test_special_answer_is_not_score(self):
        result = clean_scale(pd.Series(['7', '9', '89', None]))
        self.assertEqual(result.mean(), 8)
        self.assertEqual(result.isna().sum(), 2)
        with self.assertRaises(ValueError):
            clean_scale(pd.Series(['0', '11']))

    def test_missing_label_not_negative(self):
        target = make_target(pd.Series(['1', '2', '3', '6', None]))
        self.assertEqual(target.dropna().tolist(), [1, 1, 0, 0])
        self.assertTrue(pd.isna(target.iloc[-1]))

    def test_duplicate_person_rejected(self):
        roster=pd.DataFrame({'NOMER':['001','001'],'NOMP':['01','01'],
            'KOL_CHL':['2','2'],'TE':['01','01'],'K':['1','1']})
        with self.assertRaisesRegex(ValueError, 'Duplicate person'):
            household_roster(roster)

    def test_size_mismatch_rejected(self):
        roster=pd.DataFrame({'NOMER':['001'],'NOMP':['01'],
            'KOL_CHL':['2'],'TE':['01'],'K':['1']})
        with self.assertRaisesRegex(ValueError,'Declared'):
            household_roster(roster)


if __name__ == '__main__':
    unittest.main()

