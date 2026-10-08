"""Private, read-only IBKR bridge. Run separately from GitHub Pages.
No account, order, execution, position or portfolio endpoints are exposed.
"""
import asyncio, contextlib, hashlib, json, math, os, secrets, sqlite3, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from ib_async import IB, Contract, StartupFetch
from .engine import Bar, analyze, closed, dt, levels_for

DB_PATH=os.getenv('BR_DATABASE','break-retest.sqlite3')
TOKEN=os.getenv('BR_TOKEN','')
ORIGIN=os.getenv('BR_ORIGIN','https://smartsoftdevelop-alt.github.io')
TZ_ALIASES={'US/Eastern':'America/New_York','EST':'America/New_York','EST5EDT':'America/New_York','GB-Eire':'Europe/London','MET':'Europe/Berlin'}
ib=IB(); snapshots={}; cache={}; locks={i:asyncio.Lock() for i in range(1,5)}; connection_lock=asyncio.Lock()
status={'connected':False,'message':'Conexión IBKR pendiente','heartbeat':None}

def db():
    c=sqlite3.connect(DB_PATH);c.row_factory=sqlite3.Row;return c

def initialize():
    with db() as c:
        c.executescript('CREATE TABLE IF NOT EXISTS profiles(id INTEGER PRIMARY KEY, config TEXT NOT NULL); CREATE TABLE IF NOT EXISTS events(event_id TEXT PRIMARY KEY, profile INTEGER, time TEXT, payload TEXT); CREATE TABLE IF NOT EXISTS snapshots(profile INTEGER PRIMARY KEY, payload TEXT);')
        for i in range(1,5):
            cfg={'conid':76023663,'exchange':'LSEETF','tolerance':0,'reference':None} if i==1 else {'conid':None,'exchange':None,'tolerance':0,'reference':None}
            c.execute('INSERT OR IGNORE INTO profiles VALUES (?,?)',(i,json.dumps(cfg)))
        for row in c.execute('SELECT profile,payload FROM snapshots'):
            saved=json.loads(row['payload']);saved.update(entry_available=False,entry_message='NO HAY ENTRADA CONFIRMADA',price=None,error='Estado guardado; esperando reconexión a IBKR',quality='Histórico')
            snapshots[row['profile']]=saved

def config(i):
    with db() as c:r=c.execute('SELECT config FROM profiles WHERE id=?',(i,)).fetchone()
    if r is None:raise HTTPException(404,'Perfil inexistente')
    return json.loads(r['config'])

def safe_number(x):return float(x) if isinstance(x,(int,float)) and math.isfinite(x) and x>=0 else None

def convert(bars):
    return [Bar(b.date.isoformat(),b.open,b.high,b.low,b.close,safe_number(b.volume),safe_number(b.average)) for b in bars]

async def connect():
    async with connection_lock:
        if not ib.isConnected():
            await ib.connectAsync(os.getenv('IB_HOST','127.0.0.1'),int(os.getenv('IB_PORT','7497')),clientId=int(os.getenv('IB_CLIENT_ID','42')),readonly=True,fetchFields=StartupFetch(0),timeout=8)
        status.update(connected=True,message='IBKR conectado; comprobando datos')

async def details(cfg):
    key=('contract',cfg['conid'],cfg['exchange'])
    if key in cache:return cache[key]
    rows=await asyncio.wait_for(ib.reqContractDetailsAsync(Contract(conId=cfg['conid'],exchange=cfg['exchange'])),20)
    if len(rows)!=1:raise ValueError('Contrato ambiguo o no disponible')
    d=rows[0];c=d.contract;primary=c.primaryExchange or c.exchange
    tz=TZ_ALIASES.get(d.timeZoneId,d.timeZoneId)
    if c.conId!=cfg['conid'] or cfg['exchange'] not in d.validExchanges.split(','):raise ValueError('Bolsa o contrato no coinciden')
    if c.symbol=='CSPX' and cfg['exchange']=='LSEETF' and c.currency=='USD':market='LONDON';tz='Europe/London'
    elif primary in ('NASDAQ','NYSE','ARCA','AMEX','BATS','ISLAND','NYSEARCA','CBOE','CBOEIND') and c.currency=='USD' and tz=='America/New_York':market='US'
    else:raise ValueError('Mercado no compatible: admite CSPX Londres USD y acciones/ETF estadounidenses USD')
    ZoneInfo(tz)
    contract=Contract(conId=c.conId,exchange=cfg['exchange'],secType=c.secType,currency=c.currency)
    meta={'conid':c.conId,'ticker':c.localSymbol or c.symbol,'name':d.longName,'exchange':cfg['exchange'],'primary_exchange':primary,'currency':c.currency,'timezone':tz,'market':market,'min_tick':d.minTick,'premarket':market=='US'}
    cache[key]=(contract,meta);return contract,meta

