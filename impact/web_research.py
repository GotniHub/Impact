"""Recherche bornée et collecte de sources pour Ollama local et Gemini."""
import html
import io
import ipaddress
import os
import socket
from html.parser import HTMLParser
from urllib import request
from urllib.parse import urlsplit
from .providers import AIError, ResearchCancelled, json_request
from .domain import valid_url, value

class PageText(HTMLParser):
    def __init__(self): super().__init__(); self.skip=0; self.parts=[]
    def handle_starttag(self,tag,attrs):
        if tag in {'script','style','noscript'}: self.skip+=1
    def handle_endtag(self,tag):
        if tag in {'script','style','noscript'}: self.skip=max(0,self.skip-1)
    def handle_data(self,data):
        if not self.skip: self.parts.append(data)

def public_url(url):
    if not valid_url(url): raise AIError('URL de source invalide.')
    p=urlsplit(url)
    if p.username or p.password or p.port not in {None,80,443}: raise AIError('URL de source non publique.')
    try: addresses=socket.getaddrinfo(p.hostname,p.port or (443 if p.scheme=='https' else 80),type=socket.SOCK_STREAM)
    except OSError: raise AIError('Site source inaccessible.') from None
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise AIError('Les adresses réseau privées ne sont pas des sources publiques.')
    return url

class PublicRedirect(request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        public_url(newurl)
        return super().redirect_request(req,fp,code,msg,headers,newurl)

def read_public_page(url):
    public_url(url)
    opener=request.build_opener(PublicRedirect())
    with opener.open(request.Request(url,headers={'User-Agent':'IMPACT/1.0 public-company-research'}),timeout=20) as response:
        public_url(response.url)
        raw=response.read(2_000_001)
        content_type=response.headers.get_content_type()
        encoding=response.headers.get_content_charset() or 'utf-8'
    if len(raw)>2_000_000: raise AIError('Page trop volumineuse.')
    if content_type=='application/pdf':
        from pypdf import PdfReader
        reader=PdfReader(io.BytesIO(raw))
        return '\n'.join(p.extract_text() or '' for p in reader.pages[:10])[:7000]
    if content_type not in {'text/html','application/xhtml+xml','text/plain'}: raise AIError('Type de page non pris en charge.')
    text=raw.decode(encoding,errors='replace')
    if content_type!='text/plain':
        parser=PageText(); parser.feed(text); text=' '.join(parser.parts)
    return ' '.join(text.split())[:7000]

def normalize_quote(text): return ' '.join(html.unescape(str(text)).split()).casefold()

def collect(company,campaign,cfg):
    web=cfg['web_provider']
    if web=='direct':
        from .direct_research import collect as direct_collect
        return direct_collect(company,campaign,cfg)
    key=os.environ.get('OLLAMA_API_KEY' if web=='ollama' else 'SERPER_API_KEY','').strip()
    if not key: raise AIError('Clé API de recherche web absente.')
    identity=' '.join(str(x) for x in [company['name'],value(company,'siren'),value(company,'city'),value(company,'country')] if x)
    queries=[identity+' activité produits site officiel',identity+' chiffre affaires résultat exercice annuel',
             identity+' international export implantations industrie distribution',identity+' contact direction commercial export',
             f'{identity} {campaign["name"]} {campaign["edition"]} stand hall exposant']
    results=[]
    for query in queries:
        if web=='ollama':
            result=json_request('https://ollama.com/api/web_search',{'query':query,'max_results':5},
                                {'Authorization':'Bearer '+key},timeout=30,service='Recherche web Ollama')
            results.append(result.get('results',[]))
        else:
            result=json_request('https://google.serper.dev/search',{'q':query,'num':5},
                                {'X-API-KEY':key},timeout=30,service='Recherche web Serper')
            results.append([{'url':x.get('link'),'title':x.get('title',''),'content':x.get('snippet','')} for x in result.get('organic',[])])
    sources={}
    for field in ['website','exhibitor_url']:
        url=value(company,field)
        if valid_url(url): sources[url]={'title':field,'content':''}
    # Répartir les premières pages entre les cinq sujets, au lieu d'une seule requête.
    for index in range(5):
        for group in results:
            if index<len(group):
                item=group[index]; url=item.get('url')
                if valid_url(url) and url not in sources:
                    sources[url]={'title':str(item.get('title',''))[:300],'content':str(item.get('content',''))[:1500]}
    warnings=[]
    for url,item in list(sources.items())[:8]:
        try:
            if web=='ollama':
                fetched=json_request('https://ollama.com/api/web_fetch',{'url':url},
                                    {'Authorization':'Bearer '+key},timeout=30,service='Lecture web Ollama')
                text=str(fetched.get('content',''))[:7000]
            else: text=read_public_page(url)
            if text: item['content']+='\n'+text
        except Exception as exc:
            if isinstance(exc,ResearchCancelled): raise
            if isinstance(exc,AIError):
                # Une page absente ne doit pas être confondue avec un modèle absent.
                # Les refus d'accès et les quotas du service restent bloquants.
                if exc.http_status in {401,403,429}: raise
                if exc.http_status is None and any(s in str(exc) for s in ['Quota','Clé API','Accès au modèle']): raise
            warnings.append('Lecture complète impossible : '+url+' ; extrait de recherche utilisé si disponible.')
    # Limiter le contexte du modèle local et conserver exactement les textes transmis.
    documents=[]; remaining=60000
    for url,item in sources.items():
        content=item['content'][:min(remaining,8000)].strip()
        if content:
            documents.append({'url':url,'title':item['title'],'content':content}); remaining-=len(content)
        if remaining<=0: break
    if not documents: raise AIError('Aucune source web exploitable : enrichissement refusé.')
    return documents,warnings,{'provider':web,'search_calls':len(queries)}

def enforce_quotes(result,documents):
    texts={d['url']:normalize_quote(d['content']) for d in documents}
    removed=0
    for field in ['facts','contacts','triggers','events']:
        accepted=[]
        for item in result.get(field,[]):
            quote=normalize_quote(item.get('quote',''))
            if len(quote)>=12 and quote in texts.get(item.get('source_url'),''): accepted.append(item)
            else: removed+=1
        result[field]=accepted
    return removed
