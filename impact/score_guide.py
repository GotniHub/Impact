"""Explication du classement, en lecture seule et sans appel IA."""
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo
import streamlit as st
from .config import TRIGGERS

LABELS={'sector':'Secteur d’activité','international':'Présence internationale',
        'finance':'Chiffre d’affaires ou marge','model':'Modèle économique',
        'city':'Implantation','triggers':'Triggers SENSO'}

def number(value):
    return f'{float(value):,.4f}'.rstrip('0').rstrip('.').replace(',',' ').replace('.',',')

def score_rules(criteria):
    conditions={
        'sector':'Activité renseignée comme compatible avec les secteurs ciblés : '+(', '.join(criteria['sectors']) or 'aucun secteur défini')+'.',
        'international':'Présence commerciale dans plusieurs pays renseignée dans le dossier.',
        'finance':f'CA strictement supérieur à {number(criteria["min_revenue_eur"])} € OU marge opérationnelle '
                  f'au moins égale à {number(criteria["min_margin_pct"])} %, avec exercice et périmètre admissibles.',
        'model':'Modèle renseigné correspondant à l’une des activités ciblées : '+(', '.join(criteria['models']) or 'aucun modèle défini')+'.',
        'city':'Ville renseignée correspondant à l’une des implantations ciblées : '+(', '.join(criteria['cities']) or 'aucune ville définie')+'.',
        'triggers':f'{number(criteria["weights"]["triggers"]/4)} points par code distinct retenu ; le maximum est atteint à quatre codes.',
    }
    return [(key,label,criteria['weights'][key],conditions[key]) for key,label in LABELS.items()]

def table(headers,rows,*,points_column=None):
    head=''.join(f'<th scope="col">{escape(str(h))}</th>' for h in headers)
    body=[]
    for row in rows:
        cells=[]
        for index,item in enumerate(row):
            content=escape(str(item))
            if index==points_column: content=f'<span class="score-guide-points">{content}</span>'
            tag='th' if index==0 else 'td'; scope=' scope="row"' if index==0 else ''
            cells.append(f'<{tag}{scope}>{content}</{tag}>')
        body.append('<tr>'+''.join(cells)+'</tr>')
    st.markdown('<div class="score-guide-table-wrap"><table class="score-guide-table">'
                f'<thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>',unsafe_allow_html=True)

