import asyncio
import json
import time
from contextlib import asynccontextmanager
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend import intraday


CATALOG={'codes':['I','F','O'],'revision':'one','metadata':{'I':{'kind':'index'},'F':{'kind':'future'},'O':{'kind':'option'}}}


def test_bootstrap_filters_views_preserves_source_time_and_ages_health():
    app=FastAPI();intraday.enable(app,'http://127.0.0.1:18808');state=app.state.intraday
    with TestClient(app) as client:
        cold=client.get('/api/intraday/v1/bootstrap')
        assert cold.json()['quotes']=={} and cold.json()['connection']=='connecting'
        assert cold.headers['cache-control']=='no-store'
        state.update(catalog=CATALOG,quotes={'I':{'last':10,'acquired_at':'original','source_time':None},'F':{'last':0},'O':{'iv':0.35,'oi':None}},
                     health={'receiver_age_seconds':2,'source_status':'ok','source_mode':'wind'},received=time.monotonic()-10,epoch='a',version=7,connected=True)
        data=client.get('/api/intraday/v1/bootstrap').json()
        assert set(data['quotes'])=={'I','F'}
        assert data['quotes']['I']['acquired_at']=='original'
        assert data['quotes']['F']['last']==0
        assert data['health']['receiver_age_seconds']>=12
        assert data['epoch']=='a' and data['version']==7
        options=client.get('/api/intraday/v1/bootstrap?view=options').json()
        assert set(options['quotes'])=={'I','O'} and options['quotes']['O']['iv']==0.35
        state['connected']=False
        stale=client.get('/api/intraday/v1/bootstrap').json()
        assert stale['connection']=='disconnected' and stale['quotes']==data['quotes']
        assert stale['health']['source_status']=='waiting_for_receiver'
        assert client.get('/api/intraday/v1/bootstrap?view=unknown').status_code==400


def test_single_readonly_mirror_merges_deltas_and_retains_values_on_disconnect(monkeypatch):
    app=FastAPI();collect=intraday.enable(app,'http://127.0.0.1:18808');sent=[]
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
        yield Socket()
    async def stop(_):raise asyncio.CancelledError
    monkeypatch.setattr(intraday,'connect',connect);monkeypatch.setattr(intraday.asyncio,'sleep',stop)
    with pytest.raises(asyncio.CancelledError):asyncio.run(collect())
    assert sent==[{'type':'subscribe','kinds':['index','future','option'],'fields':['last','prev_close','bid','ask','iv','oi']}]
    state=app.state.intraday
    assert state['quotes']=={'I':{'last':2,'acquired_at':'original'}} and not state['connected']


def test_real_websocket_disconnect_retains_ram_bootstrap():
    from websockets.asyncio.server import serve
    async def run():
        async def source(ws):
            await ws.recv()
            await ws.send(json.dumps(dict(type='snapshot',catalog=CATALOG,epoch='a',version=1,
                health={'source_mode':'wind','source_status':'ok','receiver_age_seconds':0},quotes={'I':{'last':10,'acquired_at':'original'}})))
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
                    data=(await client.get('/api/intraday/v1/bootstrap')).json()
                assert data['connection']=='disconnected' and data['quotes']['I']=={'last':10,'acquired_at':'original'}
                assert data['health']['source_status']=='waiting_for_receiver'
            finally:
                task.cancel();await asyncio.gather(task,return_exceptions=True)
    asyncio.run(run())
