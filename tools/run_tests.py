"""Exécuter les tests sans charger la connexion Supabase de production depuis .env."""
import argparse
import os
from pathlib import Path
import subprocess
import sys


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--postgres',action='store_true',help='Activer IMPACT_TEST_DATABASE_URL, qui doit désigner un serveur de test séparé')
    parser.add_argument('--pattern',default='test*.py',help='Motif des fichiers de tests')
    args=parser.parse_args()
    env=dict(os.environ,IMPACT_DATABASE_URL='',IMPACT_AUTH_USERS_JSON='')
    if not args.postgres: env.pop('IMPACT_TEST_DATABASE_URL',None)
    elif not env.get('IMPACT_TEST_DATABASE_URL'): parser.error('IMPACT_TEST_DATABASE_URL est obligatoire avec --postgres')
    return subprocess.call([sys.executable,'-m','unittest','discover','-s','tests','-p',args.pattern,'-v'],
                           cwd=Path(__file__).resolve().parents[1],env=env)


if __name__=='__main__': raise SystemExit(main())
