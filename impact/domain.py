import copy
import hashlib
import re
from datetime import datetime
from difflib import SequenceMatcher
from urllib.parse import urlparse
from zoneinfo import ZoneInfo
from . import db
from .config import FIELDS, TRIGGERS
from .importers import norm, clean

LEGAL={'sas','sasu','sarl','eurl','sa','spa','srl','ltd','limited','inc','llc','gmbh'}
PUBLIC_DOMAINS={'gmail.com','hotmail.com','outlook.com','yahoo.com','orange.fr','wanadoo.fr'}
CONFIRMED={'validated'}

def canonical_name(name):
    tokens=norm(name).split()
    for suffix in [('s','p','a'),('s','a','r','l'),('s','a'),('s','r','l')]:
        if len(tokens)>=len(suffix) and tuple(tokens[-len(suffix):])==suffix:
            tokens=tokens[:-len(suffix)]+[''.join(suffix)]
            break
    while tokens and tokens[-1] in LEGAL: tokens.pop()
    return ' '.join(tokens)

def domain(url):
    value=clean(url)
    if not value: return ''
    try:
        parsed=urlparse(value if '://' in value else 'https://'+value)
        host=(parsed.hostname or '').lower().removeprefix('www.')
        return host if '.' in host and host not in PUBLIC_DOMAINS else ''
    except ValueError: return ''

def valid_url(value):
    try:
        p=urlparse(str(value)); return p.scheme in {'http','https'} and bool(p.hostname) and not p.username and not p.password
    except ValueError: return False

def siren(value):
    digits=re.sub(r'\D','',str(value or ''))
    return digits[:9] if len(digits) in {9,14} else ''

def number(value):
    if value is None or value=='': return None
    if isinstance(value,bool): return None
    if isinstance(value,(int,float)): return float(value)
    text=str(value).strip().lower().replace('\u00a0',' ').replace('€','').replace('%','').replace('eur','').strip()
    factor=1
    match=re.search(r'\s*(md|milliard|bn|m|million|k|millier)(?:s)?\s*$',text)
    if match:
        factor=1e9 if match[1] in {'md','milliard','bn'} else 1e6 if match[1] in {'m','million'} else 1e3
        text=text[:match.start()]
    text=text.replace(' ','')
    if ',' in text and '.' in text:
        text=text.replace('.','').replace(',','.') if text.rfind(',')>text.rfind('.') else text.replace(',','')
    elif ',' in text:
        text=text.replace(',','') if re.fullmatch(r'\d{1,3}(,\d{3})+',text) else text.replace(',','.')
    elif text.count('.')>1 or re.fullmatch(r'\d{1,3}(\.\d{3})+',text): text=text.replace('.','')
    try:
        result=float(text)*factor
        return result if abs(result)<1e15 and result==result else None
    except ValueError: return None

def boolean(value):
    if isinstance(value,bool): return value
    text=norm(value)
    if text in {'oui','true','vrai','yes','1'}: return True
    if text in {'non','false','faux','no','0'}: return False
    return None

def sanitize_field(field,value):
    if field in {'revenue_eur','margin_pct','employees'}:
        n=number(value)
        if n is None or (field in {'revenue_eur','employees'} and n<0) or (field=='margin_pct' and not -100<=n<=100): return None
        return n
    if field in {'sector_fit','identity','international'}: return boolean(value)
    if field=='finance_year':
        return int(str(value)) if re.fullmatch(r'20\d{2}',str(value)) else None
    if field=='siren': return siren(value) or None
    if field in {'website','exhibitor_url','company_linkedin','contact_linkedin'}:
        return str(value).strip() if valid_url(value) else None
    if field in {'email','contact_email'}:
        return str(value).strip().lower() if re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',str(value)) else None
    if field=='finance_scope': return norm(value) if norm(value) in {'entite','groupe'} else None
    if field=='entity_type': return norm(value) if norm(value) in {'entite','groupe','marque'} else None
    if field=='business_model':
        text=norm(value)
        terms=['industriel','distributeur','negoce']
        return ', '.join(t for t in terms if t in text) or text
    return clean(value)[:6000] or None

def value(company,field,confirmed=False):
    fact=company.get('facts',{}).get(field,{})
    return fact.get('value') if (not confirmed or fact.get('status') in CONFIRMED) else None

