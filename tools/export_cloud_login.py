"""Préparer les comptes Cloud dans un fichier privé ignoré par Git, sans mot de passe."""
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from impact.config import DATA
from impact.auth import valid_password_hash


def export_cloud_login(source,destination):
    # Mode lecture seule : ne pas créer de base vide ni modifier les comptes locaux.
    with closing(sqlite3.connect(Path(source).resolve().as_uri()+'?mode=ro',uri=True)) as conn:
        conn.row_factory=sqlite3.Row
        accounts=[dict(r) for r in conn.execute(
            'SELECT email,password_hash FROM users WHERE enabled=1 ORDER BY email')]
    if not accounts or any(not valid_password_hash(a['password_hash']) for a in accounts):
        raise ValueError('Aucun compte actif exportable.')
    content='# Configuration privée : à coller dans Streamlit Cloud > Settings > Secrets.\n'
    content+='# Conserver les autres secrets existants. Ne pas publier sur GitHub.\n'
    content+="IMPACT_AUTH_USERS_JSON = '''"+json.dumps(accounts,ensure_ascii=True)+"'''\n"
    destination=Path(destination)
    destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_text(content,encoding='utf-8')
    return len(accounts)


if __name__=='__main__':
    target=ROOT/'outputs'/'cloud_deployment'/'secrets.toml'
    count=export_cloud_login(DATA/'auth.sqlite3',target)
    print(f'{count} compte(s) préparé(s) dans {target}')
    print('Aucun mot de passe, jeton ou clé IA en clair exporté. Ne pas publier ce fichier.')
