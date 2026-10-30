"""Rechercher toute une campagne avec Gemini gratuit et Ollama web.

Laisser le PC, Internet et l'application ouverts. Le bouton Arrêter dans IMPACT
interrompt aussi une attente de quota. Une relance reprend les non-terminées.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ['OPENAI_API_KEY']=''
os.environ['SERPER_API_KEY']=''

from impact import db, providers, services, storage
from impact.free_batch import FreeBatchRequests


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign-id',required=True)
    parser.add_argument('--output-dir',required=True)
    args=parser.parse_args()
    output=Path(args.output_dir).resolve()
    output.relative_to(ROOT)
    output.mkdir(parents=True,exist_ok=True)
    cfg=providers.config('gemini',{'model':'gemini-3.5-flash-lite','web_provider':'ollama','gemini_tier':'free'})
    controller=FreeBatchRequests(cfg,output)
    controller.install()
    campaign,companies=services.campaign_results(args.campaign_id)
    remaining=len(services.research_candidates(companies))
    if not remaining:
        print('Toutes les entreprises ont déjà été traitées.',flush=True)
        return
    # Ne pas appeler db.init() : un autre serveur peut avoir un travail actif.
    with db.connection() as source:
        if storage.is_postgres(source):
            source.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            tables=('campaigns','companies','crm','settings','imports','jobs','audit','cache')
            snapshot={table:[dict(row) for row in source.execute(f'SELECT * FROM {table}')] for table in tables}
            (output/'before.json').write_text(json.dumps(snapshot,ensure_ascii=False),encoding='utf-8')
        else:
            import sqlite3
            from contextlib import closing
            with closing(sqlite3.connect(output/'before.sqlite3')) as backup: source.backup(backup)
    job=services.start_job(campaign['id'],remaining,False,cfg)
    campaign['criteria']['select_after_full_research']=True
    campaign['full_research_job_id']=job['id']
    campaign.pop('selection_deferred',None)
    db.put('campaigns',campaign)
    db.audit('full_free_research_started',{'campaign_id':campaign['id'],'job_id':job['id'],
             'remaining':remaining,'provider':'gemini','model':cfg['model'],'web':'ollama'})
    controller.bind(job['id'])
    (output/'job_id.txt').write_text(job['id'],encoding='utf-8')
    previous=None
    while True:
        job=db.get('jobs',job['id'])
        state=(job['status'],job['done'],job['message'])
        if state!=previous:
            live=db.all_rows('companies',campaign['id'])
            progress={'updated':db.now(),'job_id':job['id'],'campaign_id':campaign['id'],
                      'status':job['status'],'done':job['done'],'total':job['total'],
                      'message':job['message'],'retry_at':job.get('retry_at'),
                      'researched_total':sum(c.get('research_status')=='completed' for c in live),
                      'errors':job['errors'],'network_calls':controller.network_calls,
                      'cache_hits':controller.cache_hits,'paid_fallback_disabled':True}
            temporary=output/'progress.tmp'
            temporary.write_text(json.dumps(progress,ensure_ascii=False,indent=2),encoding='utf-8')
            temporary.replace(output/'progress.json')
            print(json.dumps(progress,ensure_ascii=True),flush=True)
            previous=state
        if job['status'] not in {'queued','running'}: break
        time.sleep(5)
    if job['status'] in {'completed','completed_with_errors'}:
        _,ranked=services.campaign_results(campaign['id'])
        selected=[{'id':c['id'],'name':c['name'],'selection':c['selection'],
                   'score':c['assessment']['score'],'tier':c['assessment']['tier'],
                   'blockers':c['validated_assessment']['blockers']}
                  for c in ranked if c['selection'] in {'proposed','validated'}]
        (output/'selection.json').write_text(json.dumps(selected,ensure_ascii=False,indent=2),encoding='utf-8')
        db.audit('full_free_research_finished',{'job_id':job['id'],'done':job['done'],
                 'total':job['total'],'errors':len(job['errors']),'selected':len(selected)})


if __name__=='__main__': main()
