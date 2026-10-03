"""Offline evidence viewer. All displayed text is assigned through textContent."""
import json


def render(folder, evidence):
    # Prevent source metadata from terminating the inert JSON script element.
    data = json.dumps(evidence, ensure_ascii=True, allow_nan=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    html = '''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>IFs quality evidence</title>
<style>body{font:16px system-ui;margin:2rem;color:#162b3b;background:#f7f9fc}h1{margin-bottom:.4rem}
label{display:inline-block;margin:1rem 1rem 1rem 0}select,input,button{font:inherit;padding:.4rem}
table{border-collapse:collapse;min-width:100%;width:max-content;background:white}td,th{text-align:left;padding:.5rem;border:1px solid #ccd5df;min-width:8rem;max-width:32rem;overflow-wrap:anywhere}
th{background:#e7edf4;white-space:nowrap}th button{border:0;background:none;text-align:left;cursor:pointer} .scroll{overflow:auto;max-height:65vh}
svg{background:white;border:1px solid #ccd5df;max-width:100%;height:auto}a{color:#1759a3}.muted{color:#495d70}</style>
<h1>IFs quality evidence</h1>
<p>Local calculations for human review. Findings are screening evidence, not proof of error or release approval.</p>
<p>Country means are unweighted. Totals require a declared additive measure. Missing observations are not zeros.
The full CSV and JSON files contain the evidence; review records are stored separately.</p>
<nav id="downloads"></nav>
<label for="series">Table</label> <select id="series"></select>
<label for="country">Country</label> <select id="country"></select>
<p id="chartTitle"></p><div id="plot"></div><p id="legend"></p>
<label for="kind">Evidence</label> <select id="kind"></select>
<label for="search">Search</label> <input id="search" type="search" placeholder="Country, year, finding ID…">
<p id="count" aria-live="polite"></p><div class="scroll"><table><thead id="head"></thead><tbody id="body"></tbody></table></div>
<p><button id="prev">Previous</button> <button id="next">Next</button> <span id="page"></span></p>
<script type="application/json" id="data">DATA</script>
<script>
'use strict';
const data=JSON.parse(document.getElementById('data').textContent), $=id=>document.getElementById(id);
const text=v=>v==null?'':typeof v==='object'?JSON.stringify(v):String(v);
let page=0,sort='',ascending=true;
const locations=new Map();for(const [kind,rows] of Object.entries(data))for(const row of rows)locations.set(row.id,kind);
function option(select,value,label){const o=document.createElement('option');o.value=value;o.textContent=label;select.append(o)}
for(const kind of Object.keys(data)){option($('kind'),kind,kind);const a=document.createElement('a');a.href=kind+'.csv';a.textContent=kind+'.csv';$('downloads').append(a,document.createTextNode(' · '))}
option($('series'),'','All tables');for(const t of [...new Set(data.summary.map(r=>r.table))])option($('series'),t,t);
$('kind').value='findings';
function countries(){ $('country').replaceChildren();option($('country'),'','Aggregate: unweighted country mean');
 const keys=new Set(data.observations.filter(r=>r.table===$('series').value).map(r=>JSON.stringify(r.key)));
 for(const key of keys)option($('country'),key,JSON.parse(key).join(' / ')); }
function draw(){
 $('plot').replaceChildren();$('legend').replaceChildren();const table=$('series').value,key=$('country').value;
 const units=[...new Set(data.metadata.filter(r=>r.table===table).map(r=>r.dataset+': '+(r.fields.Units||'units unspecified')))];
 $('chartTitle').textContent=table?(key?'Country trajectory — ':'Unweighted country mean — ')+table+' | '+units.join('; '):'Select a table to inspect trajectories.';
 if(!table)return;
 const rows=key?data.observations.filter(r=>r.table===table&&JSON.stringify(r.key)===key).map(r=>({dataset:r.dataset,year:+r.year,value:r.value})):
 data.aggregates.filter(r=>r.table===table&&r.cohort==='available').map(r=>({dataset:r.dataset,year:+r.year,value:r.mean}));
 const valid=rows.filter(r=>r.value!==null);if(!valid.length){$('plot').textContent='No usable observations.';return}
 const xs=valid.map(r=>r.year),ys=valid.map(r=>r.value),xmin=Math.min(...xs),xmax=Math.max(...xs),ymin=Math.min(...ys),ymax=Math.max(...ys);
 const x=v=>70+(v-xmin)/(xmax-xmin||1)*760,y=v=>290-(v-ymin)/(ymax-ymin||1)*240;
 const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox','0 0 930 340');svg.setAttribute('role','img');svg.setAttribute('aria-label',$('chartTitle').textContent);
 function el(tag,attrs,label){const n=document.createElementNS(ns,tag);for(const [k,v] of Object.entries(attrs))n.setAttribute(k,v);if(label!==undefined)n.textContent=label;svg.append(n);return n}
 el('path',{d:'M70 40 V290 H850',stroke:'#496078',fill:'none'});
 for(let i=0;i<=4;i++){const v=ymin+(ymax-ymin)*i/4;el('text',{x:4,y:294-60*i,'font-size':12},v.toPrecision(4))}
 el('text',{x:70,y:318},xmin);el('text',{x:810,y:318},xmax);
 const colors=['#1759a3','#bd4b00','#168268','#843ca3','#9c354d'];let idx=0;
 for(const dataset of [...new Set(rows.map(r=>r.dataset))]){const color=colors[idx++%colors.length];const label=document.createElement('span');label.textContent=dataset+'  ';label.style.color=color;$('legend').append(label);
 let previous=null;for(const r of rows.filter(r=>r.dataset===dataset).sort((a,b)=>a.year-b.year)){if(r.value===null){previous=null;continue}
 if(previous&&r.year-previous.year===1)el('line',{x1:x(previous.year),y1:y(previous.value),x2:x(r.year),y2:y(r.value),stroke:color,'stroke-width':2});
 const point=el('circle',{cx:x(r.year),cy:y(r.value),r:3,fill:color});const title=document.createElementNS(ns,'title');title.textContent=dataset+' '+r.year+': '+r.value;point.append(title);previous=r;}}
 $('plot').append(svg);
}
function show(){const kind=$('kind').value,query=$('search').value.toLowerCase(),table=$('series').value;
 let rows=data[kind].filter(r=>(!table||r.table===table)&&(!query||JSON.stringify(r).toLowerCase().includes(query)));
 if(sort)rows.sort((a,b)=>{const x=a[sort],y=b[sort];return (typeof x==='number'&&typeof y==='number'?x-y:text(x).localeCompare(text(y)))*(ascending?1:-1)});
 const fields=[...new Set(rows.flatMap(r=>Object.keys(r)))];page=Math.min(page,Math.max(0,Math.ceil(rows.length/100)-1));
 $('head').replaceChildren();$('body').replaceChildren();const tr=document.createElement('tr');
 for(const field of fields){const th=document.createElement('th'),b=document.createElement('button');b.textContent=field;b.onclick=()=>{ascending=sort===field?!ascending:true;sort=field;show()};th.append(b);tr.append(th)}$('head').append(tr);
 for(const row of rows.slice(page*100,(page+1)*100)){const tr=document.createElement('tr');for(const field of fields){const td=document.createElement('td');
 if(field==='evidence_id'&&locations.has(row[field])){const b=document.createElement('button');b.textContent=row[field];b.onclick=()=>{$('kind').value=locations.get(row[field]);$('search').value=row[field];page=0;sort='';show()};td.append(b)}else td.textContent=text(row[field]);tr.append(td)}$('body').append(tr)}
 $('count').textContent=rows.length+' matching rows; full evidence is available in CSV/JSON.';$('page').textContent='Page '+(page+1)+' of '+Math.max(1,Math.ceil(rows.length/100));$('prev').disabled=page===0;$('next').disabled=(page+1)*100>=rows.length;
}
$('series').onchange=()=>{page=0;countries();draw();show()};$('country').onchange=draw;
$('kind').onchange=()=>{page=0;sort='';show()};$('search').oninput=()=>{page=0;show()};$('prev').onclick=()=>{page--;show()};$('next').onclick=()=>{page++;show()};countries();draw();show();
</script></html>'''.replace('DATA</script>', data + '</script>')
    (folder / "report.html").write_text(html, encoding="utf-8")
    (folder / "report.md").write_text(
        "# Local quality evidence\n\nOpen report.html for filtering, sorting and trajectories. "
        "See checks.csv for unavailable checks and findings.csv for screening results.\n\n"
        f"Tables/datasets profiled: {len(evidence['summary'])}; findings: {len(evidence['findings'])}.\n\n"
        "This analysis does not certify a delivery or modify observations. Original inputs, "
        "settings, metadata and detailed evidence are frozen in this package. Human/AI review "
        "records are separate and do not suppress findings.\n", encoding="utf-8")
