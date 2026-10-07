import unittest
import numpy as np
import pandas as pd
from src.quality import rule_flags, features, injected_copy, model_fit, scores


class QualityTests(unittest.TestCase):
    def sample(self):
        rng=np.random.default_rng(123)
        area=rng.uniform(35,140,300)
        return pd.DataFrame(dict(OB_PL=area,J_PL=area*.6,KOL_K=rng.integers(1,5,300),
                                 household_size=rng.integers(1,7,300))).astype(float)

    def test_area_and_missing_checks(self):
        d=self.sample().iloc[:3].copy()
        d.loc[0,'J_PL']=d.loc[0,'OB_PL']+1
        d.loc[1,'KOL_K']=np.nan
        self.assertEqual(rule_flags(d).tolist(),[True,True,False])

    def test_injections_leave_source_untouched(self):
        d=self.sample(); before=d.copy(deep=True)
        changed,labels=injected_copy(d,5)
        pd.testing.assert_frame_equal(d,before)
        self.assertEqual(int((labels!='unchanged').sum()),18)
        pd.testing.assert_frame_equal(d.loc[labels=='unchanged'],changed.loc[labels=='unchanged'])
        self.assertTrue(rule_flags(changed.loc[labels=='living_exceeds_total']).all())
        self.assertFalse(rule_flags(changed.loc[labels=='area_unit_x10']).any())

    def test_model_reproducible_and_finite(self):
        d=self.sample()
        model,med,spread=model_fit(d.iloc[:200],17)
        a,_=scores(d.iloc[200:],model,med,spread)
        model2,med2,spread2=model_fit(d.iloc[:200],17)
        b,_=scores(d.iloc[200:],model2,med2,spread2)
        np.testing.assert_array_equal(a,b)
        self.assertTrue(np.isfinite(a).all())

    def test_features_do_not_use_identifiers(self):
        d=self.sample(); d['NOMER']='secret-id'
        x=features(d)
        self.assertNotIn('NOMER',x)
        np.testing.assert_allclose(x.area_per_person,d.OB_PL/d.household_size)


if __name__=='__main__': unittest.main()
