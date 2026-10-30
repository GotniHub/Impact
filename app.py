"""Lancer avec : python -m streamlit run app.py"""
import copy
import hashlib
import html
import io
import inspect
import json
from pathlib import Path
from zoneinfo import ZoneInfo
import pandas as pd
import streamlit as st
from impact import db, ai, services, exports, costs
from impact import providers
from impact import research_progress, research_timing
from impact import auth_ui,storage
from impact.config import ROOT, FIELDS, TRIGGERS
from impact.domain import value, validate_criteria, contact_rows, valid_url
from impact.importers import parse_file, table, normalize_records

st.set_page_config(page_title='IMPACT · Prospection salons',page_icon='◈',layout='wide',initial_sidebar_state='auto')

st.markdown('<style>'+(ROOT/'assets'/'impact.css').read_text(encoding='utf-8')+'</style>',unsafe_allow_html=True)
# Contrôle serveur avant toute lecture de campagne, initialisation métier ou commande.
current_user=auth_ui.require_login()

@st.cache_resource
def initialize(database_url):
    db.init(database_url=database_url)
    return True
initialize(storage.database_url())
auth_ui.session_watch()

PAGES=['Vue d’ensemble','Importer des exposants','Campagne et classement','Fiches mémos et exports','Clients et prospects','Critères et connaissances','Configuration IA']
PAGE_ICONS=['space_dashboard','upload_file','leaderboard','description','groups','tune','auto_awesome']
if 'next_page' in st.session_state:
    st.session_state['navigation']=st.session_state.pop('next_page')
if st.session_state.get('navigation') not in PAGES:
    st.session_state['navigation']=PAGES[0]

def select_page(page):
    st.session_state['navigation']=page

def select_campaign():
    st.session_state['active_campaign']=st.session_state['campaign_select']

def select_provider():
    db.setting('ai_provider',st.session_state['ai_provider_choice'])

def select_progressive(campaign_id):
    campaign=db.get('campaigns',campaign_id)
    campaign['criteria']['progressive_selection']=st.session_state['progressive_'+campaign_id]
    services.update_campaign_criteria(campaign_id,campaign['criteria'])

def goto(page):
    st.session_state['next_page']=page
    st.rerun()

with st.sidebar:
    st.markdown('<div class="brand">IMPACT</div><div class="brand-sub">PROSPECTION SALONS</div>',unsafe_allow_html=True)
    page=st.session_state['navigation']
    with st.container(key='sidebar_navigation'):
        for i,(title,icon) in enumerate(zip(PAGES,PAGE_ICONS)):
            if i in {0,4}:
                st.markdown('<div class="nav-section">'+('ESPACE DE TRAVAIL' if i==0 else 'RÉFÉRENTIELS ET RÉGLAGES')+'</div>',unsafe_allow_html=True)
            st.button(title,icon=f':material/{icon}:',key=f'nav_{i}',
                      type='primary' if title==page else 'secondary',use_container_width=True,
                      on_click=select_page,args=(title,))
    st.divider()
    campaigns=sorted(db.all_rows('campaigns'),key=lambda c:c['created'],reverse=True)
    ids=[c['id'] for c in campaigns]
    if ids:
        selected_id=st.session_state.get('active_campaign',ids[0])
        if selected_id not in ids: selected_id=ids[0]
        # Initialiser une fois, puis laisser le widget et son callback porter le choix.
        # Un index recalculé depuis l'ancien état réinitialise les anciennes versions de Streamlit.
        if st.session_state.get('campaign_select') not in ids:
            st.session_state['campaign_select']=selected_id
        campaign_id=st.selectbox('Campagne active',ids,
                                 format_func=lambda id:next(f'{c["name"]} · {c["edition"]}' for c in campaigns if c['id']==id),
                                 key='campaign_select',on_change=select_campaign)
        st.session_state['active_campaign']=campaign_id
    else: campaign_id=None
    default_provider=db.setting('ai_provider') or ai.config()['provider']
    if default_provider not in providers.LABELS: default_provider='openai'
    if st.session_state.get('ai_provider_choice') not in providers.LABELS:
        st.session_state['ai_provider_choice']=default_provider
    selected_provider=st.selectbox('Solution IA',list(providers.LABELS),
                                  format_func=providers.LABELS.get,key='ai_provider_choice',on_change=select_provider)
    AI_CONFIG=ai.config(selected_provider,(db.setting('ai_options') or {}).get(selected_provider,{}))
    st.caption('Configuration renseignée' if AI_CONFIG['configured'] else 'Configuration à compléter')
    st.caption('Import de tableaux et exports disponibles sans IA')
    st.divider()
    st.markdown('<div class="sidebar-account-label">COMPTE CONNECTÉ</div>'
                f'<div class="sidebar-account-email">{html.escape(current_user)}</div>',unsafe_allow_html=True)
    with st.container(key='auth_logout'):
        st.button('Se déconnecter',icon=':material/logout:',use_container_width=True,on_click=auth_ui.logout)

def hero(label,title,text):
    st.markdown(f'<div class="hero"><div class="eyebrow">{html.escape(label)}</div><h2>{html.escape(title)}</h2><p>{html.escape(text)}</p></div>',unsafe_allow_html=True)

def notify_error(exc):
    if isinstance(exc,(ValueError,ai.AIError,ImportError)):
        st.error(str(exc))
    else:
        st.error('Le traitement a échoué. Consulter le journal technique et réessayer avec un fichier plus simple.')
        import logging
        logging.exception('IMPACT error')

def table_width_options(widget):
    # Les anciennes versions de Streamlit attendent une largeur entière.
    if inspect.signature(widget).parameters['width'].default=='stretch':
        return {'width':'stretch'}
    return {'use_container_width':True}

def dataframe(rows,**kwargs):
    if rows: st.dataframe(pd.DataFrame(rows),hide_index=True,**table_width_options(st.dataframe),**kwargs)
    else: st.caption('Aucune ligne à afficher.')

def toggle(label,value=False,*,container=st,**kwargs):
    # Les libellés de confirmation doivent rester lisibles, y compris dans les colonnes.
    if 'wrap' in inspect.signature(st.toggle).parameters: kwargs['wrap']=True
    return container.toggle(label,value=value,**kwargs)

def selected_campaign():
    if not campaign_id:
        st.info('Importer un fichier pour créer la première campagne.')
        if st.button('Créer une campagne',type='primary'): goto('Importer des exposants')
        return None,None
    return services.campaign_results(campaign_id)

def delete_campaign_controls(campaign):
    id=campaign['id']
    count=len(db.all_rows('companies',id))
    running=any(j.get('status') in {'queued','running'} for j in db.all_rows('jobs',id))
    st.warning(f'Supprimer définitivement « {campaign["name"]} · {campaign["edition"]} » ? '
               f'Les {count} exposant(s), leurs contacts, mémos et traitements seront supprimés. '
               'La base clients/prospects sera conservée.')
    if running:
        st.info('Un traitement IA est en cours. Arrêtez-le dans Campagne et classement et attendez sa fin avant la suppression.')
    confirmed=toggle('Je confirme la suppression définitive de cette campagne.',key='delete_confirm_'+id)
    a,b=st.columns(2)
    if a.button('Confirmer la suppression',key='delete_confirm_button_'+id,disabled=not confirmed or running):
        try:
            services.delete_campaign(id)
        except Exception as exc:
            notify_error(exc)
        else:
            if st.session_state.get('active_campaign')==id:
                st.session_state.pop('active_campaign',None)
                st.session_state.pop('campaign_select',None)
            if st.session_state.get('export_package',{}).get('campaign_id')==id:
                st.session_state.pop('export_package',None)
            st.session_state.pop('last_completed_job',None)
            st.session_state.pop('delete_campaign_pending',None)
            st.session_state['success_message']=f'Campagne « {campaign["name"]} · {campaign["edition"]} » supprimée.'
            goto('Vue d’ensemble')
    if b.button('Annuler',key='delete_cancel_'+id):
        st.session_state.pop('delete_campaign_pending',None)
        goto('Vue d’ensemble')

