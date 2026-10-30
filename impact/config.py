import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get('IMPACT_DATA_DIR', str(ROOT / 'data'))).resolve()

def load_env():
    path = ROOT / '.env'
    if path.exists():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

load_env()

TRIGGERS = {
    'T1': {'name': 'Portefeuille large et multiplication des SKU', 'signal': 'Nombreuses familles, marques, formats ou segments produits.'},
    'T2': {'name': 'Longue traîne à faible rotation', 'signal': 'Références nombreuses et faiblement contributives ; une preuve de rotation ou de contribution est nécessaire.'},
    'T3': {'name': 'Complexité industrielle', 'signal': 'Variantes de recettes, formats, emballages et changements de séries.'},
    'T4': {'name': 'Multi circuits et multi usages', 'signal': 'Retail, foodservice, artisans, export ou plusieurs usages distincts.'},
    'T5': {'name': 'Croissance faible et coûts en hausse', 'signal': 'Performance récente datée et pression sur les coûts documentée.'},
    'T6': {'name': 'Retour de la complexité', 'signal': 'Rationalisation passée puis réapparition documentée de références.'},
    'T7': {'name': 'Segments de valeur', 'signal': 'Bio, free from, nutrition, éthique ou certifications ; la surcomplexité reste une hypothèse.'},
    'T8': {'name': 'Mix produit dispersé', 'signal': 'Contribution ou priorisation des best sellers documentée ; un catalogue large seul ne suffit pas.'},
}

DEFAULT_CRITERIA = {
    'target_count': 20, 'backup_count': 50,
    'sectors': ['agroalimentaire', 'industrie du froid'],
    'models': ['industriel', 'distributeur', 'negoce'],
    'cities': ['Lyon', 'Nice', 'Bordeaux', 'Paris'],
    'min_revenue_eur': 10000000, 'min_margin_pct': 10,
    'finance_max_age': 3, 'allow_group_finance': False,
    'require_international': False, 'require_stand': False,
    'weights': {'sector':25, 'international':15, 'finance':20, 'model':15, 'city':10, 'triggers':15},
    'tier1':80, 'tier2':60, 'research_limit':30,
    'progressive_selection': True,
}

FIELDS = {
    'name':'Entreprise', 'website':'Site web', 'category':'Segment', 'activity':'Activité',
    'email':'Email entreprise', 'phone':'Téléphone entreprise', 'stand':'Stand', 'hall':'Hall',
    'exhibitor_url':'Page exposant', 'country':'Pays', 'city':'Ville', 'siren':'SIREN',
    'revenue_eur':'CA en EUR', 'margin_pct':'Marge opérationnelle en %',
    'finance_year':'Exercice financier', 'finance_scope':'Périmètre financier',
    'international':'Présence internationale', 'business_model':'Modèle économique',
    'sector_fit':'Adéquation sectorielle', 'identity':'Identité', 'entity_type':'Type d’entité', 'employees':'Effectif',
    'company_linkedin':'LinkedIn entreprise', 'contact_name':'Nom du contact',
    'contact_role':'Fonction du contact', 'contact_email':'Email du contact',
    'contact_phone':'Téléphone du contact', 'contact_linkedin':'LinkedIn du contact',
    'status':'Statut CRM',
}
