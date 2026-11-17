"""Start/stop an isolated socket-only MySQL instance without touching other servers.

Requires installed MySQL 8+ binaries and requirements.txt. Fresh startup
replaces .env placeholders only; populated credential files are never overwritten.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile
import time

try:
    from scripts.reconcile_mysql import load_config
except ModuleNotFoundError:
    from reconcile_mysql import load_config

ROOT = Path(__file__).resolve().parents[1]


def start(state, env_file, mysqld):
    import pymysql

    state, env_file = state.resolve(), env_file.resolve()
    metadata_file = state / "server.json"
    fresh = not metadata_file.exists()
    if fresh:
        if state.exists() and any(state.iterdir()):
            raise ValueError("Refusing to initialize an existing nonempty state directory")
        initial_env = env_file.read_text() if env_file.exists() else None
        if initial_env is not None and "REPLACE_WITH_LOCAL_" not in initial_env:
            raise ValueError("Preserving populated .env. Use those credentials with reconcile_mysql.py instead")
        state.mkdir(mode=0o700, parents=True, exist_ok=True)
        state.chmod(0o700)
        socket_dir = Path(tempfile.mkdtemp(prefix="energy-dq-mysql-"))
        socket_dir.chmod(0o700)
        metadata = dict(data_dir=str(state / "data"), socket=str(socket_dir / "mysql.sock"),
                        pid_file=str(state / "mysql.pid"), log_file=str(state / "mysql.log"),
                        mysqld=str(Path(mysqld).resolve()))
        print("Initializing a separate local MySQL data directory", flush=True)
        subprocess.run([metadata["mysqld"], "--no-defaults", "--initialize-insecure",
                        "--datadir=" + metadata["data_dir"], "--log-error=" + metadata["log_file"],
                        "--skip-log-bin"], check=True)
        metadata_file.write_text(json.dumps(metadata, indent=2) + "\n")
    else:
        metadata = json.loads(metadata_file.read_text())
        options, _ = load_config(env_file)
        if options["unix_socket"] != metadata["socket"]:
            raise ValueError(".env points to another server; refusing to change it")
        try:
            connection = pymysql.connect(**options, connect_timeout=2)
        except pymysql.Error:
            pass
        else:
            with connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT @@datadir")
                    if Path(cursor.fetchone()[0]).resolve() != Path(metadata["data_dir"]).resolve():
                        raise ValueError("Socket belongs to another data directory")
            print("Isolated MySQL instance is already running", flush=True)
            return
    Path(metadata["socket"]).parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    command = [metadata["mysqld"], "--no-defaults", "--datadir=" + metadata["data_dir"],
               "--socket=" + metadata["socket"], "--pid-file=" + metadata["pid_file"],
               "--log-error=" + metadata["log_file"], "--skip-networking", "--mysqlx=OFF",
               "--skip-log-bin"]
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, start_new_session=True)
    for _ in range(150):
        if process.poll() is not None:
            raise RuntimeError(f"Isolated server stopped; inspect {metadata['log_file']}")
        try:
            if fresh:
                connection = pymysql.connect(unix_socket=metadata["socket"], user="root", password="", connect_timeout=1)
            else:
                connection = pymysql.connect(**options, connect_timeout=1)
            break
        except pymysql.Error:
            time.sleep(0.2)
    else:
        raise RuntimeError(f"Isolated server did not become ready; inspect {metadata['log_file']}")
    with connection:
        if fresh:
            if (env_file.read_text() if env_file.exists() else None) != initial_env:
                raise ValueError(".env changed during initialization; refusing to overwrite it")
            password = secrets.token_urlsafe(32)
            with connection.cursor() as cursor:
                cursor.execute("ALTER USER 'root'@'localhost' IDENTIFIED BY %s", (password,))
            database = "energy_dq_audit_local_" + secrets.token_hex(6)
            content = ("# Generated for this project's isolated, socket-only MySQL instance.\n"
                       "MYSQL_HOST=localhost\nMYSQL_PORT=3306\n"
                       f"MYSQL_UNIX_SOCKET={metadata['socket']}\nMYSQL_USER=root\n"
                       f"MYSQL_PASSWORD={password}\nMYSQL_DATABASE={database}\n")
            temporary = env_file.with_name(env_file.name + ".tmp")
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w") as handle:
                handle.write(content)
            temporary.replace(env_file)
    print("Isolated MySQL ready. Credentials saved privately in ignored .env (not printed).", flush=True)


def stop(state, env_file):
    import pymysql

    metadata = json.loads((state.resolve() / "server.json").read_text())
    options, _ = load_config(env_file)
    if options["unix_socket"] != metadata["socket"]:
        raise ValueError(".env points to another server; refusing shutdown")
    with pymysql.connect(**options, connect_timeout=3) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT @@datadir")
            if Path(cursor.fetchone()[0]).resolve() != Path(metadata["data_dir"]).resolve():
                raise ValueError("Refusing to stop another data directory")
            cursor.execute("SHUTDOWN")
    for _ in range(150):
        if not Path(metadata["pid_file"]).exists():
            break
        time.sleep(0.2)
    else:
        raise RuntimeError("Shutdown requested but PID file remains; inspect the isolated instance log")
    print("Isolated MySQL stopped; its data is preserved.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "stop"))
    parser.add_argument("--state-dir", type=Path, default=ROOT / ".local-mysql")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--mysqld", default=shutil.which("mysqld") or "/usr/local/mysql/bin/mysqld")
    args = parser.parse_args()
    if args.action == "start":
        start(args.state_dir, args.env_file, args.mysqld)
    else:
        stop(args.state_dir, args.env_file)


if __name__ == "__main__":
    main()
