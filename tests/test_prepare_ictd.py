"""Guard against semantic/representation regressions in the scoped GRD adapter."""
import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('prepare_ictd',Path(__file__).parents[1]/'scripts/prepare_ictd.py')
m=importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(m)
    AVAILABLE=True
except ModuleNotFoundError as exc:
    if exc.name!='openpyxl':raise
    AVAILABLE=False

@unittest.skipUnless(AVAILABLE,'Source extraction requires optional openpyxl')
class ICTDPreparationTests(unittest.TestCase):
    def test_zero_and_missing_are_distinct(self):
        self.assertEqual(m.observation(0,(),set()),(0.0,'fraction_to_percent'))
        self.assertEqual(m.observation(' ',(),set()),(None,'whitespace'))
        self.assertEqual(m.observation(None,(),set()),(None,'source_null'))

    def test_excel_error_authorization_is_cell_specific(self):
        approved={('General','Madagascar',1993,'BC')}
        self.assertEqual(m.observation('#VALUE!',next(iter(approved)),approved),(None,'approved_excel_error'))
        with self.assertRaises(ValueError):m.observation('#VALUE!',('General','Madagascar',1992,'BC'),approved)
        with self.assertRaises(ValueError):m.observation('#DIV/0!',next(iter(approved)),approved)

    def test_do_not_accept_sentinels_text_or_nonfinite(self):
        for v in ['12.0','..','#N/A',float('inf'),float('nan'),True]:
            with self.assertRaises(ValueError):m.observation(v,(),set())
        value=m.observation(-0.0123456789,(),set())[0]
        self.assertAlmostEqual(value,-1.23456789,places=14)
        self.assertNotEqual(value,round(value,2))

    def test_year_text_is_supported_without_dropping_rows(self):
        self.assertEqual(m.year_value('2019'),2019)
        for v in ['2019.0','201X',None,2025,True]:
            with self.assertRaises(ValueError):m.year_value(v)

    def test_distinguish_government_levels_and_total_variants(self):
        names=['SeriesGovRevGenTaxTot%GDPICTD','SeriesGovtRevCenTaxTot%GDPICTD','SeriesGovtCalcRevTot%GDPICTD','SeriesGovtCurRev%GDPICTD','SeriesGovRevGenTaxGoodSer%GDPICTD']
        results=m.make_specs([{'Table':t,'Variable':t[6:]} for t in names])
        self.assertEqual([(x['sheet'],x['column']) for x in results],[('General','W'),('Central','W'),('General','Q'),('Central','Q'),('General','AR')])

if __name__=='__main__':unittest.main()