def map_import(parsed,prefix):
    if not parsed['tables']:
        st.info('Ce document contient du texte libre. L’IA peut extraire une table ; vérifier ensuite chaque association.')
        st.caption('Cette extraction utilise la solution IA sélectionnée et peut être facturée en ligne. Pour un import sans IA, utiliser un tableau CSV ou XLSX.')
        if st.button('Extraire les exposants avec l’IA',disabled=not AI_CONFIG['text_configured'],key=prefix+'_extract'):
            try:
                with st.spinner('Extraction du document…'):
                    rows=ai.extract_records(parsed['text'],AI_CONFIG)
                headers=list(rows[0]); tab=table('Extraction IA',[headers]+[[r.get(h,'') for h in headers] for r in rows])
                parsed['tables']=[tab]; st.session_state[prefix+'_parsed']=parsed; st.rerun()
            except Exception as exc: notify_error(exc)
        with st.expander('Voir le texte extrait'): st.text(parsed['text'][:12000])
        return None,None
    index=st.selectbox('Feuille ou tableau',list(range(len(parsed['tables']))),format_func=lambda i:parsed['tables'][i]['name'],key=prefix+'_sheet')
    tab=parsed['tables'][index]
    st.caption(f'{len(tab["rows"])} lignes lues · {len(tab["headers"])} colonnes')
    dataframe([{k:v for k,v in r.items() if k!='_row'} for r in tab['rows'][:8]])
    mapping={}
    def field_select(field):
        options=['']+tab['headers']; suggestion=tab['mapping'].get(field,'')
        selected=st.selectbox(FIELDS[field],options,index=options.index(suggestion) if suggestion in options else 0,
                              format_func=lambda x:x or '— Non disponible —',key=f'{prefix}_{index}_{field}')
        if selected: mapping[field]=selected
    st.subheader('Associer les colonnes')
    cols=st.columns(3)
    for i,field in enumerate(['name','website','stand','category','email','phone']):
        with cols[i%3]: field_select(field)
    with st.expander('Contacts, données financières et autres colonnes'):
        cols=st.columns(3)
        rest=[f for f in FIELDS if f not in {'name','website','stand','category','email','phone','identity'}]
        for i,field in enumerate(rest):
            with cols[i%3]: field_select(field)
    return index,mapping

def upload_parser(prefix,example=False):
    uploaded=st.file_uploader('Fichier à importer',type=['xlsx','xls','xlsm','csv','tsv','json','docx','pdf','txt','html','htm'],key=prefix+'_file')
    filename,content=None,None
    if uploaded: filename=uploaded.name; content=uploaded.getvalue()
    if example and not uploaded:
        paths=sorted((ROOT/'examples').glob('*')) if (ROOT/'examples').exists() else []
        choices=['']+[p.name for p in paths if p.suffix.lower() in {'.xlsx','.csv','.json'}]
        selected=st.selectbox('Ou utiliser un exemple fourni',choices,format_func=lambda x:x or 'Choisir un exemple',key=prefix+'_example')
        if selected: filename=selected; content=(ROOT/'examples'/selected).read_bytes()
    if content:
        digest=hashlib.sha256(content).hexdigest()
        if st.session_state.get(prefix+'_digest')!=digest:
            try:
                parsed=parse_file(filename,content)
                st.session_state[prefix+'_parsed']=parsed; st.session_state[prefix+'_digest']=digest
            except Exception as exc: notify_error(exc); return None
        parsed=st.session_state[prefix+'_parsed']
        for warning in parsed['warnings']: st.caption(warning)
        return parsed
    return None

def overview():
    hero('Votre espace de préparation','Du fichier exposants au rendez vous préparé',
         'Importez votre liste, recherchez les entreprises avec l’IA et préparez vos cibles, vos contacts et vos fiches mémos dans un même espace.')
    crm=db.all_rows('crm'); total=sum(len(db.all_rows('companies',c['id'])) for c in campaigns)
    cols=st.columns(3); cols[0].metric('Campagnes',len(campaigns)); cols[1].metric('Entreprises importées',total); cols[2].metric('Clients et prospects à exclure',len(crm))
    st.write('')
    left,right=st.columns([2,1])
    with left:
        st.subheader('Vos campagnes')
        st.caption('Une campagne est votre dossier de prospection pour un salon et une édition : exposants, critères, classement, contacts et fiches mémos.')
        if not campaigns: st.info('Commencez avec votre fichier SIRHA ou un nouvel export exposants.')
        for c in campaigns:
            with st.container(border=True):
                a,b,d=st.columns([4,1,1]); a.markdown(f'**{c["name"]} · {c["edition"]}'); a.caption(c.get('source',''))
                if b.button('Ouvrir',key='open_'+c['id']):
                    st.session_state['active_campaign']=c['id']; st.session_state.pop('campaign_select',None); goto('Campagne et classement')
                if d.button('Supprimer',key='delete_'+c['id']):
                    st.session_state['delete_campaign_pending']=c['id']
                if st.session_state.get('delete_campaign_pending')==c['id']:
                    delete_campaign_controls(c)
        if st.button('Nouvelle campagne',type='primary'): goto('Importer des exposants')
    with right:
        with st.container(border=True):
            st.subheader('Le parcours')
            st.markdown('1. Importer et vérifier les colonnes\n2. Charger la base clients/prospects\n3. Enrichir avec l’IA\n4. Vérifier les sources et qualifier\n5. Exporter les mémos et contacts')
        if not AI_CONFIG['configured']: st.info('Vous pouvez importer vos tableaux, vérifier les informations, calculer le classement et exporter. Compléter Configuration IA pour activer la recherche automatique avec la solution sélectionnée.')

def imports_page():
    hero('01 · Import','Votre liste d’exposants est le point de départ','Le scraping reste en amont. Les noms de colonnes et les feuilles peuvent varier : vérifiez leur association avant de créer la campagne.')
    a,b=st.columns(2)
    salon=a.text_input('Nom du salon',key='new_salon')
    edition=b.text_input('Édition ou dates du salon',placeholder='Exemple : 2027 · 14 au 16 mars',key='new_edition')
    parsed=upload_parser('exhibitors',True)
    if parsed:
        index,mapping=map_import(parsed,'exhibitors')
        if index is not None:
            if not mapping.get('stand'): st.warning('Aucune colonne de stand associée. Les stands resteront manquants jusqu’à une recherche sourcée ou une saisie.')
            checked=toggle('J’ai vérifié les associations entreprise, stand et contacts.',key='import_checked')
            if st.button('Créer la campagne',type='primary',disabled=not checked):
                try:
                    campaign,warnings=services.import_campaign(parsed,index,mapping,salon,edition)
                    st.session_state['active_campaign']=campaign['id']; st.session_state.pop('campaign_select',None)
                    for warning in warnings: st.warning(warning)
                    goto('Campagne et classement')
                except Exception as exc: notify_error(exc)
    st.caption('PDF scannés, images et formats propriétaires : convertir en document texte ou en tableau avant import. Les exemples historiques ne sont pas ajoutés à la base clients/prospects.')

def facts_table(c):
    return [{'Champ':FIELDS.get(f,f),'Valeur':str(x['value']),'État':x.get('status',''), 'Source':x.get('source',''),
             'Extrait':x.get('quote',''),'Exercice':x.get('year'),'Périmètre':x.get('scope','')} for f,x in c['facts'].items()]

