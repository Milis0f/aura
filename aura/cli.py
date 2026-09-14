"""aura-manage: NAS accounts from a terminal (same commands as NAS Dashboard's manage.py)."""

from __future__ import annotations

import argparse
import getpass
import sys

from . import db
from .services import accounts


def _ask_password() -> str:
    while True:
        first = getpass.getpass("Mot de passe : ")
        if len(first) < accounts.MIN_PASSWORD:
            print(f"  {accounts.MIN_PASSWORD} caractères minimum.")
            continue
        if first != getpass.getpass("Confirme     : "):
            print("  Les deux saisies diffèrent.")
            continue
        return first


def _yes(question: str, default: bool) -> bool:
    answer = input(f"{question} [{'O/n' if default else 'o/N'}] ").strip().lower()
    return default if not answer else answer in ("o", "oui", "y", "yes")


def cmd_add(args: argparse.Namespace) -> None:
    password = sys.stdin.readline().rstrip("\n") if args.password_stdin else _ask_password()
    write = args.write or (not args.password_stdin and _yes("Autoriser l'écriture (envoi, suppression) ?", False))
    torrent = args.torrent or (not args.password_stdin and _yes("Autoriser les téléchargements ?", False))
    accounts.create_user(args.username, password, write, torrent)
    print(f"Compte « {args.username} » créé.")
    if not args.password_stdin and _yes("Activer la double authentification (recommandé) ?", True):
        secret, uri = accounts.start_totp(args.username)
        print("\nAjoute ce compte dans ton application d'authentification (Aegis, Bitwarden, Google Authenticator) :")
        print(f"  Clé : {secret}\n  URI : {uri}")
        code = input("Code affiché par l'application : ")
        accounts.enable_totp(args.username, code)
        print("Double authentification activée.")


def cmd_passwd(args: argparse.Namespace) -> None:
    accounts.set_password(args.username, _ask_password())
    print("Mot de passe mis à jour.")


def cmd_perms(args: argparse.Namespace) -> None:
    accounts.set_permissions(args.username, args.write == "1", args.torrent == "1")
    print("Permissions mises à jour (sessions de ce compte fermées).")


def cmd_list(_: argparse.Namespace) -> None:
    for user in accounts.list_users():
        flags = ["écriture" if user["can_write"] else "lecture seule"]
        if user["can_torrent"]:
            flags.append("téléchargements")
        flags.append("2FA" if user["has_2fa"] else "sans 2FA")
        print(f"  {user['username']:<20} {', '.join(flags):<40} {user['created_at']}")


def cmd_delete(args: argparse.Namespace) -> None:
    accounts.delete_user(args.username)
    print(f"Compte « {args.username} » supprimé.")


def cmd_audit(args: argparse.Namespace) -> None:
    for row in accounts.recent_audit(args.count):
        print(f"  {row['ts']}  {str(row['username']):<14} {str(row['ip']):<16} {row['action']:<16} {row['detail'] or ''}")


def cmd_import(args: argparse.Namespace) -> None:
    users, events = accounts.import_nasdash(args.path)
    print(f"{users} compte(s) et {events} ligne(s) de journal importés depuis {args.path}.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aura-manage", description="Comptes du NAS Aura")
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add", help="créer un compte")
    add.add_argument("username")
    add.add_argument("--write", action="store_true", help="autoriser l'écriture")
    add.add_argument("--torrent", action="store_true", help="autoriser les téléchargements")
    add.add_argument("--password-stdin", action="store_true", help="lire le mot de passe sur l'entrée standard")
    add.set_defaults(func=cmd_add)
    passwd = sub.add_parser("passwd", help="changer un mot de passe")
    passwd.add_argument("username")
    passwd.set_defaults(func=cmd_passwd)
    perms = sub.add_parser("perms", help="permissions : écriture 0|1, téléchargements 0|1")
    perms.add_argument("username")
    perms.add_argument("write", choices=("0", "1"))
    perms.add_argument("torrent", choices=("0", "1"))
    perms.set_defaults(func=cmd_perms)
    sub.add_parser("list", help="lister les comptes").set_defaults(func=cmd_list)
    delete = sub.add_parser("delete", help="supprimer un compte")
    delete.add_argument("username")
    delete.set_defaults(func=cmd_delete)
    audit = sub.add_parser("audit", help="activité récente")
    audit.add_argument("count", nargs="?", type=int, default=40)
    audit.set_defaults(func=cmd_audit)
    imp = sub.add_parser("import-nasdash", help="reprendre les comptes de NAS Dashboard")
    imp.add_argument("path", nargs="?", default="/var/lib/nasdash/nasdash.db")
    imp.set_defaults(func=cmd_import)
    args = parser.parse_args(argv)
    db.init_db()
    try:
        args.func(args)
    except accounts.AccountError as exc:
        print(exc.message, file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print()
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