async def history(contract,size,duration,rth,ttl):
    key=('bars',contract.conId,contract.exchange,size,rth)
    old=cache.get(key)
    if old:
        valid=(int((time.time()-3)/120)==old[2]) if size=='2 mins' else time.monotonic()-old[0]<ttl
        if valid:return old[1]
    result=await ib.reqHistoricalDataAsync(contract,endDateTime='',durationStr=duration,barSizeSetting=size,whatToShow='TRADES',useRTH=rth,formatDate=2,timeout=40)
    if not result:raise ValueError('DATO NO DISPONIBLE: historial IBKR vacío o sin permiso')
    cache[key]=(time.monotonic(),result,int((time.time()-3)/120));return result

def parse_schedule_date(value,tz):
    for fmt in ('%Y%m%d-%H:%M:%S','%Y%m%d %H:%M:%S','%Y%m%d:%H%M'):
        try:return datetime.strptime(value,fmt).replace(tzinfo=ZoneInfo(tz)).astimezone(timezone.utc)
        except ValueError:pass
    raise ValueError('Horario IBKR no reconocido')

async def sessions(contract,meta,now):
    key=('schedule',contract.conId,contract.exchange,now.astimezone(ZoneInfo(meta['timezone'])).date().isoformat())
    if key not in cache:
        schedule=await asyncio.wait_for(ib.reqHistoricalScheduleAsync(contract,10,useRTH=True),20)
        tz=TZ_ALIASES.get(schedule.timeZone,schedule.timeZone)
        cache[key]=[(parse_schedule_date(s.startDateTime,tz),parse_schedule_date(s.endDateTime,tz)) for s in schedule.sessions]
    rows=cache[key];today=now.astimezone(ZoneInfo(meta['timezone'])).date()
    current=[s for s in rows if s[0].astimezone(ZoneInfo(meta['timezone'])).date()==today]
    if not current:raise ValueError('Sin sesión oficial disponible para hoy; mercado cerrado o calendario no disponible')
    session=current[-1];previous=[s for s in rows if s[1]<session[0]]
    if not previous:raise ValueError('Sesión anterior no disponible')
    return session,previous[-1]

