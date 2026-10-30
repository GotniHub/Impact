"""Comptes locaux et sessions révocables. Aucun mot de passe en clair enregistré."""
import hashlib
import hmac
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

ITERATIONS=600_000
IDLE_SECONDS=30*60
SESSION_SECONDS=8*60*60
ATTEMPT_WINDOW=15*60
LOCK_SECONDS=5*60
MAX_FAILURES=5
GLOBAL_WINDOW=5*60
GLOBAL_ATTEMPTS=60
INVALID_LOGIN='Adresse email ou mot de passe incorrect.'
LOCKED_LOGIN='Trop de tentatives. Réessayez dans quelques minutes.'


def normalize_email(email):
    email=str(email).strip().casefold()
    if len(email)>254 or not re.fullmatch(r'[a-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,}',email):
        raise ValueError('Renseignez une adresse email valide.')
    return email


def hash_password(password):
    if not isinstance(password,str) or not 15<=len(password)<=1024:
        raise ValueError('Le mot de passe doit contenir de 15 à 1 024 caractères.')
    salt=secrets.token_bytes(24)
    derived=hashlib.pbkdf2_hmac('sha256',password.encode('utf-8'),salt,ITERATIONS)
    return f'pbkdf2_sha256${ITERATIONS}${salt.hex()}${derived.hex()}'


def verify_password(password,encoded):
    try:
        algorithm,iterations,salt,expected=encoded.split('$')
        if algorithm!='pbkdf2_sha256' or not ITERATIONS<=int(iterations)<=1_200_000: return False
        if not isinstance(password,str) or len(password)>1024: return False
        salt=bytes.fromhex(salt); expected=bytes.fromhex(expected)
        if len(salt)<16 or len(expected)!=32: return False
        actual=hashlib.pbkdf2_hmac('sha256',password.encode('utf-8'),salt,int(iterations))
        return hmac.compare_digest(actual,expected)
    except (ValueError,TypeError,AttributeError): return False


def token_hash(token):
    return hashlib.sha256(token.encode('ascii')).hexdigest()


def valid_password_hash(encoded):
    if not isinstance(encoded,str): return False
    match=re.fullmatch(r'pbkdf2_sha256\$(\d{6,7})\$([0-9a-f]{32,128})\$([0-9a-f]{64})',encoded)
    return bool(match and ITERATIONS<=int(match[1])<=1_200_000 and len(match[2])%2==0)


