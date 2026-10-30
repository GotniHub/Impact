import copy
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from . import db, ai
from .config import FIELDS
from .domain import make_company, merge_company, canonical_name, domain, value, rank, research_key, sanitize_field
from .importers import normalize_records, norm
from .providers import ResearchCancelled

EXECUTOR=ThreadPoolExecutor(max_workers=1,thread_name_prefix='impact-research')
START_LOCK=threading.Lock()

def create_campaign(name,edition,records,source):
    if not name.strip() or not edition.strip(): raise ValueError('Le nom du salon et son édition sont obligatoires.')
    criteria=db.setting('criteria'); criteria.setdefault('progressive_selection',True)
    c={'id':db.uid(),'name':name.strip(),'edition':edition.strip(),'created':db.now(),'criteria':criteria,'source':source}
    companies=[]
    for record in records:
        if not record.get('name','').strip(): continue
        new=make_company(record,c,f'{source} · ligne {record.get("_row","?")}')
        existing=next((x for x in companies if canonical_name(x['name'])==canonical_name(new['name']) and (not domain(value(x,'website')) or not domain(value(new,'website')) or domain(value(x,'website'))==domain(value(new,'website')))),None)
        if existing: merge_company(existing,new)
        else: companies.append(new)
    if not companies: raise ValueError('Aucune entreprise importable.')
    # Transaction pour éviter une campagne partiellement importée.
    with db.LOCK,db.connection() as conn:
        conn.execute('INSERT INTO campaigns VALUES(?,?,?,?,?)',(c['id'],c['name'],c['edition'],c['created'],json.dumps(c,ensure_ascii=False)))
        conn.executemany('INSERT INTO companies VALUES(?,?,?)',[(x['id'],c['id'],json.dumps(x,ensure_ascii=False)) for x in companies])
    db.audit('campaign_import',{'campaign_id':c['id'],'companies':len(companies),'source':source})
    return c

def import_campaign(parsed,sheet,mapping,name,edition):
    tab=parsed['tables'][sheet]
    records,warnings=normalize_records(tab,mapping)
    return create_campaign(name,edition,records,parsed['filename']+' / '+tab['name']),warnings

def delete_campaign(campaign_id):
    # Le même verrou protège le démarrage d'un lot et la suppression.
    with START_LOCK,db.LOCK,db.connection() as conn:
        row=conn.execute('SELECT payload FROM campaigns WHERE id=?',(campaign_id,)).fetchone()
        if not row: raise ValueError('Cette campagne n’existe plus.')
        campaign=json.loads(row['payload'])
        jobs=conn.execute('SELECT payload FROM jobs WHERE campaign_id=?',(campaign_id,)).fetchall()
        if any(json.loads(job['payload']).get('status') in {'queued','running'} for job in jobs):
            raise ValueError('Un traitement IA est encore en cours. Arrêtez-le et attendez sa fin avant de supprimer la campagne.')
        count=conn.execute('SELECT COUNT(*) FROM companies WHERE campaign_id=?',(campaign_id,)).fetchone()[0]
        # Supprimer les dépendances avant la campagne, dans une seule transaction.
        conn.execute('DELETE FROM jobs WHERE campaign_id=?',(campaign_id,))
        conn.execute('DELETE FROM companies WHERE campaign_id=?',(campaign_id,))
        conn.execute('DELETE FROM campaigns WHERE id=?',(campaign_id,))
        result={'campaign_id':campaign_id,'name':campaign['name'],'edition':campaign['edition'],
                'companies':count,'jobs':len(jobs)}
        conn.execute('INSERT INTO audit(created,action,payload) VALUES(?,?,?)',
                     (db.now(),'campaign_delete',json.dumps(result,ensure_ascii=False)))
    return result

def import_crm(parsed,sheet,mapping,default_status):
    records,warnings=normalize_records(parsed['tables'][sheet],mapping)
    existing=db.all_rows('crm'); count=0
    for record in records:
        status=norm(record.get('status') or default_status)
        if status not in {'client','prospect','customer','lead'}:
            warnings.append(f'{record["name"]} ignoré : statut {status or "absent"}.'); continue
        signature=(canonical_name(record['name']),domain(record.get('website')))
        old=next((x for x in existing if (canonical_name(x['name']),domain(x.get('website')))==signature),None)
        contact={'name':record.get('contact_name',''),'role':record.get('contact_role',''),'email':record.get('contact_email',''),
                 'phone':record.get('contact_phone',''),'linkedin':record.get('contact_linkedin','')}
        row={'id':old['id'] if old else db.uid(), 'name':record['name'], 'website':record.get('website') or (old or {}).get('website',''),
             'siren':record.get('siren') or (old or {}).get('siren',''), 'status':'client' if status in {'client','customer'} else 'prospect',
             'source':parsed['filename'],'updated':db.now(),'contacts':(old or {}).get('contacts',[])}
        row.update({f:record.get(f) or (old or {}).get(f,'') for f in ['email','phone','category','company_linkedin']})
        if any(contact.values()) and contact not in row['contacts']: row['contacts'].append(contact)
        db.put('crm',row)
        if old: existing[existing.index(old)]=row
        else: existing.append(row)
        count+=1
    db.audit('crm_import',{'source':parsed['filename'],'rows':count})
    return count,warnings

