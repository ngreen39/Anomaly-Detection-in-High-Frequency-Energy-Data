from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from scripts.reconcile_mysql import check_server_version, create_database, load_config


class MySQLConfigTests(unittest.TestCase):
    def config_file(self, replacements=None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / ".env"
        fields = dict(MYSQL_HOST="127.0.0.1", MYSQL_PORT="3306", MYSQL_UNIX_SOCKET="",
                      MYSQL_USER="fixture_user", MYSQL_PASSWORD='"literal # $() = password"',
                      MYSQL_DATABASE="energy_dq_audit_fixture")
        fields.update(replacements or {})
        path.write_text("\n".join(f"{key}={value}" for key, value in fields.items()))
        path.chmod(0o600)
        return path

    def test_literal_credentials_and_explicit_local_database(self):
        options, database = load_config(self.config_file())
        self.assertEqual(options["password"], "literal # $() = password")
        self.assertEqual(database, "energy_dq_audit_fixture")
        self.assertIsNone(options["unix_socket"])

    def test_placeholders_remote_hosts_and_unsafe_names_rejected(self):
        for replacement in [dict(MYSQL_PASSWORD="REPLACE_WITH_LOCAL_PASSWORD"),
                            dict(MYSQL_HOST="example.org"), dict(MYSQL_DATABASE="mysql"),
                            dict(MYSQL_DATABASE="energy_dq_audit_bad`; DROP DATABASE mysql;")]:
            with self.subTest(replacement=list(replacement)):
                with self.assertRaises(ValueError):
                    load_config(self.config_file(replacement))

    def test_credentials_require_private_permissions(self):
        path = self.config_file()
        path.chmod(0o644)
        with self.assertRaises(ValueError):
            load_config(path)

    def test_existing_database_failure_is_not_silenced(self):
        cursor = Mock()
        cursor.execute.side_effect = RuntimeError("database already exists")
        with self.assertRaisesRegex(RuntimeError, "already exists"):
            create_database(cursor, "energy_dq_audit_fixture")
        cursor.execute.assert_called_once()
        query = cursor.execute.call_args.args[0]
        self.assertTrue(query.startswith("CREATE DATABASE"))
        self.assertNotIn("IF NOT EXISTS", query)
        self.assertNotIn("DROP", query)

    def test_mysql_version_requirement(self):
        check_server_version("8.0.40")
        check_server_version("9.4.0")
        for version in ("5.7.44", "10.11.0-MariaDB"):
            with self.assertRaises(ValueError):
                check_server_version(version)


if __name__ == "__main__":
    unittest.main()
