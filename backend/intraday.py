"""Optional preview: one upstream subscription, bounded latest state, local bootstrap."""
import asyncio
import json
import time
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake


def enable(app, base):
    state=dict(catalog={'codes':[],'metadata':{},'revision':None},quotes={},health={},epoch='waiting',version=-1,connected=False,received=0)
    app.state.intraday=state

    @app.get('/api/intraday/v1/bootstrap')
    async def bootstrap(view:str='basis'):
        if view not in ('basis','options'):raise HTTPException(400,'未知页面')
        catalog=state['catalog'];kinds={'index','future' if view=='basis' else 'option'}
        wanted=[c for c in catalog['codes'] if catalog['metadata'].get(c,{}).get('kind') in kinds]
        quotes={c:state['quotes'][c] for c in wanted if c in state['quotes']}
        health={**state['health']}
        age=health.get('receiver_age_seconds')
        if age is not None:health['receiver_age_seconds']=age+max(0,time.monotonic()-state['received'])
        if not state['connected']:health['source_status']='waiting_for_receiver'
        packet=dict(type='snapshot',epoch=state['epoch'],version=state['version'],catalog=catalog,quotes=quotes,health=health,connection='connected' if state['connected'] else 'disconnected' if catalog['codes'] else 'connecting',
                    metadata={c:catalog['metadata'].get(c,{}) for c in wanted})
        return JSONResponse(packet,headers={'Cache-Control':'no-store'})


    async def collect():
        delay=1
        while True:
            try:
                async with connect(base.replace('http','ws',1).rstrip('/')+'/api/realtime/v1/stream',origin='http://127.0.0.1:8799') as ws:
                    await ws.send(json.dumps({'type':'subscribe','kinds':['index','future','option'],'fields':['last','prev_close','bid','ask','iv','oi']}))
                    async for raw in ws:
                        packet=json.loads(raw)
                        if packet['type']=='snapshot':
                            state['catalog']=packet['catalog'];state['quotes']={}
                        for code,quote in packet.get('quotes',{}).items():
                            if code not in state['catalog']['metadata']:continue
                            if quote is None:state['quotes'].pop(code,None)
                            else:state['quotes'].setdefault(code,{}).update(quote)
                        state.update(epoch=packet['epoch'],version=packet['version'],health=packet.get('health',state['health']),received=time.monotonic(),connected=True)
                        delay=1
            except (OSError,ValueError,KeyError,ConnectionClosed,InvalidHandshake):pass
            state['connected']=False
            await asyncio.sleep(delay);delay=min(delay*2,15)
    return collect
