"""Connexions SQLite locales ou PostgreSQL privées, utilisées uniquement côté serveur."""
import os
import sqlite3
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

SCHEMAS={'business':'impact','auth':'impact_auth'}


class DatabaseUnavailable(RuntimeError):
    pass


def database_url():
    return os.environ.get('IMPACT_DATABASE_URL','').strip()


class Record(dict):
    """Conserver les accès par nom et index utilisés avec sqlite3.Row."""
    def __getitem__(self,key):
        return tuple(self.values())[key] if isinstance(key,int) else super().__getitem__(key)


class Cursor:
    def __init__(self,cursor): self.raw=cursor
    @property
    def rowcount(self): return self.raw.rowcount
    def fetchone(self):
        row=self.raw.fetchone()
        return None if row is None else Record(row)
    def fetchall(self): return [Record(row) for row in self.raw.fetchall()]
    def __iter__(self):
        for row in self.raw: yield Record(row)


class PostgresConnection:
    """Adapter les requêtes internes à paramètres ?, jamais leurs valeurs."""
    dialect='postgres'
    def __init__(self,conn): self.raw=conn
    def execute(self,sql,params=None):
        return Cursor(self.raw.execute(sql.replace('?', '%s'),params))
    def executemany(self,sql,params):
        cursor=self.raw.cursor()
        cursor.executemany(sql.replace('?', '%s'),params)
        return Cursor(cursor)
    def executescript(self,script):
        # DDL interne uniquement, sans fonctions ni texte utilisateur.
        for statement in script.split(';'):
            if statement.strip(): self.raw.execute(statement)


@lru_cache(maxsize=4)
def _pool(url):
    try:
        from psycopg.conninfo import conninfo_to_dict
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool
        info=conninfo_to_dict(url)
        if not url.startswith(('postgresql://','postgres://')) or not info.get('host'):
            raise ValueError('Adresse PostgreSQL invalide.')
        local=info['host'] in {'localhost','127.0.0.1','::1'}
        sslmode=info.get('sslmode','require')
        if not local and sslmode not in {'require','verify-ca','verify-full'}:
            raise ValueError('Une connexion PostgreSQL distante exige SSL.')
        return ConnectionPool(url,min_size=0,max_size=4,timeout=12,max_idle=60,
            kwargs={'row_factory':dict_row,'prepare_threshold':None,'connect_timeout':10,
                    'sslmode':sslmode,'application_name':'impact'},
            check=ConnectionPool.check_connection,open=True)
    except Exception:
        # Une adresse de connexion peut contenir un mot de passe : ne pas la journaliser.
        raise DatabaseUnavailable('Connexion à la base distante indisponible. Vérifiez IMPACT_DATABASE_URL dans les Secrets.') from None


@contextmanager
def connection(path,scope,*,url=''):
    if scope not in SCHEMAS: raise ValueError('Espace de stockage invalide.')
    if not url:
        path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
        conn=sqlite3.connect(path,timeout=30)
        conn.row_factory=sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        if scope=='business': conn.execute('PRAGMA journal_mode=WAL')
        try:
            with conn: yield conn
        finally: conn.close()
        return
    pool=_pool(url)
    try:
        conn=pool.getconn(timeout=12)
    except Exception:
        raise DatabaseUnavailable('Connexion à la base distante indisponible. Aucun basculement vers une base locale vide.') from None
    try:
        with conn.transaction():
            conn.execute('SET LOCAL statement_timeout = 30000')
            conn.execute('SET LOCAL lock_timeout = 10000')
            conn.execute(f'SET LOCAL search_path TO {SCHEMAS[scope]}, pg_catalog')
            yield PostgresConnection(conn)
    finally:
        pool.putconn(conn)


def is_postgres(conn): return isinstance(conn,PostgresConnection)


def create_schema(conn,scope):
    if is_postgres(conn):
        conn.execute('SELECT pg_advisory_xact_lock(?)',(1739182603 if scope=='business' else 1739182604,))
        conn.execute(f'CREATE SCHEMA IF NOT EXISTS {SCHEMAS[scope]}')


def protect_schema(conn,scope,tables):
    if not is_postgres(conn): return
    schema=SCHEMAS[scope]
    # Ces schémas ne font pas partie de l'API publique Supabase.
    roles=['PUBLIC']+[row['rolname'] for row in conn.execute(
        "SELECT rolname FROM pg_roles WHERE rolname IN ('anon','authenticated')")]
    for role in roles:
        conn.execute(f'REVOKE ALL ON SCHEMA {schema} FROM {role}')
        conn.execute(f'REVOKE ALL ON ALL TABLES IN SCHEMA {schema} FROM {role}')
        conn.execute(f'REVOKE ALL ON ALL SEQUENCES IN SCHEMA {schema} FROM {role}')
    for table in tables:
        if not table.replace('_','').isalnum(): raise ValueError('Table invalide.')
        conn.execute(f'ALTER TABLE {schema}.{table} ENABLE ROW LEVEL SECURITY')


def lock_auth(conn):
    if is_postgres(conn):
        # Verrou transactionnel partagé entre serveurs, y compris via un pooler.
        conn.execute('SELECT pg_advisory_xact_lock(1739182601)')
    else: conn.execute('BEGIN IMMEDIATE')


def identity_column(conn):
    return 'BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY' if is_postgres(conn) else 'INTEGER PRIMARY KEY AUTOINCREMENT'


def duplicate_key(exc):
    return isinstance(exc,sqlite3.IntegrityError) or getattr(exc,'sqlstate',None)=='23505'
