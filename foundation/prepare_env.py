"""Create new local training credentials without printing them. Run in Ubuntu."""
import argparse
import getpass
import os
from pathlib import Path
import secrets
import string


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("environment", choices=["dev", "stage"])
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    runtime = root / ".runtime"
    db_file = runtime / f"{args.environment}.db.env"
    app_file = runtime / f"{args.environment}.app.env"
    if db_file.exists() or app_file.exists():
        parser.error("credential files already exist; no overwrite performed")
    password = getpass.getpass(f"New {args.environment} ticket_app password (24+ letters/digits): ")
    confirm = getpass.getpass("Repeat password: ")
    alphabet = string.ascii_letters + string.digits
    if password != confirm or len(password) < 24 or any(c not in alphabet for c in password):
        parser.error("passwords must match and contain at least 24 ASCII letters/digits")
    runtime.mkdir(mode=0o700, exist_ok=True)
    os.chmod(runtime, 0o700)
    admin_password = "".join(secrets.choice(alphabet) for _ in range(40))
    contents = {
        db_file: f"POSTGRES_PASSWORD={admin_password}\nPOSTGRES_DB=postgres\n",
        app_file: f"DATABASE_URL=postgresql+psycopg://ticket_app:{password}@db:5432/ticket_lab\n",
    }
    for path, content in contents.items():
        # Exclusive creation, Unix mode 0600, no secret in argv or stdout.
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
        os.chmod(path, 0o600)
        print(f"Created {path.relative_to(root)}")
    print("Use the same ticket_app password at createuser --pwprompt for this environment.")
    print("Existing PostgreSQL volumes do not adopt changed passwords from these files.")


if __name__ == "__main__":
    main()
