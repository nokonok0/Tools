import unittest
from unittest.mock import Mock

from codex_auto.cli import classify, RoutingError
from test_cli import response


class ClassificationTests(unittest.TestCase):
    def test_plain_greeting_ignores_completed_dangerous_history(self):
        decide = Mock()
        result = classify("ありがとう。", decide, [("assistant", "削除と移行は完了しました")])
        self.assertEqual((result[0], result[3]), ("routine", "rule"))
        decide.assert_not_called()

    def test_greeting_with_operation_is_not_short_circuited(self):
        decide = Mock(return_value=response("investigation"))
        self.assertEqual(classify("ありがとう。データを全部削除して", decide)[0], "investigation")
        decide.assert_called_once()

    def test_approval_and_reference_keep_context(self):
        for prompt in ("そうして", "続けて", "yes", "それを直して"):
            decide = Mock(return_value=response("investigation"))
            classify(prompt, decide, [("assistant", "private data migration")])
            self.assertIn("private data migration", decide.call_args.args[0])
            self.assertLess(decide.call_args.args[0].index(prompt), decide.call_args.args[0].index("private data"))

    def test_self_contained_question_does_not_inherit_background_risk(self):
        decide = Mock(return_value=response())
        classify("WordとLaTeXの違いを教えて", decide, [("assistant", "データ削除の調査")])
        self.assertEqual(decide.call_args.args[0], "WordとLaTeXの違いを教えて")

    def test_image_and_ambiguous_classifier_remain_conservative(self):
        with self.assertRaises(RoutingError):
            classify("ありがとう", Mock(), has_image=True)
        self.assertEqual(classify("それをお願い", Mock(return_value=response(confidence=.2)))[0], "investigation")
        self.assertEqual(classify("研究テーマを検討して", Mock(return_value=response(confidence=.7)))[0], "investigation")
