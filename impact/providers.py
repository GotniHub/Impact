"""Configurations sans secrets et sorties JSON des trois moteurs IA."""
import ipaddress
import json
import os
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib import request, error
from urllib.parse import urlsplit

LABELS={'openai':'OpenAI — priorité performance', 'ollama':'Ollama local — économique',
        'gemini':'Gemini — essai sous quotas gratuits'}
DEFAULT_MODELS={'openai':'gpt-5.5','ollama':'qwen3:8b','gemini':'gemini-3.5-flash-lite'}

class AIError(RuntimeError):
    def __init__(self,message,*,http_status=None,retry_after=None,quota_window=None):
        super().__init__(message)
        self.http_status=http_status
        self.retry_after=retry_after
        self.quota_window=quota_window


class ResearchCancelled(AIError):
    """Arrêt explicite d'une recherche, sans transformer l'entreprise en erreur."""


def quota_details(exc):
    """Ne conserver que le délai et la période, jamais le corps d'erreur distant."""
    delay=None; window=None
    header=exc.headers.get('Retry-After','') if exc.headers else ''
    try: delay=max(0,float(header))
    except (TypeError,ValueError):
        try: delay=max(0,(parsedate_to_datetime(header)-datetime.now(timezone.utc)).total_seconds())
        except (TypeError,ValueError,OverflowError): pass
    try: body=json.loads(exc.read(64000))
    except (OSError,TypeError,ValueError): body={}
    text=json.dumps(body).casefold()
    if any(s in text for s in ('perday','per_day','daily','per day')): window='day'
    elif any(s in text for s in ('hourly','perhour','per_hour')): window='hour'
    details=body.get('error',{}).get('details',[]) if isinstance(body,dict) and isinstance(body.get('error'),dict) else []
    for detail in details:
        if isinstance(detail,dict) and str(detail.get('@type','')).endswith('RetryInfo'):
            try:
                seconds=float(str(detail.get('retryDelay','')).removesuffix('s'))
                delay=max(delay or 0,seconds)
            except ValueError: pass
    if delay is not None and (delay!=delay or delay==float('inf')): delay=None
    return delay,window

def local_url(url):
    p=urlsplit(url)
    if p.scheme not in {'http','https'} or p.username or p.password or p.query or p.fragment or p.path not in {'','/'}:
        raise AIError('Adresse Ollama invalide : utiliser une adresse locale sans chemin.')
    try: local=ipaddress.ip_address(p.hostname).is_loopback
    except ValueError: local=p.hostname=='localhost'
    if not local: raise AIError('Cette option Ollama utilise uniquement un serveur local sur ce PC.')
    return url.rstrip('/')

def config(provider=None, options=None):
    options=options or {}
    provider=(provider or os.environ.get('AI_PROVIDER','openai')).lower()
    if provider not in LABELS: raise AIError('Solution IA inconnue.')
    model=options.get('model') or os.environ.get(provider.upper()+'_MODEL',DEFAULT_MODELS[provider])
    web=options.get('web_provider',os.environ.get('WEB_SEARCH_PROVIDER','ollama')).lower()
    tier=options.get('gemini_tier',os.environ.get('GEMINI_TIER','free'))
    cfg={'provider':provider,'label':LABELS[provider],'model':model,
         'web_provider':'openai' if provider=='openai' else web,'gemini_tier':tier,
         'ollama_url':os.environ.get('OLLAMA_BASE_URL','http://127.0.0.1:11434')}
    missing=[]
    if provider in {'openai','gemini'} and not os.environ.get(provider.upper()+'_API_KEY','').strip():
        missing.append(provider.upper()+'_API_KEY à renseigner dans .env')
    if not model.strip(): missing.append('Modèle à renseigner')
    if provider=='ollama':
        try: local_url(cfg['ollama_url'])
        except AIError as exc: missing.append(str(exc))
        if 'cloud' in model.lower(): missing.append('Choisir un modèle local sans suffixe cloud')
    if provider=='gemini' and tier=='free' and model not in {'gemini-3.5-flash-lite','gemini-3.1-flash-lite'}:
        missing.append('Offre gratuite : choisir Gemini Flash-Lite ou déclarer une offre payante')
    if provider=='gemini' and tier not in {'free','paid'}: missing.append('Offre Gemini inconnue : choisir free ou paid')
    cfg['text_configured']=not missing
    if provider!='openai':
        if web not in {'ollama','serper','direct'}: missing.append('Service web inconnu')
        elif web!='direct':
            env='OLLAMA_API_KEY' if web=='ollama' else 'SERPER_API_KEY'
            if not os.environ.get(env,'').strip(): missing.append(env+' à renseigner pour la recherche web')
    cfg['missing']=missing
    cfg['configured']=not missing
    return cfg

