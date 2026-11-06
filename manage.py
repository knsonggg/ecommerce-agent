"""Local operator commands; passwords are entered with getpass, never CLI arguments."""
import argparse
import getpass
import os
from pathlib import Path
from agent import load_env
from store import Store, Problem


def main():
    load_env()
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('create-admin')
    reset = commands.add_parser('reset-password')
    reset.add_argument('username')
    backup = commands.add_parser('backup')
    backup.add_argument('destination', help='Private destination, never inside tracked source')
    args = parser.parse_args()
    store = Store(os.environ.get('COMMERCE_DB') or None)
    try:
        if args.command == 'backup':
            destination = Path(args.destination).resolve()
            if destination.suffix not in ('.sqlite3', '.db'):
                raise Problem(400, '备份文件请使用 .sqlite3 或 .db 后缀')
            store.backup(destination)
            print('Backup created. Keep it private; it contains business records and password hashes.')
            return
        username = input('Administrator username: ').strip() if args.command == 'create-admin' else args.username
        password = getpass.getpass('Password (at least 12 characters): ')
        if password != getpass.getpass('Confirm password: '):
            raise Problem(400, '两次密码不一致')
        if args.command == 'create-admin':
            store.create_user({'username': username, 'password': password})
        else:
            store.reset_password(username, password)
        print('Account updated. Existing sessions are revoked after a password reset.')
    except Problem as error:
        parser.exit(1, error.message + '\n')


if __name__ == '__main__':
    main()
