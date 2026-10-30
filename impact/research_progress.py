"""Compteurs de campagne distincts des tentatives d'un lot, sans écriture."""


def summarize(companies):
    counts={'total':0,'completed':0,'ambiguous':0,'error':0,'pending':0}
    for company in companies:
        status=company.get('research_status')
        key=status if status in {'completed','ambiguous','error'} else 'pending'
        counts['total']+=1
        counts[key]+=1
    counts['processed']=counts['total']-counts['pending']
    return counts


def outcome_text(counts):
    completed=counts['completed']; ambiguous=counts['ambiguous']; errors=counts['error']
    return (f'{completed} recherche'+(' terminée' if completed==1 else 's terminées')+
            f' · {ambiguous} identité'+(' ambiguë' if ambiguous==1 else 's ambiguës')+
            f' · {errors} erreur'+('' if errors==1 else 's')+f' · {counts["pending"]} non parcouru'+
            ('' if counts['pending']==1 else 's'))


def job_caption(job):
    status=job['status']
    labels={'completed':'Dernier lot terminé','completed_with_errors':'Dernier lot terminé avec erreurs',
            'interrupted':'Dernier lot interrompu','cancelled':'Dernier lot arrêté','failed':'Dernier lot en échec'}
    text=f'{labels.get(status,"Dernier lot")} : {job["done"]}/{job["total"]} tentatives effectuées'
    if status in {'completed','completed_with_errors'}:
        errors=len(job.get('errors',[]))
        if errors: text+=f' · {errors} erreur'+('' if errors==1 else 's')
    elif job.get('message'):
        text+=' · '+job['message']
    return text
