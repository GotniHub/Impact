"""Contrôles conservateurs supplémentaires pour le petit modèle local."""
import re
from datetime import datetime, timezone

from .domain import canonical_name, number
from .importers import norm


def numeric_quote(value,quote):
    text=quote.casefold().replace('\u202f',' ').replace('\u00a0',' ')
    for item in re.finditer(r'(?<!\w)[+-]?\d+(?:[ .,]\d+)*(?:\s*(?:milliards?|millions?|md|bn|[mk](?=\s|€|eur|$)))?',text):
        n=number(item[0].strip())
        if n is not None and abs(n-value)<=max(0.000001,abs(value)*0.000001): return True
    return False


def quoted_finance_scope(quote):
    text=norm(quote)
    entity=any(term in text for term in ('comptes sociaux','comptes individuels','individual accounts',
                                        'separate financial statements','standalone financial statements'))
    group=any(term in text for term in ('comptes consolides','consolidated accounts','consolidated financial statements'))
    # Un extrait qui mentionne les deux périmètres ne permet pas de rattacher
    # automatiquement le chiffre à un seul d'entre eux.
    return 'entite' if entity and not group else 'groupe' if group and not entity else None


def apply(result,company,documents):
    facts=result['facts']; warnings=result['warnings']
    now_year=datetime.now(timezone.utc).year
    rejected=[]
    for field,fact in list(facts.items()):
        quote=norm(fact.get('quote','')); val=fact.get('value')
        keep=True
        if field in {'revenue_eur','margin_pct','employees'}:
            keep=isinstance(val,(int,float)) and numeric_quote(val,fact.get('quote',''))
            if field=='margin_pct': keep=keep and any(w in quote for w in ('marge operationnelle','operating margin','resultat operationnel'))
        elif field=='identity' and val is True:
            names=[x for x in canonical_name(company['name']).split() if len(x)>2]
            keep=bool(names) and all(x in quote for x in names)
        elif field=='international' and val is True:
            keep=any(w in quote for w in ('international','export','a travers le monde','worldwide','plusieurs pays'))
        elif field=='sector_fit':
            generic=any(w in quote for w in ('decouvrir le salon','plan du salon','grandes familles du food','coupe du monde','bocuse d or'))
            keep=(not generic and len(quote.split())>=4 and
                  any(w in quote for w in ('aliment','agro','food','froid','refriger','surgel','laitier','boulanger','patisser','chocolat','boisson')))
        elif field=='business_model':
            required={'industriel':('fabricant','fabrication','produisons','manufactur','usine','production industrielle'),
                      'distributeur':('distributeur','distribuons','distribution','grossiste','wholesal'),
                      'negoce':('negoce','trading','grossiste')}
            keep=(not any(term in quote for term in ('association','regroupe des fabricants','regroupe plus de')) and
                  any(kind in str(val) and any(term in quote for term in terms) for kind,terms in required.items()))
        elif field=='entity_type':
            terms={'marque':('marque','brand'),'groupe':('groupe','group'),
                   'entite':('siren','siret','rcs','societe',' sas',' sarl')}
            keep=any(term in ' '+quote for term in terms.get(val,()))
        elif field=='finance_year':
            keep=(type(val) is int and val<=now_year and str(val) in quote
                  and any(term in quote for term in ('exercice','annual','year ended','comptes','chiffre','revenue')))
        elif field=='finance_scope':
            keep=quoted_finance_scope(fact.get('quote',''))==val
        if not keep:
            facts.pop(field,None); rejected.append(field)
    for metric in ('revenue_eur','margin_pct'):
        fact=facts.get(metric)
        if not fact: continue
        year=fact.get('year')
        year_fact=facts.get('finance_year',{})
        dated=(str(year) in fact.get('quote','') or
               (year_fact.get('value')==year and year_fact.get('source')==fact.get('source')))
        # Une date de salon ne constitue pas un exercice comptable.
        if type(year) is not int or year>now_year or not dated:
            fact['year']=None
            facts.pop('finance_year',None)
            warnings.append('Exercice financier local non prouvé : ce chiffre ne satisfait pas le critère financier.')
        scope=facts.get('finance_scope',{})
        claimed_scope=fact.get('scope')
        metric_scope=quoted_finance_scope(fact.get('quote',''))
        scope_proved=metric_scope==claimed_scope and claimed_scope in {'entite','groupe'}
        separate_proof=(scope.get('source')==fact.get('source') and scope.get('value')==claimed_scope
                        and quoted_finance_scope(scope.get('quote',''))==claimed_scope
                        and metric_scope in {None,claimed_scope})
        if not (scope_proved or separate_proof):
            fact['scope']='unknown'; facts.pop('finance_scope',None)
            warnings.append('Périmètre financier local non prouvé : vérification nécessaire.')
    if rejected: warnings.append('Contrôle local : faits non démontrés par leur extrait écartés ('+', '.join(rejected)+').')
    for contact in result.get('contacts',[]):
        if norm(contact.get('role','')) not in norm(contact.get('quote','')): contact['role']=''
    # Les petits modèles ont mélangé des faits et des hypothèses dans les essais.
    # Le mémo local reste donc un support standard, sans assertion commerciale ajoutée.
    result['memo']={
        'pitch':f"Échange exploratoire avec {company['name']} sur l’intérêt éventuel d’un accompagnement SENSO pour les gammes et les références produits.",
        'pain_points':['Hypothèse à confirmer en rendez-vous : besoin de prioriser les références et les gammes.'],
        'questions':['Comment pilotez-vous vos gammes et vos références aujourd’hui ?',
                     'Quels sujets souhaiteriez-vous améliorer et avec quels indicateurs ?'],
        'key_message':'Vérifier les faits et les besoins avant de proposer un accompagnement.',
        'generation_mode':'local_standard',
    }
    warnings.append('Mémo local standard : les chiffres, le classement et les contacts ne sont pas validés automatiquement.')
    result['warnings']=list(dict.fromkeys(warnings))
    return result
