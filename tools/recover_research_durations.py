"""Récupère uniquement les durées historiques depuis les journaux locaux.

Aucun appel réseau, aucune modification des résultats d'entreprise.
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from impact import db
from impact.research_timing import ACTIVE,TERMINAL,timestamp,valid_seconds


def evidence(jobs,reports_root):
    choices={j['id']:[] for j in jobs}
    by_id={j['id']:j for j in jobs}
    for base,dirs,files in os.walk(reports_root):
        dirs[:]=[d for d in dirs if not d.startswith('soffice_') and d!='node_modules']
        for name in files:
            if not name.endswith('.json'): continue
            path=Path(base)/name
            try:
                if path.stat().st_size>35_000_000: continue
                report=json.loads(path.read_text(encoding='utf-8-sig'))
            except (OSError,ValueError): continue
            if not isinstance(report,dict): continue
            nested=report.get('job') if isinstance(report.get('job'),dict) else {}
            ident=report.get('job_id') or nested.get('id')
            if ident not in by_id: continue
            job=by_id[ident]; seconds=report.get('seconds',report.get('elapsed_seconds'))
            status=report.get('status',nested.get('status'))
            source='historical_report'
            complete=(status in TERMINAL and status==job['status'] and
                      report.get('done',nested.get('done',job.get('done')))==job.get('done'))
            if not valid_seconds(seconds):
                start=timestamp(job.get('created')); checkpoint=timestamp(report.get('updated'))
                if not start or not checkpoint or checkpoint<start: continue
                seconds=(checkpoint-start).total_seconds(); source='historical_checkpoint'; complete=False
            choices[ident].append({'seconds':seconds,'complete':complete,'source':source,
                                   'file':str(path.resolve().relative_to(ROOT)) if path.resolve().is_relative_to(ROOT) else path.name})
    for job in jobs:
        start=timestamp(job.get('created')); heartbeat=timestamp(job.get('worker_heartbeat'))
        if start and heartbeat and heartbeat>=start:
            choices[job['id']].append({'seconds':(heartbeat-start).total_seconds(),'complete':False,
                                       'source':'historical_heartbeat','file':None})
    return choices


def recover(campaign_id,reports_root=ROOT/'outputs'):
    jobs=db.all_rows('jobs',campaign_id)
    choices=evidence(jobs,reports_root)
    changes=[]
    for job in jobs:
        if job.get('status') in ACTIVE or valid_seconds(job.get('elapsed_seconds')): continue
        candidates=choices[job['id']]
        if not candidates: continue
        chosen=max(candidates,key=lambda item:(item['complete'],item['seconds']))
        old=dict(job)
        job.update(elapsed_seconds=round(chosen['seconds'],3),timing_complete=chosen['complete'],
                   timing_source=chosen['source'],timing_source_file=chosen['file'],timing_recovered_at=db.now())
        # Le statut et les compteurs d'origine ne sont jamais remplacés.
        assert all(job[key]==value for key,value in old.items())
        db.put('jobs',job)
        changes.append({'id':job['id'],'seconds':job['elapsed_seconds'],'complete':chosen['complete'],
                        'source':chosen['source'],'file':chosen['file']})
    return changes


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign-id',required=True)
    args=parser.parse_args()
    print(json.dumps(recover(args.campaign_id),ensure_ascii=True,indent=2))
