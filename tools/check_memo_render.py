"""Échantillon de mise en page, sans données commerciales réelles."""
from pathlib import Path
from impact import exports
from impact.config import ROOT,DEFAULT_CRITERIA

out=ROOT/'outputs'/'qa'
out.mkdir(parents=True,exist_ok=True)
fields={'activity':'Entreprise de contrôle du rendu avec plusieurs gammes de produits et plusieurs circuits commerciaux.',
        'category':'Agroalimentaire','stand':'A12 / B14','hall':'2','business_model':'industriel','city':'Lyon',
        'revenue_eur':12500000.0,'margin_pct':12.5,'finance_year':2025,'finance_scope':'entite','employees':150}
c={'name':'Entreprise de test du rendu','facts':{k:{'value':v,'status':'validated','source':'Échantillon de contrôle','quote':str(v),'year':2025,'scope':'entite'} for k,v in fields.items()},
   'memo':{'pitch':'Examiner les arbitrages de gamme et la contribution par circuit.',
           'pain_points':['Hypothèse à confirmer : la diversité des formats peut augmenter la complexité opérationnelle.'],
           'questions':['Comment mesurez vous la contribution de chaque référence ?'],
           'key_message':'Confirmer le besoin avant de proposer une méthode d’arbitrage.'},
   'triggers':[{'code':'T1','observation':'Plusieurs familles de produits dans le catalogue de contrôle.','status':'validated','source':'Échantillon de contrôle'}],
   'events':[], 'contacts':[], 'assessment':{'tier':1,'score':85},'validated_assessment':{'tier':1,'score':85},'selection':'validated'}
campaign={'name':'Salon de contrôle','edition':'2027','criteria':DEFAULT_CRITERIA}
(out/'memo_test.docx').write_bytes(exports.document_bytes(campaign,[c],True))
(out/'memo_test.pdf').write_bytes(exports.pdf_bytes(campaign,[c],True))
print(out)