def make_company(record,campaign,source):
    facts={}
    year=sanitize_field('finance_year',record.get('finance_year',''))
    scope=sanitize_field('finance_scope',record.get('finance_scope',''))
    for field,raw in record.items():
        if field not in FIELDS or field=='name' or field.startswith('contact_') or field=='status': continue
        val=sanitize_field(field,raw)
        if val is not None:
            facts[field]={'value':val,'status':'imported','source':source,'quote':str(raw), 'year':year,'scope':scope,'checked_at':db.now()}
    contacts=[]
    if any(record.get(k) for k in ['contact_name','contact_email','contact_linkedin']):
        contacts=[{'name':record.get('contact_name',''),'role':record.get('contact_role',''),
                   'email':sanitize_field('contact_email',record.get('contact_email','')) or '',
                   'phone':record.get('contact_phone',''),'linkedin':sanitize_field('contact_linkedin',record.get('contact_linkedin','')) or '',
                   'status':'imported','source':source,'quote':' | '.join(record.get(k,'') for k in ['contact_name','contact_role','contact_email']), 'checked_at':db.now()}]
    return {'id':db.uid(),'campaign_id':campaign['id'],'name':record['name'], 'facts':facts,'contacts':contacts,
            'triggers':[], 'events':[], 'memo':{}, 'source_rows':[source], 'research_status':'pending','match_decisions':{},'warnings':[]}

def merge_company(existing,new):
    for field,fact in new['facts'].items():
        previous=existing['facts'].get(field)
        if not previous: existing['facts'][field]=fact
        elif previous['value']!=fact['value']:
            if field in {'stand','hall'}:
                values=list(dict.fromkeys(str(previous['value']).split(' ; ')+[str(fact['value'])]))
                previous['value']=' ; '.join(values)
            else:
                existing['warnings'].append(f'Valeurs différentes pour {FIELDS.get(field,field)} : {previous["value"]} / {fact["value"]}')
    for contact in new['contacts']:
        signature=lambda x:(norm(x.get('name')),x.get('email'),x.get('linkedin'))
        if not any(signature(c)==signature(contact) for c in existing['contacts']): existing['contacts'].append(contact)
    existing['source_rows']+=new['source_rows']

def exclusion(company,crm):
    name=canonical_name(company['name']); site=domain(value(company,'website')); sid=siren(value(company,'siren'))
    ambiguous=[]
    for row in crm:
        if norm(row.get('status')) not in {'client','prospect','customer','lead'}: continue
        other=canonical_name(row.get('name'))
        why=None
        if sid and sid==siren(row.get('siren')): why='SIREN identique'
        elif site and site==domain(row.get('website')): why='Domaine identique'
        elif name and name==other: why='Nom normalisé identique'
        if why: return {'state':'excluded','reason':f'{why} avec {row["name"]} ({row["status"]})','crm_id':row['id']}
        ratio=SequenceMatcher(None,name,other).ratio()
        if min(len(name),len(other))>=8 and ratio>=0.88:
            decision=company.get('match_decisions',{}).get(row['id'],{})
            if decision.get('action')=='include': continue
            if decision.get('action')=='exclude': return {'state':'excluded','reason':f'Rapprochement confirmé avec {row["name"]}','crm_id':row['id']}
            ambiguous.append({'crm_id':row['id'],'name':row['name'],'similarity':round(ratio,3)})
    if ambiguous: return {'state':'review','reason':'Ressemblance avec la base existante à vérifier','matches':ambiguous}
    return {'state':'clear','reason':''}

