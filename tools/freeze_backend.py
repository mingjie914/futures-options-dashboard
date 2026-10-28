"""Explicit import of a stable futures backend; never modify its source/runtime."""
from pathlib import Path
import argparse,hashlib,json,os,shutil,sqlite3,socket,uuid

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'futures'


def runtime_root():
    return Path(os.environ.get('DASHBOARD_RUNTIME_ROOT') or ROOT/'runtime').resolve()


def review_hashes(folder):
    return {str(p.relative_to(folder)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(folder.rglob('*')) if p.is_file()}


def publish_frontend(source=None, *, rollback=False):
    """Publish one immutable web build; API code and data pointers stay independent."""
    runtime=runtime_root()
    pointer=runtime/'frontend-release.json'
    previous=json.loads(pointer.read_text('utf-8')) if pointer.exists() else {}
    if rollback:
        release=previous.get('previous')
        if not release:raise RuntimeError('没有可回滚的前端版本')
        (runtime/release).resolve().relative_to(runtime.resolve())
        if not (runtime/release/'index.html').is_file():raise RuntimeError('前端回滚版本不完整')
    else:
        source=Path(source or ROOT/'frontend/dist')
        if not (source/'index.html').is_file():raise RuntimeError('请先构建完整前端')
        hashes=review_hashes(source)
        version=hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest()[:16]
        release='releases/web-'+version
        target=runtime/release
        if not target.exists():
            stage=runtime/'releases'/('web-stage-'+uuid.uuid4().hex)
            stage.parent.mkdir(parents=True,exist_ok=True)
            shutil.copytree(source,stage)
            if review_hashes(stage)!=hashes or review_hashes(source)!=hashes:
                raise RuntimeError('构建产物复制期间变化，未切换前端')
            stage.rename(target)
        elif review_hashes(target)!=hashes:
            raise RuntimeError('已有不可变前端版本校验失败')
    if release==previous.get('release'):return previous
    result={'release':release,'previous':previous.get('release')}
    temporary=pointer.with_suffix('.tmp')
    temporary.write_text(json.dumps(result,indent=2),encoding='utf-8')
    temporary.replace(pointer)
    return result


def refresh_reviews(target):
    # Stage both collections first; retain the previous data for rollback.
    stage=runtime_root()/'releases'/('futures-data-'+uuid.uuid4().hex[:12])
    stage.mkdir(parents=True)
    hashes={}
    names=['fundamental_lab','ceic_lab']
    for name in names:
        src=SOURCE/'runtime'/name
        if not src.is_dir():raise RuntimeError('期货源数据目录缺失：'+name)
        before=review_hashes(src)
        shutil.copytree(src,stage/name)
        if before!=review_hashes(stage/name) or before!=review_hashes(src):
            raise RuntimeError('期货数据在复制期间变化；保留原发布版本，请重试')
        hashes[name]=before
    previous=stage/'previous';previous.mkdir()
    installed=[]
    try:
        for name in names:
            dst=target/name
            for path in [dst,previous/name,stage/name]:
                path.resolve().relative_to(runtime_root())
            if dst.exists():dst.rename(previous/name)
            try:(stage/name).rename(dst)
            except Exception:
                if (previous/name).exists():(previous/name).rename(dst)
                raise
            installed.append(name)
    except Exception:
        for name in reversed(installed):
            (target/name).rename(stage/name)
            if (previous/name).exists():(previous/name).rename(target/name)
        raise
    manifest={'source':str(SOURCE/'runtime'),'previous':str(previous),'hashes':hashes}
    (stage/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    return str(stage/'manifest.json')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--refresh-data',action='store_true',help='同步已保存的期货审阅数据，备份旧版本，不覆盖自选数据库')
    parser.add_argument('--frontend',action='store_true',help='同时冻结完整前端并原子切换指针')
    parser.add_argument('--rollback-frontend',action='store_true',help='只回滚完整前端，不改变 API 或数据')
    args=parser.parse_args()
    for port in [8790,8794,8795,8796]:
        with socket.socket() as sock:
            if sock.connect_ex(('127.0.0.1',port))==0:
                raise SystemExit('请先正常停止统一终端，再发布期货版本；不会停止现有进程')
    if args.rollback_frontend:
        print(json.dumps(publish_frontend(rollback=True)))
        return
    sources=sorted((SOURCE/'backend').glob('*.py'))
    if not sources:raise SystemExit('仓库内期货源码缺失：'+str(SOURCE/'backend'))
    content={p.name:p.read_bytes() for p in sources}
    hashes={k:hashlib.sha256(v).hexdigest() for k,v in content.items()}
    version=hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest()[:16]
    runtime=runtime_root()
    release=runtime/'releases'/('futures-'+version)
    (release/'backend').mkdir(parents=True,exist_ok=True)
    for name,raw in content.items():(release/'backend'/name).write_bytes(raw)
    if any(hashlib.sha256(p.read_bytes()).hexdigest()!=hashes[p.name] for p in sources):raise RuntimeError('期货源在复制期间变化，请重试')
    target=runtime/'futures'
    target.mkdir(exist_ok=True)
    data_manifest=refresh_reviews(target) if args.refresh_data else None
    # Evidence directories remain isolated and keep their original capability flags.
    for name in ['fundamental_lab','ceic_lab']:
        src=SOURCE/'runtime'/name;dst=target/name
        if src.exists() and not dst.exists():shutil.copytree(src,dst)
    src=SOURCE/'runtime/workbench.sqlite';dst=target/'workbench.sqlite'
    if src.exists() and not dst.exists():
        with sqlite3.connect(src.as_uri()+'?mode=ro',uri=True) as source,sqlite3.connect(dst) as dest:source.backup(dest)
    manifest={'source':'futures','path_base':'runtime_root','release':release.relative_to(runtime).as_posix(),
              'runtime':target.relative_to(runtime).as_posix(),'hashes':hashes,'version':version}
    if not data_manifest and (runtime/'futures-release.json').is_file():
        data_manifest=json.loads((runtime/'futures-release.json').read_text('utf-8')).get('data_manifest')
    if data_manifest:manifest['data_manifest']=data_manifest
    (runtime/'futures-release.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    if args.frontend:publish_frontend()
    print(json.dumps({'version':version,'files':len(hashes)}))


if __name__=='__main__':main()
