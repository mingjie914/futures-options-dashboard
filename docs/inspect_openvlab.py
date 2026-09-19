import requests,json,pathlib
from bs4 import BeautifulSoup
p=pathlib.Path(__file__).parent/'openvlab-reference';p.mkdir(exist_ok=True)
r=requests.get('https://www.openvlab.cn/teaching',timeout=20);r.raise_for_status()
(p/'teaching.html').write_text(r.text,encoding='utf-8')
s=BeautifulSoup(r.text,'html.parser')
print(json.dumps({'scripts':[x.get('src') for x in s.find_all('script') if x.get('src')],'links':[x.get('href') for x in s.find_all('a')],'images':[x.get('src') for x in s.find_all('img')]},ensure_ascii=False))
for url in ['/shared-static/teaching/strategyPageOverview.png','/shared-static/teaching/multiExpiryStrategy.png','/shared-static/teaching/profitLossTable.png','/_next/static/chunks/1hfguic7cv0ho.js','/_next/static/chunks/0cz1d0mv5g_q7.js']:
    q=requests.get('https://www.openvlab.cn'+url,timeout=20);q.raise_for_status();(p/url.split('/')[-1]).write_bytes(q.content);print(url,len(q.content))
