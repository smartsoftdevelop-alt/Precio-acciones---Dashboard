"""Deterministic closed-bar analysis. No broker or order functions.
Policy v1: exact close for break/invalidation; zone tolerance only for retest touch.
The retest must occur on a later bar, approach from the broken side and close there.
"""
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from math import isfinite

LABELS = {'PDH':'Previo Día Alto','PDL':'Previo Día Bajo','PMH':'Premercado Alto',
          'PML':'Premercado Bajo','ORH':'Apertura Rango Alto','ORL':'Apertura Rango Bajo'}
STATES = {'waiting':'⚪ ESPERANDO','near':'🟡 CERCA DE NIVEL','break':'🟠 BREAK · Roto',
          'await':'🔵 ESPERANDO RETEST · Esperando retesteo',
          'retest':'🟢 RETEST CONFIRMADO · Retesteo confirmado',
          'continue':'🚀 SIGUE · Continuación','invalid':'🔴 SETUP INVALIDADO'}

def dt(s):
    d = datetime.fromisoformat(s.replace('Z','+00:00')) if isinstance(s,str) else s
    if d.tzinfo is None: raise ValueError('Fecha sin zona horaria')
    return d.astimezone(timezone.utc)

@dataclass(frozen=True)
class Bar:
    time: str
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None
    wap: float | None = None
    def valid(self):
        vals = (self.open,self.high,self.low,self.close)
        return all(isinstance(x,(int,float)) and isfinite(x) and x>0 for x in vals) and self.low<=min(self.open,self.close)<=max(self.open,self.close)<=self.high

def closed(bars, seconds, now):
    result = {}
    for b in bars:
        if not b.valid(): raise ValueError('Vela inválida; análisis suspendido')
        if dt(b.time)+timedelta(seconds=seconds)<=dt(now): result[b.time]=b
    return sorted(result.values(),key=lambda b:dt(b.time))

def extrema(bars):
    return (max(b.high for b in bars), min(b.low for b in bars)) if bars else (None,None)

def levels_for(market, previous, premarket, opening, session_open):
    """Inputs already restricted to official sessions. OR candles must be closed."""
    ph,pl=extrema(previous); result=[]
    def add(code,value,available):
        result.append({'code':code,'label':code+' · '+LABELS[code],'price':value,'available_at':available})
    for c,v in [('PDH',ph),('PDL',pl)]: add(c,v,session_open)
    if market=='US':
        ph,pl=extrema(premarket)
        for c,v in [('PMH',ph),('PML',pl)]: add(c,v,session_open)
    elif market=='LONDON':
        ph,pl=extrema(opening)
        available=(dt(session_open)+timedelta(minutes=15)).isoformat()
        for c,v in [('ORH',ph),('ORL',pl)]: add(c,v,available)
    else: raise ValueError('Mercado no compatible')
    return result

def ema(values,n):
    if len(values)<n:return None
    x=sum(values[:n])/n
    for v in values[n:]: x=v*2/(n+1)+x*(1-2/(n+1))
    return x

def favorable(delta,direction):
    if delta is None or direction is None:return 'Sin determinar'
    return 'Favorable' if delta*direction>0 else 'Contraria' if delta*direction<0 else 'Neutral'

def volume_class(b,previous):
    vs=[x.volume for x in previous[-20:] if x.volume is not None and x.volume>=0 and isfinite(x.volume)]
    if b.volume is None or b.volume<0 or len(vs)<5 or sum(vs)<=0:return 'DATO NO DISPONIBLE'
    ratio=b.volume/(sum(vs)/len(vs))
    return 'Superior' if ratio>=1.5 else 'Inferior' if ratio<0.75 else 'Normal'

def candle_features(b,previous,d):
    body=abs(b.close-b.open); upper=b.high-max(b.open,b.close); lower=min(b.open,b.close)-b.low
    wick=lower if d==1 else upper
    return {'body':body,'upper_wick':upper,'lower_wick':lower,'direction':'Alcista' if b.close>b.open else 'Bajista' if b.close<b.open else 'Neutral',
        'hammer':lower>=2*max(body,1e-10) and upper<=max(body,1e-10),
        'rejection':wick>=max(body,1e-10),'defended':True,'volume':volume_class(b,previous)}