def review_company(company):
    st.subheader(company['name'])
    st.caption('Importé = lu dans votre fichier. Sourcé = proposition IA avec citation. Validé = information vérifiée par vous.')
    dataframe(facts_table(company),column_config={'Source':st.column_config.LinkColumn()})
    for warning in company.get('warnings',[]): st.caption('À vérifier : '+warning)
    if company['assessment']['exclusion']['state']=='review':
        for match in company['assessment']['exclusion']['matches']:
            with st.container(border=True):
                st.write(f'Ressemblance avec **{match["name"]}** ({match["similarity"]:.0%})')
                reason=st.text_input('Motif de décision',key=f'reason_{company["id"]}_{match["crm_id"]}')
                a,b=st.columns(2)
                for button,action in [(a,'exclude'),(b,'include')]:
                    if button.button('Même entreprise : exclure' if action=='exclude' else 'Entreprise différente',key=f'match_{action}_{company["id"]}_{match["crm_id"]}',disabled=not reason.strip()):
                        current=db.get('companies',company['id']); current['match_decisions'][match['crm_id']]={'action':action,'reason':reason}
                        db.put('companies',current); db.audit('crm_match_review',{'company_id':company['id'],'crm_id':match['crm_id'],'action':action,'reason':reason}); st.rerun()
    with st.form('review_'+company['id']):
        a,b=st.columns(2)
        edits={}
        edits['identity']=a.selectbox('Identité de l’entreprise confirmée',['','oui','non'],index=1 if value(company,'identity') is True else 2 if value(company,'identity') is False else 0)
        edits['sector_fit']=b.selectbox('Activité conforme aux secteurs cibles',['','oui','non'],index=1 if value(company,'sector_fit') is True else 2 if value(company,'sector_fit') is False else 0)
        edits['international']=a.selectbox('Présence internationale',['','oui','non'],index=1 if value(company,'international') is True else 2 if value(company,'international') is False else 0)
        edits['business_model']=b.text_input('Modèle économique',value=str(value(company,'business_model') or ''),placeholder='industriel, distributeur, negoce')
        edits['entity_type']=a.selectbox('Type d’entité',['','entite','groupe','marque'],index=['','entite','groupe','marque'].index(value(company,'entity_type')) if value(company,'entity_type') in ['','entite','groupe','marque'] else 0)
        for i,field in enumerate(['activity','city','country','website','stand','hall','email','phone','siren','category','company_linkedin']):
            edits[field]=(a if i%2==0 else b).text_input(FIELDS[field],value=str(value(company,field) or ''))
        edits['revenue_eur']=a.text_input('CA en EUR',value=str(value(company,'revenue_eur') if value(company,'revenue_eur') is not None else ''),help='Accepte 12 500 000 ou 12,5 M. Aucune conversion de devise automatique.')
        edits['margin_pct']=b.text_input('Marge opérationnelle en %',value=str(value(company,'margin_pct') if value(company,'margin_pct') is not None else ''))
        edits['finance_year']=a.text_input('Exercice financier',value=str(value(company,'finance_year') or ''))
        edits['finance_scope']=b.selectbox('Périmètre financier',['','entite','groupe'],index=['','entite','groupe'].index(value(company,'finance_scope')) if value(company,'finance_scope') in ['','entite','groupe'] else 0)
        st.markdown('**Contacts de l’entreprise**')
        contact_columns=['name','role','email','phone','linkedin','source','quote','status','checked_at']
        contacts_df=pd.DataFrame([{k:c.get(k,'') for k in contact_columns} for c in company['contacts']],columns=contact_columns)
        edited_contacts=st.data_editor(contacts_df,num_rows='dynamic',hide_index=True,**table_width_options(st.data_editor),key='contacts_'+company['id'],
                                      column_config={'name':'Nom','role':'Fonction','email':'Email','phone':'Téléphone','linkedin':st.column_config.LinkColumn('LinkedIn'),
                                                     'status':st.column_config.SelectboxColumn('État',options=['imported','sourced','manual','validated']), 'source':'Source','quote':'Extrait','checked_at':'Vérifié le'})
        triggers=copy.deepcopy(company.get('triggers',[])); events=copy.deepcopy(company.get('events',[]))
        if triggers:
            st.markdown('**Signaux proposés par l’IA**')
            for i,t in enumerate(triggers):
                from impact.signals import evidence_review
                supported,explanation=evidence_review(t)
                approve=toggle(f'{t["code"]} · {t["observation"]}',value=t.get('status')=='validated',key=f'trigger_{company["id"]}_{i}')
                st.caption(t.get('quote',''))
                if not supported: st.caption('Non compté dans le score : '+explanation)
                if valid_url(t.get('source')): st.link_button('Consulter la source',t['source'])
                t['status']='validated' if approve else 'sourced'
        for i,e in enumerate(events):
            approve=toggle(f'Évolution {e["year"]} · {e["event"]}',value=e.get('status')=='validated',key=f'event_{company["id"]}_{i}')
            if valid_url(e.get('source')): st.link_button('Consulter cette évolution',e['source'])
            e['status']='validated' if approve else 'sourced'
        options=list(edits)
        approve_fields=st.multiselect('Informations vérifiées à valider',options,default=[f for f in options if company['facts'].get(f,{}).get('status')=='validated'],format_func=lambda f:FIELDS.get(f,f))
        confirmation=toggle('J’ai contrôlé les sources des champs sélectionnés et la correspondance de l’entreprise.')
        submitted=st.form_submit_button('Enregistrer la vérification',type='primary')
    if submitted:
        try:
            if approve_fields and not confirmation: raise ValueError('Confirmer le contrôle des informations sélectionnées avant leur validation.')
            contacts=[]
            for row in edited_contacts.fillna('').to_dict('records'):
                if not any(row.get(k) for k in ['name','email','linkedin']): continue
                if row.get('email') and not __import__('re').fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',row['email']): raise ValueError('Email contact invalide.')
                if row.get('linkedin') and not valid_url(row['linkedin']): raise ValueError('URL du contact invalide.')
                if row.get('status')=='validated' and not confirmation: raise ValueError('Confirmer le contrôle des contacts validés.')
                row['checked_at']=db.now(); contacts.append(row)
            for field,raw in edits.items():
                if raw and __import__('impact.domain',fromlist=['sanitize_field']).sanitize_field(field,raw) is None: raise ValueError(f'{FIELDS[field]} : valeur invalide.')
            services.save_review(company['id'],edits,contacts,triggers,events,approve_fields)
            st.success('Vérification enregistrée.'); st.rerun()
        except Exception as exc: notify_error(exc)

@st.fragment(run_every='3s')
def campaign_research_progress(id):
    auth_ui.guard_session()
    counts=research_progress.summarize(db.all_rows('companies',id))
    title=f'{counts["processed"]}/{counts["total"]} exposants parcourus'
    if counts['total'] and counts['processed']==counts['total']:
        title+=' — toute la liste a été parcourue'
    st.info('**'+title+'.**\n\n'+research_progress.outcome_text(counts)+'.')
    explanation='Le total parcouru inclut les recherches terminées, les identités ambiguës et les erreurs. '
    if counts['error'] or counts['ambiguous']:
        explanation+='Les erreurs et les identités ambiguës restent à résoudre. '
    st.caption(explanation+'Une recherche terminée reste distincte d’une vérification humaine.')
    timing=research_timing.summarize(db.all_rows('jobs',id))
    if timing['entries']:
        total=research_timing.format_duration(timing['seconds']) if timing['known_count'] else 'Non enregistré'
        if timing['partial'] and timing['known_count']: total='Au moins '+total
        latest=timing['latest']; latest_job=latest['job']
        st.markdown('<div class="research-timing"><div class="research-timing-item">'
                    '<span class="research-timing-label">Temps cumulé connu</span>'
                    f'<strong>{html.escape(total)}</strong>'
                    f'<span class="research-timing-note">{timing["known_count"]} lots avec une durée disponible sur {len(timing["entries"])}</span>'
                    '</div><div class="research-timing-item"><span class="research-timing-label">Durée du dernier lot</span>'
                    f'<strong>{research_timing.format_duration(latest["seconds"])}</strong>'
                    f'<span class="research-timing-note">{latest_job["done"]}/{latest_job["total"]} tentatives effectuées</span>'
                    '</div></div>',unsafe_allow_html=True)
        if timing['partial']:
            st.caption('Historique partiel : certaines durées anciennes sont incomplètes ou absentes. '
                       'Les durées manquantes ne sont pas inventées ; le cumul connu est un minimum.')
        with st.expander('Détail du temps de traitement'):
            st.write('Le cumul additionne les durées disponibles des lots de cette campagne, essais et reprises compris. '
                     'Les attentes des services sont incluses ; les périodes sans traitement entre deux lots ne sont pas ajoutées. '
                     'L’affichage est en heures et minutes, à la minute inférieure.')
            statuses={'completed':'Terminé','completed_with_errors':'Terminé avec erreurs','running':'En cours',
                      'queued':'En attente','cancelled':'Arrêté','interrupted':'Interrompu','failed':'En échec'}
            rows=[]
            for entry in timing['entries']:
                job=entry['job']; start=research_timing.timestamp(job.get('started_at') or job.get('created'))
                rows.append({'Lancement':start.astimezone(ZoneInfo('Africa/Casablanca')).strftime('%d/%m/%Y %H:%M') if start else 'Non enregistré',
                             'Lot':f'{job["done"]}/{job["total"]} tentatives',
                             'État':statuses.get(job['status'],job['status']),
                             'Durée':research_timing.format_duration(entry['seconds']),
                             'Précision':'En cours' if entry['live'] else 'Complète' if entry['complete'] else
                                         'Partielle' if entry['seconds'] is not None else 'Non enregistrée'})
            dataframe(rows)


