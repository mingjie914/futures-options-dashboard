"""Optional preview: one upstream subscription, bounded latest state, local bootstrap."""
import asyncio
import json
import time
from contextlib import suppress
from urllib.parse import urlsplit
from fastapi import WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake


def merge_packet(state, packet):
    if packet['type']=='snapshot':
        state['catalog']=packet['catalog'];state['quotes']={};state['intraday']={}
    updates=packet.get('intraday_all') or {}
    if (latest:=packet.get('intraday')) is not None:updates[latest['series']]=latest
    for name,latest in updates.items():
        previous=state['intraday'].get(name)
        if not previous or previous['trade_date']!=latest['trade_date']:previous={}
        points={p['time']:p for p in previous.get('points',[])}
        points.update((p['time'],p) for p in latest['points'])
        state['intraday'][name]={'series':name,**previous,**latest,'points':sorted(points.values(),key=lambda p:p['time'])}
    for code,quote in packet.get('quotes',{}).items():
        if code not in state['catalog']['metadata']:continue
        if quote is None:state['quotes'].pop(code,None)
        else:state['quotes'].setdefault(code,{}).update(quote)
    state.update(epoch=packet['epoch'],version=packet['version'],connected=True)
    if packet.get('health'):state.update(health=packet['health'],received=time.monotonic())


def enable(app, base, origin='http://127.0.0.1:8799'):
    state=dict(catalog={'codes':[],'metadata':{},'revision':None},quotes={},intraday={},health={},epoch='waiting',version=-1,connected=False,received=0)
    app.state.intraday=state
    hubs={}
    app.state.intraday_hubs=hubs

    def snapshot(current, series=None):
        health={**current['health']}
        if health.get('receiver_age_seconds') is not None:
            health['receiver_age_seconds']+=max(0,time.monotonic()-current['received'])
        return json.dumps(dict(type='snapshot',epoch=current['epoch'],version=current['version'],catalog=current['catalog'],
            quotes=current['quotes'],
            intraday=current['intraday'].get(series),health=health),separators=(',',':'))

    async def relay(hub, subscription):
        code=1013
        try:
            async with connect(base.replace('http','ws',1).rstrip('/')+'/api/realtime/v1/stream',origin=origin,max_size=2**21) as upstream:
                await upstream.send(json.dumps(subscription))
                async for raw in upstream:
                    packet=json.loads(raw)
                    if packet['type']!='error':merge_packet(hub['state'],packet)
                    for queue in hub['queues']:
                        if queue.full():
                            while not queue.empty():queue.get_nowait()
                            queue.put_nowait(snapshot(hub['state'],subscription.get('series')))
                        else:queue.put_nowait(raw)
        except ConnectionClosed as exc:code=1008 if exc.rcvd and exc.rcvd.code==1008 else 1013
        except (OSError,ValueError,KeyError,InvalidHandshake):pass
        finally:
            hub['closed']=True
            for queue in hub['queues']:
                while not queue.empty():queue.get_nowait()
                queue.put_nowait((code,))

    @app.websocket('/api/realtime/v1/stream')
    async def stream(ws: WebSocket):
        if ws.headers.get('origin') and urlsplit(ws.headers['origin']).netloc!=ws.headers.get('host'):
            await ws.close(code=1008);return
        await ws.accept()
        try:
            subscription=await asyncio.wait_for(ws.receive_json(),10)
            while True:
                if subscription.get('type')!='subscribe' or subscription.get('mirror'):
                    await ws.close(code=1008);return
                subscription={k:subscription[k] for k in ('type','codes','fields','kinds','series','token') if k in subscription}
                for key in ('codes','fields','kinds'):
                    if key in subscription:subscription[key]=sorted(set(subscription[key]))
                key=json.dumps(subscription,sort_keys=True,separators=(',',':'))
                hub=hubs.get(key)
                if not hub or hub['closed']:
                    hub=dict(state=dict(state,quotes={},intraday={},version=-1),queues=set(),closed=False)
                    hubs[key]=hub;hub['task']=asyncio.create_task(relay(hub,subscription))
                queue=asyncio.Queue(maxsize=32);hub['queues'].add(queue)
                if hub['state']['version']>=0:queue.put_nowait(snapshot(hub['state'],subscription.get('series')))
                async def send():
                    try:
                        while True:
                            raw=await queue.get()
                            if isinstance(raw,tuple):await ws.close(code=raw[0]);return
                            await asyncio.wait_for(ws.send_text(raw),10)
                    except (TimeoutError,WebSocketDisconnect):
                        with suppress(RuntimeError):await ws.close(code=1013)
                sender=asyncio.create_task(send())
                try:subscription=await ws.receive_json()
                finally:
                    sender.cancel()
                    with suppress(asyncio.CancelledError,WebSocketDisconnect,RuntimeError,TimeoutError):await sender
                    hub['queues'].discard(queue)
                    if not hub['queues']:
                        if hubs.get(key) is hub:hubs.pop(key)
                        hub['task'].cancel()
                        with suppress(asyncio.CancelledError):await hub['task']
        except (WebSocketDisconnect,ValueError,TypeError,TimeoutError):pass

    @app.get('/api/intraday/v1/bootstrap')
    async def bootstrap(series: str=''):
        catalog=state['catalog']
        health={**state['health']}
        age=health.get('receiver_age_seconds')
        if age is not None:health['receiver_age_seconds']=age+max(0,time.monotonic()-state['received'])
        if not state['connected']:health['source_status']='waiting_for_receiver'
        quotes={code:quote for code,quote in state['quotes'].items() if catalog['metadata'][code].get('kind')!='option' or catalog['metadata'][code].get('series')==series}
        packet=dict(type='snapshot',epoch=state['epoch'],version=state['version'],catalog=catalog,quotes=quotes,intraday=state['intraday'].get(series),health=health,connection='connected' if state['connected'] else 'disconnected' if catalog['codes'] else 'connecting')
        return JSONResponse(packet,headers={'Cache-Control':'no-store'})


    async def collect():
        delay=1
        while True:
            try:
                async with connect(base.replace('http','ws',1).rstrip('/')+'/api/realtime/v1/stream',origin=origin,max_size=2**21) as ws:
                    await ws.send(json.dumps({'type':'subscribe','mirror':True,'kinds':['index','future','option'],'fields':['last','prev_close','iv','oi']}))
                    async for raw in ws:
                        packet=json.loads(raw)
                        merge_packet(state,packet)
                        delay=1
            except (OSError,ValueError,KeyError,ConnectionClosed,InvalidHandshake):pass
            state['connected']=False
            await asyncio.sleep(delay);delay=min(delay*2,15)
    return collect
