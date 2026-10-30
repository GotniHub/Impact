"""Exécution bornée aux fournisseurs gratuits configurés, avec attente des quotas.

Ce contrôleur s'installe uniquement dans le processus de traitement complet.
Les appels interactifs ordinaires continuent de s'arrêter sur un quota.
"""
import hashlib
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from . import db, providers, web_research
from .providers import AIError, ResearchCancelled


def retry_delay(exc,host,attempt=1,now=None):
    now=now or datetime.now(timezone.utc)
    delay=max(0,exc.retry_after or 0)
    if exc.quota_window=='day':
        zone=ZoneInfo('America/Los_Angeles') if host=='generativelanguage.googleapis.com' else timezone.utc
        local=now.astimezone(zone)
        tomorrow=(local+timedelta(days=1)).replace(hour=0,minute=0,second=0,microsecond=0)
        delay=max(delay,(tomorrow-now).total_seconds()+60)
    elif not delay:
        delay=3600 if host=='ollama.com' or exc.quota_window=='hour' else min(60*2**min(attempt-1,6),3600)
    return max(5,delay+5)


class FreeBatchRequests:
    def __init__(self,cfg,output_dir):
        if (cfg.get('provider')!='gemini' or cfg.get('gemini_tier')!='free'
                or cfg.get('web_provider')!='ollama'
                or cfg.get('model') not in {'gemini-3.5-flash-lite','gemini-3.1-flash-lite'}
                or not cfg.get('configured')):
            raise AIError('Le traitement gratuit exige Gemini Flash-Lite, une offre gratuite et Ollama web.')
        self.cfg=dict(cfg)
        self.output_dir=Path(output_dir)
        self.output_dir.mkdir(parents=True,exist_ok=True)
        self.original=providers.json_request
        self.job_id=None
        self.ready=threading.Event()
        self.network_calls=0
        self.cache_hits=0

    def install(self):
        providers.json_request=self.request
        web_research.json_request=self.request

    def bind(self,job_id):
        self.job_id=job_id
        self.ready.set()

    def check_running(self):
        if not self.ready.wait(10): raise ResearchCancelled('Traitement non initialisé : relancer le lot.')
        job=db.get('jobs',self.job_id)
        if job.get('cancel') or job.get('status') not in {'queued','running'}:
            raise ResearchCancelled('Arrêt demandé ou serveur redémarré. Les résultats terminés sont conservés.')
        return job

    def log(self,event):
        # Aucun en-tête, clé ou texte de requête dans les journaux.
        with (self.output_dir/'requests.jsonl').open('a',encoding='utf-8') as stream:
            stream.write(json.dumps({'at':db.now(),**event},ensure_ascii=False)+'\n')

    def wait_quota(self,exc,host,attempt):
        delay=retry_delay(exc,host,attempt)
        until=datetime.now(timezone.utc)+timedelta(seconds=delay)
        job=self.check_running()
        message=job['message']
        service='Ollama web' if host=='ollama.com' else 'Gemini'
        display=until.astimezone(ZoneInfo('Africa/Casablanca')).strftime('%d/%m à %H:%M')
        job.update(message=f'Pause quota gratuit {service} · reprise prévue le {display}',
                   retry_at=until.isoformat(),quota_service=service,
                   quota_pauses=job.get('quota_pauses',0)+1)
        db.put('jobs',job)
        self.log({'host':host,'status':'quota_wait','retry_at':until.isoformat(),'wait_seconds':round(delay)})
        # Attente interruptible par le bouton Arrêter de l'application.
        deadline=time.monotonic()+delay
        while time.monotonic()<deadline:
            self.check_running()
            time.sleep(min(5,max(0,deadline-time.monotonic())))
        job=self.check_running()
        job['message']=message
        job.pop('retry_at',None); job.pop('quota_service',None)
        db.put('jobs',job)

    def request(self,url,payload=None,*args,**kwargs):
        p=urlsplit(url)
        web=(p.scheme=='https' and p.hostname=='ollama.com'
             and p.path in {'/api/web_search','/api/web_fetch'})
        model=(p.scheme=='https' and p.hostname=='generativelanguage.googleapis.com'
               and p.path=='/v1beta/interactions' and isinstance(payload,dict)
               and payload.get('model')==self.cfg['model'])
        if not (web or model): raise AIError('Traitement gratuit : fournisseur ou modèle non autorisé.')
        self.check_running()
        key='free-web-v1:'+hashlib.sha256(json.dumps([url,payload],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        if web:
            try:
                cache=db.get('cache',key)
                if datetime.fromisoformat(cache['created'])>datetime.now(timezone.utc)-timedelta(hours=24):
                    self.cache_hits+=1
                    return cache['result']
            except ValueError: pass
        attempt=0
        while True:
            self.check_running()
            started=time.monotonic()
            try:
                self.network_calls+=1
                result=self.original(url,payload,*args,**kwargs)
            except AIError as exc:
                self.log({'host':p.hostname,'path':p.path,'status':'error',
                          'http_status':exc.http_status,'seconds':round(time.monotonic()-started,2)})
                if exc.http_status!=429: raise
                attempt+=1
                self.wait_quota(exc,p.hostname,attempt)
                continue
            self.check_running()
            if web: db.put('cache',{'key':key,'created':db.now(),'result':result})
            self.log({'host':p.hostname,'path':p.path,'status':'ok','seconds':round(time.monotonic()-started,2)})
            return result
