"""Séparer un signal SENSO documenté d'une proposition IA insuffisamment étayée."""
import re
from .config import TRIGGERS
from .importers import norm


def evidence_review(trigger):
    code=trigger.get('code')
    if code not in TRIGGERS: return False,'Code de trigger inconnu.'
    if trigger.get('status')=='validated': return True,'Vérifié manuellement.'
    raw=trigger.get('quote',''); text=norm(raw)
    if len(raw.strip())<12 or not str(trigger.get('source','')).startswith(('https://','http://')):
        return False,'Source ou extrait exploitable manquant.'
    has=lambda words:any(w in text for w in words)
    breadth=has(('catalog','portefeuille','assortiment','gammes','collections','references','sku','produits','products','marques','formats'))
    variety=has(('large','etendu','nombreu','plusieurs','multiple','diversif','variet','gammes','collections','ranges')) or raw.count(',')>=2 or raw.count('\n')>=2
    if code=='T1':
        count=bool(re.search(r'\b(?:[2-9]\d|\d{3,})\s+(?:marques|produits|references|products|sku)\b',text))
        catalogue_lines=len([line for line in raw.splitlines() if line.strip()])>=3 and has(('best seller','bestseller'))
        ok=breadth and (variety or count) or catalogue_lines
        reason='Diversité ou ampleur du portefeuille non démontrée dans cet extrait.'
    elif code=='T2':
        ok=has(('faible rotation','faiblement contribut','faible contribution','peu rentable','slow moving','slow-moving','low turnover','low contribution'))
        reason='La faible rotation ou contribution ne se déduit pas d’un catalogue ou de produits de niche.'
    elif code=='T3':
        variants=sum(has(group) for group in [('recette','recipe'),('format','grammage','size'),('emballage','packaging'),('ingredient',)])
        ok=has(('petites series','small batches','changement de serie','changeover','variantes','variants')) or variants>=2
        reason='Variantes ou contraintes industrielles non démontrées dans cet extrait.'
    elif code=='T4':
        channels=[('retail','grande distribution','gms','buraliste','commerce de proximite'),
                  ('foodservice','food service','restauration','restaurant','cuisinier','hotel','camping'),
                  ('artisan','boulanger','patissier'),('export','international'),
                  ('boutique','e commerce','en ligne','b2c'),('industrie','industriel','b2b')]
        ok=sum(has(group) for group in channels)>=2 or has(('multi circuits','multicanal','plusieurs circuits','nombreux circuits','multiple channels'))
        reason='Plusieurs circuits ou usages distincts non démontrés dans cet extrait.'
    elif code=='T5':
        ok=(has(('croissance faible','faible croissance','croissance est faible','croissance molle','croissance est molle','stagnation','ralentissement','baisse du chiffre','weak growth','slow growth','declining revenue')) and
            has(('hausse des couts','couts en hausse','pression sur les couts','inflation','rising costs','cost pressure')))
        reason='Performance et hausse des coûts non documentées ensemble.'
    elif code=='T6':
        ok=(has(('rationalis','suppression','reduction des references','nettoyage','simplif','reduced sku')) and
            has(('a nouveau','de nouveau','reappar','retour','repouss','again','creep')))
        reason='Un nettoyage passé et une nouvelle prolifération doivent être documentés.'
    elif code=='T7':
        ok=bool(re.search(r'\b(?:bio|organic|gluten|lactose|vegan|vegetal\w*|nutrition\w*|nutriscore|nutri score|ethique|equitable|certifi\w*|labels?|free from|responsable\w*|bien etre)\b',text))
        reason='Un positionnement premium seul ne démontre pas un segment bio, nutrition, free from ou éthique.'
    else:
        best=has(('best seller','bestseller','meilleures ventes','produits phares','references phares'))
        ok=(best and breadth and variety) or has(('contribution par reference','mix peu rentable','faible contribution du mix','underperforming mix'))
        reason='Best-sellers et dispersion du mix, ou contribution du mix, non démontrés dans cet extrait.'
    return bool(ok),'Signal étayé par l’extrait, à vérifier.' if ok else reason


def partition(company,confirmed=False):
    accepted=[]; pending=[]
    for trigger in company.get('triggers',[]):
        if confirmed and trigger.get('status')!='validated': continue
        ok,reason=evidence_review(trigger)
        if ok: accepted.append(trigger)
        else: pending.append((trigger,reason))
    return accepted,pending
