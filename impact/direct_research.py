"""Lecture bornée des sites fournis, sans moteur ni API de recherche externe."""
import hashlib
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib import error, request, robotparser
from urllib.parse import urljoin, urlsplit, urlunsplit

from . import db
from .domain import valid_url, value, domain
from .providers import AIError
from .web_research import PublicRedirect, public_url, PageText

AGENT='IMPACT'
HEADERS={'User-Agent':'IMPACT/1.0 public-company-research'}
LOCK=threading.Lock()
LAST_REQUEST={}
ROBOTS={}


class Links(HTMLParser):
    def __init__(self):
        super().__init__(); self.links=[]; self.href=None; self.label=[]
    def handle_starttag(self,tag,attrs):
        if tag=='a': self.href=dict(attrs).get('href'); self.label=[]
    def handle_data(self,text):
        if self.href: self.label.append(text)
    def handle_endtag(self,tag):
        if tag=='a' and self.href:
            if len(self.links)<300: self.links.append((self.href,' '.join(self.label)))
            self.href=None


class Content(PageText):
    SKIP={'script','style','noscript','nav','svg','select','button'}
    def handle_starttag(self,tag,attrs):
        if tag in self.SKIP: self.skip+=1
    def handle_endtag(self,tag):
        if tag in self.SKIP: self.skip=max(0,self.skip-1)


class PageRedirect(PublicRedirect):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        public_url(newurl)
        if not robots(newurl).can_fetch(AGENT,newurl):
            raise AIError('Lecture directe : redirection refusée par robots.txt.')
        return super().redirect_request(req,fp,code,msg,headers,newurl)


def cached(key):
    try:
        row=db.get('cache',key)
        if datetime.fromisoformat(row['created'])>datetime.now(timezone.utc)-timedelta(hours=24): return row['result']
    except ValueError: pass
    return None


def read(url,limit=2_000_000,guard_redirect=False):
    public_url(url)
    host=urlsplit(url).netloc.casefold()
    # Un seul accès par seconde et par hôte, y compris robots.txt.
    with LOCK:
        pause=max(0,1-(time.monotonic()-LAST_REQUEST.get(host,0)))
        if pause: time.sleep(pause)
        LAST_REQUEST[host]=time.monotonic()
    opener=request.build_opener(PageRedirect() if guard_redirect else PublicRedirect())
    with opener.open(request.Request(url,headers=HEADERS),timeout=12) as response:
        public_url(response.url)
        raw=response.read(limit+1)
        if len(raw)>limit: raise AIError('Page trop volumineuse.')
        return raw,response.headers.get_content_type(),response.headers.get_content_charset(),response.url


def robots(url):
    p=urlsplit(url); root=urlunsplit((p.scheme,p.netloc,'','',''))
    existing=ROBOTS.get(root)
    if existing and time.monotonic()-existing[0]<3600: return existing[1]
    policy=robotparser.RobotFileParser(root+'/robots.txt')
    key='direct-robots-v1:'+hashlib.sha256(root.encode()).hexdigest()
    stored=cached(key)
    if stored is None:
        try:
            raw,_,encoding,_=read(root+'/robots.txt',512_000)
            stored={'lines':raw.decode(encoding or 'utf-8',errors='replace').splitlines()}
        except error.HTTPError as exc:
            if exc.code in {404,410}: stored={'lines':[]}
            else: raise AIError('Lecture directe : robots.txt inaccessible ou accès limité, site ignoré.') from None
        except (OSError,AIError):
            raise AIError('Lecture directe : robots.txt inaccessible, site ignoré.') from None
        db.put('cache',{'key':key,'created':db.now(),'result':stored})
    policy.parse(stored['lines'])
    ROBOTS[root]=(time.monotonic(),policy)
    return policy


def fetch_page(url):
    # Le contrôle public précède toute requête ou lecture de cache.
    public_url(url)
    policy=robots(url)
    if not policy.can_fetch(AGENT,url): raise AIError('Lecture directe : chemin refusé par robots.txt.')
    delay=policy.crawl_delay(AGENT) or policy.crawl_delay('*') or 1
    if delay>30: raise AIError('Lecture directe : délai demandé par le site trop long, site ignoré.')
    host=urlsplit(url).netloc.casefold()
    with LOCK:
        pause=max(0,delay-(time.monotonic()-LAST_REQUEST.get(host,0)))
    if pause: time.sleep(pause)
    key='direct-page-v1:'+hashlib.sha256(url.encode()).hexdigest()
    old=cached(key)
    if old is not None: return old,True
    raw,kind,encoding,final=read(url,guard_redirect=True)
    if not robots(final).can_fetch(AGENT,final): raise AIError('Lecture directe : destination refusée par robots.txt.')
    if kind=='application/pdf':
        import io
        from pypdf import PdfReader
        text=' '.join((p.extract_text() or '') for p in PdfReader(io.BytesIO(raw)).pages[:6])
        links=[]
    elif kind in {'text/html','application/xhtml+xml','text/plain'}:
        # Certains sites déclarent leur encodage seulement dans la page HTML.
        if not encoding:
            match=re.search(br'charset\s*=\s*[\"\x27]?([\w-]+)',raw[:5000],re.I)
            encoding=match[1].decode('ascii') if match else 'utf-8'
        try: html=raw.decode(encoding,errors='replace')
        except LookupError: html=raw.decode('utf-8',errors='replace')
        parser=Content(); parser.feed(html); text=' '.join(parser.parts)
        parser_links=Links(); parser_links.feed(html)
        links=[{'url':urljoin(final,href).split('#')[0],'label':label[:200]} for href,label in parser_links.links]
    else: raise AIError('Lecture directe : format de source non pris en charge.')
    result={'url':final,'text':' '.join(text.split())[:25000],'links':links}
    db.put('cache',{'key':key,'created':db.now(),'result':result})
    return result,False


