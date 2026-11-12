import numpy as np, pandas as pd
from engine import load, run, stats, atr
pd.set_option("display.width",250,"display.max_columns",30)

def supertrend(df,n,mult):
    a=atr(df,n).values;h=df.high.values;l=df.low.values;c=df.close.values
    hl2=(h+l)/2;ub=hl2+mult*a;lb=hl2-mult*a
    fu=ub.copy();fl=lb.copy();d=np.ones(len(df))
    for i in range(1,len(df)):
        fu[i]=ub[i] if (ub[i]<fu[i-1] or c[i-1]>fu[i-1]) else fu[i-1]
        fl[i]=lb[i] if (lb[i]>fl[i-1] or c[i-1]<fl[i-1]) else fl[i-1]
        if d[i-1]==1: d[i]=-1 if c[i]<fl[i] else 1
        else: d[i]=1 if c[i]>fu[i] else -1
    return pd.Series(d,index=df.index)

def strat(df,n,mult,ema):
    d=supertrend(df,n,mult);e=df.close.ewm(span=ema,adjust=False).mean()
    flip_up=(d==1)&(d.shift()==-1);flip_dn=(d==-1)&(d.shift()==1)
    s=pd.Series(0,index=df.index)
    s[flip_up&(df.close>e)]=1;s[flip_dn&(df.close<e)]=-1
    ex=pd.Series(0,index=df.index);ex[d==-1]=1;ex[d==1]=-1   # close longs when ST bearish
    return s,ex

if __name__=="__main__":
    cfg={"H1":("xau_H1.csv","2023-01-01"),"H4":("xau_H4.csv","2023-01-01"),"M15":("xau_M15.csv","2025-01-01")}
    rows=[]
    for tf,(f,split) in cfg.items():
        df=load(f)
        for (n,m) in ((10,3.0),(10,2.0),(14,3.0)):
            for ema in (50,200):
                s,ex=strat(df,n,m,ema)
                for stop in (2.0,3.0):
                    t=run(df,s,stop_atr=stop,trail_atr=None,exit_signal=ex)
                    if len(t)==0: continue
                    al=stats(t);a=stats(t[t.open<split]);b=stats(t[t.open>=split])
                    rows.append(dict(tf=tf,ST=f"{n}/{m}",EMA=ema,stop=stop,n=al["n"],tr_yr=al["trades/yr"],win=al["win%"],PF=al["PF"],totR=al["totR"],maxDD=al["maxDD_R"],IS_PF=a.get("PF"),OOS_PF=b.get("PF"),OOS_R=b.get("totR")))
    o=pd.DataFrame(rows);print(o.to_string(index=False));o.to_csv("results_supertrend.csv",index=False)