@st.fragment(run_every='3s')
def job_progress(id):
    auth_ui.guard_session()
    jobs=sorted(db.all_rows('jobs',id),key=lambda j:j['created'],reverse=True)
    if not jobs: return
    job=jobs[0]
    if job['status'] in {'queued','running'}:
        st.progress(min(1,max(0,job['done']/max(job['total'],1))),
                    text=f'Lot en cours : {job["done"]}/{job["total"]} tentatives effectuées · {job["message"]}')
        if st.button('Arrêter après l’entreprise en cours',key='cancel_'+job['id']): services.cancel_job(job['id'])
    else:
        st.caption(research_progress.job_caption(job))
        if st.session_state.get('last_completed_job')!=job['id']:
            st.session_state['last_completed_job']=job['id']; st.rerun()
    if job['errors']:
        with st.expander('Erreurs de recherche'): dataframe(job['errors'])

def research_cost_preview(companies,limit,refresh,configuration=None):
    cfg=configuration or AI_CONFIG
    st.subheader('Estimation avant recherche')
    model=AI_CONFIG['model']
    candidates=services.research_candidates(companies,refresh,priority=cfg.get('research_scope')=='priority')
    batch_count=min(int(limit),len(candidates))
    st.caption(f'{AI_CONFIG["label"]} · Modèle : {model} · {batch_count} entreprise(s) dans ce lot · '
               f'{len(candidates)} entreprise(s) à rechercher au total. '
               'Les exclusions et les entreprises déjà traitées sont prises en compte selon les options ci-dessus.')
    st.caption('Calcul local gratuit, disponible sans clé IA. Aucun appel payant n’est lancé pour afficher cette estimation.')
    rates=costs.configuration_rates(AI_CONFIG)
    web_unknown=rates is not None and rates['search_per_thousand'] is None
    manual=toggle('Saisir ou ajuster les tarifs de la simulation',key='estimate_manual_'+model,value=rates is None)
    if manual:
        defaults=rates or {'input_per_million':0.0,'output_per_million':0.0,'search_per_thousand':10.0}
        cols=st.columns(3)
        rates={
            'input_per_million':cols[0].number_input('Lecture ($ / million de tokens)',min_value=0.0,value=defaults['input_per_million'],step=0.01,key='estimate_input_'+model),
            'output_per_million':cols[1].number_input('Production ($ / million de tokens)',min_value=0.0,value=defaults['output_per_million'],step=0.01,key='estimate_output_'+model),
            'search_per_thousand':cols[2].number_input('Recherche web ($ / 1 000 appels)',min_value=0.0,value=defaults['search_per_thousand'] or 0.0,step=0.01,key='estimate_search_'+model),
        }
        if AI_CONFIG['web_provider']=='serper': rates['pack_size']=50000
        web_unknown=False
        if not rates['input_per_million'] and not rates['output_per_million'] and costs.configuration_rates(AI_CONFIG) is None:
            st.info('Saisir les tarifs du modèle pour obtenir une estimation. Aucun tarif gratuit n’est supposé pour ce modèle.')
            rates=None
    elif rates is None:
        st.info('Tarif non référencé pour ce modèle. Activer la saisie des tarifs pour calculer une estimation.')
    if rates is not None:
        if web_unknown:
            st.info('Recherche web Ollama : accès gratuit limité. Quota disponible et coût hors quota inconnus ; '
                    'les montants ci-dessous concernent seulement l’analyse IA, sans total web garanti.')
        if AI_CONFIG['provider']=='gemini' and AI_CONFIG['gemini_tier']=='free':
            st.caption('Analyse Gemini à 0 $ uniquement si votre clé relève bien de l’offre gratuite et de ses quotas. '
                       'Ce choix dans IMPACT ne change pas la facturation de votre compte Google.')
        if AI_CONFIG['web_provider']=='direct':
            st.info('Lecture des sites fournis : aucune API de recherche. Les refus et limitations des sites sont respectés. '
                    'La couverture est plus limitée qu’une recherche sur tout le web ; les informations absentes restent inconnues.')
            if AI_CONFIG['provider']=='ollama':
                st.caption('Analyse sur ce PC : aucun frais de tokens ni quota d’API IA. Le matériel, Internet et l’électricité restent nécessaires.')
        rows=[]
        for label,count in [('Ce lot',batch_count),('Toute la recherche restante',len(candidates))]:
            scenarios=costs.estimate(count,{**rates,'search_per_thousand':rates['search_per_thousand'] or 0.0})
            row={'Périmètre':label,'Entreprises':count}
            for key,title in [('courte','Simulation courte'),('centrale','Simulation centrale'),('approfondie','Simulation approfondie')]:
                row[title+(' (analyse seule)' if web_unknown else '')]=f'{scenarios[key]["total"]:,.2f} $'.replace(',',' ').replace('.',',')
            rows.append(row)
        dataframe(rows)
        st.caption('Montants en dollars, hors taxes. Ces simulations ne sont ni un devis ni un plafond de dépense. '
                   'Le coût réel dépend des pages trouvées, du texte traité, du raisonnement et des reprises. '
                   'Le cache peut réduire le coût ; la simulation suppose une nouvelle recherche pour chaque entreprise.')
    if AI_CONFIG['web_provider']=='serper':
        st.caption('Recherche Serper : simulation avec achat de packs de 50 000 recherches ; les crédits déjà disponibles ne sont pas consultés. [Tarifs Serper](https://serper.dev/).')
    source=costs.configuration_rates(AI_CONFIG)
    if source and not manual:
        st.caption(f'Tarifs vérifiés le {source["checked_at"]} : [modèle]({source["source"]}).')
    else:
        st.caption(f'Les tarifs saisis servent uniquement à la simulation. [Vérifier les tarifs officiels]({costs.PRICING_SOURCE}).')
    if toggle('Voir les hypothèses de calcul',key='estimate_assumptions'):
        st.caption('Hypothèses par entreprise, tous appels inclus : courte = 10 000 tokens lus, 3 000 produits, 3 recherches ; '
                   'centrale = 20 000 lus, 5 000 produits, 5 recherches ; approfondie = 40 000 lus, 10 000 produits, 10 recherches. '
                   'Le texte des sources web est inclus. Ces hypothèses ne modifient pas le traitement lancé.')
    st.caption('L’estimation porte sur la recherche des entreprises. Le solde de votre compte n’est pas consulté.')
    return batch_count

