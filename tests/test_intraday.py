import asyncio
import json
import time
from contextlib import asynccontextmanager
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend import intraday


CATALOG={'codes':['I','F','O'],'revision':'one','metadata':{'I':{'kind':'index'},'F':{'kind':'future'},'O':{'kind':'option','series':'MO2611'}}}


def test_same_subscription_shares_upstream_and_rebases_new_visitors(monkeypatch):
    app=FastAPI();intraday.enable(app,'http://receiver','https://mingjiec.com');calls=[];queues=[];closed=[]
    initial=dict(type='snapshot',catalog=CATALOG,epoch='a',version=1,health={'receiver_age_seconds':0},
        quotes={'I':{'last':10},'O':{'iv':.3}},intraday=dict(series='MO2611',trade_date='2026-11-05',revision=1,points=[dict(time='09:30',future_last=10)]))
    @asynccontextmanager
    async def connect(*args,**kwargs):
        queue=asyncio.Queue();queues.append(queue)
        class Source:
            async def send(self,raw):calls.append(json.loads(raw))
            async def __aiter__(self):
                yield json.dumps(initial)
                while True:yield await queue.get()
        try:yield Source()
        finally:closed.append(True)
    monkeypatch.setattr(intraday,'connect',connect)
    sub=dict(type='subscribe',series='MO2611',fields=['last','iv'])
    with TestClient(app) as client:
        with client.websocket_connect('/api/realtime/v1/stream') as one:
            one.send_json(sub);assert one.receive_json()['version']==1
            patch=dict(type='delta',epoch='a',version=2,quotes={'I':{'last':11},'O':None})
            client.portal.call(queues[0].put,json.dumps(patch));assert one.receive_json()==patch
            with client.websocket_connect('/api/realtime/v1/stream') as two:
                two.send_json(dict(sub,fields=['iv','last']))
                cached=two.receive_json()
                assert len(calls)==1 and cached['quotes']=={'I':{'last':11}} and cached['version']==2
                revision=dict(type='intraday',epoch='a',version=3,intraday=dict(series='MO2611',trade_date='2026-11-05',revision=2,points=[dict(time='09:30',future_last=None)]))
                client.portal.call(queues[0].put,json.dumps(revision))
                assert one.receive_json()==two.receive_json()==revision
            # The remaining visitor still owns the upstream; changing series releases it.
            assert not closed
            one.send_json(dict(sub,series='IO2611'))
            assert one.receive_json()['type']=='snapshot'
            assert len(calls)==2 and calls[1]['series']=='IO2611' and len(closed)==1
    assert len(closed)==2 and not app.state.intraday_hubs


def test_cross_origin_websocket_is_rejected_before_opening_upstream(monkeypatch):
    app=FastAPI();intraday.enable(app,'http://receiver')
    monkeypatch.setattr(intraday,'connect',lambda *a,**k:pytest.fail('unexpected upstream'))
    from starlette.websockets import WebSocketDisconnect
    with TestClient(app) as client,pytest.raises(WebSocketDisconnect) as failure:
        with client.websocket_connect('/api/realtime/v1/stream',headers={'origin':'https://foreign.example'}):pass
    assert failure.value.code==1008


def test_slow_visitor_rebases_without_blocking_upstream_and_disconnect_cleans_hub(monkeypatch):
    app=FastAPI();intraday.enable(app,'http://receiver');controls={}
    from starlette.websockets import WebSocket,WebSocketDisconnect
    send_text=WebSocket.send_text
    async def slow_send(ws,raw):
        await controls['release'].wait();await send_text(ws,raw)
    monkeypatch.setattr(WebSocket,'send_text',slow_send)
    @asynccontextmanager
    async def connect(*a,**k):
        controls.update(queue=asyncio.Queue(),release=asyncio.Event())
        class Source:
            async def send(self,raw):pass
            async def __aiter__(self):
                yield json.dumps(dict(type='snapshot',catalog=CATALOG,epoch='a',version=1,quotes={'I':{'last':1}}))
                while True:
                    raw=await controls['queue'].get()
                    if raw is None:raise OSError('upstream offline')
                    yield raw
        yield Source()
    monkeypatch.setattr(intraday,'connect',connect)
    async def feed():
        while not controls:await asyncio.sleep(0)
        for n in range(2,71):
            await controls['queue'].put(json.dumps(dict(type='delta',epoch='a',version=n,quotes={'I':{'last':n}})))
        while next(iter(app.state.intraday_hubs.values()))['state']['version']<70:await asyncio.sleep(0)
        assert max(q.qsize() for q in next(iter(app.state.intraday_hubs.values()))['queues'])<=32
        controls['release'].set()
    with TestClient(app) as client:
        with client.websocket_connect('/api/realtime/v1/stream') as ws:
            ws.send_json({'type':'subscribe'});client.portal.call(feed)
            seen=[]
            while not seen or seen[-1]['version']<70:seen.append(ws.receive_json())
            assert any(p['type']=='snapshot' and p['version']>1 for p in seen)
            assert seen[-1]['quotes']['I']['last']==70
            client.portal.call(controls['queue'].put,None)
            with pytest.raises(WebSocketDisconnect) as failure:ws.receive_json()
            assert failure.value.code==1013
    assert not app.state.intraday_hubs


