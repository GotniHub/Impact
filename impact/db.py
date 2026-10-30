import json
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from .config import DATA, DEFAULT_CRITERIA

LOCK=threading.RLock()
def now(): return datetime.now(timezone.utc).isoformat(timespec='seconds')
def uid(): return uuid.uuid4().hex

def external_worker_alive(job):
    pid=job.get('worker_pid')
    if type(pid) is not int or pid<=0: return False
    try:
        heartbeat=datetime.fromisoformat(job.get('worker_heartbeat',''))
        if (datetime.now(timezone.utc)-heartbeat).total_seconds()>120: return False
        if os.name=='nt':
            import ctypes
            from ctypes import wintypes
            kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
            kernel.OpenProcess.restype=wintypes.HANDLE
            kernel.GetExitCodeProcess.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
            kernel.CloseHandle.argtypes=[wintypes.HANDLE]
            handle=kernel.OpenProcess(0x1000,False,pid)
            if not handle: return False
            try:
                code=wintypes.DWORD()
                return bool(kernel.GetExitCodeProcess(handle,ctypes.byref(code))) and code.value==259
            finally: kernel.CloseHandle(handle)
        os.kill(pid,0)
        return True
    except (OSError,TypeError,ValueError): return False


def heartbeat_job(job_id):
    with LOCK,connection() as c:
        row=c.execute('SELECT payload FROM jobs WHERE id=?',(job_id,)).fetchone()
        if not row: return
        job=json.loads(row['payload'])
        if job.get('status') in {'running','queued'} and job.get('worker_pid')==os.getpid():
            job['worker_heartbeat']=now()
            c.execute('UPDATE jobs SET payload=? WHERE id=?',(json.dumps(job,ensure_ascii=False),job_id))
@contextmanager
def connection():
    DATA.mkdir(parents=True,exist_ok=True)
    c=sqlite3.connect(DATA/'impact.sqlite3', timeout=30)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON'); c.execute('PRAGMA journal_mode=WAL')
    try:
        with c:
            yield c
    finally:
        c.close()

def init():
    with LOCK, connection() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS campaigns(id TEXT PRIMARY KEY, name TEXT NOT NULL, edition TEXT NOT NULL, created TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS companies(id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL REFERENCES campaigns(id), payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS crm(id TEXT PRIMARY KEY, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS imports(id TEXT PRIMARY KEY, created TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, campaign_id TEXT REFERENCES campaigns(id), created TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT, created TEXT NOT NULL, action TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS cache(key TEXT PRIMARY KEY, created TEXT NOT NULL, payload TEXT NOT NULL);
        ''')
        c.execute('INSERT OR IGNORE INTO settings VALUES (?,?)',('criteria',json.dumps(DEFAULT_CRITERIA)))
        # Une interruption du serveur doit être visible et permettre la reprise.
        rows=c.execute('SELECT id,payload FROM jobs').fetchall()
        for row in rows:
            job=json.loads(row['payload'])
            if job.get('status') in {'running','queued'}:
                if external_worker_alive(job): continue
                job.update(status='interrupted',message='Serveur interrompu. Relancer pour reprendre les entreprises restantes.')
                c.execute('UPDATE jobs SET payload=? WHERE id=?',(json.dumps(job),row['id']))

def put(table, data):
    allowed={'campaigns','companies','crm','imports','jobs','cache'}
    if table not in allowed: raise ValueError('Table interdite')
    id=data.get('id') or data.get('key') or uid()
    data['id']=id
    columns={'campaigns':['id','name','edition','created'], 'companies':['id','campaign_id'], 'crm':['id'], 'imports':['id','created'], 'jobs':['id','campaign_id','created'], 'cache':['key','created']}[table]
    vals=[id if k in {'id','key'} else data.get(k,now() if k=='created' else '') for k in columns]
    with LOCK, connection() as c:
        if table=='jobs':
            previous=c.execute('SELECT payload FROM jobs WHERE id=?',(id,)).fetchone()
            if previous:
                previous=json.loads(previous['payload'])
                if previous.get('cancel'): data['cancel']=True
                if previous.get('worker_heartbeat','')>data.get('worker_heartbeat',''):
                    data['worker_heartbeat']=previous['worker_heartbeat']
        c.execute(f'INSERT OR REPLACE INTO {table} ({",".join(columns)},payload) VALUES ({",".join("?" for _ in range(len(vals)+1))})',[*vals,json.dumps(data,ensure_ascii=False)])
    return data

def all_rows(table, campaign_id=None):
    if table not in {'campaigns','companies','crm','imports','jobs','cache'}: raise ValueError('Table interdite')
    with connection() as c:
        rows=c.execute(f'SELECT payload FROM {table}'+(' WHERE campaign_id=?' if campaign_id else ''), (campaign_id,) if campaign_id else ()).fetchall()
    return [json.loads(r['payload']) for r in rows]

def get(table,id):
    if table not in {'campaigns','companies','crm','imports','jobs','cache'}: raise ValueError('Table interdite')
    with connection() as c:
        row=c.execute(f'SELECT payload FROM {table} WHERE {"key" if table=="cache" else "id"}=?',(id,)).fetchone()
    if not row: raise ValueError('Élément introuvable.')
    return json.loads(row['payload'])

def setting(key,value=None):
    with LOCK, connection() as c:
        if value is not None:
            c.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',(key,json.dumps(value,ensure_ascii=False)))
            return value
        row=c.execute('SELECT payload FROM settings WHERE key=?',(key,)).fetchone()
        return json.loads(row['payload']) if row else None

def audit(action,payload):
    with LOCK, connection() as c:
        c.execute('INSERT INTO audit(created,action,payload) VALUES(?,?,?)',(now(),action,json.dumps(payload,ensure_ascii=False)))

def audit_rows():
    with connection() as c:
        return [dict(r) for r in c.execute('SELECT * FROM audit ORDER BY id DESC LIMIT 500')]
