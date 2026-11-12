import pandas as pd, engine
from engine import load, run, stats
pd.set_option("display.width",250,"display.max_columns",30)

def daily_trend(h1):
    d=h1.close.resample("1D").last().dropna()
    up=(d>d.rolling(200).mean())
    return up.shift(1)     # last COMPLETED day

def pullback(df,up_day,pe,fast_tf=True):
    e=df.close.ewm(span=pe,adjust=False).mean();e2=df.close.ewm(span=pe*2+10 if pe<50 else 100,adjust=False).mean()
    day=df.index.normalize();u=up_day.reindex(day).values
    u=pd.Series(u,index=df.index).fillna(False).astype(bool)
    have=pd.Series(up_day.reindex(day).notna().values,index=df.index)
    long_=u&have&(e>e2)&(df.low<=e)&(df.close>e)&(df.close>df.open)
    short_=(~u)&have&(e<e2)&(df.high>=e)&(df.close<e)&(df.close<df.open)
    s=pd.Series(0,index=df.index);s[long_]=1;s[short_]=-1
    return s

if __name__=="__main__":
    h1=load("xau_H1.csv");ut=daily_trend(h1)
    cfg={"H1":("xau_H1.csv","2023-01-01"),"H4":("xau_H4.csv","2023-01-01"),"M15":("xau_M15.csv","2025-01-01")}
    rows=[]
    for tf,(f,split) in cfg.items():
        df=load(f)
        for pe in (20,50):
            s=pullback(df,ut,pe)
            for mode,kw in (("trail3",dict(stop_atr=2.0,trail_atr=3.0)),("trail4",dict(stop_atr=3.0,trail_atr=4.0)),("TP2R",dict(stop_atr=1.5,trail_atr=None,tp_r=2.0,max_bars=60)),("TP3R",dict(stop_atr=2.0,trail_atr=None,tp_r=3.0,max_bars=100))):
                t=run(df,s,**kw)
                if len(t)==0: continue
                al=stats(t);a=stats(t[t.open<split]);b=stats(t[t.open>=split])
                rows.append(dict(tf=tf,EMA=pe,exit=mode,n=al["n"],tr_yr=al["trades/yr"],win=al["win%"],PF=al["PF"],totR=al["totR"],maxDD=al["maxDD_R"],IS_PF=a.get("PF"),OOS_PF=b.get("PF")))
    o=pd.DataFrame(rows);print(o.to_string(index=False));o.to_csv("results_pullback.csv",index=False)
    print(o.groupby("tf").agg(best=("PF","max"),worst=("PF","min"),profitable=("PF",lambda x:(x>1).sum()),total=("PF","count")))
