"""Écran de connexion et contrôle serveur avant affichage des données Streamlit."""
import logging
import json
import os
import streamlit as st
from . import auth,db

SESSION_KEY='_impact_auth_session'
LOGIN_EMAIL='_impact_login_email'
LOGIN_PASSWORD='_impact_login_password'
LOGIN_ERROR='_impact_login_error'


@st.cache_resource
def _store(path,users_json=''):
    store=auth.AuthStore(path)
    if users_json:
        try: accounts=json.loads(users_json)
        except (ValueError,TypeError):
            raise ValueError('Configuration des comptes Cloud invalide.') from None
        store.sync_users(accounts)
    return store


def get_store():
    # Les secrets TOML à la racine sont exposés comme variables par Streamlit.
    return _store(str(db.DATA/'auth.sqlite3'),os.environ.get('IMPACT_AUTH_USERS_JSON',''))


def _clear_session():
    # Les aperçus, imports et exports privés ne doivent pas rester en mémoire de cette session.
    for key in list(st.session_state): del st.session_state[key]


def authenticated_user(*,touch=False):
    try: return get_store().user_for_session(st.session_state.get(SESSION_KEY),touch=touch)
    except Exception:
        logging.error('IMPACT authentication storage unavailable')
        return None


def _submit_login():
    email=st.session_state.get(LOGIN_EMAIL,'')
    password=st.session_state.get(LOGIN_PASSWORD,'')
    # Callback exécuté avant le rendu des widgets : le champ secret est aussitôt vidé.
    st.session_state[LOGIN_PASSWORD]=''
    try: result=get_store().authenticate(email,password)
    except Exception:
        logging.error('IMPACT authentication request failed')
        result={'error':'Connexion indisponible. Contactez votre administrateur.'}
    if result.get('token'):
        _clear_session()
        st.session_state[SESSION_KEY]=result['token']
    else: st.session_state[LOGIN_ERROR]=result['error']


def logout():
    try: get_store().logout(st.session_state.get(SESSION_KEY))
    except Exception: logging.error('IMPACT logout storage unavailable')
    finally: _clear_session()


def guard_session():
    """Protéger également les fragments et dialogues relancés sans le script principal."""
    if authenticated_user(): return
    _clear_session()
    st.rerun(scope='app')


@st.fragment(run_every='60s')
def session_watch():
    # Une actualisation automatique ne prolonge pas le délai d'inactivité.
    guard_session()


def login_page():
    with st.container(key='auth_shell'):
        st.markdown('''<div class="auth-topbar"><div><div class="auth-wordmark">IMPACT</div>
            <div class="auth-subbrand">PROSPECTION SALONS</div></div>
            <span class="auth-private"><i></i> ESPACE PRIVÉ</span></div>''',unsafe_allow_html=True)
        columns=st.columns([1.08,1],gap='large',vertical_alignment='center')
        with columns[0]:
            st.markdown('''<div class="auth-story"><div class="auth-story-eyebrow">VOTRE PROCHAINE CAMPAGNE COMMENCE ICI</div>
                <h1>Des rencontres<br>qui font avancer<br>vos projets.</h1>
                <p>Retrouvez vos campagnes, identifiez les entreprises à rencontrer et préparez vos rendez-vous en un même espace.</p>
                <div class="auth-story-steps"><div><span>01</span>Centralisez vos exposants</div>
                <div><span>02</span>Identifiez vos cibles prioritaires</div><div><span>03</span>Préparez vos fiches terrain</div></div>
                <div class="auth-story-footer">VOS CAMPAGNES. VOS CIBLES. VOTRE IMPACT.</div></div>''',unsafe_allow_html=True)
        with columns[1],st.container(key='auth_card'):
            st.markdown('''<div class="auth-lock" aria-hidden="true"><svg width="25" height="25" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="10" width="14" height="11" rx="3"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/><path d="M12 14v3"/></svg></div>
                <div class="auth-card-eyebrow">CONNEXION</div><h2>Bienvenue sur IMPACT</h2>
                <p class="auth-card-intro">Connectez-vous à votre espace de prospection.</p>''',unsafe_allow_html=True)
            try: ready=get_store().has_users()
            except Exception: ready=False
            if not ready: st.error('Accès indisponible. Contactez votre administrateur pour activer votre compte.')
            with st.form('impact_login'):
                st.text_input('Adresse email professionnelle',placeholder='vous@entreprise.fr',key=LOGIN_EMAIL,max_chars=254)
                st.text_input('Mot de passe',type='password',placeholder='Votre mot de passe',key=LOGIN_PASSWORD,max_chars=1024)
                st.form_submit_button('Se connecter',type='primary',use_container_width=True,
                                      on_click=_submit_login,disabled=not ready)
            if st.session_state.get(LOGIN_ERROR): st.error(st.session_state[LOGIN_ERROR])
            st.markdown('<p class="auth-help">Besoin d’un accès ou d’un nouveau mot de passe ?<br>Contactez votre administrateur.</p>',unsafe_allow_html=True)
        st.markdown('<div class="auth-bottom">Un espace de travail réservé à votre équipe.</div>',unsafe_allow_html=True)


def require_login():
    email=authenticated_user(touch=True)
    if email: return email
    if st.session_state.get(SESSION_KEY): _clear_session()
    login_page()
    st.stop()