def campaign_results(id):
    c=db.get('campaigns',id)
    companies=rank(db.all_rows('companies',id),c['criteria'],db.all_rows('crm'),
                   crm_empty_confirmed=c.get('crm_review',{}).get('empty_confirmed') is True,
                   selection_decisions=c.get('selection_decisions'))
    c['selection_deferred']=False
    if c.get('full_research_job_id') and not c['criteria'].get('progressive_selection'):
        try: job=db.get('jobs',c['full_research_job_id'])
        except ValueError: job=None
        c['selection_deferred']=not job or job['status'] not in {'completed','completed_with_errors'}
        if c['selection_deferred']:
            for company in companies:
                if company['selection']!='validated': company['selection']='none'
    return c,companies

def set_selection_dismissed(campaign_id,company_ids,*,dismissed=True,reason=''):
    """Décision commerciale propre à une campagne, sans modifier les faits ni le CRM."""
    ids=set(company_ids)
    if not ids: raise ValueError('Choisissez au moins une entreprise.')
    if type(dismissed) is not bool: raise ValueError('Décision invalide.')
    reason=reason.strip() or 'Ne convient pas à cette campagne'
    with db.LOCK,db.connection() as conn:
        row=conn.execute('SELECT payload FROM campaigns WHERE id=?',(campaign_id,)).fetchone()
        if not row: raise ValueError('Cette campagne n’existe plus.')
        campaign=json.loads(row['payload'])
        companies=[json.loads(r['payload']) for r in conn.execute('SELECT payload FROM companies WHERE campaign_id=?',(campaign_id,))]
        by_id={c['id']:c for c in companies}
        if not ids<=by_id.keys(): raise ValueError('Une entreprise ne fait pas partie de cette campagne.')
        crm=[json.loads(r['payload']) for r in conn.execute('SELECT payload FROM crm')]
        decisions=campaign.setdefault('selection_decisions',{})
        def selected():
            ranked=rank(companies,campaign['criteria'],crm,
                        crm_empty_confirmed=campaign.get('crm_review',{}).get('empty_confirmed') is True,
                        selection_decisions=decisions)
            return {c['id']:c['name'] for c in ranked if c['selection'] in {'validated','proposed','shortlisted'}}
        before=selected()
        for id in sorted(ids):
            if dismissed: decisions[id]={'dismissed':True,'name':by_id[id]['name'],'reason':reason,'updated':db.now()}
            else: decisions.pop(id,None)
        after=selected()
        result={'campaign_id':campaign_id,'dismissed':dismissed,'company_ids':sorted(ids),'reason':reason,
                'removed':[before[id] for id in before if id not in after],
                'added':[after[id] for id in after if id not in before]}
        conn.execute('UPDATE campaigns SET payload=? WHERE id=?',(json.dumps(campaign,ensure_ascii=False),campaign_id))
        conn.execute('INSERT INTO audit(created,action,payload) VALUES(?,?,?)',
                     (db.now(),'campaign_selection_decision',json.dumps(result,ensure_ascii=False)))
    return result


def update_campaign_criteria(id,criteria):
    c=db.get('campaigns',id)
    criteria=copy.deepcopy(criteria)
    criteria.setdefault('progressive_selection',c['criteria'].get('progressive_selection',True))
    c['criteria']=criteria; db.put('campaigns',c)
    db.audit('campaign_criteria',{'campaign_id':id,'criteria':criteria})

