import numpy as np, pandas as pd
from engine import load, run, stats
from pullback_bt import daily_trend, pullback
from supertrend_bt import strat as st_strat
pd.set_option("display.width",250,"display.max_columns",30)
H1=load("xau_H1.csv");H4=load("xau_H4.csv");M15=load("xau_M15.csv");ut=daily_trend(H1)

def donch(df,n):
    hi=df.high.rolling(n).max().shift();lo=df.low.rolling(n).min().shift()
    up=(df.close>hi)&(df.close.shift()<=hi.shift());dn=(df.close<lo)&(df.close.shift()>=lo.shift())
    day=df.index.normalize();u=pd.Series(ut.reindex(day).values,index=df.index)
    have=u.notna();u=u.fillna(False).astype(bool)
    s=pd.Series(0,index=df.index);s[up&u&have]=1;s[dn&(~u)&have]=-1
    return s
comp={}
comp["Donchian H1"]=run(H1,donch(H1,100),stop_atr=3.0,trail_atr=4.0)
s,ex=st_strat(H4,10,2.0,200);comp["Supertrend H4"]=run(H4,s,stop_atr=3.0,trail_atr=None,exit_signal=ex)
comp["Pullback H4"]=run(H4,pullback(H4,ut,20),stop_atr=2.0,trail_atr=3.0)
comp["Pullback H1"]=run(H1,pullback(H1,ut,20),stop_atr=3.0,trail_atr=4.0)
comp["Pullback M15"]=run(M15,pullback(M15,ut,20),stop_atr=3.0,trail_atr=4.0)
comp["Donchian M15"]=run(M15,donch(M15,400),stop_atr=3.0,trail_atr=4.0)

def merged(names,start=None):
    t=pd.concat([comp[n].assign(src=n) for n in names]).sort_values("close")
    if start: t=t[t.open>=start]
    return t
def row(label,t):
    s=stats(t)
    return dict(portfolio=label,trades=s["n"],tr_yr=s["trades/yr"],PF=s["PF"],totR=s["totR"],R_yr=s["R/yr"],maxDD=s["maxDD_R"],MAR=round(s["R/yr"]/s["maxDD_R"],2))
def concurrent(t):
    ev=pd.concat([pd.Series(1,index=t.open),pd.Series(-1,index=t.close)]).sort_index()
    return ev.cumsum().max()

sets={"A: Donchian H1 (now live)":["Donchian H1"],
 "B: Donch H1 + Supertrend H4":["Donchian H1","Supertrend H4"],
 "C: Donch H1 + ST H4 + Pullback H4":["Donchian H1","Supertrend H4","Pullback H4"],
 "D: C + Pullback H1":["Donchian H1","Supertrend H4","Pullback H4","Pullback H1"]}
for title,start,extra in (("FULL 2016-2026 (H1+H4)",None,{}),("2022-08 onward (adds M15)","2022-08-08",{"E: D + Pullback M15":["Donchian H1","Supertrend H4","Pullback H4","Pullback H1","Pullback M15"],"F: E + Donchian M15":["Donchian H1","Supertrend H4","Pullback H4","Pullback H1","Pullback M15","Donchian M15"]})):
    rows=[]
    for k,v in {**sets,**extra}.items():
        t=merged(v,start);r=row(k,t);r["max_open"]=concurrent(t);rows.append(r)
    print("\n==",title);print(pd.DataFrame(rows).to_string(index=False))
# singles on 2022-08+
print("\n== single components since 2022-08")
print(pd.DataFrame([row(n,merged([n],"2022-08-08")) for n in comp]).to_string(index=False))
# monthly correlation (full)
mm=pd.DataFrame({n:comp[n].set_index("close").R.resample("1ME").sum() for n in ["Donchian H1","Supertrend H4","Pullback H4","Pullback H1"]}).fillna(0)
print("\nmonthly R correlation\n",mm.corr().round(2))
# yearly for set D
t=merged(sets["D: C + Pullback H1"]);print("\nYearly R, set D:\n",t.groupby(t.open.dt.year).R.sum().round(1).to_dict())
t=merged(sets["C: Donch H1 + ST H4 + Pullback H4"]);print("Yearly R, set C:\n",t.groupby(t.open.dt.year).R.sum().round(1).to_dict())
