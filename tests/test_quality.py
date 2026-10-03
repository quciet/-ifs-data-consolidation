"""Read-only statistical evidence tests, exclusively synthetic databases."""
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ifs_pipeline.quality import Evidence, change, inspect, rules_for, run_quality, verify_quality, record_review, delivery_quality
from ifs_pipeline.storage import PipelineError, sha256, write_json, read_json
from ifs_pipeline.model import Series


class QualityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def db(self, name, rows, years=None, dyadic=False):
        path = self.root / name
        years = years or [str(y) for y in range(2000, 2006)]
        with sqlite3.connect(path) as c:
            keys = 'Actor TEXT, Actor_FIPS TEXT, Partner TEXT, Partner_FIPS TEXT' if dyadic else 'Country TEXT, FIPS_CODE TEXT'
            c.execute('CREATE TABLE SeriesTest ('+keys+','+','.join('"'+y+'" DOUBLE' for y in years)+')')
            for key, values in rows.items():
                c.execute('INSERT INTO SeriesTest VALUES ('+','.join('?' for _ in range(len(key)+len(years)))+')', (*key,*values))
            c.execute('CREATE TABLE DataDict ("Table" TEXT, Variable TEXT, Units TEXT)')
            c.execute('INSERT INTO DataDict VALUES (?,?,?)', ('SeriesTest','Test','persons'))
        c.close()
        return path

    def run_spec(self, base, incoming, settings=None, final=None):
        ds=[dict(id='base',role='base',path=str(base)),dict(id='batch-A',role='incoming',path=str(incoming),applied_tables=['SeriesTest'])]
        if final:
            ds.append(dict(id='final',role='final',path=str(final)))
        spec = self.root/'spec.json'
        write_json(spec,dict(version=1,tables=['SeriesTest'],datasets=ds,settings=settings or {}))
        out=self.root/'evidence'
        run_quality(spec,out)
        return out,read_json(out/'evidence.json')

    def test_scale_freeze_and_review(self):
        rows={(f'C{i}',f'K{i}'):[i+y+1 for y in range(6)] for i in range(10)}
        base=self.db('base.db',rows)
        incoming=self.db('incoming.db',{k:[v*1000 for v in vs] for k,vs in rows.items()})
        before={p:sha256(p) for p in (base,incoming)}
        out,e=self.run_spec(base,incoming)
        self.assertTrue(any(f['rule']=='possible_scale_change' and f['dataset']=='batch-A' for f in e['findings']))
        self.assertEqual(before,{p:sha256(p) for p in before})
        verify_quality(out)
        finding=e['findings'][0]['id']
        record_review(out,finding,'Test reviewer','requires_investigation','Synthetic example')
        verify_quality(out)
        self.assertEqual(len(list((out/'reviews').glob('*.json'))),1)
        with self.assertRaises(PipelineError):
            record_review(out,'missing','Reviewer','explained','Reason')
        (out/'coverage.csv').write_text('tampered')
        with self.assertRaises(PipelineError):
            verify_quality(out)

    def test_matched_coverage_does_not_flag_loss_as_value_jump(self):
        base=self.db('base.db',{('A','A'):[10,10],('B','B'):[1000,None]},['2000','2001'])
        out,e=self.run_spec(base,base,{'defaults':{'aggregation':'sum'}})
        available=[r for r in e['aggregates'] if r['dataset']=='base' and r['cohort']=='available']
        self.assertEqual([r['total'] for r in available],[1010,10])
        matched=next(r for r in e['aggregates'] if r['dataset']=='base' and r.get('metric')=='total' and r['cohort']=='matched')
        self.assertEqual((matched['before'],matched['after'],matched['count']),(10,10,1))
        self.assertFalse(any(f['rule']=='aggregate_jump' for f in e['findings']))

    def test_country_swap_unchanged_total(self):
        a,b=('A','A'),('B','B')
        base=self.db('base.db',{a:[10,11,12,13,14,15],b:[90,91,92,93,94,95]})
        incoming=self.db('incoming.db',{a:[90,91,92,93,94,95],b:[10,11,12,13,14,15]})
        _,e=self.run_spec(base,incoming,{'defaults':{'aggregation':'sum'}})
        self.assertTrue(any(f['rule']=='possible_country_swap' for f in e['findings']))
        sums={d:[r['total'] for r in e['aggregates'] if r['dataset']==d and r['cohort']=='available'] for d in ('base','batch-A')}
        self.assertEqual(sums['base'],sums['batch-A'])

    def test_spike_zero_negative_and_frequency(self):
        e=Evidence();r=rules_for({},'SeriesTest')
        s=Series('SeriesTest',('Country','FIPS_CODE'),['2000','2001','2002'],{('A','A'):{'2000':10,'2001':1000,'2002':10}})
        inspect(e,'SeriesTest','incoming',s,r)
        self.assertIn('spike_reversal',{x['rule'] for x in e.rows['findings']})
        self.assertEqual(change(0,10,r)['reason'],'near_zero_denominator')
        self.assertEqual(change(-10,-5,r)['relative_change'],0.5)
        s=Series('SeriesTest',s.keys,['2000','2005'],{('A','A'):{'2000':10,'2005':20}})
        e=Evidence();inspect(e,'SeriesTest','incoming',s,{**r,'frequency_years':5})
        self.assertNotIn('interval_gap',{x['rule'] for x in e.rows['findings']})

    def test_blending_boundary_and_origin(self):
        base=self.db('base.db',{('A','A'):[1,1]},['2000','2001'])
        incoming=self.db('incoming.db',{('A','A'):[None,100]},['2000','2001'])
        final=self.db('final.db',{('A','A'):[1,100]},['2000','2001'])
        _,e=self.run_spec(base,incoming,final=final)
        flags=[f for f in e['findings'] if f['rule']=='blending_boundary_jump']
        self.assertEqual(flags[0]['origins'],['base','batch-A'])
        self.assertEqual([r['origin'] for r in e['observations'] if r['dataset']=='final'],['base','batch-A'])

    def test_invalid_text_and_dyadic_are_not_passes(self):
        base=self.db('base.db',{('A','A'):['oops',1]},['2000','2001'])
        incoming=self.db('incoming.db',{('A','A','B','B'):[1,2]},['2000','2001'],True)
        _,e=self.run_spec(base,incoming)
        self.assertTrue(any(x['status']=='insufficient_evidence' for x in e['checks']))
        self.assertTrue(any(x['reason']=='dyadic_checks_not_implemented' for x in e['checks']))
        self.assertEqual(e['summary'],[])

    def test_repeatability_and_no_overwrite(self):
        base=self.db('base.db',{('A','A'):[1,2,3,4,5,6]})
        out,_=self.run_spec(base,base)
        second=self.root/'second'
        run_quality(self.root/'spec.json',second)
        self.assertEqual(sha256(out/'evidence.json'),sha256(second/'evidence.json'))
        with self.assertRaises(FileExistsError):
            run_quality(self.root/'spec.json',out)

    def test_invalid_rules(self):
        for setting in ({'jump_fraction':-1},{'near_zero':0},{'min_pairs':True},{'scale_share':2},{'aggregation':'guess'},{'oops':1}):
            with self.assertRaises(PipelineError):
                rules_for({'defaults':setting},'SeriesTest')

    def test_constant_history_and_overflow(self):
        years=[str(y) for y in range(2000,2008)]
        s=Series('SeriesTest',('Country','FIPS_CODE'),years,{('A','A'):dict(zip(years,[1]*7+[100]))})
        e=Evidence();inspect(e,'SeriesTest','base',s,rules_for({},'SeriesTest'))
        self.assertIn('unusual_country_movement',{f['rule'] for f in e.rows['findings']})
        self.assertEqual(change(-1e308,1e308,rules_for({},'SeriesTest'))['reason'],'arithmetic_overflow')

    def test_temporal_swaps_and_reversal(self):
        base=self.db('base.db',{('A','A'):[10,90,10],('B','B'):[90,10,90]},['2000','2001','2002'])
        _,e=self.run_spec(base,base)
        candidates=[x for x in e['swaps'] if x['dataset']=='base' and x['kind']=='temporal']
        self.assertEqual(len(candidates),2)
        self.assertEqual(candidates[0]['year_before'],'2000')

    def test_multiple_batches_exact_precedence_and_tampered_final(self):
        years=['2000','2001']
        base=self.db('base.db',{('A','A'):[1,1]},years)
        a=self.db('a.db',{('A','A'):[5,None]},years)
        b=self.db('b.db',{('A','A'):[6,9]},years)
        final=self.db('final.db',{('A','A'):[6,9]},years)
        ds=[dict(id='base',role='base',path=str(base)),dict(id='A',role='incoming',path=str(a),applied_tables=['SeriesTest']),dict(id='B',role='incoming',path=str(b),applied_tables=['SeriesTest']),dict(id='final',role='final',path=str(final))]
        spec=self.root/'spec.json';write_json(spec,dict(version=1,tables=['SeriesTest'],datasets=ds))
        out=self.root/'good';run_quality(spec,out)
        e=read_json(out/'evidence.json')
        self.assertEqual({x['origin'] for x in e['observations'] if x['dataset']=='final'},{'B'})
        ds[1],ds[2]=ds[2],ds[1];write_json(spec,dict(version=1,tables=['SeriesTest'],datasets=ds))
        out=self.root/'bad';run_quality(spec,out)
        e=read_json(out/'evidence.json')
        self.assertTrue(any(x['rule']=='declared_exact_origins' and x['status']=='insufficient_evidence' for x in e['checks']))

    def test_native_delivery_adapter_and_historical_evidence_unchanged(self):
        from ifs_pipeline.delivery import prepare_delivery, consolidate_delivery
        import shutil
        from ifs_pipeline.demo import create_demo
        create_demo(self.root/'demo')
        base=self.root/'demo/baseline'
        source=self.root/'source.db'
        shutil.copyfile(base/'DataDict.db',source)
        from ifs_pipeline.model import write_series
        conn=sqlite3.connect(source)
        with conn:
            write_series(conn,Series('SeriesDemoPopulation',('Country','FIPS_CODE'),['2023'],{('Example A','AAA'):{'2023':115},('Example B','BBB'):{'2023':None}}))
        conn.close()
        delivery=self.root/'delivery';prepare_delivery(base,delivery,'SeriesDemoPopulation')
        shutil.copyfile(source,delivery/'IFsDataImport/IFsDataImport_Test.db')
        consolidate_delivery(delivery)
        report=delivery/'Consolidation Report'
        state=read_json(report/'delivery.json')
        self.assertTrue(state['batches'][0]['applied_tables'], state['batches'][0]['table_outcomes'])
        before={str(p):sha256(p) for p in report.rglob('*') if p.is_file()}
        out=self.root/'native-quality';delivery_quality(delivery,out)
        self.assertEqual(before,{str(p):sha256(p) for p in report.rglob('*') if p.is_file()})
        verify_quality(out)
        self.assertEqual(read_json(out/'manifest.json')['native_delivery']['verification'],'passed')

    def test_report_script_escaping_and_explicit_baseline_name_view(self):
        old=('Old <script>','A');new=('New </script>','A')
        base=self.db('base.db',{old:[1,2]},['2000','2001'])
        incoming=self.db('incoming.db',{new:[1,2]},['2000','2001'])
        ds=[dict(id='base',role='base',path=str(base)),dict(id='in',role='incoming',path=str(incoming))]
        spec=self.root/'spec.json';write_json(spec,dict(version=1,tables=['SeriesTest'],datasets=ds,baseline_names={'SeriesTest':[{'before':old[0],'after':new[0],'code':'A'}]}))
        out=self.root/'view';run_quality(spec,out)
        self.assertNotIn('New </script>',(out/'report.html').read_text(encoding='utf-8'))
        e=read_json(out/'evidence.json')
        self.assertTrue(all(r['outcome']=='unchanged' for r in e['comparisons'] if r['kind']=='baseline'))


if __name__=='__main__':
    unittest.main()
