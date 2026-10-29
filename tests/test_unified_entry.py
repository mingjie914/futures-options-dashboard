import importlib.util
import json
import sys
from pathlib import Path
import pytest


ROOT=Path(__file__).resolve().parents[1]


def test_gateway_keeps_old_lazy_assets_and_independent_seat_contract(tmp_path,monkeypatch):
    import httpx
    from fastapi.testclient import TestClient
    for version in ['old','new']:
        folder=tmp_path/f'releases/web-{version}';(folder/'assets').mkdir(parents=True)
        (folder/'index.html').write_text(version)
        (folder/'assets'/f'{version}.js').write_text(version+' module')
    (tmp_path/'frontend-release.json').write_text(json.dumps({'release':'releases/web-new'}))
    monkeypatch.setenv('DASHBOARD_RUNTIME_ROOT',str(tmp_path))
    monkeypatch.setenv('DASHBOARD_PUBLIC_READONLY','1')
    gateway=load_script('merged_gateway_test',ROOT/'backend/gateway.py')
    seen=[]
    def upstream(request):
        seen.append(str(request.url))
        if request.headers.get('host') == 'mingjiec.com':
            assert request.headers['origin'] == 'https://mingjiec.com'
        if request.url.path=='/api/seats/v1/views/unavailable':
            raise httpx.ConnectError('seat API offline',request=request)
        return httpx.Response(200,json={'batch':16},headers={'x-data-version':'seat16','etag':'"seat16"'})
    with TestClient(gateway.app) as client:
        gateway.app.state.client=httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        for module in ['seats','futures','options']:
            assert client.get('/'+module+'/').text=='new'
            assert client.get('/'+module+'/',headers={'sec-fetch-site':'cross-site'}).text=='new'
        # A pointer swap does not alter this running gateway's pinned HTML.
        (tmp_path/'frontend-release.json').write_text(json.dumps({'release':'releases/web-old'}))
        assert client.get('/seats/').text=='new'
        assert client.get('/assets/old.js').text=='old module'
        assert client.get('/assets/new.js').text=='new module'
        for path in ['/assets/missing.js','/seats/missing.js','/seatsbad','/api/unknown/v1/meta']:
            assert client.get(path).status_code==404
        result=client.get('/api/seats/v1/meta/options?product_code=RB.SHFE')
        assert result.json()=={'batch':16} and result.headers['x-data-version']=='seat16'
        assert seen==['http://127.0.0.1:8796/api/seats/v1/meta/options?product_code=RB.SHFE']
        assert client.get('/api/futures/v1/health',headers={'Host':'mingjiec.com','Origin':'https://mingjiec.com'}).status_code==200
        assert client.get('/api/futures/v1/health',headers={'Host':'mingjiec.com','Origin':'https://other.example'}).status_code==403
        assert client.post('/api/seats/v1/meta/options').status_code==403
        assert client.get('/api/seats/v1/meta/options',headers={'origin':'https://other.test'}).status_code==403
        assert client.get('/api/seats/v1/meta/options',headers={'sec-fetch-site':'cross-site'}).status_code==403
        assert client.get('/api/seats/v1/views/unavailable').status_code==503
        assert client.get('/api/options/v1/snapshot').status_code==200
        assert client.get('/api/futures/v1/products').status_code==200


def test_frontend_release_rolls_back_all_modules_without_data_changes(tmp_path,monkeypatch):
    publish=load_script('frontend_release_test',ROOT/'tools/freeze_backend.py')
    runtime=tmp_path/'runtime';source=tmp_path/'dist';source.mkdir()
    monkeypatch.setattr(publish,'runtime_root',lambda:runtime)
    (source/'index.html').write_text('old shell')
    (source/'modules.js').write_text('old seats futures options')
    first=publish.publish_frontend(source)
    (source/'index.html').write_text('new shell')
    (source/'modules.js').write_text('new seats futures options')
    second=publish.publish_frontend(source)
    assert first['release']!=second['release'] and second['previous']==first['release']
    assert (runtime/first['release']/'modules.js').read_text()=='old seats futures options'
    rolled=publish.publish_frontend(rollback=True)
    assert rolled['release']==first['release'] and rolled['previous']==second['release']
    assert publish.publish_frontend(rollback=True)['release']==second['release']
    assert list(runtime.glob('*release.json'))==[runtime/'frontend-release.json']
    # A failed copy/verification cannot switch the live pointer.
    (source/'modules.js').write_text('incomplete')
    original=publish.review_hashes
    monkeypatch.setattr(publish,'review_hashes',lambda folder: {'broken':'value'} if 'web-stage-' in str(folder) else original(folder))
    with pytest.raises(RuntimeError):publish.publish_frontend(source)
    assert json.loads((runtime/'frontend-release.json').read_text())['release']==second['release']


