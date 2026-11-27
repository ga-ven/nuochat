import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from nuochat.stats import ActivityRecord, ActivityStore


class StatsTests(unittest.TestCase):
    def record(self, request_id, status="待审核", auto_send=False, tokens=120):
        return ActivityRecord(
            request_id=request_id,
            timestamp=datetime.now().astimezone().isoformat(timespec="seconds"),
            title="客户甲",
            reply="建议回复",
            source_text="客户问题",
            status=status,
            auto_send=auto_send,
            had_image=False,
            prompt_tokens=100,
            completion_tokens=20,
            total_tokens=tokens,
            model="test-model",
        )

    def test_records_persist_and_summary_tracks_workload(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stats.json"
            store = ActivityStore(path)
            store.add(self.record("one"))
            store.add(self.record("two", status="已发送", auto_send=True))
            store.resolve("one", "已丢弃")

            loaded = ActivityStore(path)
            summary = loaded.summary()
            self.assertEqual(summary.today_generated, 2)
            self.assertEqual(summary.today_sent, 1)
            self.assertEqual(summary.today_auto_sent, 1)
            self.assertEqual(summary.today_manual_sent, 0)
            self.assertEqual(summary.today_total_tokens, 240)
            self.assertEqual(summary.total_tokens, 240)
            statuses = {record.request_id: record.status for record in loaded.recent(3)}
            self.assertEqual(statuses["one"], "已丢弃")

    def test_store_keeps_only_latest_records(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ActivityStore(Path(directory) / "stats.json", max_records=2)
            store.add(self.record("one"))
            store.add(self.record("two"))
            store.add(self.record("three"))
            self.assertEqual([record.request_id for record in store.recent(3)], ["three", "two"])

    def test_invalid_stats_file_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stats.json"
            path.write_text("not json", encoding="utf-8")
            self.assertEqual(ActivityStore(path).recent(), [])


if __name__ == "__main__":
    unittest.main()