def render_score_guide(campaign,companies):
    criteria=campaign['criteria']; weights=criteria['weights']
    campaign_label=f'{campaign["name"]} · {campaign["edition"]}'
    with st.container(key='score_guide_content'):
        st.markdown('<div class="score-guide-summary"><div><div class="score-guide-eyebrow">LA MÉTHODE IMPACT</div>'
                    '<h3>Des points pour chaque critère</h3>'
                    f'<p>Règles de la campagne {escape(campaign_label)}</p></div>'
                    f'<div class="score-guide-total"><strong>{number(sum(weights.values()))}</strong>'
                    '<span>points maximum</span></div></div>',unsafe_allow_html=True)
        tabs=st.tabs(['Barème des critères','Exemple réel','Tiers et présélection'])
        with tabs[0]:
            st.write('Le score additionne les points de six critères. Hors triggers, chaque critère rapporte tous ses points ou zéro. '
                     'Une donnée manquante ne reçoit aucun point ; il n’y a pas de malus.')
            table(['Critère','Condition d’attribution','Maximum'],
                  [(label,condition,f'{number(weight)} pts') for _,label,weight,condition in score_rules(criteria)],points_column=2)
            with st.expander('Comprendre la règle financière'):
                year=datetime.now(ZoneInfo('Africa/Casablanca')).year
                st.write(f'Un CA supérieur à {number(criteria["min_revenue_eur"])} € ou une marge opérationnelle '
                         f'd’au moins {number(criteria["min_margin_pct"])} % suffit. Si les deux conditions sont remplies, '
                         f'le total reste {number(weights["finance"])} points.')
                st.write(f'Le millésime doit être connu : les exercices {year-criteria["finance_max_age"]} à {year} '
                         'inclus sont admissibles actuellement. Un exercice futur est refusé.')
                st.write('Le périmètre doit correspondre à l’entité. Les comptes de groupe sont aussi admis '
                         'si le dossier identifie bien un groupe.' if criteria['allow_group_finance'] else
                         'Le périmètre doit correspondre à l’entité. Les chiffres du groupe ne sont pas admis dans cette campagne.')
                st.caption('Le seuil du CA est strict : un CA exactement égal au seuil ne suffit pas. '
                           'La marge brute ou l’EBITDA ne remplacent pas automatiquement la marge opérationnelle.')
            with st.expander('Comprendre les points des triggers T1 à T8'):
                st.write(f'Points triggers = {number(weights["triggers"])} × min(nombre de codes distincts retenus ÷ 4, 1).')
                table(['Codes distincts','0','1','2','3','4 à 8'],[('Points',*[number(weights['triggers']*n/4) for n in range(5)])])
                st.write('Un code répété compte une seule fois. Un signal doit passer les contrôles sur sa source et son extrait, '
                         'ou être validé manuellement. Une proposition insuffisamment étayée ne rapporte aucun point.')
                table(['Code','Signal recherché'],[(code,item['name']) for code,item in TRIGGERS.items()])
                st.caption('Le mode de qualification locale rapide ne recherche pas de nouveaux triggers ; il conserve ceux déjà présents.')
            st.caption('Le score mesure l’intérêt commercial des informations disponibles. '
                       'Ce n’est ni un pourcentage de fiabilité ni une validation humaine.')
        with tabs[1]:
            if not companies: st.info('Importez des exposants pour consulter un exemple réel.')
            else:
                by_id={c['id']:c for c in companies}
                company_id=st.selectbox('Entreprise de l’exemple',list(by_id),format_func=lambda cid:by_id[cid]['name'],
                                        key='score_example_'+campaign['id'])
                company=by_id[company_id]; confirmed=company.get('selection')=='validated'
                assessment=company['validated_assessment'] if confirmed else company['assessment']
                st.caption('Calcul fondé sur les informations validées manuellement.' if confirmed else
                           'Calcul fondé sur les données actuellement enregistrées, dont les faits sourcés restent à vérifier.')
                rows=[]; contributions=[]
                for key,label,weight,_ in score_rules(criteria):
                    component=assessment['components'][key]; earned=weight*float(component); contributions.append(number(earned))
                    status=(f'{number(float(component)*4)} code(s) distinct(s) retenu(s), plafonné à 4' if key=='triggers' else
                            'Condition remplie selon les données retenues' if component else 'Condition non documentée ou non remplie')
                    rows.append((label,status,f'{number(earned)} / {number(weight)}'))
                table(['Critère','Lecture du dossier','Points obtenus'],rows,points_column=2)
                st.markdown('<div class="score-guide-example-total"><div><span>TOTAL ARRONDI</span>'
                            f'<strong>{number(assessment["score"])} <small>/ 100</small></strong></div>'
                            f'<span class="score-guide-tier">Tier {assessment["tier"]}</span></div>',unsafe_allow_html=True)
                st.write(' + '.join(contributions)+f' → **{number(assessment["score"])} / 100**.')
                st.caption('Le total est arrondi à une décimale avant l’attribution du tier. '
                           'Exemples de l’arrondi utilisé : 73,75 → 73,8 et 96,25 → 96,2.')
                if assessment['blockers']:
                    st.markdown('**Pourquoi ce score ne suffit pas à qualifier le dossier**')
                    for blocker in assessment['blockers']: st.markdown('- '+blocker)
                st.caption('Cet exemple reprend les données visibles lors de l’ouverture. '
                           'Actualisez le classement pour intégrer les derniers résultats de la recherche.')
        with tabs[2]:
            table(['Niveau','Score commercial arrondi','Priorité'],[
                ('Tier 1',f'De {criteria["tier1"]} à 100','Très forte priorité commerciale'),
                ('Tier 2',f'De {criteria["tier2"]} à moins de {criteria["tier1"]}','Forte priorité commerciale'),
                ('Tier 3',f'Moins de {criteria["tier2"]}','Priorité secondaire selon les données disponibles')])
            st.write('Le tier dépend du score. Il ne prouve pas que tous les critères obligatoires sont remplis. '
                     'Une entreprise peut être Tier 1 et rester à documenter, par exemple si ses données financières manquent.')
            st.markdown('#### Comment les entreprises sont présélectionnées')
            st.write(f'L’objectif est de retenir {criteria["target_count"]} entreprises. Les dossiers vérifiés manuellement '
                     'qui satisfont les conditions sont prioritaires, puis viennent les dossiers proposés avec critères documentés.')
            if criteria.get('progressive_selection'):
                st.write('Avec la présélection progressive, les places restantes peuvent être complétées par des dossiers '
                         'à documenter, selon un indice de préclassement distinct. La liste peut évoluer avec chaque actualisation.')
                table(['Indice du préclassement','Points maximum'],[
                    ('Secteur compatible, y compris indices du fichier','50'),
                    ('Modèle compatible ou indice de fabrication, distribution ou négoce','15'),
                    ('Condition financière admissible','15'),('International renseigné','10'),
                    ('Site ou page exposant disponible','5'),('Implantation cible','5')],points_column=1)
                st.caption('Les dossiers à documenter sont départagés par cet indice décroissant, puis par leur score commercial '
                           'décroissant, puis par leur nom normalisé. Cet indice ne détermine pas le tier.')
            else:
                st.write('La présélection progressive est désactivée dans cette campagne. '
                         'Les propositions suivent le score et les règles de qualification de la campagne.')
            st.markdown('#### Qualification et vérification')
            st.write('L’identité, le secteur, le modèle économique, la condition financière et les exclusions clients/prospects '
                     f'sont contrôlés séparément. Une présélection de {criteria["target_count"]} noms ne garantit pas '
                     'autant de dossiers complets.')
            optional=[]
            if criteria['require_international']: optional.append('la présence internationale')
            if criteria['require_stand']: optional.append('le numéro de stand')
            st.write('Cette campagne exige également '+' et '.join(optional)+'.' if optional else
                     'L’international et le stand ne sont pas obligatoires dans cette campagne. '
                     'L’international peut rapporter des points ; le stand et le hall ne rapportent aucun point au score.')
            st.caption('« Critères documentés » signifie que les données collectées satisfont les conditions. '
                       '« Vérifiées manuellement » signifie qu’une personne a contrôlé et validé les informations nécessaires.')