def load_script(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_repeat_launch_reuses_service_without_starting_children(monkeypatch,capsys):
    launch=load_script('unified_start',ROOT/'start.py')
    monkeypatch.setattr(launch,'entry_ready',lambda:True)
    monkeypatch.setattr(sys,'argv',['start.py','--module','futures'])
    def forbidden(*args,**kwargs):
        pytest.fail('重复启动不应创建进程')
    monkeypatch.setattr(launch.subprocess,'Popen',forbidden)
    launch.main()
    assert 'http://127.0.0.1:8790/futures/' in capsys.readouterr().out


def test_local_data_configuration_survives_restart_and_env_wins(tmp_path,monkeypatch):
    launch=load_script('configured_start',ROOT/'start.py')
    monkeypatch.setattr(launch,'ROOT',tmp_path)
    monkeypatch.delenv('FUTURES_DATA_ROOT',raising=False)
    (tmp_path/'local-settings.json').write_text(json.dumps({'FUTURES_DATA_ROOT':'data/daily'}))
    assert launch.local_environment(tmp_path)['FUTURES_DATA_ROOT']==str(tmp_path/'data/daily')
    monkeypatch.setenv('FUTURES_DATA_ROOT','explicit-mount')
    assert launch.local_environment(tmp_path)['FUTURES_DATA_ROOT']=='explicit-mount'


def test_data_publication_preserves_user_state_and_previous_data(tmp_path,monkeypatch):
    publish=load_script('freeze_for_test',ROOT/'tools/freeze_backend.py')
    source=tmp_path/'source';target=tmp_path/'terminal/runtime/futures'
    monkeypatch.setattr(publish,'SOURCE',source)
    monkeypatch.setattr(publish,'ROOT',tmp_path/'terminal')
    for name in ['fundamental_lab','ceic_lab']:
        (source/'runtime'/name).mkdir(parents=True)
        (source/'runtime'/name/'current.json').write_text('new',encoding='utf-8')
        (target/name).mkdir(parents=True)
        (target/name/'current.json').write_text('old',encoding='utf-8')
    user_state=target/'workbench.sqlite'
    user_state.write_bytes(b'untouched user state')
    manifest=json.loads(Path(publish.refresh_reviews(target)).read_text('utf-8'))
    for name in ['fundamental_lab','ceic_lab']:
        assert (target/name/'current.json').read_text()=='new'
        assert (Path(manifest['previous'])/name/'current.json').read_text()=='old'
        assert (source/'runtime'/name/'current.json').read_text()=='new'
    assert user_state.read_bytes()==b'untouched user state'


def test_data_source_change_does_not_replace_current_data(tmp_path,monkeypatch):
    publish=load_script('freeze_changing_source',ROOT/'tools/freeze_backend.py')
    source=tmp_path/'source';target=tmp_path/'terminal/runtime/futures'
    monkeypatch.setattr(publish,'SOURCE',source)
    monkeypatch.setattr(publish,'ROOT',tmp_path/'terminal')
    (source/'runtime/fundamental_lab').mkdir(parents=True)
    (source/'runtime/fundamental_lab/current.json').write_text('new')
    (target/'fundamental_lab').mkdir(parents=True)
    (target/'fundamental_lab/current.json').write_text('old')
    real_hashes=publish.review_hashes
    calls=[]
    def changing(folder):
        value=real_hashes(folder)
        if folder==source/'runtime/fundamental_lab':
            calls.append(folder)
            if len(calls)>1:value['current.json']='changed-during-copy'
        return value
    monkeypatch.setattr(publish,'review_hashes',changing)
    with pytest.raises(RuntimeError,match='复制期间变化'):
        publish.refresh_reviews(target)
    assert (target/'fundamental_lab/current.json').read_text()=='old'


def test_backend_release_can_live_outside_checkout_and_be_moved(tmp_path,monkeypatch):
    publish=load_script('freeze_external_runtime',ROOT/'tools/freeze_backend.py')
    source=tmp_path/'source';(source/'backend').mkdir(parents=True)
    (source/'backend/api.py').write_text('app = None\n')
    storage=tmp_path/'mounted-volume'
    monkeypatch.setattr(publish,'ROOT',tmp_path/'checkout')
    monkeypatch.setattr(publish,'SOURCE',source)
    monkeypatch.setenv('DASHBOARD_RUNTIME_ROOT',str(storage))
    monkeypatch.setattr(sys,'argv',['freeze_backend.py'])
    class FreePort:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def connect_ex(self,*args):return 1
    monkeypatch.setattr(publish.socket,'socket',lambda:FreePort())
    publish.main()
    manifest=json.loads((storage/'futures-release.json').read_text('utf-8'))
    assert manifest['path_base']=='runtime_root'
    moved=tmp_path/'other-volume';storage.rename(moved)
    assert (moved/manifest['release']/'backend/api.py').read_text()=='app = None\n'
    assert (moved/manifest['runtime']).is_dir()
