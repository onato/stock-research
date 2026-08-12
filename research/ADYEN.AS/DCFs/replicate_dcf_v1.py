"""Replicate ADYEN_DCF_v1_preH1FY26.xlsx in code, to price corrections and run
sensitivities the workbook cannot express.

Reproduces the workbook to within EUR0.11/share on every scenario (bear 571.08 vs
571.11, base 1047.59 vs 1047.67, bull 1418.15 vs 1418.27, weighted 1105.81 vs
1105.89) -- so deltas computed here are trustworthy.

Two findings, 12-Aug-2026 (pre-H1-print):
  1. SBC 3.0% (workbook estimate) -> 1.85% (FY23-25 actual, from the Metrics CSV)
     lifts weighted value 1105.89 -> 1118.31, i.e. +18.5% -> +19.8% vs EUR933.20.
  2. Value is nearly insensitive to a ONE-YEAR growth miss (FY26 at 8% still leaves
     ~+11% upside) but very sensitive to a PERMANENT shift: break-even is roughly a
     -3pp shift applied across the whole decade.

Not part of the tested pipeline -- an analysis artifact kept beside the workbook.
Run: python3 research/ADYEN.AS/DCFs/replicate_dcf_v1.py
"""

NR0=2364.2; WACC=0.105; DA=0.06; CAPEX=0.05; CAP=20.0; SH=31.55; NETCASH=4200.0; PRICE=933.2
DF=[0.905,0.819,0.7412,0.6707,0.607,0.5493,0.4971,0.4499,0.4071,0.3684]
S={
 'bear':dict(g=[.15,.11,.09,.07,.06,.05,.04,.04,.03,.03],
   m=[.55,.542,.534,.525,.516,.517,.508,.508,.499,.50],
   sbc=[.03,.032,.034,.035,.036,.037,.038,.038,.039,.04],
   tax=[.23,.238,.245,.25,.255,.258,.258,.258,.258,.258], tg=.015, float=988.0465, w=.15),
 'base':dict(g=[.18,.17,.16,.15,.13,.12,.11,.10,.09,.08],
   m=[.56,.575,.584,.588,.593,.594,.596,.596,.596,.595],
   sbc=[.03,.03,.029,.028,.028,.027,.027,.026,.026,.025],
   tax=[.23,.235,.24,.245,.25,.25,.25,.25,.25,.25], tg=.03, float=1910.4564, w=.50),
 'bull':dict(g=[.20,.20,.19,.18,.17,.16,.14,.13,.12,.11],
   m=[.565,.584,.603,.617,.626,.630,.632,.633,.632,.632],
   sbc=[.03,.029,.028,.027,.026,.025,.024,.023,.022,.022],
   tax=[.228,.232,.236,.24,.245,.248,.25,.25,.25,.25], tg=.035, float=2761.2962, w=.35),
}
def run(p, sbc_override=None, floatv=None):
    nr=NR0; pv=0; fcf=None
    sbc_sched = [sbc_override]*10 if sbc_override is not None else p['sbc']
    for i in range(10):
        nr*= (1+p['g'][i])
        ebitda_pre = nr*p['m'][i]
        sbc = nr*sbc_sched[i]
        da = nr*DA; capex = nr*CAPEX
        ebit = ebitda_pre - sbc - da
        nopat = ebit*(1-p['tax'][i])
        fcf = nopat + da - capex
        pv += fcf*DF[i]
    tv_g = fcf*(1+p['tg'])/(WACC-p['tg']); tv_c = fcf*CAP
    tv = min(tv_g,tv_c)
    ev = pv + tv*DF[9]
    fl = p['float'] if floatv is None else floatv
    return (ev + fl + NETCASH)/SH
print(f"{'':6} {'replicated':>12} {'workbook':>11}")
wbv={'bear':571.108709494419,'base':1047.66557507784,'bull':1418.26710824382}
tot=0
for k,p in S.items():
    v=run(p); tot+=v*p['w']
    print(f"{k:6} {v:12.2f} {wbv[k]:11.2f}  diff {v-wbv[k]:+.2f}")
print(f"weighted {tot:.2f}  workbook 1105.89  diff {tot-1105.89:+.2f}")

print()
print("=== SBC correction: 3.0% estimate -> 1.85% actual (FY23-25 avg) ===")
tot2=0
for k,p in S.items():
    old=run(p); new=run(p,sbc_override=0.0185); tot2+=new*p['w']
    print(f"{k:6} {old:10.2f} -> {new:10.2f}   {new-old:+8.2f}")
print(f"weighted {1105.81:.2f} -> {tot2:.2f}   {tot2-1105.81:+.2f}")
print(f"upside vs {PRICE}: {100*(tot2/PRICE-1):+.1f}%  (was {100*(1105.81/PRICE-1):+.1f}%)")

print()
print("=== Sensitivity: what FY26 reported growth breaks even at EUR933.2? ===")
import copy
for g1 in [0.20,0.18,0.16,0.15,0.14,0.12,0.10,0.08]:
    t=0
    for k,p in S.items():
        q=copy.deepcopy(p); q['g']=[g1]+p['g'][1:]
        t+=run(q,sbc_override=0.0185)*p['w']
    print(f"  FY26 growth {g1:5.0%} -> weighted {t:8.2f}  ({100*(t/PRICE-1):+6.1f}%)")