@st.dialog('Comprendre le score',width='large')
def score_guide_dialog(campaign,companies):
    auth_ui.guard_session()
    from impact.score_guide import render_score_guide
    render_score_guide(campaign,companies)
    if st.button('Fermer l’explication',key='close_score_guide',icon=':material/close:'):
        st.rerun()


def selection_controls(campaign,companies):
    with st.expander('Remplacer des cibles',icon=':material/swap_horiz:'):
        st.caption('Écartez une entreprise pour cette campagne : la présélection est recalculée avec les meilleurs candidats restants. '
                   'Les données sont conservées. Aucun appel IA n’est lancé et aucune vérification humaine n’est ajoutée.')
        choices={c['id']:c for c in companies if c['selection'] in {'validated','proposed','shortlisted','backup'}}
        with st.form('dismiss_selection_'+campaign['id']):
            ids=st.multiselect('Entreprises à écarter',list(choices),
                               format_func=lambda id:choices[id]['name'],key='dismiss_ids_'+campaign['id'])
            reason=st.text_input('Motif',value='Ne convient pas à cette campagne',max_chars=500)
            submit=st.form_submit_button('Écarter et recalculer le Top',type='primary',disabled=not choices)
        if submit:
            try:
                result=services.set_selection_dismissed(campaign['id'],ids,reason=reason)
                st.session_state.pop('export_package',None)
                st.session_state['selection_notice']=(campaign['id'],result)
                st.rerun()
            except ValueError as exc: st.error(str(exc))
        dismissed=[c for c in companies if c.get('selection_decision',{}).get('dismissed')]
        if dismissed:
            st.markdown('**Entreprises écartées de cette campagne**')
            dataframe([{'Entreprise':c['name'],'Motif':c['selection_decision'].get('reason','')} for c in dismissed])
            restore=st.selectbox('Entreprise à réintégrer',[c['id'] for c in dismissed],
                                 format_func=lambda id:next(c['name'] for c in dismissed if c['id']==id),
                                 key='restore_selection_'+campaign['id'])
            if st.button('Réintégrer dans le classement',key='restore_selection_button_'+campaign['id']):
                result=services.set_selection_dismissed(campaign['id'],[restore],dismissed=False)
                st.session_state.pop('export_package',None)
                st.session_state['selection_notice']=(campaign['id'],result)
                st.rerun()
    notice=st.session_state.pop('selection_notice',None)
    if notice and notice[0]==campaign['id']:
        result=notice[1]
        st.success('Classement recalculé. '+('Nouvelles cibles : '+', '.join(result['added'])+'.' if result['added'] else 'Décision enregistrée.'))
        st.caption('Dans Fiches mémos et exports, préparez de nouveaux fichiers pour obtenir cette sélection.')


def campaign_page():
    campaign,companies=selected_campaign()
    if not campaign: return
    hero('02 · Recherche et sélection',f'{campaign["name"]} · {campaign["edition"]}',
         'Préclassez votre fichier, recherchez les exposants prioritaires et suivez les informations encore à documenter. La vérification manuelle reste distincte de la sélection automatique.')
    progressive=campaign['criteria'].get('progressive_selection',False)
    toggle('Présélection progressive sans attendre toute la liste',value=progressive,
           key='progressive_'+campaign['id'],on_change=select_progressive,args=(campaign['id'],))
    crm=db.all_rows('crm')
    if not crm:
        if campaign.get('crm_review',{}).get('empty_confirmed') is True:
            st.caption('Pour cette campagne de test, vous avez confirmé n’avoir aucune entreprise à exclure. '
                       'Les faits sourcés par l’IA restent à vérifier avant validation.')
        else: st.warning('La base clients/prospects est vide. Les exclusions ne peuvent pas être vérifiées et aucune cible ne sera qualifiée définitivement.')
    campaign_research_progress(campaign['id'])
    if campaign.get('selection_deferred'):
        candidates_found=sum(c.get('research_status')=='completed' and c['assessment']['eligible'] for c in companies)
        st.caption(f'{candidates_found} candidat(s) répondent aux critères d’après les données sourcées, à vérifier. '
                   'La sélection finale attend la fin du traitement complet.')
    selected_count=sum(c['selection'] in {'validated','proposed','shortlisted'} for c in companies)
    if progressive:
        st.caption('La présélection est recalculée à chaque actualisation du classement. '
                   'Elle peut manquer de données et ne garantit pas que tous les critères sont satisfaits.')
    cols=st.columns(4)
    cols[0].metric('Exposants',len(companies))
    cols[1].metric('Présélection automatique',f'{selected_count} / {campaign["criteria"]["target_count"]}')
    cols[2].metric('Critères documentés',sum(c['selection'] in {'validated','proposed'} for c in companies))
    cols[3].metric('Vérifiées manuellement',sum(c['selection']=='validated' for c in companies))
    st.caption('Les critères documentés proviennent des données collectées ; leur exactitude reste à vérifier. '
               f'Exclus ou à rapprocher : {sum(c["assessment"]["exclusion"]["state"]!="clear" for c in companies)}.')
    with st.expander('Pourquoi certaines entreprises restent à documenter ?'):
        researched_rows=[c for c in companies if c.get('research_status')=='completed']
        dataframe([{'Critère':label,'Documenté sur les entreprises recherchées':sum(bool(c['assessment']['components'][key]) for c in researched_rows)}
                   for key,label in [('sector','Secteur'),('model','Fabrication / distribution / négoce'),('finance','CA ou marge, avec exercice et périmètre')]])
        st.caption('CA strictement supérieur au seuil OU marge opérationnelle au moins égale au seuil. '
                   'Un chiffre absent ne prouve ni la réussite ni l’échec du critère. Les seuils restent inchangés.')
    with st.expander('Lancer l’enrichissement IA',expanded=not any(c.get('research_status')=='completed' for c in companies)):
        a,b=st.columns([2,1]); limit=a.number_input('Nombre maximal d’entreprises de ce lot',1,500,campaign['criteria']['research_limit'],key='research_limit_'+campaign['id'])
        refresh=toggle('Rechercher à nouveau les entreprises déjà traitées',container=b)
        run_config=copy.deepcopy(AI_CONFIG)
        if progressive:
            priority=toggle('Rechercher seulement les exposants prioritaires',value=AI_CONFIG['provider']=='ollama' and AI_CONFIG['web_provider']=='direct',key='priority_'+campaign['id'])
            if priority:
                run_config['research_scope']='priority'
                st.caption('Préclassement local de tout le fichier, puis recherche des candidats compatibles avec le secteur. '
                           'Ce parcours accélère la sélection ; il ne constitue pas une recherche approfondie de chaque exposant.')
        if AI_CONFIG['provider']=='ollama' and AI_CONFIG['web_provider']=='direct':
            if toggle('Qualification locale rapide',value=True,key='quick_'+campaign['id']):
                run_config['research_profile']='quick'
                st.caption('Deux pages maximum et extraction courte des critères. Contacts importés conservés ; '
                           'contacts nominatifs, signaux détaillés et mémo personnalisé demandent un approfondissement.')
                st.caption('Les triggers T1 à T8 ne sont pas recherchés à nouveau dans ce mode ; les signaux déjà obtenus sont conservés.')
        all_local=False
        if AI_CONFIG['provider']=='ollama' and AI_CONFIG['web_provider']=='direct':
            all_local=toggle('Traiter toutes les entreprises restantes en local',key='all_local_'+campaign['id'])
            if all_local:
                run_config.pop('research_scope',None)
                limit=max(1,len(services.research_candidates(companies,refresh)))
                st.caption('Passage sur toute la liste : plus long. Les erreurs et sites inaccessibles seront signalés.')
        batch_count=research_cost_preview(companies,limit,refresh,run_config)
        calls=batch_count*(2 if AI_CONFIG['provider']=='openai' else 1)
        st.caption(f'{calls} requêtes d’analyse IA prévues hors reprises. '+
                   ('Lecture des pages fournies sans API de recherche.' if AI_CONFIG['web_provider']=='direct' else 'Le service de recherche web a ses propres tarifs et quotas.'))
        if st.button('Rechercher et préparer les fiches',type='primary',disabled=not AI_CONFIG['configured'] or not batch_count):
            try:
                job=services.start_job(campaign['id'],int(limit),refresh,run_config)
                if all_local:
                    campaign['full_research_job_id']=job['id']
                    campaign['criteria']['select_after_full_research']=True
                    campaign.pop('selection_deferred',None); db.put('campaigns',campaign)
                st.rerun()
            except Exception as exc: notify_error(exc)
        if not AI_CONFIG['configured']: st.info('Configuration IA à compléter : '+' ; '.join(AI_CONFIG['missing']))
        st.caption('Changer de solution conserve les résultats existants. Pour les comparer, activer la nouvelle recherche des entreprises déjà traitées. Un lot en cours conserve sa solution initiale.')
    job_progress(campaign['id'])
    if st.button('Actualiser le classement'): st.rerun()
    tabs=st.tabs(['Classement','Vérifier une entreprise','Exclusions et rapprochements','Règles de la campagne'])
    with tabs[0]:
        toolbar=st.columns([3,1.35],vertical_alignment='center')
        toolbar[0].markdown('#### Classement des exposants')
        with toolbar[1],st.container(key='score_help_action'):
            if st.button('Comprendre le score',key='open_score_guide',icon=':material/calculate:',use_container_width=True):
                score_guide_dialog(campaign,companies)
        selection_controls(campaign,companies)
        filters=st.columns(2)
        search=filters[0].text_input('Rechercher une entreprise')
        selection=filters[1].selectbox('Afficher',['Toutes','Présélection automatique','Critères documentés','Vérifiées manuellement','À documenter','Réserve','Écartées'])
        choices={'Présélection automatique':{'validated','proposed','shortlisted'},'Critères documentés':{'validated','proposed'},
                 'Vérifiées manuellement':{'validated'},'À documenter':{'shortlisted'},'Réserve':{'backup'}}
        view=[c for c in companies if search.lower() in c['name'].lower() and
              (selection=='Toutes' or selection=='Écartées' and c.get('selection_decision',{}).get('dismissed')
               or selection in choices and c['selection'] in choices[selection])]
        rows=[]
        labels={'validated':'Vérifiée manuellement','proposed':'Critères documentés · à vérifier',
                'shortlisted':'Présélection · à documenter','backup':'Réserve','none':'Hors sélection actuelle'}
        for c in view:
            a=c['validated_assessment'] if c['selection']=='validated' else c['assessment']
            rows.append({'Entreprise':c['name'],'Sélection':'Écartée de cette campagne' if c.get('selection_decision',{}).get('dismissed') else labels[c['selection']],'Tier':a['tier'],'Score':a['score'],
                         'Stand':value(c,'stand') or 'Manquant','Hall':value(c,'hall') or '',
                         'Recherche':{'completed':'Terminée','pending':'À rechercher','error':'Erreur','ambiguous':'Identité ambiguë'}.get(c.get('research_status'),c.get('research_status','À rechercher')),
                         'Exclusion':c['assessment']['exclusion']['reason'],
                         'À documenter':' ; '.join(a['blockers']),
                         'Vérification humaine':'Effectuée' if c['selection']=='validated' else 'À faire'})
        st.caption('Le score /100 mesure l’intérêt commercial des données disponibles, pas leur fiabilité. '
                   'La présélection utilise aussi les segments du fichier ; ces indices ne sont pas ajoutés comme faits vérifiés.')
        dataframe(rows)
    with tabs[1]:
        ids=[c['id'] for c in companies]
        if ids:
            id=st.selectbox('Entreprise à vérifier',ids,format_func=lambda id:next(c['name'] for c in companies if c['id']==id))
            review_company(next(c for c in companies if c['id']==id))
    with tabs[2]:
        dataframe([{'Entreprise':c['name'],'Décision':c['assessment']['exclusion']['state'],'Raison':c['assessment']['exclusion']['reason']} for c in companies if c['assessment']['exclusion']['state']!='clear'])
    with tabs[3]:
        st.json(campaign['criteria'])
        if st.button('Appliquer les critères actuels à cette campagne'):
            services.update_campaign_criteria(campaign['id'],db.setting('criteria')); st.rerun()
    with st.expander('Supprimer cette campagne'):
        delete_campaign_controls(campaign)