def save_review(id,fact_edits,contacts,triggers,events,approve_fields):
    company=db.get('companies',id)
    for field,raw in fact_edits.items():
        if field not in FIELDS or field=='name': continue
        val=sanitize_field(field,raw)
        previous=company['facts'].get(field,{})
        if val is None:
            company['facts'].pop(field,None); continue
        changed=val!=previous.get('value')
        company['facts'][field]={**previous,'value':val,'status':'validated' if field in approve_fields else ('manual' if changed else previous.get('status','manual')),
                                 'source':'Saisie utilisateur' if changed else previous.get('source','Saisie utilisateur'),
                                 'quote':str(val) if changed else previous.get('quote',str(val)),
                                 'checked_at':db.now()}
    for field in ['revenue_eur','margin_pct']:
        if field in company['facts']:
            company['facts'][field]['year']=value(company,'finance_year')
            company['facts'][field]['scope']=value(company,'finance_scope')
    campaign=db.get('campaigns',company['campaign_id'])
    if 'sector_fit' in company['facts']: company['facts']['sector_fit']['criteria_sectors']=campaign['criteria']['sectors']
    company['contacts']=contacts
    company['triggers']=triggers
    company['events']=events
    db.put('companies',company)
    db.audit('company_review',{'company_id':id,'approved_fields':list(approve_fields),'changed_fields':list(fact_edits)})

def research_candidates(companies,refresh=False,*,priority=False):
    candidates=[c for c in companies if c['assessment']['exclusion']['state']=='clear'
            and (refresh or c.get('research_status')!='completed')]
    if priority:
        # campaign_results prépare screening quand le mode progressif est actif.
        candidates=[c for c in candidates if c.get('screening',{}).get('candidate')]
        candidates.sort(key=lambda c:(c.get('selection') not in {'shortlisted','proposed','validated'},
                                      -c['screening']['priority'],-c['assessment']['score'],canonical_name(c['name'])))
    return candidates

def start_job(campaign_id,limit,refresh=False,configuration=None,*,worker_pid=None):
    cfg=copy.deepcopy(configuration or ai.config())
    if not cfg['configured']: raise ai.AIError('Configuration IA incomplète : '+' ; '.join(cfg['missing']))
    with START_LOCK:
        if any(j['status'] in {'running','queued'} for j in db.all_rows('jobs')):
            raise ValueError('Une recherche est déjà en cours. Attendre sa fin ou l’arrêter.')
        if cfg['provider']=='ollama':
            from .providers import local_models
            if cfg['model'] not in local_models(cfg):
                raise ai.AIError('Accès au modèle local impossible : l’installer ou choisir un modèle déjà présent dans Configuration IA.')
        campaign,companies=campaign_results(campaign_id)
        candidates=research_candidates(companies,refresh,priority=cfg.get('research_scope')=='priority')[:limit]
        if not candidates: raise ValueError('Aucune entreprise restante à enrichir.')
        job={'id':db.uid(),'campaign_id':campaign_id,'created':db.now(),'status':'queued','done':0,'total':len(candidates),
             'message':'Recherche en attente','errors':[], 'cancel':False,'criteria_snapshot':campaign['criteria'],
             'company_ids':[c['id'] for c in candidates],'refresh':refresh,'model':cfg['model'],'ai_config':cfg}
        if worker_pid:
            job.update(worker_pid=worker_pid,worker_heartbeat=db.now())
        db.put('jobs',job)
        EXECUTOR.submit(run_job,job['id'])
        return job

