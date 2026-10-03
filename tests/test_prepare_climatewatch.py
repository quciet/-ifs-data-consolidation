import importlib.util
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location('prepare_climatewatch', Path(__file__).resolve().parents[1] / 'scripts/prepare_climatewatch.py')
cw = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cw)


class ClimateWatchPreparationTests(unittest.TestCase):
    def test_numeric_missing_zero_and_sinks(self):
        self.assertIsNone(cw.number(' '))
        self.assertEqual(cw.number('0'), 0.0)
        self.assertEqual(cw.number('-12.005'), -12.005)
        self.assertEqual(cw.number('5.1e-05'), 0.000051)

    def test_invalid_tokens_and_precision_loss_are_not_missing(self):
        for token in ['NA', '#VALUE!', 'NaN', 'inf', '1,000', '1%', '1e999', '1e-999', '9007199254740993', '0.1234567890123456789']:
            with self.subTest(token=token), self.assertRaises(ValueError):
                cw.number(token)

    def test_source_names_override_conflicting_codes(self):
        index = {'mongolia': {'MON'}, 'montenegro': {'MNG'}, 'mng': {'MNG'}}
        labels = {'MNG': ('Mongolia', 'local GDP'), 'MNE': ('Montenegro', 'local GDP')}
        profile = {'reviewed_name_spellings': {}, 'excluded_entities': {}}
        mapping, audit = cw.concordance(labels, labels, index, {'MON':'Mongolia','MNG':'Montenegro'}, profile)
        self.assertEqual(mapping, {'MNG':'MON','MNE':'MNG'})
        self.assertEqual(len(audit), 2)

    def test_unknown_or_converging_identity_stops(self):
        profile = {'reviewed_name_spellings': {}, 'excluded_entities': {}}
        for labels in [{'XXX':('Unknown','local')}, {'A':('Mongolia','local'),'B':('Mongolia','local')}]:
            with self.assertRaises(ValueError):
                cw.concordance(labels, labels, {'mongolia': {'MON'}}, {'MON':'Mongolia'}, profile)

    def test_reviewed_encoding_variant_keeps_original_label(self):
        label = 'Korea, Dem. People\u00e2\u20ac\u2122s Rep.'
        profile = {'reviewed_name_spellings': {label:'North Korea'}, 'excluded_entities': {}}
        mapping, audit = cw.concordance(['PRK'], {'PRK':(label,'local')}, {'north korea':{'PRK'}}, {'PRK':'North Korea'}, profile)
        self.assertEqual(mapping, {'PRK':'PRK'})
        self.assertEqual(audit[0]['source_country_name'], label)

    def test_exclusion_requires_exact_reviewed_identity(self):
        profile = {'reviewed_name_spellings': {}, 'excluded_entities': {'WORLD':'World'}}
        result, _ = cw.concordance(['WORLD'], {'WORLD':('World','local')}, {}, {}, profile)
        self.assertIsNone(result['WORLD'])
        with self.assertRaises(ValueError):
            cw.concordance(['WORLD'], {'WORLD':('Country A','local')}, {}, {}, profile)

    def test_duplicate_source_keys_fail_instead_of_overwriting(self):
        row = {'Country':'MNG','Sector':'Energy','Gas':'CO2','Source':'Climate Watch','1990':'2'}
        self.assertEqual(len(cw.index_rows([row])), 1)
        with self.assertRaises(ValueError):
            cw.index_rows([row, {**row, '1990':'3'}])


if __name__ == '__main__':
    unittest.main()
