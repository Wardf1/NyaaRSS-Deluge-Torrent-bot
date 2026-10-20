import hashlib
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

if sys.platform == "win32":
    # Production uses Linux flock; Windows tests exercise control flow only.
    sys.modules["fcntl"] = types.SimpleNamespace(
        flock=lambda *a: None, LOCK_EX=2, LOCK_SH=1, LOCK_UN=8)
import torrent_recovery as recovery
import bot


def bencode(value):
    if isinstance(value, int):
        return b"i" + str(value).encode() + b"e"
    if isinstance(value, bytes):
        return str(len(value)).encode() + b":" + value
    if isinstance(value, list):
        return b"l" + b"".join(map(bencode, value)) + b"e"
    return b"d" + b"".join(bencode(k) + bencode(v) for k, v in sorted(value.items())) + b"e"


def torrent(data=b"abcdefghijk", name=b"episode.mkv", multi=False):
    info = {b"name": name, b"piece length": 4,
            b"pieces": b"".join(hashlib.sha1(data[i:i+4]).digest() for i in range(0,len(data),4))}
    if multi:
        info[b"files"] = [{b"path":[b"a.mkv"], b"length":3},
                         {b"path":[b"b.mkv"], b"length":len(data)-3}]
    else:
        info[b"length"] = len(data)
    raw=bencode({b"info":info})
    return raw, recovery.decode_torrent(raw)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.con=sqlite3.connect(":memory:")
        recovery.init_storage(self.con)
        self.raw,self.meta=torrent()
    def tearDown(self):
        self.con.close()
        self.temp.cleanup()
    def verify(self,meta=None,plan=None):
        return recovery.verified_plan(meta or self.meta,[str(self.root)],"Series S01E01",{},plan,
                                      lambda *a: "renamed.mkv" if not (meta or {}).get("multi") else None)
    def test_complete_file(self):
        (self.root/"episode.mkv").write_bytes(b"abcdefghijk")
        self.assertEqual(len(self.verify()),1)
    def test_truncated_file(self):
        (self.root/"episode.mkv").write_bytes(b"abc")
        self.assertIsNone(self.verify())
    def test_corrupt_same_length(self):
        (self.root/"episode.mkv").write_bytes(b"abcdefghijX")
        self.assertIsNone(self.verify())
    def test_renamed_file_after_interruption(self):
        (self.root/"renamed.mkv").write_bytes(b"abcdefghijk")
        self.assertEqual(len(self.verify()),1)
    def test_wrong_release_not_selected(self):
        (self.root/"another-release-S01E01.mkv").write_bytes(b"abcdefghijk")
        self.assertIsNone(self.verify())
    def test_multifile_piece_boundary(self):
        raw,meta=torrent(name=b"folder",multi=True)
        (self.root/"folder").mkdir()
        (self.root/"folder/a.mkv").write_bytes(b"abc")
        (self.root/"folder/b.mkv").write_bytes(b"defghijk")
        plan=recovery.verified_plan(meta,[str(self.root)],"",{},None,lambda *a:None)
        self.assertEqual(len(plan),2)
        (self.root/"folder/b.mkv").write_bytes(b"Xefghijk")
        self.assertIsNone(recovery.verified_plan(meta,[str(self.root)],"",{},None,lambda *a:None))
    def test_path_traversal_rejected(self):
        with self.assertRaises(ValueError):torrent(name=b"../episode.mkv")
    def test_hash_mismatch_rejected(self):
        with self.assertRaises(ValueError):
            recovery.store_source(self.con,"0"*40,self.meta,[str(self.root)])
    def test_metadata_durable_before_rpc(self):
        client=bot.DelugeWeb("http://localhost","unused")
        client.session.get=Mock(return_value=Mock(content=self.raw))
        def rpc(*args):
            self.assertEqual(recovery.source_record(self.con,self.meta["hash"])[0],self.meta)
            raise RuntimeError("connection failed")
        client.call=Mock(side_effect=rpc)
        with self.assertRaises(RuntimeError):
            client.add_torrent_url("http://localhost/file",{},lambda m:
                recovery.store_source(self.con,m["hash"],m,[str(self.root)]))
        self.assertEqual(recovery.source_record(self.con,self.meta["hash"])[0],self.meta)
    def test_null_add_is_not_success(self):
        client=bot.DelugeWeb("http://localhost","unused")
        client.session.get=Mock(return_value=Mock(content=self.raw))
        client.call=Mock(side_effect=[None,{}])
        with self.assertRaises(RuntimeError):client.add_torrent_url("url",{})
    def test_existing_torrent_is_confirmed(self):
        client=bot.DelugeWeb("http://localhost","unused")
        client.session.get=Mock(return_value=Mock(content=self.raw))
        client.call=Mock(side_effect=[None,{"name":"episode.mkv"}])
        self.assertEqual(client.add_torrent_url("url",{}),self.meta["hash"])
    def test_corrupt_queue_is_preserved(self):
        q=self.root/"queue.json";q.write_text("{broken")
        cfg={"transcode":{"enabled":True,"queue_file":str(q)}}
        with self.assertRaises(json.JSONDecodeError):
            bot.enqueue_transcode_job(cfg,"id","title",self.root/"episode.mkv")
        self.assertEqual(q.read_text(),"{broken")
    def test_recovery_fetch_backoff(self):
        with patch.object(recovery.requests,"get",side_effect=RuntimeError("offline")) as get:
            self.assertEqual(recovery.recover_source(self.con,"id","url",{}, {},[3]),(None,[]))
            recovery.recover_source(self.con,"id","url",{}, {},[3])
            self.assertEqual(get.call_count,1)
    def test_lost_torrent_recovered_and_queued_once(self):
        (self.root/"episode.mkv").write_bytes(b"abcdefghijk")
        cfg={"default_download_location":str(self.root),"series":{"Series":{"transcode":True}},
             "transcode":{"enabled":True,"queue_file":str(self.root/"queue.json")}}
        with patch.object(bot,"DB_FILE",str(self.root/"seen.db")):
            con=bot.init_db()
        tid=self.meta["hash"]
        bot.mark_seen(con,tid,"Series S01E01","url")
        recovery.store_source(con,tid,self.meta,[str(self.root)])
        client=Mock();client.call.return_value={}
        bot.process_completed_renames(con,client,cfg)
        bot.process_completed_renames(con,client,cfg)
        self.assertEqual(con.execute("select renamed from seen").fetchone()[0],1)
        jobs=json.loads((self.root/"queue.json").read_text())
        self.assertEqual(len(jobs),1)
        self.assertEqual(jobs[0]["torrent_id"],tid)
        con.close()
    def test_present_torrent_never_modified(self):
        (self.root/"episode.mkv").write_bytes(b"abcdefghijk")
        cfg={"series":{"Series":{"set_name":"renamed"}}}
        with patch.object(bot,"DB_FILE",str(self.root/"seen.db")):con=bot.init_db()
        tid=self.meta["hash"];bot.mark_seen(con,tid,"Series S01E01","url")
        client=Mock();client.call.return_value={tid:{"save_path":str(self.root),"files":self.meta["files"],"progress":100}}
        bot.process_completed_renames(con,client,cfg)
        self.assertTrue((self.root/"episode.mkv").exists())
        self.assertEqual(con.execute("select renamed from seen").fetchone()[0],0)
        con.close()
    def test_main_does_not_mark_failed_add_seen(self):
        cfg={"transcode":{},"series":{},"deluge_url":"http://localhost","deluge_password":"unused",
             "default_download_location":str(self.root),"rss_url":"url"}
        item={"id":self.meta["hash"],"title":"Series S01E01","torrent_url":"url"}
        client=Mock();client.add_torrent_url.side_effect=RuntimeError("not added")
        with patch.object(bot,"DB_FILE",str(self.root/"seen.db")), \
             patch.object(bot,"load_config",return_value=cfg), \
             patch.object(bot,"DelugeWeb",return_value=client), \
             patch.object(bot,"process_completed_renames"), \
             patch.object(bot,"fetch_rss",return_value=""), \
             patch.object(bot,"parse_rss",return_value=[item]), \
             patch.object(bot,"should_add",return_value=True), \
             patch.object(bot,"process_journal_counter"):
            bot.main()
        con=sqlite3.connect(self.root/"seen.db")
        self.assertEqual(con.execute("select count(*) from seen").fetchone()[0],0)
        con.close()

    def test_same_path_different_torrent_is_not_suppressed(self):
        q=self.root/"queue.json"
        q.write_text(json.dumps([{"torrent_id":"old","file_path":str((self.root/"episode.mkv").resolve()),
                                  "status":"done"}]))
        cfg={"transcode":{"enabled":True,"queue_file":str(q)}}
        bot.enqueue_transcode_job(cfg,"new","title",self.root/"episode.mkv")
        self.assertEqual(len(json.loads(q.read_text())),2)
    def test_active_path_collision_is_rejected(self):
        q=self.root/"queue.json"
        q.write_text(json.dumps([{"torrent_id":"old","file_path":str((self.root/"episode.mkv").resolve()),
                                  "status":"processing"}]))
        cfg={"transcode":{"enabled":True,"queue_file":str(q)}}
        with self.assertRaises(RuntimeError):
            bot.enqueue_transcode_job(cfg,"new","title",self.root/"episode.mkv")
        self.assertEqual(len(json.loads(q.read_text())),1)
    def test_multifile_rename_collision_is_rejected(self):
        raw,meta=torrent(name=b"folder",multi=True)
        (self.root/"folder").mkdir()
        (self.root/"folder/a.mkv").write_bytes(b"abc")
        (self.root/"folder/b.mkv").write_bytes(b"defghijk")
        with self.assertRaises(ValueError):
            recovery.verified_plan(meta,[str(self.root)],"",{},None,lambda *a:"same.mkv")
    def test_main_persists_plan_at_add_time(self):
        cfg={"transcode":{},"series":{"Series":{}},"deluge_url":"http://localhost","deluge_password":"unused",
             "default_download_location":str(self.root),"rss_url":"url"}
        item={"id":self.meta["hash"],"title":"Series S01E01","torrent_url":"url"}
        client=Mock();client.call.return_value=False
        def add(url,options,prepare):
            prepare(self.meta)
            return self.meta["hash"]
        client.add_torrent_url.side_effect=add
        with patch.object(bot,"DB_FILE",str(self.root/"seen.db")), \
             patch.object(bot,"load_config",return_value=cfg), \
             patch.object(bot,"DelugeWeb",return_value=client), \
             patch.object(bot,"process_completed_renames"), \
             patch.object(bot,"fetch_rss",return_value=""), \
             patch.object(bot,"parse_rss",return_value=[item]), \
             patch.object(bot,"should_add",return_value=True), \
             patch.object(bot,"send_discord_webhook"), \
             patch.object(bot,"process_journal_counter"):
            bot.main()
        con=sqlite3.connect(self.root/"seen.db")
        self.assertTrue(con.execute("select rename_plan from seen").fetchone()[0])
        self.assertEqual(con.execute("select count(*) from torrent_sources").fetchone()[0],1)
        con.close()


if __name__=="__main__":
    unittest.main(verbosity=2)
