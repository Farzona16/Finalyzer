from tests.helpers import build_demo
from core.config import Settings
from core.insights.runner import run_insights
from core.insights.plain import explain, PlainContext, jargon_in
from core.metrics.registry import available_metrics
bad=0; total=0
for n in ('retail','bank','sales','statistics'):
    d=build_demo(n); r=run_insights(d.con,d.model,Settings(_env_file=None),n)
    metrics={m.id:m for m in available_metrics(d.model,n)}
    for loc in ('uz','en'):
        ctx=PlainContext(loc,'UZS',metrics)
        for f in r.findings:
            for lvl in ('simple','simpler','expert'):
                try:
                    c=explain(f,ctx,lvl)
                except Exception as e:
                    print('ERR',n,loc,f.type,lvl,repr(e)); bad+=1; continue
                total+=1
                j=jargon_in(c)
                if j: print('JARGON',n,loc,lvl,f.type,j,'|',c.headline); bad+=1
print(total,bad)
d=build_demo('sales'); r=run_insights(d.con,d.model,Settings(_env_file=None),'sales')
metrics={m.id:m for m in available_metrics(d.model,'sales')}
for f in r.findings[:6]:
    c=explain(f,PlainContext('uz','UZS',metrics),'expert'); print(c.headline,'|',c.details,'|',c.evidence)
