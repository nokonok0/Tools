from copy import deepcopy
import unittest
from unittest.mock import patch

from codex_auto import cli
from codex_auto.routing import TurnRouter
from test_cli import MODELS, response


def turn(identifier, text, thread="thread-1"):
    return {"id": identifier, "method": "turn/start", "params": {
        "threadId": thread, "input": [{"type": "text", "text": text}],
        "model": "gpt-6.1-sol", "effort": "low", "approvalPolicy": "on-request",
        "sandboxPolicy": {"type": "readOnly"},
        "collaborationMode": {"mode": "default", "settings": {
            "model": "gpt-6.1-sol", "reasoning_effort": "low", "developer_instructions": None}}}}


class TurnRoutingTests(unittest.TestCase):
    def test_session_picker_deduplicates_ids_not_names_without_changing_cursor(self):
        router = TurnRouter(MODELS)
        router.client_message({"id": 1, "method": "thread/list", "params": {"limit": 25}})
        first = {"id": "one", "name": "same title", "updatedAt": 100}
        second = {"id": "two", "name": "same title", "updatedAt": 90}
        message = {"id": 1, "result": {"data": [first, second, dict(first), dict(first)],
                                       "nextCursor": "opaque-cursor"}}
        router.server_message(message)
        self.assertEqual(message["result"]["data"], [first, second])
        self.assertEqual(message["result"]["nextCursor"], "opaque-cursor")
        self.assertNotIn(1, router.pending)

    def test_banned_startup_and_menu_models_are_rejected(self):
        for model in cli.BANNED_MODELS:
            with self.assertRaises(cli.RoutingError):
                TurnRouter(MODELS, model=model)
            router = TurnRouter(MODELS)
            for method in ("thread/start", "thread/resume", "thread/settings/update", "turn/settings/update"):
                for params in ({"model": model}, {"collaborationMode": {"settings": {"model": model}}}):
                    with self.assertRaises(cli.RoutingError):
                        router.client_message({"id": 1, "method": method, "params": params})
            self.assertFalse(router.pending)

    def test_remote_model_list_is_filtered_without_touching_other_responses(self):
        router = TurnRouter(MODELS)
        router.client_message({"id": 1, "method": "model/list", "params": {}})
        message = {"id": 1, "result": {"data": [
            {"id": "a", "model": "gpt-6-astra"}, {"id": "b", "model": "gpt-6-sol"},
            {"id": "c", "model": "gpt-6.1-sol"}], "nextCursor": "cursor"}}
        router.server_message(message)
        self.assertEqual(message["result"]["data"], [{"id": "c", "model": "gpt-6.1-sol"}])
        self.assertEqual(message["result"]["nextCursor"], "cursor")
        other = {"id": 2, "result": {"data": [{"id": "gpt-6-astra"}]}}
        before = deepcopy(other)
        router.server_message(other)
        self.assertEqual(other, before)

    def test_each_turn_changes_model_without_changing_thread_or_permissions(self):
        router = TurnRouter(MODELS)
        with patch.object(cli.Ollaya, "decide", side_effect=[response("routine"), response("implementation"), response("investigation")]) as decide:
            for identifier, model in enumerate(("gpt-6-luna", "gpt-6.1-sol", "gpt-6.1-sol"), 1):
                original = turn(identifier, f"task-{identifier}")
                before = deepcopy(original)
                changed, result = router.client_message(original)
                self.assertEqual(original, before)
                self.assertEqual(changed["params"]["model"], model)
                self.assertEqual(changed["params"]["effort"], "low")
                self.assertEqual(changed["params"]["collaborationMode"]["settings"]["model"], model)
                self.assertEqual(changed["params"]["threadId"], "thread-1")
                self.assertEqual(changed["params"]["sandboxPolicy"], original["params"]["sandboxPolicy"])
                self.assertEqual(changed["params"]["approvalPolicy"], "on-request")
                router.server_message({"id": identifier, "result": {"turn": {"id": str(identifier)}}})
            self.assertEqual(decide.call_count, 3)
            self.assertIn("task-1", decide.call_args.args[0])
            self.assertIn("task-3", decide.call_args.args[0])

    def test_history_is_bounded_and_isolated_per_thread(self):
        router = TurnRouter(MODELS)
        for _ in range(10):
            router.remember("one", "assistant", "x" * 10000)
        self.assertEqual(len(router.history("one")), 4)
        self.assertLess(sum(len(text) for _, text in router.history("one")), 1300)
        with patch.object(cli.Ollaya, "decide", return_value=response()) as decide:
            router.client_message(turn(1, "new", "two"))
        self.assertEqual(decide.call_args.args[0], "new")
        for identifier in range(100):
            router.remember(str(identifier), "user", "short")
        self.assertEqual(len(router.histories), 16)

    def test_failed_turn_is_not_added_and_steering_is_not_rewritten(self):
        router = TurnRouter(MODELS)
        with patch.object(cli.Ollaya, "decide", return_value=response()) as decide:
            router.client_message(turn(1, "failed"))
            router.server_message({"id": 1, "error": {"message": "failure"}})
            self.assertFalse(router.history("thread-1"))
            steer = {"id": 2, "method": "turn/steer", "params": {
                "threadId": "thread-1", "input": [{"type": "text", "text": "changed"}]}}
            self.assertEqual(router.client_message(steer), (steer, None))
            self.assertEqual(decide.call_count, 1)

    def test_resume_history_and_final_reply_are_used(self):
        router = TurnRouter(MODELS)
        router.client_message({"id": 1, "method": "thread/resume", "params": {"threadId": "t"}})
        router.server_message({"id": 1, "result": {"thread": {"id": "t", "turns": [{"items": [
            {"type": "userMessage", "content": [{"type": "text", "text": "migration"}]},
            {"type": "agentMessage", "text": "confirm plan"}]}]}}})
        router.server_message({"method": "item/completed", "params": {"threadId": "t", "item": {
            "type": "agentMessage", "text": "new reply", "phase": "final_answer"}}})
        with patch.object(cli.Ollaya, "decide", return_value=response()) as decide:
            router.client_message(turn(2, "yes", "t"))
        self.assertIn("migration", decide.call_args.args[0])
        self.assertIn("new reply", decide.call_args.args[0])

    def test_startup_manual_and_successful_model_menu_changes_are_respected(self):
        router = TurnRouter(MODELS, model="custom", effort="high")
        with patch.object(cli.Ollaya, "decide") as decide:
            changed, _ = router.client_message(turn(1, "task"))
            self.assertEqual(changed["params"]["model"], "custom")
            decide.assert_not_called()
        router = TurnRouter(MODELS)
        router.current["thread-1"] = ("gpt-6-luna", "low")
        request = {"id": 5, "method": "thread/settings/update", "params": {
            "threadId": "thread-1", "model": "gpt-6.1-sol", "effort": "high"}}
        router.client_message(request)
        router.server_message({"id": 5, "result": {}})
        with patch.object(cli.Ollaya, "decide") as decide:
            changed, _ = router.client_message(turn(6, "task"))
            self.assertEqual(changed["params"]["model"], "gpt-6.1-sol")
            decide.assert_not_called()

    def test_nested_same_value_manual_selection_is_pinned_only_after_success(self):
        router = TurnRouter(MODELS)
        router.current["thread-1"] = ("gpt-6.1-sol", "low")
        request = {"id": 5, "method": "thread/settings/update", "params": {
            "threadId": "thread-1", "model": None, "effort": "high",
            "collaborationMode": {"mode": "default", "settings": {
                "model": "gpt-6.1-sol", "reasoning_effort": "low"}}}}
        router.client_message(request)
        router.server_message({"id": 5, "error": {"message": "failure"}})
        self.assertFalse(router.pins)
        router.client_message(request)
        router.server_message({"id": 5, "result": {}})
        with patch.object(cli.Ollaya, "decide") as decide:
            changed, result = router.client_message(turn(6, "investigate"))
            self.assertEqual(result["source"], "manual")
            self.assertEqual(changed["params"]["effort"], "low")
            self.assertEqual(changed["params"]["collaborationMode"]["settings"]["reasoning_effort"], "low")
            decide.assert_not_called()
        request["params"]["collaborationMode"]["settings"]["reasoning_effort"] = "medium"
        router.client_message(request)
        router.server_message({"id": 5, "result": {}})
        self.assertEqual(router.select("thread-1", "task")["effort"], "medium")

    def test_manual_choice_survives_failure_then_returns_to_auto(self):
        router = TurnRouter(MODELS)
        request = {"id": 10, "method": "thread/settings/update", "params": {
            "threadId": "thread-1", "model": "gpt-6.1-sol", "effort": "medium"}}
        router.client_message(request)
        router.server_message({"id": 10, "result": {}})
        with patch.object(cli.Ollaya, "decide", return_value=response("routine")) as decide:
            changed, _ = router.client_message(turn(11, "translate"))
            self.assertEqual(changed["params"]["effort"], "medium")
            router.server_message({"id": 11, "error": {"message": "failure"}})
            changed, _ = router.client_message(turn(12, "retry"))
            self.assertEqual(changed["params"]["effort"], "medium")
            decide.assert_not_called()
            router.server_message({"id": 12, "result": {"turn": {"id": "ok"}}})
            changed, result = router.client_message(turn(13, "translate"))
            self.assertEqual(result["source"], "ollaya")
            self.assertEqual(changed["params"]["model"], "gpt-6-luna")
            self.assertEqual(changed["params"]["effort"], "low")

    def test_new_manual_choice_is_not_consumed_by_old_turn_response(self):
        router = TurnRouter(MODELS)
        request = {"id": 10, "method": "thread/settings/update", "params": {
            "threadId": "thread-1", "model": "gpt-6.1-sol", "effort": "low"}}
        router.client_message(request)
        router.server_message({"id": 10, "result": {}})
        router.client_message(turn(11, "task"))
        request["id"] = 12
        request["params"]["effort"] = "medium"
        router.client_message(request)
        router.server_message({"id": 12, "result": {}})
        router.server_message({"id": 11, "result": {"turn": {"id": "old"}}})
        self.assertEqual(router.select("thread-1", "task")["effort"], "medium")

    def test_images_and_unavailable_classifier_use_high_model(self):
        router = TurnRouter(MODELS)
        message = turn(1, "inspect this")
        message["params"]["input"].append({"type": "localImage", "path": "PRIVATE.png"})
        with patch.object(cli.Ollaya, "decide") as decide:
            changed, _ = router.client_message(message)
            self.assertEqual(changed["params"]["model"], "gpt-6.1-sol")
            self.assertEqual(changed["params"]["effort"], "low")
            decide.assert_not_called()
        with patch.object(cli.Ollaya, "decide", side_effect=TimeoutError):
            changed, result = router.client_message(turn(2, "new task"))
            self.assertEqual(result["source"], "fallback")
            self.assertEqual(changed["params"]["effort"], "low")
            self.assertEqual(changed["params"]["model"], "gpt-6.1-sol")