def test_bootstrap_uses_one_catalog_preserves_source_time_and_ages_health():
    app=FastAPI();intraday.enable(app,'http://127.0.0.1:18808');state=app.state.intraday
    with TestClient(app) as client:
        cold=client.get('/api/intraday/v1/bootstrap')
        assert cold.json()['quotes']=={} and cold.json()['connection']=='connecting'
        assert cold.headers['cache-control']=='no-store'
        state.update(catalog=CATALOG,quotes={'I':{'last':10,'acquired_at':'original','source_time':None},'F':{'last':0},'O':{'iv':0.35,'oi':None}},
                     health={'receiver_age_seconds':2,'source_status':'ok','source_mode':'wind'},received=time.monotonic()-10,epoch='a',version=7,connected=True)
        data=client.get('/api/intraday/v1/bootstrap?series=MO2611').json()
        assert set(data['quotes'])=={'I','F','O'}
        assert set(client.get('/api/intraday/v1/bootstrap?series=IO2611').json()['quotes'])=={'I','F'}
        assert set(client.get('/api/intraday/v1/bootstrap').json()['quotes'])=={'I','F'}
        assert data['catalog']==CATALOG and 'metadata' not in data
        assert data['quotes']['I']['acquired_at']=='original'
        assert data['quotes']['F']['last']==0
        assert data['health']['receiver_age_seconds']>=12
        assert data['epoch']=='a' and data['version']==7
        state['connected']=False
        stale=client.get('/api/intraday/v1/bootstrap?series=MO2611').json()
        assert stale['connection']=='disconnected' and stale['quotes']==data['quotes']
        assert stale['health']['source_status']=='waiting_for_receiver'


def test_single_readonly_mirror_merges_deltas_and_retains_values_on_disconnect(monkeypatch):
    app=FastAPI();collect=intraday.enable(app,'http://127.0.0.1:18808','https://mingjiec.com');sent=[]
    class Socket:
        async def send(self,raw):sent.append(json.loads(raw))
        async def __aiter__(self):
            for packet in [dict(type='snapshot',catalog=CATALOG,quotes={'I':{'last':1,'acquired_at':'original'},'O':{'iv':0.3}},version=1),
                           dict(type='delta',quotes={'I':{'last':2},'O':None,'UNKNOWN':{'last':999}},version=2),
                           dict(type='status',version=2)]:
                yield json.dumps(dict(epoch='a',health={'source_mode':'wind','receiver_age_seconds':0},**packet))
            raise OSError('test disconnect')
    @asynccontextmanager
    async def connect(url,**kwargs):
        assert url=='ws://127.0.0.1:18808/api/realtime/v1/stream'
        assert kwargs['origin']=='https://mingjiec.com'
        yield Socket()
    async def stop(_):raise asyncio.CancelledError
    monkeypatch.setattr(intraday,'connect',connect);monkeypatch.setattr(intraday.asyncio,'sleep',stop)
    with pytest.raises(asyncio.CancelledError):asyncio.run(collect())
    assert sent==[{'type':'subscribe','mirror':True,'kinds':['index','future','option'],'fields':['last','prev_close','iv','oi']}]
    state=app.state.intraday
    assert state['quotes']=={'I':{'last':2,'acquired_at':'original'}} and not state['connected']


