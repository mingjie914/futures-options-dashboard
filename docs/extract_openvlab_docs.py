import pathlib,json,re
from bs4 import BeautifulSoup
p=pathlib.Path(__file__).parent/'openvlab-reference'
s=BeautifulSoup((p/'teaching.html').read_text(encoding='utf-8'),'html.parser')
docs=[]
for tag in s.find_all('script'):
    raw=tag.string or ''
    if raw.startswith('self.__next_f.push('):
        try: a=json.loads(raw[len('self.__next_f.push('):-1])
        except ValueError: continue
        if len(a)>1 and isinstance(a[1],str) and len(a[1])>200 and ('## ' in a[1]) and not a[1].startswith(('0:', '1:')):
            docs.append(a[1])
for i,t in enumerate(docs):
    (p/f'doc-{i}.md').write_text(t,encoding='utf-8')
    print('DOC',i,'LENGTH',len(t));print(t[:220]);print('\n'.join(x for x in t.splitlines() if x.startswith(('#','- ','  - ')))[:6500])
