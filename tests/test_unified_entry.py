import importlib.util
import json
import sys
from pathlib import Path
import pytest


ROOT=Path(__file__).resolve().parents[1]


def load_script(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_repeat_launch_reuses_service_without_starting_children(monkeypatch,capsys):
    launch=load_script('unified_start',ROOT/'start.py')
    monkeypatch.setattr(launch,'entry_ready',lambda:True)
    monkeypatch.setattr(launch.sys,'argv',['start.py','--module','futures'])
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