def analyze(bars,levels,now,session_open,session_close,tolerance=0,context=None,reference=None,previous_close=None):
    if not isfinite(tolerance) or tolerance<0:raise ValueError('Tolerancia inválida')
    bars=closed(bars,120,now)
    bars=[b for b in bars if dt(session_open)<=dt(b.time) and dt(b.time)+timedelta(minutes=2)<=dt(session_close)]
    context=closed(context or [],900,now)
    machines={l['code']:{'state':'waiting','level':l,'direction':None,'entry':None} for l in levels if l['price'] is not None}
    events=[]
    def event(m,state,b,**extra):
        m['state']=state
        e={'state':state,'label':STATES[state],'level':m['level']['label'],'level_price':m['level']['price'],
           'direction':'Alcista' if m['direction']==1 else 'Bajista','time':(dt(b.time)+timedelta(minutes=2)).isoformat(),
           'close':b.close,'volume':b.volume,**extra}
        events.append(e);m['last_event']=e
    for i,b in enumerate(bars):
        prev=bars[i-1] if i else None
        contiguous=prev is not None and dt(b.time)-dt(prev.time)==timedelta(minutes=2)
        for m in machines.values():
            l=m['level'];p=l['price']
            # A bar straddling OR completion cannot break a level not known at its open.
            if dt(b.time)<dt(l['available_at']): continue
            if not contiguous:
                if m['state'] in ('break','await','retest','continue'):event(m,'invalid',b,reason='Hueco entre velas; secuencia no verificable')
                m['entry']=None
                # First regular candle may cross by its own open, never overnight close.
                before=b.open
            else:before=prev.close
            if m['state'] in ('waiting','near','invalid'):
                d=1 if before<=p<b.close else -1 if before>=p>b.close else None
                if d:
                    m.update(direction=d,entry=None,extreme=None,break_bar=asdict(b),break_volume=volume_class(b,bars[:i]))
                    event(m,'break',b)
                elif m['state']!='invalid':m['state']='near' if abs(b.close-p)<=max(tolerance,p*0.001) else 'waiting'
                continue
            d=m['direction']
            if (b.close-p)*d<0:
                event(m,'invalid',b,reason='Cierre de 2 minutos al otro lado del nivel');m['entry']=None;continue
            if m['state'] in ('break','await'):
                if m['state']=='break':event(m,'await',b)
                # Return from above/below, not a gap starting on the wrong side.
                touch=b.low<=p+tolerance and b.high>=p-tolerance
                approach=prev is not None and (prev.close-p)*d>0 and (b.open-p)*d>0
                returning=(b.low<prev.close if d==1 else b.high>prev.close) if prev else False
                if touch and approach and returning and (b.close-p)*d>0:
                    m.update(entry=b.close,entry_time=(dt(b.time)+timedelta(minutes=2)).isoformat(),
                             extreme=b.close,extreme_time=(dt(b.time)+timedelta(minutes=2)).isoformat(),
                             continuation_threshold=max(x.high for x in bars[:i+1]) if d==1 else min(x.low for x in bars[:i+1]),
                             retest=candle_features(b,bars[:i],d))
                    event(m,'retest',b,entry=b.close,features=m['retest'])
            elif m['state'] in ('retest','continue'):
                extreme=b.high if d==1 else b.low
                if (extreme-m['extreme'])*d>0:m.update(extreme=extreme,extreme_time=(dt(b.time)+timedelta(minutes=2)).isoformat())
                if m['state']=='retest' and (extreme-m['continuation_threshold'])*d>0:event(m,'continue',b,entry=m['entry'])
    candidates=list(machines.values())
    rank={'continue':6,'retest':5,'break':4,'await':3,'invalid':2,'near':1,'waiting':0}
    selected=max(candidates,key=lambda m:(7 if m['state']=='retest' and bars and m.get('entry_time')==(dt(bars[-1].time)+timedelta(minutes=2)).isoformat() else rank[m['state']],m.get('last_event',{}).get('time',''))) if candidates else {'state':'waiting','direction':None,'entry':None}
    d=selected['direction'];last=bars[-1] if bars else None
    values=[b.close for b in bars]; e9=ema(values,9);e21=ema(values,21)
    valid_volume=bool(bars) and all(b.volume is not None and b.volume>=0 and isfinite(b.volume) for b in bars)
    total=sum(b.volume for b in bars) if valid_volume else 0
    # IBKR trade-weighted bar averages, no invented VWAP if average/volume missing.
    vwap=sum(b.wap*b.volume for b in bars if b.volume>0)/total if total and all(b.wap is not None and isfinite(b.wap) and b.wap>0 for b in bars if b.volume>0) else None
    c15=[b for b in context if dt(b.time)>=dt(session_open) and dt(b.time)+timedelta(minutes=15)<=dt(session_close)]
    level=selected.get('level',{}).get('price')
    confirmation15=None if not c15 or not d or level is None else (c15[-1].close-level)*d>0
    ref=closed(reference or [],120,now)
    aligned=[b for b in ref if last and b.time==last.time]
    cross=favorable(aligned[-1].close-aligned[-1].open,d) if aligned else 'DATO NO DISPONIBLE'
    gap=(bars[0].open-previous_close)/previous_close*100 if bars and previous_close else None
    bias='Sin determinar'
    if len(c15)>=2: bias='Alcista' if c15[-1].high>c15[-2].high and c15[-1].low>c15[-2].low else 'Bajista' if c15[-1].high<c15[-2].high and c15[-1].low<c15[-2].low else 'Neutral'
    confirmations={'ema9':e9,'ema21':e21,'ema':favorable(e9-e21 if e9 is not None and e21 is not None else None,d),
       'vwap':vwap,'vwap_state':favorable(last.close-vwap if last and vwap else None,d),'volume':volume_class(last,bars[:-1]) if last else 'DATO NO DISPONIBLE',
       'fifteen':confirmation15,'cross':cross,'bias':bias,'gap_percent':gap,'news':'DATO NO DISPONIBLE'}
    entry=selected.get('entry');extreme=selected.get('extreme')
    move=(extreme-entry)*d if entry and extreme is not None else None
    return {'state':selected['state'],'state_label':STATES[selected['state']], 'selected':selected,'setups':candidates,
      'events':events,'bars':[asdict(b) for b in bars],'bars15':[asdict(b) for b in c15], 'levels':levels,
      'last_close':last.close if last else None,'last_bar':(dt(last.time)+timedelta(minutes=2)).isoformat() if last else None,
      'entry':entry,'movement':move,'movement_percent':move/entry*100 if entry and move is not None else None,
      'confirmations':confirmations,'policy':'v1'}
