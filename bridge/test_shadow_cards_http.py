import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import http_receiver
from shadow_loop import ShadowDecisionStore


class ShadowCardsHttpTests(unittest.TestCase):
    def test_shadow_card_report_is_non_executable(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(http_receiver, "DATA_DIR", Path(temp) / "data"):
                report = http_receiver.build_commerce_shadow_cards()
        self.assertEqual("shadow_only", report["mode"])
        self.assertFalse(report["platform_write_attempted"])
        self.assertFalse(report["can_execute"])

    def test_human_feedback_is_local_only(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ShadowDecisionStore(Path(temp))
            store.append({
                "shadow_id": "shadow-http-1",
                "snapshot": {"metrics": {"roi": 1.0}},
                "decision_proposal": {
                    "action": "OBSERVE",
                    "reason_code": "NO_ACTIONABLE_RISK",
                    "evidence_hash": "b" * 64,
                },
                "human_action": None,
            })
            updated = store.update_human_action("shadow-http-1", "confirmed", "先观察 2 分钟")
            self.assertEqual("confirmed", updated["human_action"]["action"])
            self.assertEqual("human_confirmed", updated["status"])


if __name__ == "__main__":
    unittest.main()
