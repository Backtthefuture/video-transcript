import io
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import sph_resolver  # noqa: E402
import transcript  # noqa: E402


class WechatResolverErrorTests(unittest.TestCase):
    def test_missing_state_is_auth_required_without_browser(self):
        with patch.object(sph_resolver, "load_state", return_value=None), patch.object(
            sph_resolver.asyncio, "run", side_effect=AssertionError("browser should not run")
        ):
            status = sph_resolver.check_login_state()

        self.assertFalse(status["loggedIn"])
        self.assertEqual(status["code"], "WECHAT_AUTH_REQUIRED")
        self.assertTrue(status["authOnly"])

    def test_resolve_without_state_has_stable_code(self):
        with patch.object(sph_resolver, "load_state", return_value=None):
            with self.assertRaises(sph_resolver.WechatResolverError) as caught:
                sph_resolver.resolve_wechat("https://weixin.qq.com/sph/test")

        self.assertEqual(caught.exception.code, "WECHAT_AUTH_REQUIRED")
        self.assertEqual(caught.exception.stage, "auth")

    def test_empty_parse_result_is_not_reported_as_login_failure(self):
        with patch.object(
            sph_resolver,
            "_http_json",
            return_value=({"code": 0, "data": {}}, 200),
        ):
            with self.assertRaises(sph_resolver.WechatResolverError) as caught:
                sph_resolver.http_parse_share_url(
                    "https://weixin.qq.com/sph/test", "hy_token=local-only"
                )

        self.assertEqual(caught.exception.code, "WECHAT_PARSE_EMPTY")
        self.assertEqual(caught.exception.stage, "parse")

    def test_empty_media_stream_has_stream_stage(self):
        state = {
            "cookies": [
                {"domain": ".yuanbao.tencent.com", "name": "hy_token", "value": "local-only"}
            ]
        }
        parsed = {"playable_url": "https://example.invalid/?token=t&eid=e"}
        empty_feed = {"data": {"feedInfo": {}, "authorInfo": {}}}
        with patch.object(sph_resolver, "http_get_userinfo", return_value={"id": "u"}), patch.object(
            sph_resolver, "http_parse_share_url", return_value=parsed
        ), patch.object(sph_resolver, "fetch_feed_info", return_value=(empty_feed, None)):
            with self.assertRaises(sph_resolver.WechatResolverError) as caught:
                sph_resolver.resolve_via_http("https://weixin.qq.com/sph/test", state)

        self.assertEqual(caught.exception.code, "WECHAT_STREAM_EMPTY")
        self.assertEqual(caught.exception.stage, "stream")

    def test_browser_crash_does_not_hide_http_stage(self):
        state = {"cookies": [{"name": "hy_token", "value": "local-only"}]}
        parse_error = sph_resolver.WechatResolverError(
            "WECHAT_PARSE_EMPTY", "parse", "empty parse response"
        )
        with patch.object(sph_resolver, "load_state", return_value=state), patch.object(
            sph_resolver, "resolve_via_http", side_effect=parse_error
        ), patch.object(
            sph_resolver, "resolve_via_browser", side_effect=RuntimeError("chromium unavailable")
        ):
            with self.assertRaises(sph_resolver.WechatResolverError) as caught:
                sph_resolver.resolve_wechat("https://weixin.qq.com/sph/test")

        self.assertEqual(caught.exception.code, "WECHAT_PARSE_EMPTY")
        self.assertEqual(caught.exception.stage, "parse")
        self.assertIn("浏览器兜底也失败", caught.exception.message)

    def test_probe_failure_emits_machine_readable_json(self):
        error = sph_resolver.WechatResolverError(
            "WECHAT_PARSE_EMPTY", "parse", "no media profile"
        )
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch.object(sys, "argv", ["sph_resolver.py", "--probe", "https://weixin.qq.com/sph/test"]), patch.object(
            sph_resolver, "resolve_wechat", side_effect=error
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = sph_resolver.main()

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 1)
        self.assertEqual(payload["code"], "WECHAT_PARSE_EMPTY")
        self.assertEqual(payload["stage"], "parse")


class VideoDownloadBridgeTests(unittest.TestCase):
    def test_default_bridge_forces_yuanbao_login(self):
        with tempfile.NamedTemporaryFile() as media, patch.dict(
            os.environ, {"VIDEO_DOWNLOAD_WECHAT_RESOLVER": ""}
        ), patch.object(
            transcript,
            "_run_video_download_json",
            return_value={"ok": True, "path": media.name, "title": "test"},
        ) as run_download:
            path, title = transcript.download_via_video_download(
                "https://weixin.qq.com/sph/test"
            )

        args = run_download.call_args.args[0]
        self.assertEqual(path, media.name)
        self.assertEqual(title, "test")
        self.assertEqual(args[-2:], ["--wechat-resolver", "yuanbao-login"])

    def test_bridge_preserves_explicit_resolver_override(self):
        with tempfile.NamedTemporaryFile() as media, patch.dict(
            os.environ, {"VIDEO_DOWNLOAD_WECHAT_RESOLVER": "cookie"}
        ), patch.object(
            transcript,
            "_run_video_download_json",
            return_value={"ok": True, "path": media.name, "title": "test"},
        ) as run_download:
            transcript.download_via_video_download("https://weixin.qq.com/sph/test")

        args = run_download.call_args.args[0]
        self.assertEqual(args[-2:], ["--wechat-resolver", "cookie"])

    def test_bridge_uses_current_python_interpreter(self):
        completed = SimpleNamespace(returncode=0, stdout='{"ok": true}', stderr="")
        with patch.object(transcript, "find_video_download_script", return_value=__file__), patch.object(
            subprocess, "run", return_value=completed
        ) as run_process:
            transcript._run_video_download_json(["https://example.invalid/video"])

        command = run_process.call_args.args[0]
        self.assertEqual(command[0], sys.executable)



class InstallerInvariantTests(unittest.TestCase):
    def test_cloud_installer_has_no_local_asr_setup(self):
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn("quarkclouddrive", installer)
        self.assertIn("WECHAT_RESOLVER=yuanbao-login", installer)
        self.assertNotIn("pip install funasr", installer)
        self.assertNotIn("pip install torchaudio", installer)

    def test_bootstrap_preserves_user_data(self):
        bootstrap = (ROOT / "bootstrap.sh").read_text(encoding="utf-8")
        self.assertIn("--exclude='.env'", bootstrap)
        self.assertIn("--exclude='outputs/'", bootstrap)
        self.assertIn("VIDEO_TRANSCRIPT_TARGET", bootstrap)
        self.assertNotIn('rm -rf "$TARGET"', bootstrap)


if __name__ == "__main__":
    unittest.main()