def validate_criteria(data):
    c=copy.deepcopy(data)
    for field in ['target_count','backup_count','finance_max_age','tier1','tier2','research_limit']:
        if isinstance(c.get(field),bool) or not isinstance(c.get(field),int): raise ValueError(f'{field} doit être un entier.')
    if not 1<=c['target_count']<=200 or not 0<=c['backup_count']<=500 or not 1<=c['research_limit']<=500: raise ValueError('Nombre de cibles ou limite de recherche invalide.')
    if not 0<=c['tier2']<c['tier1']<=100: raise ValueError('Tier 1 doit être supérieur à Tier 2 et inférieur ou égal à 100.')
    if not 0<=c['finance_max_age']<=10: raise ValueError('Ancienneté financière invalide.')
    if not 0<=float(c['min_revenue_eur'])<1e14 or not -100<=float(c['min_margin_pct'])<=100: raise ValueError('Seuil financier invalide.')
    keys={'sector','international','finance','model','city','triggers'}
    if set(c.get('weights',{}))!=keys or any(not isinstance(v,(int,float)) or v<0 for v in c['weights'].values()) or sum(c['weights'].values())!=100:
        raise ValueError('Les six poids doivent être positifs ou nuls et totaliser 100.')
    for field in ['sectors','models','cities']:
        if not isinstance(c.get(field),list) or any(not isinstance(x,str) for x in c[field]): raise ValueError('Listes de critères invalides.')
    for field in ['allow_group_finance','require_international','require_stand']:
        if not isinstance(c.get(field),bool): raise ValueError('Options de critères invalides.')
    if 'progressive_selection' in c and not isinstance(c['progressive_selection'],bool): raise ValueError('Option de présélection invalide.')
    return c

def assess(company,criteria,crm,confirmed=False,*,crm_empty_confirmed=False):
    v=lambda f:value(company,f,confirmed)
    matches=exclusion(company,crm)
    year_now=datetime.now(ZoneInfo('Africa/Casablanca')).year
    finance=False
    for field in ['revenue_eur','margin_pct']:
        fact=company.get('facts',{}).get(field,{})
        n=v(field)
        year=fact.get('year') or v('finance_year')
        scope=fact.get('scope') or v('finance_scope')
        if n is not None and isinstance(year,int) and year_now-criteria['finance_max_age']<=year<=year_now and (scope=='entite' or (scope=='groupe' and criteria['allow_group_finance'] and v('entity_type')=='groupe')):
            finance|=n>criteria['min_revenue_eur'] if field=='revenue_eur' else n>=criteria['min_margin_pct']
    from .signals import partition
    evidence_triggers,pending_triggers=partition(company,confirmed)
    components={
        'sector':v('sector_fit') is True and (not company.get('facts',{}).get('sector_fit',{}).get('criteria_sectors') or company['facts']['sector_fit']['criteria_sectors']==criteria['sectors']),
        'international':v('international') is True,
        'finance':finance,
        'model':any(norm(m) in norm(v('business_model')) for m in criteria['models']),
        'city':bool(v('city')) and norm(v('city')) in set(map(norm,criteria['cities'])),
        'triggers':min(len({t['code'] for t in evidence_triggers})/4,1),
    }
    score=round(sum(criteria['weights'][k]*float(vv) for k,vv in components.items()),1)
    blockers=[]
    if matches['state']!='clear': blockers.append(matches['reason'])
    if not crm and not crm_empty_confirmed: blockers.append('Base clients/prospects absente : exclusion non vérifiée')
    if v('identity') is not True: blockers.append('Identité entreprise à confirmer')
    if not components['sector']: blockers.append('Secteur cible non confirmé')
    if not finance: blockers.append('CA > seuil ou marge ≥ seuil non documenté avec exercice et périmètre admissibles')
    if not components['model']: blockers.append('Modèle économique cible non confirmé')
    if criteria['require_international'] and not components['international']: blockers.append('International obligatoire non confirmé')
    if criteria['require_stand'] and not v('stand'): blockers.append('Stand obligatoire manquant')
    return {'score':score,'components':components,'exclusion':matches,'blockers':blockers,
            'trigger_review':[{'code':t.get('code'),'reason':reason} for t,reason in pending_triggers],
            'eligible':not blockers,'tier':1 if score>=criteria['tier1'] else 2 if score>=criteria['tier2'] else 3}