def test_real_websocket_disconnect_retains_ram_bootstrap():
    from websockets.asyncio.server import serve
    async def run():
        calls=[]
        async def source(ws):
            calls.append(json.loads(await ws.recv()))
            await ws.send(json.dumps(dict(type='snapshot',catalog=CATALOG,epoch='a',version=1,
                health={'source_mode':'wind','source_status':'ok','receiver_age_seconds':0},quotes={'I':{'last':10,'acquired_at':'original'}},
                intraday_all={name:dict(series=name,trade_date='2026-11-03',revision=1,volume_unit='contracts',points=[dict(time='09:30',future_last=price,future_volume=0)]) for name,price in [('MO2611',6000),('IO2611',4000)]})))
            await ws.send(json.dumps(dict(type='intraday_batch',epoch='a',version=2,intraday_all={'MO2611':dict(series='MO2611',trade_date='2026-11-03',revision=2,points=[dict(time='09:31',future_last=6010,future_volume=1)])})))
            await ws.close()
        async with serve(source,'127.0.0.1',0) as server:
            app=FastAPI();collect=intraday.enable(app,'http://127.0.0.1:'+str(server.sockets[0].getsockname()[1]))
            task=asyncio.create_task(collect())
            try:
                for _ in range(100):
                    if app.state.intraday['quotes'] and not app.state.intraday['connected']:break
                    await asyncio.sleep(.01)
                import httpx
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://local') as client:
                    data=(await client.get('/api/intraday/v1/bootstrap?series=MO2611')).json()
                    other=(await client.get('/api/intraday/v1/bootstrap?series=IO2611')).json()
                assert data['connection']=='disconnected' and data['quotes']['I']=={'last':10,'acquired_at':'original'}
                assert data['health']['source_status']=='waiting_for_receiver'
                assert data['intraday']['volume_unit']=='contracts'
                assert [p['future_volume'] for p in data['intraday']['points']]==[0,1]
                assert other['intraday']['series']=='IO2611' and len(other['intraday']['points'])==1
                assert len(calls)==1 and calls[0]['mirror'] is True and 'series' not in calls[0]
            finally:
                task.cancel();await asyncio.gather(task,return_exceptions=True)
    asyncio.run(run())


def test_intraday_minute_upserts_keep_quotes_and_reset_on_source_day_change(monkeypatch):
    app=FastAPI();collect=intraday.enable(app,'http://source')
    def minutes(day,revision,points):
        return dict(series='MO2611',trade_date=day,revision=revision,future_code='IM2611.CFE',time_basis='observation',points=points)
    class Socket:
        async def send(self,raw):pass
        async def __aiter__(self):
            packets=[dict(type='snapshot',catalog=CATALOG,quotes={'I':{'last':10}},health={'source_mode':'wind','receiver_age_seconds':2},intraday=minutes('2026-11-03',1,[{'time':'09:30','future_last':6000,'atm_iv':.3}])),
                     dict(type='intraday',intraday=minutes('2026-11-03',2,[{'time':'09:31','future_last':6010,'atm_iv':None}])),
                     dict(type='intraday',intraday=minutes('2026-11-03',3,[{'time':'09:31','future_last':6020,'atm_iv':.31}])),
                     dict(type='intraday',intraday=minutes('2026-11-04',4,[{'time':'09:30','future_last':6100,'atm_iv':.32}]))]
            packets[0]['intraday']['price_time_basis']='wind_bar'
            for version,packet in enumerate(packets):
                yield json.dumps(dict(epoch='a',version=version,**packet))
                if version==2:assert app.state.intraday['intraday']['MO2611']['price_time_basis']=='wind_bar'
            raise OSError('disconnected')
    @asynccontextmanager
    async def connect(*args,**kwargs):yield Socket()
    async def stop(_):raise asyncio.CancelledError
    monkeypatch.setattr(intraday,'connect',connect);monkeypatch.setattr(intraday.asyncio,'sleep',stop)
    with pytest.raises(asyncio.CancelledError):asyncio.run(collect())
    state=app.state.intraday
    assert state['quotes']=={'I':{'last':10}}
    assert state['intraday']['MO2611']['trade_date']=='2026-11-04'
    assert 'price_time_basis' not in state['intraday']['MO2611']
    assert state['intraday']['MO2611']['points']==[{'time':'09:30','future_last':6100,'atm_iv':.32}]
    assert state['health']=={'source_mode':'wind','receiver_age_seconds':2}
