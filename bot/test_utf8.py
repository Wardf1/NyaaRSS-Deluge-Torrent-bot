import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

if sys.platform=="win32":
    import types
    sys.modules["fcntl"]=types.SimpleNamespace(flock=lambda *a:None,LOCK_EX=2,LOCK_SH=1,LOCK_UN=8)

import bot
import transcode_worker as worker

TEXT="Zażółć gęślą jaźń — Łódź 🎬"


class Utf8Tests(unittest.TestCase):
    def test_console_overrides_ascii_environment(self):
        env=dict(os.environ,PYTHONIOENCODING="ascii",PYTHONUTF8="0")
        code="from utf8_io import configure_utf8_output; configure_utf8_output(); import sys; s="+ascii(TEXT)+"; print(s); print(s,file=sys.stderr)"
        result=subprocess.run([sys.executable,"-c",code],capture_output=True,env=env,check=True)
        self.assertEqual(result.stdout.decode("utf-8").strip(),TEXT)
        self.assertEqual(result.stderr.decode("utf-8").strip(),TEXT)

    def test_ffmpeg_output_decoded_as_utf8(self):
        payload=repr(TEXT.encode("utf-8")+b"\xff")
        result=worker.run_command([sys.executable,"-c","import sys; sys.stdout.buffer.write("+payload+"); sys.stderr.buffer.write("+payload+")"])
        self.assertEqual(result.stdout,TEXT+"\ufffd")
        self.assertEqual(result.stderr,TEXT+"\ufffd")

    def test_rss_does_not_use_wrong_http_charset(self):
        response=Mock(content=('<?xml version="1.0" encoding="utf-8"?><rss><channel><item><title>'+TEXT+'</title></item></channel></rss>').encode("utf-8"))
        response.text="incorrect decoding"
        with patch.object(bot.requests,"get",return_value=response):
            rss=bot.fetch_rss("http://example.invalid")
        self.assertEqual(bot.parse_rss(rss)[0]["title"],TEXT)

    def test_windows_bom_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            config=Path(tmp)/"config.json"
            config.write_text(json.dumps({"text":TEXT},ensure_ascii=False),encoding="utf-8-sig")
            with patch.object(bot,"CONFIG_FILE",str(config)):
                self.assertEqual(bot.load_config()["text"],TEXT)
            with patch.object(worker,"CONFIG_FILE",str(config)):
                self.assertEqual(worker.load_config()["text"],TEXT)

if __name__=="__main__":
    unittest.main(verbosity=2)