class AuthStore:
    def __init__(self,path,*,clock=time.time):
        self.path=Path(path)
        self.clock=clock
        # Un identifiant inconnu exige le même calcul qu'un compte existant.
        self._dummy=f'pbkdf2_sha256${ITERATIONS}$'+secrets.token_hex(24)+'$'+'00'*32
        with self.connection() as conn:
            conn.executescript('''
            CREATE TABLE IF NOT EXISTS users(
                email TEXT PRIMARY KEY,password_hash TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,
                version INTEGER NOT NULL DEFAULT 1,created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions(
                token_hash TEXT PRIMARY KEY,email TEXT NOT NULL REFERENCES users(email),
                version INTEGER NOT NULL,created REAL NOT NULL,last_seen REAL NOT NULL,expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS attempts(
                bucket TEXT PRIMARY KEY,started REAL NOT NULL,failures INTEGER NOT NULL,blocked_until REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS events(
                id INTEGER PRIMARY KEY,created REAL NOT NULL,event TEXT NOT NULL,email TEXT NOT NULL);
            ''')

    @contextmanager
    def connection(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        conn=sqlite3.connect(self.path,timeout=15)
        conn.row_factory=sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        try:
            with conn: yield conn
        finally: conn.close()

    def has_users(self):
        with self.connection() as conn:
            return conn.execute('SELECT 1 FROM users WHERE enabled=1 LIMIT 1').fetchone() is not None

    def list_users(self):
        with self.connection() as conn:
            return [dict(r) for r in conn.execute('SELECT email,enabled,created FROM users ORDER BY email')]

    def sync_users(self,accounts):
        """Comptes Cloud gérés par les secrets ; valider avant toute modification."""
        if not isinstance(accounts,list) or not 1<=len(accounts)<=1000:
            raise ValueError('Configuration des comptes Cloud invalide.')
        users={}
        for account in accounts:
            if not isinstance(account,dict) or set(account)-{'email','password_hash','enabled'}:
                raise ValueError('Configuration des comptes Cloud invalide.')
            email=normalize_email(account.get('email',''))
            encoded=account.get('password_hash'); enabled=account.get('enabled',True)
            if email in users or not valid_password_hash(encoded) or type(enabled) is not bool:
                raise ValueError('Configuration des comptes Cloud invalide.')
            users[email]=(encoded,int(enabled))
        with self.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            for row in conn.execute('SELECT email,password_hash,enabled FROM users').fetchall():
                if row['email'] not in users:
                    conn.execute('UPDATE users SET enabled=0,version=version+1 WHERE email=? AND enabled=1',(row['email'],))
                    conn.execute('DELETE FROM sessions WHERE email=?',(row['email'],))
            for email,(encoded,enabled) in users.items():
                row=conn.execute('SELECT password_hash,enabled FROM users WHERE email=?',(email,)).fetchone()
                if row is None:
                    conn.execute('INSERT INTO users(email,password_hash,enabled,created) VALUES(?,?,?,?)',
                                 (email,encoded,enabled,self.clock()))
                elif row['password_hash']!=encoded or row['enabled']!=enabled:
                    conn.execute('UPDATE users SET password_hash=?,enabled=?,version=version+1 WHERE email=?',
                                 (encoded,enabled,email))
                    conn.execute('DELETE FROM sessions WHERE email=?',(email,))

    def add_user(self,email,password):
        email=normalize_email(email); encoded=hash_password(password)
        with self.connection() as conn:
            try: conn.execute('INSERT INTO users(email,password_hash,created) VALUES(?,?,?)',(email,encoded,self.clock()))
            except sqlite3.IntegrityError: raise ValueError('Ce compte existe déjà.') from None
        return email

    def reset_password(self,email,password):
        email=normalize_email(email); encoded=hash_password(password)
        with self.connection() as conn:
            if not conn.execute('UPDATE users SET password_hash=?,version=version+1 WHERE email=?',(encoded,email)).rowcount:
                raise ValueError('Compte introuvable.')
            conn.execute('DELETE FROM sessions WHERE email=?',(email,))

    def set_enabled(self,email,enabled):
        email=normalize_email(email)
        with self.connection() as conn:
            if not conn.execute('UPDATE users SET enabled=? WHERE email=?',(int(bool(enabled)),email)).rowcount:
                raise ValueError('Compte introuvable.')
            if not enabled: conn.execute('DELETE FROM sessions WHERE email=?',(email,))

    def authenticate(self,email,password):
        try: normalized=normalize_email(email)
        except ValueError: normalized=str(email).strip().casefold()[:254]
        bucket='email:'+hashlib.sha256(normalized.encode('utf-8')).hexdigest()
        now=self.clock()
        with self.connection() as conn:
            # Sérialiser aussi entre processus : pas de contournement par requêtes simultanées.
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('DELETE FROM attempts WHERE started<? AND blocked_until<=?',(now-ATTEMPT_WINDOW,now))
            account=conn.execute('SELECT * FROM attempts WHERE bucket=?',(bucket,)).fetchone()
            global_row=conn.execute('SELECT * FROM attempts WHERE bucket=?',('global',)).fetchone()
            if account and account['blocked_until']>now: return {'error':LOCKED_LOGIN}
            if global_row and global_row['started']>now-GLOBAL_WINDOW and global_row['failures']>=GLOBAL_ATTEMPTS:
                return {'error':LOCKED_LOGIN}
            global_started=global_row['started'] if global_row and global_row['started']>now-GLOBAL_WINDOW else now
            global_count=global_row['failures']+1 if global_row and global_row['started']>now-GLOBAL_WINDOW else 1
            conn.execute('INSERT OR REPLACE INTO attempts VALUES(?,?,?,0)',('global',global_started,global_count))
            user=conn.execute('SELECT * FROM users WHERE email=?',(normalized,)).fetchone()
            encoded=user['password_hash'] if user else self._dummy
            valid=verify_password(password,encoded)
            if not valid or not user or not user['enabled']:
                recent=account and account['started']>now-ATTEMPT_WINDOW and account['blocked_until']==0
                count=account['failures']+1 if recent else 1
                started=account['started'] if recent else now
                conn.execute('INSERT OR REPLACE INTO attempts VALUES(?,?,?,?)',
                             (bucket,started,count,now+LOCK_SECONDS if count>=MAX_FAILURES else 0))
                conn.execute('INSERT INTO events(created,event,email) VALUES(?,?,?)',(now,'login_failed',normalized))
                return {'error':INVALID_LOGIN}
            conn.execute('DELETE FROM attempts WHERE bucket=?',(bucket,))
            conn.execute('DELETE FROM sessions WHERE expires<=? OR last_seen<=?',(now,now-IDLE_SECONDS))
            token=secrets.token_urlsafe(32)
            conn.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?)',
                         (token_hash(token),normalized,user['version'],now,now,now+SESSION_SECONDS))
            conn.execute('INSERT INTO events(created,event,email) VALUES(?,?,?)',(now,'login_success',normalized))
            return {'email':normalized,'token':token}

    def user_for_session(self,token,*,touch=False):
        if not isinstance(token,str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}',token): return None
        now=self.clock(); key=token_hash(token)
        with self.connection() as conn:
            row=conn.execute('''SELECT s.*,u.enabled,u.version AS current_version FROM sessions s
                                JOIN users u ON u.email=s.email WHERE s.token_hash=?''',(key,)).fetchone()
            if not row: return None
            if not row['enabled'] or row['version']!=row['current_version'] or row['expires']<=now or row['last_seen']+IDLE_SECONDS<=now:
                conn.execute('DELETE FROM sessions WHERE token_hash=?',(key,)); return None
            if touch: conn.execute('UPDATE sessions SET last_seen=? WHERE token_hash=?',(now,key))
            return row['email']

    def logout(self,token):
        if not isinstance(token,str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}',token): return
        with self.connection() as conn:
            row=conn.execute('SELECT email FROM sessions WHERE token_hash=?',(token_hash(token),)).fetchone()
            conn.execute('DELETE FROM sessions WHERE token_hash=?',(token_hash(token),))
            if row: conn.execute('INSERT INTO events(created,event,email) VALUES(?,?,?)',(self.clock(),'logout',row['email']))
