"""Copie vérifiée de SQLite vers une base PostgreSQL vide, sans aucun appel IA."""
import argparse
import hashlib
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from impact import db,storage
from impact.config import DATA,DEFAULT_CRITERIA

TABLES=('campaigns','companies','crm','settings','imports','jobs','audit','cache')
MARKER='sqlite_migration'


def read_snapshot(source):
    """Snapshot cohérent en mémoire ; le fichier local reste ouvert en lecture seule."""
    uri=Path(source).resolve().as_uri()+'?mode=ro'
    with closing(sqlite3.connect(uri,uri=True)) as local,closing(sqlite3.connect(':memory:')) as snapshot:
        local.backup(snapshot)
        snapshot.row_factory=sqlite3.Row
        return {table:[dict(row) for row in snapshot.execute(f'SELECT * FROM {table} ORDER BY 1')]
                for table in TABLES}


def fingerprint(snapshot):
    # Les collations SQLite et PostgreSQL peuvent trier les mêmes clés différemment.
    canonical={table:sorted(rows,key=lambda row:json.dumps(row,ensure_ascii=True,sort_keys=True))
               for table,rows in snapshot.items()}
    text=json.dumps(canonical,ensure_ascii=True,sort_keys=True,separators=(',',':'))
    return hashlib.sha256(text.encode()).hexdigest()


def migrate(source,*,url='',apply=False):
    snapshot=read_snapshot(source)
    report={'mode':'copie' if apply else 'simulation','counts':{t:len(snapshot[t]) for t in TABLES},
            'source_sha256':fingerprint(snapshot),'ai_calls':0}
    if not apply: return report
    if not url: raise ValueError('IMPACT_DATABASE_URL est obligatoire pour la copie. Aucune base locale de destination ne sera utilisée.')
    # Initialiser la destination n'interrompt aucun traitement existant.
    db.init(database_url=url,recover_jobs=False)
    with db.connection(database_url=url) as conn:
        conn.execute('SELECT pg_advisory_xact_lock(1739182602)')
        conn.execute('LOCK TABLE '+','.join(TABLES)+' IN EXCLUSIVE MODE')
        previous=conn.execute('SELECT payload FROM settings WHERE key=?',(MARKER,)).fetchone()
        if previous:
            marker=json.loads(previous['payload'])
            if marker.get('source_sha256')==report['source_sha256']:
                report['already_imported']=True
                return report
        # Ne jamais écraser une base déjà utilisée, même partiellement.
        for table in TABLES:
            if table=='settings':
                rows=conn.execute('SELECT key,payload FROM settings').fetchall()
                if any(r['key']!='criteria' or json.loads(r['payload'])!=DEFAULT_CRITERIA for r in rows):
                    raise ValueError('La destination contient déjà des données ou des réglages. Copie annulée sans écrasement.')
            elif conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]:
                raise ValueError('La destination contient déjà des données. Copie annulée sans écrasement.')
        for table in TABLES:
            rows=snapshot[table]
            if table=='settings': conn.execute('DELETE FROM settings')
            if not rows: continue
            columns=list(rows[0])
            sql=f'INSERT INTO {table} ({",".join(columns)}) VALUES ({",".join("?" for _ in columns)})'
            # Bornes de lot : pas de requête monolithique pour tout le salon.
            for start in range(0,len(rows),100):
                conn.executemany(sql,[[row[column] for column in columns] for row in rows[start:start+100]])
        # Vérification complète avant commit : chaque valeur doit être identique.
        copied={table:[dict(r) for r in conn.execute(f'SELECT * FROM {table} ORDER BY 1')] for table in TABLES}
        if fingerprint(copied)!=report['source_sha256']:
            raise RuntimeError('Vérification de copie échouée : transaction annulée.')
        conn.execute("SELECT setval(pg_get_serial_sequence('impact.audit','id'),COALESCE((SELECT MAX(id) FROM audit),1),(SELECT COUNT(*)>0 FROM audit))")
        marker={'source_sha256':report['source_sha256'],'copied_at':db.now(),'counts':report['counts']}
        conn.execute('INSERT INTO settings VALUES (?,?)',(MARKER,json.dumps(marker)))
        report['verified']=True
    return report


def main():
    parser=argparse.ArgumentParser(description='Transférer les résultats SQLite existants vers Supabase, sans relancer l’IA')
    parser.add_argument('--source',type=Path,default=DATA/'impact.sqlite3')
    parser.add_argument('--apply',action='store_true',help='Copier vers IMPACT_DATABASE_URL ; sinon simulation locale uniquement')
    args=parser.parse_args()
    try: report=migrate(args.source,url=storage.database_url(),apply=args.apply)
    except (OSError,ValueError,sqlite3.Error,storage.DatabaseUnavailable) as exc:
        parser.exit(1,str(exc)+'\n')
    except Exception:
        parser.exit(1,'La copie a échoué. Aucun mot de passe ni contenu de connexion n’est affiché. La transaction a été annulée.\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__': main()