def priority(item):
    text=(item['url']+' '+item['label']).casefold()
    topics=[(110,('rapport-annuel','annual-report','chiffre','financial','finance')),
            (100,('qui-sommes','a-propos','apropos','about','notre-entreprise')),
            (90,('mentions-legales','legal-notice','mentions légales')),
            (80,('international','export','implantation')),
            (70,('contact','nos-produits','products','nos-marques'))]
    return max((score for score,words in topics if any(word in text for word in words)),default=0)


def relevant_excerpt(text,company_name,limit):
    """Extraits littéraux répartis sur la page, plutôt que son bandeau cookies."""
    terms=('chiffre','revenue','turnover','comptes sociaux','consolidated','marge','exercice','fabric',
           'manufactur','distrib','grossiste','aliment','food','export','international')
    names=[x for x in re.findall(r'\w+',company_name.casefold()) if len(x)>3]
    windows=[]
    for start in range(0,len(text),360):
        chunk=text[start:start+500]; lower=chunk.casefold()
        score=sum(4 if t in terms[:8] else 1 for t in terms if t in lower)+sum(2 for n in names if n in lower)
        windows.append((score,start,min(len(text),start+500)))
    chosen=[]; used=0
    for score,start,end in sorted(windows,key=lambda w:(-w[0],w[1])):
        if used>=limit: break
        if any(start<b and end>a for a,b in chosen): continue
        end=min(end,start+limit-used); chosen.append((start,end)); used+=end-start+3
    return ' … '.join(text[a:b] for a,b in sorted(chosen))[:limit]


def collect(company,campaign,cfg):
    quick=cfg.get('research_profile')=='quick'
    seeds=[]
    for field in ('website','exhibitor_url'):
        url=value(company,field)
        if valid_url(url) and url not in seeds: seeds.append(url)
    if not seeds: raise AIError('Lecture directe : aucun site ou lien exposant dans le fichier. Recherche externe nécessaire.')
    warnings=['Mode sans API de recherche : seuls les sites fournis et leurs liens sont consultés. Les informations absentes restent inconnues.']
    pages=[]; attempts=0; hits=0
    def fetch(url):
        nonlocal attempts,hits
        attempts+=1
        try:
            page,hit=fetch_page(url); hits+=int(hit)
            if page['text']: pages.append(page)
            return page
        except (AIError,OSError,ValueError) as exc:
            # Un refus, CAPTCHA ou limite du site n'est jamais contourné.
            status=getattr(exc,'code',None)
            reason=f'HTTP {status}' if status else str(exc) if isinstance(exc,AIError) else 'site inaccessible'
            warnings.append('Lecture directe impossible : '+url+' ('+reason+').')
            return None
    home=fetch(seeds[0])
    if home and value(company,'website'):
        links={item['url']:item for item in home['links']
               if valid_url(item['url']) and domain(item['url'])==domain(home['url'])
               and not urlsplit(item['url']).query and priority(item)>0}
        for item in sorted(links.values(),key=lambda item:-priority(item))[:0 if quick and len(seeds)>1 else 1 if quick else 3]:
            if item['url'] not in {p['url'] for p in pages}: fetch(item['url'])
    for url in seeds[1:]: fetch(url)
    documents=[]; remaining=4000 if quick else 6000
    # Répartir le petit contexte local entre les pages, en gardant leurs citations.
    for page in pages:
        budget=min(2000 if quick else 1500,remaining)
        text=relevant_excerpt(page['text'],company['name'],budget)
        if text: documents.append({'url':page['url'],'title':'Page publique fournie ou liée','content':text})
        remaining-=len(text)
        if remaining<=0: break
    if not documents: raise AIError('Lecture directe : aucune page publique exploitable. Ne pas inventer de résultat.')
    return documents,warnings,{'provider':'direct','search_calls':0,'page_attempts':attempts,'cache_hits':hits,'api_calls':0}
