# Copyright 2026 AI Shopping Feeds
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Tests for credential loading and the OAuth token file."""

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import google.auth.exceptions

from search_console_mcp import auth, server


def _token(scopes):
    return json.dumps(
        {
            "client_id": "id.apps.googleusercontent.com",
            "client_secret": "secret",
            "refresh_token": "refresh",
            "token_uri": "https://oauth2.googleapis.com/token",
            "scopes": scopes,
        }
    )


class TestAuth(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        self.env = patch.dict(
            os.environ,
            {"XDG_CONFIG_HOME": str(self.root / "config")},
            clear=True,
        )
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.dir.cleanup()

    def test_scopes(self):
        self.assertEqual(auth.scopes_for(False), [auth.READ_ONLY_SCOPE])
        self.assertEqual(auth.scopes_for(True), [auth.WRITE_SCOPE])

    def test_token_path_defaults_to_config_dir_and_env_overrides(self):
        if os.name != "nt":
            self.assertEqual(
                auth.token_path(),
                self.root / "config" / "search-console-mcp" / "token.json",
            )
        os.environ[auth.TOKEN_FILE_ENV_VAR] = str(self.root / "t.json")
        self.assertEqual(auth.token_path(), self.root / "t.json")

    @unittest.skipIf(os.name == "nt", "POSIX permissions")
    def test_save_token_is_private(self):
        path = self.root / "new" / "token.json"
        auth.save_token(path, "{}")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
        auth.save_token(path, '{"a": 1}')
        self.assertEqual(json.loads(path.read_text()), {"a": 1})
        self.assertFalse(path.with_name("token.json.tmp").exists())

    def test_loads_saved_user_token(self):
        auth.save_token(auth.token_path(), _token([auth.READ_ONLY_SCOPE]))
        creds, source = auth.load_credentials(allow_write=False)
        self.assertEqual(creds.refresh_token, "refresh")
        self.assertIn("OAuth user token", source)

    def test_read_only_token_refused_in_write_mode(self):
        auth.save_token(auth.token_path(), _token([auth.READ_ONLY_SCOPE]))
        with self.assertRaisesRegex(RuntimeError, "--allow-write"):
            auth.load_credentials(allow_write=True)

    def test_write_token_works_in_both_modes(self):
        auth.save_token(auth.token_path(), _token([auth.WRITE_SCOPE]))
        auth.load_credentials(allow_write=True)
        auth.load_credentials(allow_write=False)

    def test_missing_service_account_file(self):
        os.environ[auth.SERVICE_ACCOUNT_ENV_VAR] = str(self.root / "nope.json")
        with self.assertRaisesRegex(RuntimeError, "doesn't exist"):
            auth.load_credentials(False)

    def test_service_account_takes_precedence(self):
        key = self.root / "key.json"
        key.write_text("{}")
        os.environ[auth.SERVICE_ACCOUNT_ENV_VAR] = str(key)
        auth.save_token(auth.token_path(), _token([auth.READ_ONLY_SCOPE]))
        fake = MagicMock(service_account_email="sa@p.iam.gserviceaccount.com")
        with patch.object(
            auth.service_account.Credentials,
            "from_service_account_file",
            return_value=fake,
        ) as load:
            creds, source = auth.load_credentials(False)
        load.assert_called_once_with(str(key), scopes=[auth.READ_ONLY_SCOPE])
        self.assertIs(creds, fake)
        self.assertIn("sa@p.iam", source)

    def test_falls_back_to_adc_and_explains_when_missing(self):
        with patch.object(
            auth.google.auth,
            "default",
            side_effect=google.auth.exceptions.DefaultCredentialsError("x"),
        ):
            with self.assertRaisesRegex(
                RuntimeError, "search-console-mcp auth --client-secrets"
            ):
                auth.load_credentials(False)
        adc = MagicMock()
        with patch.object(
            auth.google.auth, "default", return_value=(adc, "proj")
        ) as default:
            creds, source = auth.load_credentials(False)
        default.assert_called_once_with(scopes=[auth.READ_ONLY_SCOPE])
        self.assertIs(creds, adc)
        self.assertEqual(source, "Application Default Credentials")

    def test_client_file_must_be_a_desktop_client(self):
        web = self.root / "web.json"
        web.write_text(json.dumps({"web": {"client_id": "x"}}))
        with self.assertRaisesRegex(ValueError, "Desktop app"):
            auth._check_client_file(web)
        with self.assertRaises(FileNotFoundError):
            auth._check_client_file(self.root / "missing.json")

    def test_auth_command_runs_flow_and_reports(self):
        client = self.root / "client.json"
        client.write_text(json.dumps({"installed": {"client_id": "x"}}))
        with (
            patch.object(
                auth, "run_oauth_flow", return_value=Path("/tmp/t.json")
            ) as flow,
            patch("builtins.print") as printed,
        ):
            server.main(["auth", "--client-secrets", str(client)])
        flow.assert_called_once()
        self.assertFalse(flow.call_args.kwargs["allow_write"])
        self.assertIn("read-only token", printed.call_args_list[0].args[0])


class TestTokenProvider(unittest.TestCase):

    def test_refreshes_once_and_caches(self):
        creds = MagicMock(valid=False, token="abc")

        def refresh(_request):
            creds.valid = True

        creds.refresh.side_effect = refresh
        with patch.object(
            auth, "load_credentials", return_value=(creds, "test")
        ) as load:
            provider = auth.TokenProvider()
            self.assertEqual(provider.access_token(), "abc")
            self.assertEqual(provider.access_token(), "abc")
        load.assert_called_once_with(False)
        creds.refresh.assert_called_once()
        self.assertEqual(provider.source, "test")


if __name__ == "__main__":
    unittest.main()