def json_request(url, payload=None, headers=None, timeout=180, service='IA'):
    body=None if payload is None else json.dumps(payload,ensure_ascii=False).encode()
    for attempt in range(3):
        req=request.Request(url,data=body,headers={'Content-Type':'application/json',**(headers or {})})
        try:
            with request.urlopen(req,timeout=timeout) as response:
                raw=response.read(12_000_001)
            if len(raw)>12_000_000: raise AIError('Réponse '+service+' trop volumineuse.')
            result=json.loads(raw)
            if not isinstance(result,dict): raise AIError('Réponse '+service+' invalide.')
            return result
        except error.HTTPError as exc:
            # Aucun basculement automatique vers un fournisseur payant à la fin d'un quota.
            if exc.code in {500,502,503,504} and attempt<2:
                time.sleep(2**(attempt+1)); continue
            messages={400:'Requête refusée : vérifier le modèle et sa compatibilité.',
                      401:'Clé API refusée.',403:'Accès au modèle ou au service refusé.',
                      404:'Accès au modèle impossible : modèle absent ou indisponible.',
                      429:'Quota ou limite de débit atteint. Réessayer plus tard.'}
            message=messages.get(exc.code,f'Erreur HTTP {exc.code}.')
            if service=='Lecture web Ollama' and exc.code==404:
                message='Page source introuvable ou indisponible.'
            delay,window=quota_details(exc) if exc.code==429 else (None,None)
            raise AIError(service+' : '+message,http_status=exc.code,retry_after=delay,quota_window=window) from None
        except (error.URLError,TimeoutError,OSError):
            if attempt<2: time.sleep(2); continue
            raise AIError('Connexion '+service+' impossible ou délai dépassé.') from None
        except (ValueError,UnicodeError):
            raise AIError('Réponse JSON '+service+' invalide.') from None

def validate_schema(value,schema):
    kind=schema.get('type'); kinds=kind if isinstance(kind,list) else [kind]
    matches={'null':value is None,'object':isinstance(value,dict),'array':isinstance(value,list),
             'string':isinstance(value,str),'boolean':type(value) is bool,'integer':type(value) is int,
             'number':type(value) in {int,float}}
    if not any(matches.get(k,False) for k in kinds): raise AIError('Structure JSON IA invalide : aucune donnée acceptée.')
    if 'enum' in schema and value not in schema['enum']: raise AIError('Valeur JSON IA invalide.')
    if isinstance(value,str) and len(value)>schema.get('maxLength',float('inf')):
        raise AIError('Texte JSON IA trop long : aucune donnée acceptée.')
    if isinstance(value,list) and len(value)>schema.get('maxItems',float('inf')):
        raise AIError('Trop d’éléments JSON IA : aucune donnée acceptée.')
    if isinstance(value,dict):
        props=schema.get('properties',{})
        if not set(schema.get('required',[]))<=value.keys(): raise AIError('Champs JSON IA manquants.')
        if schema.get('additionalProperties') is False and not value.keys()<=props.keys(): raise AIError('Champs JSON IA inattendus.')
        for key,item in value.items():
            if key in props: validate_schema(item,props[key])
    elif isinstance(value,list):
        for item in value: validate_schema(item,schema['items'])

def structured(input_text,schema,system,cfg):
    if not cfg['text_configured']: raise AIError('Configuration IA incomplète : '+ '; '.join(cfg['missing']))
    if cfg['provider']=='ollama':
        endpoint=local_url(cfg['ollama_url'])+'/api/chat'
        compact=cfg['web_provider']=='direct'
        quick=compact and cfg.get('research_profile')=='quick'
        local_context=8192 if cfg['model'].startswith('qwen3:1.7b') else 6144
        response=json_request(endpoint,{'model':cfg['model'],'stream':False,'think':False,
            'messages':[{'role':'system','content':system},{'role':'user','content':input_text}],
            'format':schema,'options':{'temperature':0,'num_ctx':6144 if quick else local_context if compact else 32768,'num_predict':1800 if quick else 3200 if compact else 12000}},
            timeout=float(os.environ.get('OLLAMA_TIMEOUT','300')),service='Ollama local')
        if not response.get('done') or response.get('done_reason')=='length': raise AIError('Réponse Ollama incomplète : aucune donnée acceptée.')
        text=response.get('message',{}).get('content','')
        usage={'input_tokens':response.get('prompt_eval_count',0),'output_tokens':response.get('eval_count',0)}
    elif cfg['provider']=='gemini':
        response=json_request('https://generativelanguage.googleapis.com/v1beta/interactions',
            {'model':cfg['model'],'input':input_text,'system_instruction':system,'store':False,
             'response_format':{'type':'text','mime_type':'application/json','schema':schema},
             'generation_config':{'max_output_tokens':12000}},
            headers={'x-goog-api-key':os.environ.get('GEMINI_API_KEY','').strip()},service='Gemini')
        if response.get('status')!='completed': raise AIError('Réponse Gemini incomplète : aucune donnée acceptée.')
        text='\n'.join(part.get('text','') for step in response.get('steps',[]) if step.get('type')=='model_output'
                       for part in step.get('content',[]) if part.get('type')=='text')
        usage=response.get('usage',{})
    else: raise AIError('Moteur JSON inconnu.')
    try: result=json.loads(text)
    except (ValueError,TypeError): raise AIError('JSON IA invalide : aucune donnée acceptée.') from None
    validate_schema(result,schema)
    return result,usage

def local_models(cfg):
    response=json_request(local_url(cfg['ollama_url'])+'/api/tags',timeout=5,service='Ollama local')
    return [m['name'] for m in response.get('models',[]) if m.get('name') and 'cloud' not in m['name'].lower()]
