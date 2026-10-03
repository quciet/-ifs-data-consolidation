"""Generate an isolated synthetic quality package for offline viewer demonstration."""
import argparse
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ifs_pipeline.model import Series, write_series
from ifs_pipeline.quality import run_quality
from ifs_pipeline.storage import write_json


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',required=True,type=Path)
    args=p.parse_args()
    root=args.output.resolve();root.mkdir(parents=True,exist_ok=False)
    years=[str(y) for y in range(2010,2020)]
    datasets=[]
    for label,role in [('base','base'),('incoming','incoming'),('final','final')]:
        path=root/(label+'.db');conn=sqlite3.connect(path)
        try:
            with conn:
                conn.execute('CREATE TABLE DataDict ("Table" TEXT, Variable TEXT, Units TEXT, Definition TEXT)')
                for name in ('SeriesSyntheticScale','SeriesSyntheticSwap','SeriesSyntheticCoverage'):
                    rows={(f'Example {i:02d}',f'X{i:02d}'):{y:(i+1)*10+j for j,y in enumerate(years)} for i in range(12)}
                    if name=='SeriesSyntheticScale' and role!='base':
                        rows={k:{y:v*1000 for y,v in c.items()} for k,c in rows.items()}
                    if name=='SeriesSyntheticSwap' and role!='base':
                        a,b=('Example 00','X00'),('Example 11','X11');rows[a],rows[b]=rows[b],rows[a]
                    if name=='SeriesSyntheticCoverage' and role=='incoming':
                        rows[('Example 11','X11')]['2019']=None
                    write_series(conn,Series(name,('Country','FIPS_CODE'),years,rows))
                    conn.execute('INSERT INTO DataDict VALUES (?,?,?,?)',(name,name[6:],'synthetic units','Synthetic test data; not real country observations'))
        finally:
            conn.close()
        d=dict(id=label,role=role,path=str(path))
        if role=='incoming':
            d['applied_tables']=['SeriesSyntheticScale','SeriesSyntheticSwap','SeriesSyntheticCoverage']
        datasets.append(d)
    spec=root/'spec.json'
    write_json(spec,dict(version=1,tables=datasets[1]['applied_tables'],datasets=datasets,settings={'defaults':{'aggregation':'sum'}}))
    print(run_quality(spec,root/'evidence'))


if __name__=='__main__':
    main()
