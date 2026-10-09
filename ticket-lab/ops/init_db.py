# Initialize ONLY the isolated PostgreSQL exercise database, once or consistently.
import os
import sys
import psycopg
from psycopg import sql


def main():
    with psycopg.connect(host="postgres", dbname="postgres", user="postgres",
                          password=os.environ["ADMIN_PASSWORD"], connect_timeout=5, autocommit=True) as conn:
        role = conn.execute("SELECT rolsuper FROM pg_roles WHERE rolname='ticket_app'").fetchone()
        if role is None:
            conn.execute(sql.SQL("CREATE ROLE ticket_app LOGIN PASSWORD {}").format(sql.Literal(os.environ["APP_PASSWORD"])))
        elif role[0]:
            raise RuntimeError("ticket_app must not be superuser")
        owner = conn.execute("SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname='ticket_lab'").fetchone()
        if owner is None:
            conn.execute("CREATE DATABASE ticket_lab OWNER ticket_app")
        elif owner[0] != "ticket_app":
            raise RuntimeError("Existing database has unexpected owner")
    # Existing credentials are checked, not silently replaced.
    with psycopg.connect(host="postgres", dbname="ticket_lab", user="ticket_app",
                          password=os.environ["APP_PASSWORD"], connect_timeout=5):
        pass
    print("Training role and database initialized/verified")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Initialization failed: {type(error).__name__}", file=sys.stderr)
        raise SystemExit(1)
