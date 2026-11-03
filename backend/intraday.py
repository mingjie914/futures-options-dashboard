"""Optional preview: one upstream subscription, bounded latest state, local bootstrap."""
import asyncio
import json
import time
from fastapi.responses import JSONResponse
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake


def enable(app, base):
    state=dict(catalog={'codes':[],'metadata':{},'revision':None},quotes={},intraday=None,health={},epoch='waiting',version=-1,connected=False,received=0)
    app.state.intraday=state

    @app.get('/api/intraday/v1/bootstrap')
    async def bootstrap(series: str=''):
        catalog=state['catalog']
        health={**state['health']}
        age=health.get('receiver_age_seconds')
        if age is not None:health['receiver_age_seconds']=age+max(0,time.monotonic()-state['received'])
        if not state['connected']:health['source_status']='waiting_for_receiver'
        quotes={code:quote for code,quote in state['quotes'].items() if catalog['metadata'][code].get('kind')!='option' or catalog['metadata'][code].get('series')==series}
        minutes=state['intraday']
        packet=dict(type='snapshot',epoch=state['epoch'],version=state['version'],catalog=catalog,quotes=quotes,intraday=minutes if minutes and minutes['series']==series else None,health=health,connection='connected' if state['connected'] else 'disconnected' if catalog['codes'] else 'connecting')
        return JSONResponse(packet,headers={'Cache-Control':'no-store'})


    async def collect():
        delay=1
        while True:
            try:
                async with connect(base.replace('http','ws',1).rstrip('/')+'/api/realtime/v1/stream',origin='http://127.0.0.1:8799') as ws:
                    await ws.send(json.dumps({'type':'subscribe','kinds':['index','future','option'],'fields':['last','prev_close','iv','oi']}))
                    async for raw in ws:
                        packet=json.loads(raw)
                        if packet['type']=='snapshot':
                            state['catalog']=packet['catalog'];state['quotes']={};state['intraday']=None
                        if (latest:=packet.get('intraday')) is not None:
                            previous=state['intraday']
                            if not previous or (previous['series'],previous['trade_date'])!=(latest['series'],latest['trade_date']):previous={}
                            points={p['time']:p for p in previous.get('points',[])}
                            points.update((p['time'],p) for p in latest['points'])
                            state['intraday']={**previous,**latest,'points':sorted(points.values(),key=lambda p:p['time'])}
                        for code,quote in packet.get('quotes',{}).items():
                            if code not in state['catalog']['metadata']:continue
                            if quote is None:state['quotes'].pop(code,None)
                            else:state['quotes'].setdefault(code,{}).update(quote)
                        state.update(epoch=packet['epoch'],version=packet['version'],connected=True)
                        if packet.get('health'):state.update(health=packet['health'],received=time.monotonic())
                        delay=1
            except (OSError,ValueError,KeyError,ConnectionClosed,InvalidHandshake):pass
            state['connected']=False
            await asyncio.sleep(delay);delay=min(delay*2,15)
    return collect