async def build(i):
    async with locks[i]:
        cfg=config(i)
        if not cfg['conid']:
            snapshots[i]={'id':i,'configured':False,'config':cfg,'message':'Selecciona un activo'};return
        try:
            await connect();contract,meta=await details(cfg)
            now=datetime.now(timezone.utc);(op,cl),(pop,pcl)=await sessions(contract,meta,now)
            two=convert(await history(contract,'2 mins','2 D',False,100))
            context=convert(await history(contract,'15 mins','10 D',True,600))
            day=await history(contract,'1 day','1 M',True,21600)
            # Only the previous official session. Never use premarket/after-hours for prior levels.
            prior=[b for b in closed(context,900,now) if pop<=dt(b.time) and dt(b.time)+timedelta(minutes=15)<=pcl]
            expected=int((pcl-pop).total_seconds()/900)
            if len(prior)<expected:prior=[]
            prestart=op.astimezone(ZoneInfo(meta['timezone'])).replace(hour=4,minute=0,second=0,microsecond=0).astimezone(timezone.utc)
            pre=[b for b in closed(two,120,now) if prestart<=dt(b.time) and dt(b.time)+timedelta(minutes=2)<=op] if now>=op and meta['market']=='US' else []
            opening=[b for b in closed(context,900,now) if dt(b.time)==op]
            levels=levels_for(meta['market'],prior,pre,opening,op.isoformat())
            refs=[];refmeta=None;ref_error=None
            if cfg.get('reference'):
                try:
                    rc,refmeta=await details(cfg['reference']);refs=convert(await history(rc,'2 mins','2 D',False,100))
                except Exception as exc:ref_error=str(exc)
            result=analyze(two,levels,now,op.isoformat(),cl.isoformat(),cfg['tolerance'],context,refs,prior[-1].close if prior else None)
            current=op<=now<cl;last=dt(result['last_bar']) if result['last_bar'] else None
            fresh=current and last is not None and 0<=(now-last).total_seconds()<=180
            ticker=ib.ticker(contract)
            if ticker is None:ticker=ib.reqMktData(contract,'',False,False)
            price=safe_number(ticker.last);ticktime=ticker.time.isoformat() if ticker.time else None
            realtime=ticker.marketDataType==1 and ticktime is not None and (now-dt(ticktime)).total_seconds()<180
            quality='Actual' if fresh and realtime else 'Retrasado o sin cotización actual verificable' if current else 'Fuera de sesión'
            # A detected historical sequence never becomes a current entry without fresh real-time data.
            result['entry_available']=fresh and realtime and result['state']=='retest' and result['selected'].get('entry_time')==result['last_bar']
            result['entry_message']='POSIBLE ENTRADA' if result['entry_available'] else 'NO HAY ENTRADA CONFIRMADA'
            result.update(id=i,configured=True,meta=meta,config=cfg,price=price if realtime else None,price_time=ticktime,quality=quality,
                session_open=op.isoformat(),session_close=cl.isoformat(),updated_at=now.isoformat(),error=None,
                reference_meta=refmeta,reference_error=ref_error,daily=[{'date':str(b.date),'open':b.open,'high':b.high,'low':b.low,'close':b.close,'volume':safe_number(b.volume)} for b in day],
                message='Motor automático activo' if fresh else 'Esperando datos actuales de la sesión')
            with db() as c:
                for e in result['events']:
                    payload={**e,'ticker':meta['ticker'],'exchange':meta['exchange'],'currency':meta['currency'],'conid':meta['conid'],'timezone':meta['timezone'],'tolerance':cfg['tolerance'],'policy':'v1','source':'IBKR','observed_at':now.isoformat(),'historical_reconstruction':not (fresh and realtime) or (now-dt(e['time'])).total_seconds()>180}
                    identity=[i,meta['conid'],meta['exchange'],cfg['tolerance'],e['time'],e['level'],e['state'],'v1']
                    key=hashlib.sha256(json.dumps(identity).encode()).hexdigest()
                    c.execute('INSERT OR IGNORE INTO events VALUES (?,?,?,?)',(key,i,e['time'],json.dumps(payload,allow_nan=False)))
                c.execute('INSERT OR REPLACE INTO snapshots VALUES (?,?)',(i,json.dumps(result,allow_nan=False)))
            snapshots[i]=result
        except Exception as exc:
            old=snapshots.get(i,{})
            snapshots[i]={**old,'id':i,'config':cfg,'configured':True,'entry_available':False,'entry_message':'NO HAY ENTRADA CONFIRMADA','price':None,'error':str(exc),'message':'DATO NO DISPONIBLE','quality':'Sin conexión o datos incompletos'}

async def worker():
    while True:
        status['heartbeat']=datetime.now(timezone.utc).isoformat()
        # Sequential requests reduce IBKR pacing pressure and isolate failed profiles.
        for i in range(1,5):
            await build(i)
        status['connected']=ib.isConnected()
        await asyncio.sleep(15)

