"""Local same-origin entry. Only fixed internal destinations; no business logic."""
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit
import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import Response, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

ROOT=Path(__file__).resolve().parents[1]
TARGETS={'futures':'http://127.0.0.1:8795','options':'http://127.0.0.1:8794'}


@asynccontextmanager
async def lifespan(app):
    async with httpx.AsyncClient(timeout=110,trust_env=False) as client:
        app.state.client=client
        yield


app=FastAPI(lifespan=lifespan,docs_url=None,redoc_url=None)


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
    suffix='/api/'+path if module=='futures' else '/api/options/v1/'+path
    target=TARGETS[module]+suffix
    if request.url.query:target+='?'+request.url.query
    body=await request.body()
    if len(body)>1024*1024:raise HTTPException(413,'请求过大')
    headers={k:v for k,v in request.headers.items() if k.lower() in ('host','origin','content-type','if-none-match','sec-fetch-site')}
    try:
        r=await request.app.state.client.request(request.method,target,content=body,headers=headers)
        return Response(r.content,status_code=r.status_code,headers={k:v for k,v in r.headers.items() if k.lower() in ('content-type','etag','content-disposition','cache-control')})
    except httpx.HTTPError:
        return JSONResponse({'detail':module+'模块暂时不可用；其他模块可继续使用'},status_code=503)


@app.api_route('/api/{path:path}',methods=['GET','POST','PUT','PATCH','DELETE'])
def unknown(path:str):raise HTTPException(404,'未知API')


dist=ROOT/'frontend/dist'
if (dist/'assets').exists():app.mount('/assets',StaticFiles(directory=dist/'assets'),name='assets')


@app.get('/{path:path}')
def index(path:str):
    if path and not path.startswith(('futures','options')):raise HTTPException(404,'文件不存在')
    if not (dist/'index.html').exists():raise HTTPException(503,'请先完成前端构建')
    return FileResponse(dist/'index.html',headers={'Cache-Control':'no-cache'})
