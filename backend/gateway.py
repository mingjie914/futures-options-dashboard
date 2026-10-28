"""Local same-origin entry. Only fixed internal destinations; no business logic."""
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit
import httpx
import os
import json
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import Response, JSONResponse, FileResponse
from futures.backend.transport import ResearchHTTP

ROOT=Path(__file__).resolve().parents[1]
TARGETS={
    'seats':os.environ.get('DASHBOARD_SEATS_API','http://127.0.0.1:8796').rstrip('/'),
    'futures':os.environ.get('DASHBOARD_FUTURES_API','http://127.0.0.1:8795').rstrip('/'),
    'options':os.environ.get('DASHBOARD_OPTIONS_API','http://127.0.0.1:8794').rstrip('/'),
}


@asynccontextmanager
async def lifespan(app):
    async with httpx.AsyncClient(timeout=110,trust_env=False) as client:
        app.state.client=client
        yield


app=FastAPI(lifespan=lifespan,docs_url=None,redoc_url=None)
app.add_middleware(ResearchHTTP)


@app.middleware('http')
async def same_origin(request,call_next):
    origin=request.headers.get('origin')
    if (origin and urlsplit(origin).netloc!=request.headers.get('host')) or request.headers.get('sec-fetch-site')=='cross-site':
        return JSONResponse({'detail':'仅允许同源访问'},status_code=403)
    return await call_next(request)


@app.get('/health')
def health():return {'status':'ok','service':'unified-local-entry'}


@app.api_route('/api/{module}/v1/{path:path}',methods=['GET','POST','PUT','PATCH','DELETE'])
async def proxy(module:str,path:str,request:Request):
    if module not in TARGETS:raise HTTPException(404,'未知模块')
    if os.environ.get('DASHBOARD_PUBLIC_READONLY')=='1':
        if module=='futures' and path=='watchlists' and request.method=='GET':
            return JSONResponse({'items':[],'read_only':True})
        if request.method!='GET':
            raise HTTPException(403,'公开看板仅提供行情与统计查询')
    suffix=('/health/live' if path=='health' else '/api/seats/v1/'+path) if module=='seats' else '/api/'+path if module=='futures' else '/api/options/v1/'+path
    target=TARGETS[module]+suffix
    if request.url.query:target+='?'+request.url.query
    body=await request.body()
    if len(body)>1024*1024:raise HTTPException(413,'请求过大')
    headers={k:v for k,v in request.headers.items() if k.lower() in ('host','origin','content-type','if-none-match','a-im','sec-fetch-site')}
    headers['accept-encoding']='identity'  # httpx returns decoded content; compress once at this boundary.
    try:
        r=await request.app.state.client.request(request.method,target,content=body,headers=headers)
        return Response(r.content,status_code=r.status_code,headers={k:v for k,v in r.headers.items() if k.lower() in ('content-type','etag','cache-control','x-snapshot-state','vary','im','delta-base','x-data-version','x-request-id')})
    except httpx.HTTPError:
        return JSONResponse({'detail':module+'模块暂时不可用；其他模块可继续使用'},status_code=503)


@app.api_route('/api/{path:path}',methods=['GET','POST','PUT','PATCH','DELETE'])
def unknown(path:str):raise HTTPException(404,'未知API')


runtime=Path(os.environ.get('DASHBOARD_RUNTIME_ROOT') or ROOT/'runtime').resolve()
pointer=runtime/'frontend-release.json'
dist=(runtime/json.loads(pointer.read_text('utf-8'))['release']).resolve() if pointer.exists() else ROOT/'frontend/dist'
if pointer.exists():dist.relative_to(runtime)
# Retained hashed assets keep already-open pages usable after a release/restart.
asset_roots=[dist/'assets',*(p/'assets' for p in sorted((runtime/'releases').glob('web-*')) if not p.name.startswith('web-stage-'))]


@app.get('/assets/{path:path}')
def asset(path:str):
    for folder in asset_roots:
        file=(folder/path).resolve()
        if file.is_relative_to(folder.resolve()) and file.is_file():
            return FileResponse(file,headers={'Cache-Control':'public, max-age=31536000, immutable'})
    raise HTTPException(404,'文件不存在')


@app.get('/inter-OFL.txt')
def font_license():return FileResponse(dist/'inter-OFL.txt')


@app.get('/{path:path}')
def index(path:str):
    if path and path.rstrip('/') not in TARGETS:raise HTTPException(404,'文件不存在')
    if not (dist/'index.html').exists():raise HTTPException(503,'请先完成前端构建')
    return FileResponse(dist/'index.html',headers={'Cache-Control':'no-cache'})