@asynccontextmanager
async def lifespan(app):
    if len(TOKEN)<24:raise RuntimeError('Define BR_TOKEN con al menos 24 caracteres; no uses la contraseña de IBKR')
    initialize()
    task=asyncio.create_task(worker())
    yield
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):await task
    ib.disconnect()

app=FastAPI(title='Break & Retest · Roto y retesteo',lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
app.add_middleware(CORSMiddleware,allow_origins=[ORIGIN],allow_methods=['GET','PUT'],allow_headers=['Authorization','Content-Type'])

@app.middleware('http')
async def auth(request:Request,call_next):
    from starlette.responses import JSONResponse
    if request.url.path.startswith('/api/') and request.method!='OPTIONS' and (not TOKEN or not secrets.compare_digest(request.headers.get('Authorization',''),'Bearer '+TOKEN)):
        return JSONResponse({'detail':'Acceso no autorizado'},401)
    response=await call_next(request);response.headers['Cache-Control']='no-store';return response

@app.get('/api/status')
async def get_status():return status

@app.get('/api/profiles')
async def get_profiles():return [snapshots.get(i,{'id':i,'config':config(i),'configured':bool(config(i)['conid']),'message':'Esperando primera lectura'}) for i in range(1,5)]

@app.get('/api/profiles/{i}')
async def get_profile(i:int):
    cfg=config(i);return snapshots.get(i,{'id':i,'config':cfg,'configured':bool(cfg['conid']),'message':'Esperando primera lectura'})

class Reference(BaseModel):
    conid:int=Field(gt=0)
    exchange:str=Field(min_length=1,max_length=30)
class Settings(BaseModel):
    conid:int=Field(gt=0)
    exchange:str=Field(min_length=1,max_length=30)
    tolerance:float=Field(default=0,ge=0,le=100,allow_inf_nan=False)
    reference:Reference|None=None

@app.put('/api/profiles/{i}')
async def put_profile(i:int,value:Settings):
    config(i)
    async with locks[i]:
        cfg=value.model_dump()
        try:
            await connect();await details(cfg)
            if cfg['reference']:await details(cfg['reference'])
        except Exception as exc:raise HTTPException(422,str(exc))
        with db() as c:c.execute('UPDATE profiles SET config=? WHERE id=?',(json.dumps(cfg),i))
        snapshots.pop(i,None)
    asyncio.create_task(build(i))
    return {'saved':True}

@app.get('/api/search')
async def search(q:str):
    if not 1<=len(q.strip())<=40:raise HTTPException(422,'Escribe un ticker')
    try:
        await connect();items=await asyncio.wait_for(ib.reqMatchingSymbolsAsync(q.strip()),15)
        return [{'conid':x.contract.conId,'ticker':x.contract.symbol,'exchange':x.contract.primaryExchange or x.contract.exchange,'currency':x.contract.currency,'name':x.contract.description or x.contract.symbol} for x in (items or []) if x.contract.secType in ('STK','IND')]
    except Exception as exc:raise HTTPException(503,str(exc))

@app.get('/api/profiles/{i}/history')
async def get_history(i:int,offset:int=0,limit:int=100):
    config(i)
    with db() as c:
        rows=c.execute('SELECT payload FROM events WHERE profile=? ORDER BY time DESC,event_id LIMIT ? OFFSET ?',(i,min(max(limit,1),1000),max(offset,0))).fetchall()
        count=c.execute('SELECT count(*) FROM events WHERE profile=?',(i,)).fetchone()[0]
    return {'total':count,'events':[json.loads(r['payload']) for r in rows]}

# Serve only the public dashboard files, never the database, token or source folder.
PUBLIC_FILES={'index.html','app.js','styles.css','break-retest.js','break-retest.css'}
@app.get('/')
async def homepage():
    from fastapi.responses import FileResponse
    return FileResponse(Path(__file__).resolve().parent.parent/'index.html')

@app.get('/{filename}')
async def public_asset(filename:str):
    from fastapi.responses import FileResponse
    if filename not in PUBLIC_FILES:raise HTTPException(404,'No disponible')
    return FileResponse(Path(__file__).resolve().parent.parent/filename)
