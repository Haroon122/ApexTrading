import pandas as pd
from engine import load, run, stats
from strategies import london_breakout
pd.set_option("display.width",250,"display.max_columns",30)
cfg={"H1":("xau_H1.csv",1,9,"2023-01-01"),"H4":("xau_H4.csv",0,8,"2023-01-01"),"M15":("xau_M15.csv",1,9,"2025-01-01")}
rows=[]
for tf,(f,r0,r1,split) in cfg.items():
    df=load(f)
    for (t0,t1) in ((9,13),(9,17)):
        for tp in (1.5,2.0,None):
            for stop in (1.0,1.5):
                s,_=london_breakout(df,r0,r1,t0,t1)
                t=run(df,s,stop_atr=stop,trail_atr=None if tp else 1.5,tp_r=tp,eod_hour=22)
                if len(t)==0: continue
                a=stats(t[t.open<split]);b=stats(t[t.open>=split]);al=stats(t)
                rows.append(dict(tf=tf,win=f"{t0}-{t1}",tp=tp,stopATR=stop,n=al["n"],win_pct=al["win%"],PF=al["PF"],totR=al["totR"],maxDD=al["maxDD_R"],IS_PF=a.get("PF"),OOS_PF=b.get("PF"),OOS_R=b.get("totR")))
o=pd.DataFrame(rows);print(o.to_string(index=False));o.to_csv("results_london.csv",index=False)