def run_job(job_id):
    timer=time.monotonic()
    job=db.get('jobs',job_id)
    job.update(status='running',started_at=db.now(),elapsed_seconds=0.0,timing_updated_at=db.now(),
               timing_complete=False,timing_source='recorded')
    db.put('jobs',job)
    campaign=db.get('campaigns',job['campaign_id']); criteria=job['criteria_snapshot']
    cfg=job.get('ai_config') or ai.config('openai',{'model':job['model']})
    fatal=False
    for company_id in job['company_ids']:
        current=db.get('jobs',job_id)
        if current.get('status') not in {'running','queued'}:
            job.update(status='interrupted',message=current.get('message','Traitement interrompu.')); break
        if current.get('cancel'):
            job.update(status='cancelled',message='Arrêt demandé. Les résultats terminés sont conservés.'); break
        company=db.get('companies',company_id)
        job['message']='Recherche : '+company['name']
        job.update(elapsed_seconds=round(time.monotonic()-timer,3),timing_updated_at=db.now())
        db.put('jobs',job)
        try:
            cache_key=research_key(company,campaign,criteria,job['model'],cfg)
            result=None
            if not job['refresh']:
                try:
                    cache=db.get('cache',cache_key)
                    if datetime.fromisoformat(cache['created'])>datetime.now(timezone.utc)-timedelta(days=7): result=cache['result']
                except ValueError: pass
            if result is None:
                result=ai.research(company,campaign,criteria,cfg)
                db.put('cache',{'key':cache_key,'created':db.now(),'result':result})
            current=db.get('jobs',job_id)
            if current.get('status') not in {'running','queued'}:
                job.update(status='interrupted',message=current.get('message','Traitement interrompu.')); break
            # Recharger : une validation faite pendant la recherche doit être conservée.
            company=db.get('companies',company_id)
            for field,fact in result['facts'].items():
                if company['facts'].get(field,{}).get('status')=='validated': continue
                if field in {'stand','hall'} and company['facts'].get(field):
                    if company['facts'][field]['value']!=fact['value']: company['warnings'].append('Stand/hall IA différent du fichier : vérifier les sources et l’édition.')
                    continue
                company['facts'][field]=fact
            signatures={(norm(c.get('name')),c.get('email'),c.get('linkedin')) for c in company['contacts']}
            for contact in result['contacts']:
                signature=(norm(contact['name']),contact['email'],contact['linkedin'])
                if signature not in signatures: company['contacts'].append(contact); signatures.add(signature)
            if cfg.get('research_profile')!='quick':
                company['triggers']=[t for t in company['triggers'] if t.get('status')=='validated']+[t for t in result['triggers'] if t['code'] not in {old['code'] for old in company['triggers'] if old.get('status')=='validated'}]
                company['events']=[e for e in company['events'] if e.get('status')=='validated']+result['events']
                company['memo']=result['memo']; company['research_sources']=result.get('sources',[])
            else:
                # Une qualification courte ne remplace pas un dossier détaillé existant.
                if not company.get('memo'): company['memo']=result['memo']
                company['research_sources']=list(dict.fromkeys(company.get('research_sources',[])+result.get('sources',[])))
            company['research_usage']=result.get('usage',{}); company['warnings']=list(dict.fromkeys(company['warnings']+result['warnings']))
            company['research_status']='completed' if result.get('identity_match') else 'ambiguous'
            company['research_provider']=cfg['provider']; company['research_model']=cfg['model']
            company['research_profile']=cfg.get('research_profile','standard')
            company['researched_at']=db.now(); db.put('companies',company)
            db.audit('research_completed',{'company_id':company_id,'provider':cfg['provider'],'model':job['model'],'usage':result.get('usage',{})})
        except ResearchCancelled as exc:
            job.update(status='cancelled',message=str(exc)); break
        except Exception as exc:
            message=str(exc) if isinstance(exc,(ai.AIError,ValueError)) else f'Erreur technique : {type(exc).__name__}'
            company['research_status']='error'; company['research_error']=message; db.put('companies',company)
            job['errors'].append({'company':company['name'],'message':message})
            if any(s in message for s in ['Clé API','Quota','Accès au modèle','Requête refusée']): fatal=True
        job['done']+=1
        # Conserver un arrêt demandé durant un appel réseau.
        job['cancel']=db.get('jobs',job_id).get('cancel',False)
        job.update(elapsed_seconds=round(time.monotonic()-timer,3),timing_updated_at=db.now())
        db.put('jobs',job)
        if fatal: break
    if job['status'] not in {'cancelled','interrupted'}:
        job.update(status='failed' if fatal else 'completed_with_errors' if job['errors'] else 'completed',
                   message='Traitement interrompu par une erreur IA.' if fatal else 'Recherche terminée. Les données sourcées sont prêtes à vérifier.')
    job.update(finished_at=db.now(),elapsed_seconds=round(time.monotonic()-timer,3),
               timing_updated_at=db.now(),timing_complete=True)
    db.put('jobs',job)

def cancel_job(job_id):
    with START_LOCK:
        job=db.get('jobs',job_id); job['cancel']=True; db.put('jobs',job)

def add_to_crm(companies):
    crm=db.all_rows('crm'); added=0
    for company in companies:
        if company.get('selection')!='validated': continue
        if any(canonical_name(x['name'])==canonical_name(company['name']) for x in crm): continue
        row={'id':db.uid(),'name':company['name'],'website':value(company,'website') or '', 'siren':value(company,'siren') or '',
             'status':'prospect','source':'Campagne '+company['campaign_id'],'updated':db.now(),
             'contacts':[c for c in company['contacts'] if c.get('status')=='validated']}
        row.update({f:value(company,f,True) or '' for f in ['email','phone','category','company_linkedin']})
        db.put('crm',row); crm.append(row); added+=1
    db.audit('qualified_to_crm',{'count':added})
    return added