def rank(companies,criteria,crm,*,crm_empty_confirmed=False,selection_decisions=None):
    results=[]
    for company in companies:
        company=copy.deepcopy(company)
        company['selection_decision']=copy.deepcopy((selection_decisions or {}).get(company['id'],{}))
        company['assessment']=assess(company,criteria,crm,crm_empty_confirmed=crm_empty_confirmed)
        company['validated_assessment']=assess(company,criteria,crm,True,crm_empty_confirmed=crm_empty_confirmed)
        results.append(company)
    results.sort(key=lambda c:(-c['assessment']['score'],canonical_name(c['name']),c['id']))
    for c in results: c['selection']='none'
    available=lambda c:not c['selection_decision'].get('dismissed')
    valid=sorted([c for c in results if available(c) and c['validated_assessment']['eligible']],key=lambda c:(-c['validated_assessment']['score'],canonical_name(c['name']),c['id']))
    for c in valid[:criteria['target_count']]: c['selection']='validated'
    def can_propose(c):
        if not available(c) or c['selection']!='none' or c['assessment']['exclusion']['state']!='clear' or c['assessment']['score']<=0: return False
        if criteria.get('progressive_selection'):
            from .prioritization import screen
            return c.get('research_status')=='completed' and c['assessment']['eligible'] and screen(c,criteria)['candidate']
        if not criteria.get('select_after_full_research'): return True
        # Une sélection issue du lot complet exige les critères obligatoires.
        # L'absence de CRM reste visible et interdit la validation définitive.
        components=c['assessment']['components']
        return (c.get('research_status')=='completed' and value(c,'identity') is True
                and all(components[k] for k in ('sector','finance','model'))
                and (not criteria['require_international'] or components['international'])
                and (not criteria['require_stand'] or bool(value(c,'stand'))))
    proposals=[c for c in results if can_propose(c)]
    for c in proposals[:max(0,criteria['target_count']-len(valid[:criteria['target_count']]))]: c['selection']='proposed'
    if criteria.get('progressive_selection'):
        from .prioritization import apply_shortlist
        apply_shortlist(results,criteria)
    # Constituer la réserve après le Top : une promotion libère une place en réserve.
    backups=[c for c in results if available(c) and c['selection']=='none'
             and c['assessment']['exclusion']['state']=='clear'
             and (not criteria.get('progressive_selection') or
                  c.get('research_status')=='completed' and c['screening']['candidate'])]
    for c in backups[:criteria['backup_count']]: c['selection']='backup'
    return results

def contact_rows(companies,validated_only=False):
    rows=[]; seen=set()
    for c in companies:
        if validated_only and c.get('selection')!='validated': continue
        if c.get('assessment',{}).get('exclusion',{}).get('state')!='clear': continue
        contacts=c.get('contacts') or [{}]
        for contact in contacts:
            if validated_only and contact and contact.get('status')!='validated': continue
            email=contact.get('email') or value(c,'email',validated_only) or ''
            linkedin=contact.get('linkedin') or ''
            signature=(canonical_name(c['name']),email.lower(),norm(contact.get('name')),linkedin)
            if signature in seen: continue
            seen.add(signature)
            rows.append({'Entreprise':c['name'],'Segment':value(c,'category') or '', 'Email':email,
                         'Téléphone':contact.get('phone') or value(c,'phone',validated_only) or '',
                         'Stand':value(c,'stand',validated_only) or '', 'Hall':value(c,'hall',validated_only) or '',
                         'Site web':value(c,'website') or '', 'LinkedIn Entreprise':value(c,'company_linkedin') or '',
                         'Nom du contact':contact.get('name',''),'Fonction':contact.get('role',''), 'LinkedIn Contact':linkedin,
                         'Tier':c['validated_assessment']['tier'] if c.get('selection')=='validated' else c['assessment']['tier'],
                         'Score':c['validated_assessment']['score'] if c.get('selection')=='validated' else c['assessment']['score'],
                         'Type email':'Contact' if contact.get('email') else 'Entreprise',
                         'Qualification':c.get('selection','none'),'Statut contact':contact.get('status','imported'),
                         'Source':contact.get('source') or c.get('facts',{}).get('email',{}).get('source',''),
                         'Vérifié le':contact.get('checked_at','')})
    return rows

def research_key(company,campaign,criteria,model,configuration=None):
    cfg=configuration or {'provider':'openai','web_provider':'openai'}
    material={'name':company['name'],'website':value(company,'website'),'siren':value(company,'siren'),
              'exhibitor_url':value(company,'exhibitor_url'),'edition':campaign['edition'],'salon':campaign['name'],
              'criteria':criteria,'knowledge':db.setting('knowledge'),'model':model,'schema_version':8,
              'research_profile':cfg.get('research_profile','standard'),
              'provider':cfg['provider'],'web_provider':cfg['web_provider'],
              'ollama_url':cfg.get('ollama_url') if cfg['provider']=='ollama' else None}
    return hashlib.sha256(__import__('json').dumps(material,sort_keys=True).encode()).hexdigest()
