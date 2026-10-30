"""Recherche locale sans API IA distante ni moteur de recherche.

Exemple : .venv/Scripts/python.exe tools/research_local.py --campaign-id ID --limit 3
Utiliser --all après le test pour traiter les entreprises restantes.
"""
import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
for key in ('OPENAI_API_KEY','GEMINI_API_KEY','SERPER_API_KEY','OLLAMA_API_KEY'):
    os.environ[key]=''

from impact import db, providers, services


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign-id',required=True)
    parser.add_argument('--limit',type=int,default=3)
    parser.add_argument('--all',action='store_true')
    parser.add_argument('--model',default='qwen3:1.7b')
    parser.add_argument('--quick',action='store_true',help='Qualification courte, deux pages maximum.')
    parser.add_argument('--priority',action='store_true',help='Uniquement les candidats prioritaires du préclassement.')
    parser.add_argument('--output-dir',required=True)
    args=parser.parse_args()
    if args.limit<1: parser.error('La limite doit être positive.')
    output=Path(args.output_dir).resolve(); output.relative_to(ROOT)
    output.mkdir(parents=True,exist_ok=True)
    cfg=providers.config('ollama',{'model':args.model,'web_provider':'direct'})
    if args.quick: cfg['research_profile']='quick'
    if args.priority: cfg['research_scope']='priority'
    original=providers.json_request
    calls=[]
    def local_only(url,payload=None,*extra,**kwargs):
        p=urlsplit(url)
        if (p.hostname not in {'127.0.0.1','localhost','::1'} or p.port!=11434
                or p.path not in {'/api/tags','/api/chat'}):
            raise providers.AIError('Test local : API distante interdite.')
        if p.path=='/api/chat' and payload.get('model')!=args.model:
            raise providers.AIError('Test local : modèle non autorisé.')
        start=time.monotonic()
        result=original(url,payload,*extra,**kwargs)
        entry={'path':p.path,'seconds':round(time.monotonic()-start,2),
               'input_tokens':result.get('prompt_eval_count'),'output_tokens':result.get('eval_count')}
        calls.append(entry)
        (output/'requests.json').write_text(json.dumps(calls,indent=2),encoding='utf-8')
        return result
    providers.json_request=local_only
    campaign,companies=services.campaign_results(args.campaign_id)
    remaining=len(services.research_candidates(companies,priority=args.priority))
    if not remaining: print('Aucune entreprise restante.',flush=True); return
    started=time.monotonic()
    job=services.start_job(args.campaign_id,remaining if args.all else args.limit,False,cfg,worker_pid=os.getpid())
    if args.all:
        campaign['full_research_job_id']=job['id']
        campaign['criteria']['select_after_full_research']=True
        campaign.pop('selection_deferred',None); db.put('campaigns',campaign)
    (output/'job_id.txt').write_text(job['id'],encoding='utf-8')
    stop=threading.Event()
    def heartbeat():
        while not stop.wait(15): db.heartbeat_job(job['id'])
    thread=threading.Thread(target=heartbeat,daemon=True); thread.start()
    previous=None
    try:
        while True:
            job=db.get('jobs',job['id'])
            state=(job['status'],job['done'],job['message'])
            if state!=previous:
                live=db.all_rows('companies',args.campaign_id)
                progress={'updated':db.now(),'job_id':job['id'],'status':job['status'],
                          'done':job['done'],'total':job['total'],'message':job['message'],
                          'researched_total':sum(c.get('research_status')=='completed' for c in live),
                          'errors':job['errors'],'seconds':round(time.monotonic()-started,2),
                          'mode':'ollama_local_direct','external_ai_api_calls':0,'search_api_calls':0}
                (output/'progress.json').write_text(json.dumps(progress,ensure_ascii=False,indent=2),encoding='utf-8')
                print(json.dumps(progress,ensure_ascii=True),flush=True)
                previous=state
            if job['status'] not in {'queued','running'}: break
            time.sleep(5)
    finally: stop.set()
    result={'job':job,'seconds':round(time.monotonic()-started,2),
            'companies':[db.get('companies',key) for key in job['company_ids']],
            'local_requests':calls,'external_ai_api_calls':0,'search_api_calls':0}
    (output/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    if args.all and job['status'] in {'completed','completed_with_errors'}:
        _,ranked=services.campaign_results(args.campaign_id)
        (output/'selection.json').write_text(json.dumps([
            {'name':c['name'],'score':c['assessment']['score'],'tier':c['assessment']['tier'],
             'selection':c['selection'],'blockers':c['validated_assessment']['blockers'],
             'research_status':c.get('research_status'),'sources':c.get('research_sources',[])}
            for c in ranked if c['selection'] in {'validated','proposed','shortlisted'}],ensure_ascii=False,indent=2),encoding='utf-8')
        (output/'summary.json').write_text(json.dumps({
            'updated':db.now(),'campaign_id':args.campaign_id,'total':len(ranked),
            'research_completed':sum(c.get('research_status')=='completed' for c in ranked),
            'research_errors':sum(c.get('research_status')=='error' for c in ranked),
            'research_ambiguous':sum(c.get('research_status')=='ambiguous' for c in ranked),
            'research_pending':sum(c.get('research_status','pending')=='pending' for c in ranked),
            'selected':sum(c['selection'] in {'validated','proposed','shortlisted'} for c in ranked),
            'criteria_documented':sum(c['selection'] in {'validated','proposed'} for c in ranked),
            'manually_verified':sum(c['selection']=='validated' for c in ranked),
            'external_ai_api_calls':0,'search_api_calls':0,
        },ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__': main()
