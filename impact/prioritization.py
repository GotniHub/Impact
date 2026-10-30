"""Préclassement local : indices d'intérêt, sans transformer une hypothèse en fait."""
from datetime import datetime
from .domain import value, canonical_name
from .importers import norm


SECTOR_TERMS={
    'agroalimentaire':('aliment','agro','food','surgel','laitier','fromag','boulanger','patisser',
                      'chocolat','boisson','biscuit','viande','poisson','caviar','charcut','epicer','pizza'),
    'industrie du froid':('refriger','frigor','industrie du froid','equipement froid','cold chain','chaine du froid'),
}


def screen(company,criteria):
    """Ce résultat guide la recherche ; il ne satisfait jamais un critère manquant."""
    a=company['assessment']; facts=company.get('facts',{})
    text=norm(' '.join(str(value(company,k) or '') for k in ('category','activity')))
    category=norm(value(company,'category') or '')
    activity=norm(value(company,'activity') or '')
    matches=[]
    for sector in criteria['sectors']:
        terms=SECTOR_TERMS.get(norm(sector),tuple(norm(sector).split()))
        if any(len(term)>2 and term in text for term in terms): matches.append(sector)
    known_sectors={norm(s) for s in criteria['sectors']}
    cold=any(t in text for t in SECTOR_TERMS['industrie du froid']) and 'industrie du froid' in known_sectors
    # Les fournisseurs de matériels pour l'alimentaire ne sont pas eux-mêmes
    # des producteurs alimentaires. Une association de fabricants ne l'est pas non plus.
    non_food_category=any(t in category for t in ('equipement','emballage','vaisselle','mobilier','informatique','logiciel','formation','conseil'))
    # Un segment « pâtisserie » peut désigner un fabricant d'accessoires.
    # Ces descriptions explicites de matériels ne prouvent aucune production alimentaire.
    non_food_activity=any(t in activity for t in ('rhodoid','feuilles guitare','bandes en acetate',
                          'plateaux pour gateau en carton','fabrication d emballages',
                          'fabrication de vaisselle','fabrication de moules'))
    association=any(t in activity[:250] for t in ('association','interprofession','syndicat professionnel'))
    incompatible=known_sectors<=SECTOR_TERMS.keys() and (((non_food_category or non_food_activity) and not cold) or association)
    sector=(a['components']['sector'] or bool(matches)) and not incompatible
    reasons=[]
    if incompatible: reasons.append('Activité de matériel, d’emballage ou d’association hors secteur cible')
    if a['components']['sector']: reasons.append('Secteur indiqué dans les données collectées')
    elif matches: reasons.append('Segment du fichier compatible : '+', '.join(matches))
    model=a['components']['model']
    model_hint=any(t in text for t in ('fabricant','fabrication','manufactur','industriel','distributeur','grossiste','negoce'))
    if model or model_hint: reasons.append('Indice de fabrication, distribution ou négoce')
    # Un critère explicitement défavorable ne devient pas une simple inconnue.
    contradicted=(value(company,'identity') is False or value(company,'sector_fit') is False
                  or bool(value(company,'business_model')) and not model)
    finance_known=set()
    for field in ('revenue_eur','margin_pct'):
        fact=facts.get(field,{})
        year=fact.get('year') or value(company,'finance_year')
        scope=fact.get('scope') or value(company,'finance_scope')
        if (value(company,field) is not None and type(year) is int
                and datetime.now().year-criteria['finance_max_age']<=year<=datetime.now().year
                and (scope=='entite' or scope=='groupe' and criteria['allow_group_finance']
                     and value(company,'entity_type')=='groupe')): finance_known.add(field)
    finance_failed=len(finance_known)==2 and not a['components']['finance']
    if finance_failed: contradicted=True
    for required,field in (('require_international','international'),('require_stand','stand')):
        if criteria[required] and value(company,field) is False: contradicted=True
    candidate=sector and not contradicted and a['exclusion']['state']=='clear'
    priority=(50*int(sector)+15*int(model or model_hint)+15*int(a['components']['finance'])
              +10*int(value(company,'international') is True)+5*int(bool(value(company,'website') or value(company,'exhibitor_url')))
              +5*int(a['components']['city']))
    return {'candidate':bool(candidate),'priority':priority,'reasons':reasons,
            'finance_status':'documenté' if a['components']['finance'] else 'hors seuil' if finance_failed else 'à documenter'}


def apply_shortlist(companies,criteria):
    for c in companies: c['screening']=screen(c,criteria)
    places=max(0,criteria['target_count']-sum(c['selection'] in {'validated','proposed'} for c in companies))
    pool=sorted([c for c in companies if c['selection'] not in {'validated','proposed'}
                 and not c.get('selection_decision',{}).get('dismissed') and c['screening']['candidate']],
                key=lambda c:(-c['screening']['priority'],-c['assessment']['score'],canonical_name(c['name']),c['id']))
    for c in pool[:places]: c['selection']='shortlisted'
    order={'validated':0,'proposed':1,'shortlisted':2,'backup':3,'none':4}
    companies.sort(key=lambda c:(order[c['selection']],-c['assessment']['score'] if c['selection'] in {'validated','proposed'}
                                else -c['screening']['priority'],-c['assessment']['score'],canonical_name(c['name']),c['id']))
    return companies
