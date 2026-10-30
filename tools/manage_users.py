"""Gestion des comptes IMPACT depuis le serveur. Aucun mot de passe dans les arguments."""
import argparse
import getpass
import secrets
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from impact import db
from impact.auth import AuthStore


def main():
    parser=argparse.ArgumentParser(description='Gérer les comptes locaux IMPACT')
    parser.add_argument('action',choices=['add','reset-password','disable','enable','list'])
    parser.add_argument('email',nargs='?')
    parser.add_argument('--generate-password',action='store_true',help='Générer et afficher une seule fois le mot de passe')
    args=parser.parse_args()
    if args.action!='list' and not args.email: parser.error('Adresse email obligatoire')
    store=AuthStore(db.DATA/'auth.sqlite3')
    try:
        if args.action=='list':
            for user in store.list_users(): print(user['email']+' : '+('actif' if user['enabled'] else 'désactivé'))
        elif args.action in {'add','reset-password'}:
            password=secrets.token_urlsafe(18) if args.generate_password else getpass.getpass('Mot de passe (15 caractères minimum) : ')
            if not args.generate_password and password!=getpass.getpass('Confirmer le mot de passe : '):
                raise ValueError('Les mots de passe ne correspondent pas.')
            if args.action=='add': email=store.add_user(args.email,password)
            else: store.reset_password(args.email,password); email=args.email
            print('Compte configuré : '+email)
            if args.generate_password: print('Mot de passe : '+password)
        else:
            store.set_enabled(args.email,args.action=='enable')
            print('Compte '+('activé' if args.action=='enable' else 'désactivé')+' : '+args.email)
    except ValueError as exc: parser.exit(1,str(exc)+'\n')


if __name__=='__main__': main()
