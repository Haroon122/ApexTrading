import pandas as pd
from engine import load, run, stats
pd.set_option("display.width",250,"display.max_columns",30)

def sweep(df,n,trend=False):
    ph=df.high.rolling(n).max().shift();pl=df.low.rolling(n).min().shift()
    up=(df.low<pl)&(df.close>pl)&(df.close>df.open)     # sweep of lows, rejection -> long
    dn=(df.high>ph)&(df.close<ph)&(df.close<df.open)    # sweep of highs -> short
    if trend:
        e=df.close.ewm(span=200,adjust=False).mean()
        up&=df.close>e;dn&=df.close<e
    s=pd.Series(0,index=df.index);s[up]=1;s[dn]=-1
    return s

if __name__=="__main__":
    cfg={"H1":("xau_H1.csv","2023-01-01"),"H4":("xau_H4.csv","2023-01-01"),"M15":("xau_M15.csv","2025-01-01")}
    rows=[]
    for tf,(f,split) in cfg.items():
        df=load(f)
        for n in (20,50):
            for tr in (False,True):
                s=sweep(df,n,tr)
                for tp in (1.5,2.0,3.0):
                    for stop in (1.0,1.5):
                        t=run(df,s,stop_atr=stop,trail_atr=None,tp_r=tp,max_bars=48)
                        if len(t)==0: continue
                        al=stats(t);a=stats(t[t.open<split]);b=stats(t[t.open>=split])
                        rows.append(dict(tf=tf,N=n,EMA200=tr,tp=tp,stop=stop,n=al["n"],tr_yr=al["trades/yr"],win=al["win%"],PF=al["PF"],totR=al["totR"],maxDD=al["maxDD_R"],IS_PF=a.get("PF"),OOS_PF=b.get("PF")))
    o=pd.DataFrame(rows);print(o.to_string(index=False));o.to_csv("results_sweep.csv",index=False)
    print(o.groupby("tf").agg(best=("PF","max"),worst=("PF","min"),profitable=("PF",lambda x:(x>1).sum()),total=("PF","count")))
