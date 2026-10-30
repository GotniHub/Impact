"""Durées des lots : mesures conservées, historique partiel explicite."""
import math
from datetime import datetime,timezone

ACTIVE={'queued','running'}
TERMINAL={'completed','completed_with_errors','cancelled','failed','interrupted'}


def timestamp(value):
    try:
        stamp=datetime.fromisoformat(value)
        return stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp.astimezone(timezone.utc)
    except (TypeError,ValueError):
        return None


def valid_seconds(value):
    return type(value) in {int,float} and math.isfinite(value) and value>=0


def format_duration(seconds):
    if not valid_seconds(seconds): return 'Non enregistrée'
    hours,minutes=divmod(int(seconds)//60,60)
    return f'{hours} h {minutes:02d} min'


def duration(job,now=None):
    now=timestamp(now) if isinstance(now,str) else now
    now=now or datetime.now(timezone.utc)
    if now.tzinfo is None: now=now.replace(tzinfo=timezone.utc)
    seconds=job.get('elapsed_seconds')
    active=job.get('status') in ACTIVE
    if valid_seconds(seconds):
        checkpoint=timestamp(job.get('timing_updated_at'))
        if active and checkpoint:
            seconds+=max(0,(now-checkpoint).total_seconds())
        complete=bool(job.get('timing_complete')) or bool(active and checkpoint)
        return {'seconds':seconds,'complete':complete,'live':active}
    started=timestamp(job.get('started_at'))
    if active and started:
        return {'seconds':max(0,(now-started).total_seconds()),'complete':True,'live':True}
    if job.get('status')=='queued':
        return {'seconds':0,'complete':True,'live':True}
    return {'seconds':None,'complete':False,'live':False}


def summarize(jobs,now=None):
    entries=[]; seen=set()
    for job in sorted(jobs,key=lambda j:j.get('created',''),reverse=True):
        if job['id'] in seen: continue
        seen.add(job['id'])
        entries.append({'job':job,**duration(job,now)})
    known=[entry for entry in entries if entry['seconds'] is not None]
    return {'seconds':sum(entry['seconds'] for entry in known),
            'known_count':len(known),'missing_count':len(entries)-len(known),
            'partial_count':sum(not entry['complete'] for entry in known),
            'partial':any(not entry['complete'] for entry in entries),
            'live':any(entry['live'] for entry in entries),'entries':entries,
            'latest':entries[0] if entries else None}
