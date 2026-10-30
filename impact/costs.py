"""Simulations locales des frais IA ; aucun appel réseau ni lecture de solde."""
import math
import re

PRICING_CHECKED = '2026-10-15'
PRICING_SOURCE = 'https://developers.openai.com/api/docs/pricing'
# Tarifs standard en dollars, hors taxes, cache, remises et contexte long.
MODEL_RATES = {
    'gpt-5.5': (5.0, 30.0),
    'gpt-5.4': (2.5, 15.0),
    'gpt-5.4-mini': (0.75, 4.5),
}
SCENARIOS = (
    ('courte', 10000, 3000, 3),
    ('centrale', 20000, 5000, 5),
    ('approfondie', 40000, 10000, 10),
)

def model_rates(model):
    for name,(input_price,output_price) in MODEL_RATES.items():
        # Un modèle Pro ou un alias inconnu ne doit pas hériter d'un tarif erroné.
        if re.fullmatch(re.escape(name)+r'(?:-\d{4}-\d{2}-\d{2})?', model):
            return {'input_per_million':input_price,'output_per_million':output_price,
                    'search_per_thousand':10.0,'checked_at':PRICING_CHECKED,
                    'source':f'https://developers.openai.com/api/docs/models/{name}'}
    return None

def configuration_rates(cfg):
    provider=cfg['provider']
    if provider=='openai': return model_rates(cfg['model'])
    source='https://ollama.com/pricing'
    if provider=='ollama': input_price=output_price=0.0
    else:
        source='https://ai.google.dev/gemini-api/docs/pricing'
        paid={'gemini-3.5-flash-lite':(0.30,2.50),'gemini-3.1-flash-lite':(0.25,1.50)}
        if cfg['model'] not in paid: return None
        input_price,output_price=(0.0,0.0) if cfg['gemini_tier']=='free' else paid[cfg['model']]
    rates={'input_per_million':input_price,'output_per_million':output_price,
           'search_per_thousand':None,'checked_at':PRICING_CHECKED,'source':source}
    if cfg['web_provider']=='serper': rates.update(search_per_thousand=1.0,pack_size=50000)
    elif cfg['web_provider']=='direct': rates['search_per_thousand']=0.0
    return rates

def estimate(company_count, rates):
    if isinstance(company_count,bool) or not isinstance(company_count,int) or company_count<0:
        raise ValueError('Le nombre d’entreprises doit être un entier positif ou nul.')
    prices={}
    for field in ('input_per_million','output_per_million','search_per_thousand'):
        raw=rates.get(field)
        if isinstance(raw,bool): raise ValueError('Tarifs invalides.')
        try: price=float(raw)
        except (TypeError,ValueError): raise ValueError('Tarifs invalides.') from None
        if not math.isfinite(price) or price<0: raise ValueError('Tarifs invalides.')
        prices[field]=price
    result={}
    for name,input_tokens,output_tokens,searches in SCENARIOS:
        # Les volumes couvrent les deux requêtes, le contenu web et le raisonnement.
        text=company_count*(input_tokens*prices['input_per_million']+output_tokens*prices['output_per_million'])/1_000_000
        searches_count=company_count*searches
        if rates.get('pack_size'):
            searches_count=math.ceil(searches_count/rates['pack_size'])*rates['pack_size']
        web=searches_count*prices['search_per_thousand']/1000
        result[name]={'text':round(text,2),'web':round(web,2),'total':round(text+web,2)}
    return result