def exports_page():
    campaign,companies=selected_campaign()
    if not campaign: return
    hero('03 · Livrables','Des fiches pour le terrain et des contacts exploitables','Le stand et le hall apparaissent en tête de chaque mémo. Les faits, les signaux observés et les hypothèses commerciales sont distingués.')
    mode=st.radio('Contenu à exporter',list(exports.EXPORT_MODES),horizontal=True)
    crm=db.all_rows('crm')
    signature=exports.export_signature(campaign,companies,crm,mode)
    confirmed=mode=='Vérifiées manuellement'
    selected=exports.selected_for_export(companies,mode)
    if mode=='Présélection automatique': st.caption('Comprend des entreprises à documenter. Le CA manquant reste inconnu ; aucune conformité complète n’est supposée.')
    st.caption(f'{len(selected)} fiche(s) dans ce mode · objectif {campaign["criteria"]["target_count"]}')
    if not selected: st.info('Aucune cible dans ce mode. Enrichir ou vérifier les entreprises, ou consulter le classement.')
    if selected:
        assessment_key='validated_assessment' if confirmed else 'assessment'
        tier_columns=st.columns(3)
        for tier,column in enumerate(tier_columns,1):
            column.metric(f'Tier {tier}',sum(c[assessment_key]['tier']==tier for c in selected))
        st.caption('Tier 1 = priorité la plus forte ; Tier 3 = priorité secondaire. Le niveau reste provisoire si des informations manquent. '
                   'Les exports sont regroupés par Tier 1, Tier 2 puis Tier 3.')
        tier_filter=st.selectbox('Tier de l’aperçu',[0,1,2,3],format_func=lambda t:'Tous les tiers' if t==0 else exports.tier_title(t))
        preview_companies=[c for c in selected if tier_filter==0 or c[assessment_key]['tier']==tier_filter]
        if not preview_companies: st.info('Aucune fiche dans ce tier pour le contenu sélectionné.')
        preview=st.selectbox('Aperçu de fiche',[c['id'] for c in preview_companies],format_func=lambda id:next(f'Tier {c[assessment_key]["tier"]} · {c["name"]}' for c in preview_companies if c['id']==id)) if preview_companies else None
    else: preview=None
    if preview:
        c=next(c for c in selected if c['id']==preview)
        with st.container(border=True):
            st.markdown('**'+exports.tier_title(c[assessment_key]['tier'])+f' · Score {c[assessment_key]["score"]}/100**')
            st.subheader(c['name']); st.markdown(f'**STAND {value(c,"stand",confirmed) or "NON RENSEIGNÉ"} · HALL {value(c,"hall",confirmed) or "NON RENSEIGNÉ"}**')
            for title,lines in exports.memo_sections(c,confirmed):
                with st.expander(title,expanded=title in {'Qui est cette entreprise','Chiffres clés','Proposition de pitch'}):
                    for line in lines: st.write(line)
    if st.button('Préparer les exports',type='primary'):
        try:
            with st.spinner('Préparation des fichiers…'):
                st.session_state['export_package']=exports.prepare_package(campaign,companies,crm,mode)
        except Exception as exc: notify_error(exc)
    package=st.session_state.get('export_package')
    if package and package['campaign_id']==campaign['id'] and package['confirmed']==confirmed and package.get('mode')==mode:
        if package.get('signature')!=signature:
            st.session_state.pop('export_package',None)
            st.info('La sélection ou les données ont changé. Cliquez sur Préparer les exports pour générer les fichiers actualisés.')
            return
        for message in package.get('errors',[]): st.warning(message)
        st.caption('Exports préparés à partir des données affichées. Après une modification, utiliser à nouveau Préparer les exports.')
        cols=st.columns(3)
        cols[0].download_button('Tableur de prospection',package['xlsx'],'Prospection_IMPACT.xlsx','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        cols[1].download_button('Contacts CSV',package['csv'],'Contacts_IMPACT.csv','text/csv')
        if package['zip']: cols[2].download_button('Dossier complet ZIP',package['zip'],'Dossier_IMPACT.zip','application/zip')
        if package['docx'] or package['pdf']:
            a,b=st.columns(2)
            if package['docx']: a.download_button('Mémos Word',package['docx'],'Fiches_Memos.docx','application/vnd.openxmlformats-officedocument.wordprocessingml.document')
            if package['pdf']: b.download_button('Mémos PDF',package['pdf'],'Fiches_Memos.pdf','application/pdf')
    if selected and confirmed:
        st.divider(); st.caption('Cette action ajoute les cibles validées à la base prospects. Elles seront ensuite exclues des campagnes, y compris du classement actuel.')
        if st.button('Ajouter les cibles validées à la base prospects'):
            count=services.add_to_crm(selected); st.success(f'{count} entreprise(s) ajoutée(s).'); st.session_state.pop('export_package',None)

def crm_page():
    hero('Base de référence','Exclure les entreprises déjà suivies','Chargez votre véritable fichier clients/prospects. Le rapprochement utilise le SIREN, le domaine et le nom normalisé. Les ressemblances de noms demandent une décision.')
    dataframe(db.all_rows('crm'))
    base=db.all_rows('crm')
    if base:
        with st.expander('Corriger les entreprises de la base'):
            with st.form('crm_edit'):
                frame=pd.DataFrame([{k:x.get(k,'') for k in ['id','name','website','siren','status']} for x in base])
                edited=st.data_editor(frame,hide_index=True,**table_width_options(st.data_editor),disabled=['id'],
                                      column_config={'id':None,'name':'Entreprise','website':'Site web','siren':'SIREN',
                                                     'status':st.column_config.SelectboxColumn('Statut',options=['prospect','client','inactif'])})
                if st.form_submit_button('Enregistrer les corrections'):
                    try:
                        for row in edited.fillna('').to_dict('records'):
                            if not row['name'].strip(): raise ValueError('Une entreprise doit conserver son nom.')
                            if row['website'] and not valid_url(row['website']): raise ValueError('Site web invalide : utiliser une URL http ou https.')
                            current=db.get('crm',row['id']); current.update(row); current['updated']=db.now(); db.put('crm',current)
                        db.audit('crm_edit',{'rows':len(edited)}); st.rerun()
                    except Exception as exc: notify_error(exc)
            st.caption('Le statut inactif retire une société des exclusions sans supprimer son historique.')
    with st.expander('Importer ou compléter la base',expanded=not db.all_rows('crm')):
        parsed=upload_parser('crm')
        if parsed:
            index,mapping=map_import(parsed,'crm')
            if index is not None:
                default=st.selectbox('Statut si la colonne est absente',['prospect','client'])
                checked=toggle('Ce fichier contient bien les entreprises déjà clientes ou prospects à exclure.')
                if st.button('Importer la base',type='primary',disabled=not checked):
                    try:
                        count,warnings=services.import_crm(parsed,index,mapping,default)
                        st.success(f'{count} ligne(s) intégrée(s).')
                        for warning in warnings: st.warning(warning)
                    except Exception as exc: notify_error(exc)
    with st.expander('Ajouter une entreprise manuellement'):
        with st.form('new_crm'):
            name=st.text_input('Entreprise'); website=st.text_input('Site web'); siren=st.text_input('SIREN'); status=st.selectbox('Statut',['prospect','client'])
            if st.form_submit_button('Ajouter à la base'):
                if not name.strip(): st.error('Le nom est obligatoire.')
                else:
                    db.put('crm',{'id':db.uid(),'name':name.strip(),'website':website.strip(),'siren':siren.strip(),'status':status,'source':'Saisie utilisateur','updated':db.now(),'contacts':[]})
                    db.audit('crm_manual_add',{'name':name}); st.rerun()
    if db.all_rows('crm'):
        rows=exports.aggregate_rows(db.all_rows('crm'),[])
        st.download_button('Télécharger la base agrégée CSV',exports.csv_bytes(rows),'Base_agregee_IMPACT.csv','text/csv')

def criteria_page():
    hero('Règles et connaissance','Des critères qui évoluent avec votre prospection','Les règles déterminent le score et les conditions d’admission. L’IA apporte les informations ; elle ne choisit pas les poids ni les seuils.')
    criteria=copy.deepcopy(db.setting('criteria'))
    with st.form('criteria'):
        a,b,c=st.columns(3)
        criteria['target_count']=a.number_input('Nombre de cibles',1,200,criteria['target_count'])
        criteria['backup_count']=b.number_input('Nombre de cibles en réserve',0,500,criteria['backup_count'])
        criteria['research_limit']=c.number_input('Taille du lot IA par défaut',1,500,criteria['research_limit'])
        sectors=a.text_area('Secteurs cibles, un par ligne','\n'.join(criteria['sectors']))
        models=b.text_area('Modèles économiques, un par ligne','\n'.join(criteria['models']))
        cities=c.text_area('Villes prioritaires, une par ligne','\n'.join(criteria['cities']))
        criteria['sectors']=[x.strip() for x in sectors.splitlines() if x.strip()]
        criteria['models']=[x.strip() for x in models.splitlines() if x.strip()]
        criteria['cities']=[x.strip() for x in cities.splitlines() if x.strip()]
        criteria['min_revenue_eur']=a.number_input('CA strictement supérieur à, en EUR',0.0,1e13,float(criteria['min_revenue_eur']),step=1000000.0)
        criteria['min_margin_pct']=b.number_input('Ou marge opérationnelle au moins égale à, en %',-100.0,100.0,float(criteria['min_margin_pct']))
        criteria['finance_max_age']=c.number_input('Ancienneté financière maximale, en années',0,10,criteria['finance_max_age'])
        criteria['allow_group_finance']=toggle('Accepter le CA du groupe si la cible est le groupe',criteria['allow_group_finance'],container=a)
        criteria['require_international']=toggle('International obligatoire',criteria['require_international'],container=b)
        criteria['require_stand']=toggle('Stand obligatoire pour qualifier',criteria['require_stand'],container=c)
        st.subheader('Poids du score sur 100')
        labels={'sector':'Secteur','international':'International','finance':'Finance','model':'Modèle économique','city':'Ville prioritaire','triggers':'Signaux SENSO'}
        cols=st.columns(3)
        for i,key in enumerate(labels): criteria['weights'][key]=cols[i%3].number_input(labels[key],0,100,int(criteria['weights'][key]))
        criteria['tier1']=a.number_input('Tier 1 à partir de',1,100,criteria['tier1'])
        criteria['tier2']=b.number_input('Tier 2 à partir de',0,99,criteria['tier2'])
        st.caption('Finance admissible : CA strictement supérieur au seuil OU marge opérationnelle au moins égale au seuil, avec exercice récent et périmètre identifié. Quatre signaux SENSO distincts donnent le maximum des points signaux. Les inconnues ne donnent aucun point.')
        if st.form_submit_button('Enregistrer les critères',type='primary'):
            try:
                db.setting('criteria',validate_criteria(criteria)); db.audit('criteria_update',criteria)
                st.success('Critères enregistrés pour les nouvelles campagnes. Appliquer explicitement aux campagnes existantes dans leur onglet Règles.')
            except Exception as exc: notify_error(exc)
    st.subheader('Connaissance commerciale')
    with st.form('knowledge'):
        text=st.text_area('Offres et contexte de vente',value=db.setting('knowledge') or 'SENSO : optimisation des gammes, arbitrage SKU et contribution par circuit. Les autres présentations Adventae, Advent+ et Sales Academy restent à compléter.',height=180)
        if st.form_submit_button('Enregistrer la connaissance'):
            db.setting('knowledge',text[:60000]); db.audit('knowledge_update',{'characters':len(text)}); st.success('Connaissance enregistrée pour les prochaines recherches.')
    source=st.file_uploader('Ajouter une présentation ou un document de connaissance',type=['docx','pdf','txt'],key='knowledge_file')
    if source and st.button('Extraire et ajouter le texte'):
        try:
            parsed=parse_file(source.name,source.getvalue())
            text=parsed['text']+'\n'+'\n'.join(' | '.join(str(r.get(h,'')) for h in t['headers']) for t in parsed['tables'] for r in t['rows'])
            if not text.strip(): raise ValueError('Aucun texte extrait.')
            db.setting('knowledge',((db.setting('knowledge') or '')+'\n'+text)[:60000]); st.success('Document ajouté. Vérifier le texte avant utilisation.')
        except Exception as exc: notify_error(exc)
    with st.expander('Les huit signaux du document Triggers'):
        dataframe([{'Code':k,'Signal':v['name'],'Preuve attendue':v['signal']} for k,v in TRIGGERS.items()])

def configuration_page():
    hero('Solutions IA','Choisissez votre solution et préparez sa connexion',
         'Le choix se fait dans la barre latérale. Les clés restent dans le fichier .env sur le serveur ; elles ne sont pas enregistrées dans les campagnes ou les exports.')
    dataframe([
        {'Solution':'OpenAI','Usage':'Priorité performance en ligne','Nécessaire':'Clé OpenAI et compte API crédité','Web':'Inclus dans la connexion'},
        {'Solution':'Ollama local','Usage':'Calcul sur le PC sans frais de tokens','Nécessaire':'Ollama installé et modèle local téléchargé','Web':'Sites fournis sans clé, ou Ollama web / Serper'},
        {'Solution':'Gemini Flash-Lite','Usage':'Essai gratuit sous quotas ; offre payante possible','Nécessaire':'Clé Gemini et vérification de l’offre du compte','Web':'Sites fournis sans clé, ou Ollama web / Serper'},
    ])
    provider=AI_CONFIG['provider']
    st.write('**Solution sélectionnée :** '+AI_CONFIG['label'])
    st.write('**Modèle :** '+AI_CONFIG['model'])
    if AI_CONFIG['missing']: st.info('À compléter : '+' ; '.join(AI_CONFIG['missing']))
    elif AI_CONFIG['provider']=='ollama' and AI_CONFIG['web_provider']=='direct':
        st.success('Mode local configuré : aucune clé API nécessaire. Le modèle installé sur ce serveur effectue l’analyse.')
    else: st.success('Configuration renseignée. Validité des clés, solde et quotas non vérifiés.')
    all_options=db.setting('ai_options') or {}
    with st.form('ai_options'):
        model=st.text_input('Modèle de cette solution',value=AI_CONFIG['model'])
        web=AI_CONFIG['web_provider']
        tier=AI_CONFIG['gemini_tier']
        if provider!='openai':
            web_labels={'ollama':'Ollama web — gratuit sous limites','serper':'Serper — packs payants',
                        'direct':'Lecture des sites fournis — sans API de recherche'}
            web=st.selectbox('Service de recherche web',list(web_labels),index=list(web_labels).index(web) if web in web_labels else 0,
                             format_func=web_labels.get)
            if web=='direct':
                st.caption('Aucune clé de recherche web. Lecture des sites du fichier et de quelques liens internes. '
                           'Ce mode ne fait pas une recherche générale sur Internet. Les blocages des sites sont respectés.')
                if provider=='ollama':
                    st.caption('Les faits passent des contrôles supplémentaires. Les mémos sont des supports standards à personnaliser et vérifier.')
        if provider=='gemini':
            tier=st.selectbox('Offre de votre compte Gemini',['free','paid'],index=0 if tier=='free' else 1,
                              format_func=lambda x:'Gratuite — sous quotas' if x=='free' else 'Payante')
            st.caption('Ce réglage sert à l’estimation et ne modifie pas la facturation chez Google. Vérifier l’offre réelle dans AI Studio. Les contenus de l’offre gratuite peuvent servir à améliorer les produits Google.')
        if st.form_submit_button('Enregistrer les options IA'):
            if not model.strip(): st.error('Renseigner le nom du modèle.')
            else:
                all_options[provider]={'model':model.strip(),'web_provider':web,'gemini_tier':tier}
                db.setting('ai_options',all_options); st.rerun()
    if provider=='openai':
        st.code('OPENAI_API_KEY=votre_cle\nOPENAI_MODEL=gpt-5.5',language='bash')
        st.caption('GPT-5.4 mini peut être choisi pour réduire le coût. Le modèle doit être accessible au compte et compatible avec la recherche web et les sorties structurées.')
        st.link_button('Créer une clé OpenAI','https://platform.openai.com/api-keys')
    elif provider=='ollama':
        st.code('OLLAMA_BASE_URL=http://127.0.0.1:11434\nOLLAMA_MODEL='+AI_CONFIG['model']+
                ('\nOLLAMA_API_KEY=votre_cle_web_ollama' if AI_CONFIG['web_provider']=='ollama' else ''),language='bash')
        st.caption('Installer Ollama, démarrer son application puis utiliser un modèle local. Aucun modèle n’est téléchargé automatiquement. Les modèles cloud sont exclus de ce choix local.')
        st.code('ollama pull '+AI_CONFIG['model'],language='powershell')
        if st.button('Détecter les modèles locaux',key='detect_ollama_models'):
            try: st.session_state['local_models']=providers.local_models(AI_CONFIG)
            except Exception as exc: notify_error(exc)
        available=st.session_state.get('local_models',[])
        if available:
            chosen=st.selectbox('Modèle local installé',available)
            if st.button('Utiliser ce modèle local'):
                all_options[provider]={**all_options.get(provider,{}),'model':chosen}
                db.setting('ai_options',all_options); st.rerun()
        st.link_button('Installer Ollama','https://ollama.com/download')
    else:
        st.code('GEMINI_API_KEY=votre_cle\nGEMINI_MODEL=gemini-3.5-flash-lite\nGEMINI_TIER=free\nOLLAMA_API_KEY=votre_cle_web_ollama',language='bash')
        st.caption('L’analyse peut utiliser le quota gratuit Gemini. La recherche Google intégrée payante n’est pas activée : le service web sélectionné fournit les sources. Aucun basculement payant automatique en cas de quota atteint.')
        st.link_button('Créer une clé Gemini','https://aistudio.google.com/api-keys')
    if provider!='openai' and AI_CONFIG['web_provider']!='direct':
        st.caption('Ollama web nécessite un compte et une clé, même si le modèle tourne sur le PC. Sa gratuité est limitée. Serper nécessite sa propre clé et des crédits.')
        st.link_button('Créer une clé Ollama web','https://ollama.com/settings/keys')
        st.code('SERPER_API_KEY=votre_cle_serper',language='bash')
        st.link_button('Service web Serper','https://serper.dev/')
    st.caption('Renseigner uniquement les clés des services choisis dans .env puis redémarrer IMPACT. Import de tableaux, vérification, classement et exports restent disponibles sans IA.')
    st.subheader('Journal des opérations')
    dataframe(db.audit_rows())
    st.download_button('Exporter le journal JSON',json.dumps(db.audit_rows(),ensure_ascii=False,indent=2),'Journal_IMPACT.json','application/json')

pages={'Vue d’ensemble':overview,'Importer des exposants':imports_page,'Campagne et classement':campaign_page,
       'Fiches mémos et exports':exports_page,'Clients et prospects':crm_page,'Critères et connaissances':criteria_page,'Configuration IA':configuration_page}
if 'success_message' in st.session_state:
    st.success(st.session_state.pop('success_message'))
pages[page]()
