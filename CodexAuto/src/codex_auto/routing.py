from collections import OrderedDict, deque
from copy import deepcopy

from .cli import Ollaya, RoutingError, assess, choose_model


def text_input(items):
    return "\n".join(item["text"] for item in (items or [])
                     if isinstance(item, dict) and item.get("type") == "text"
                     and isinstance(item.get("text"), str))


class TurnRouter:
    def __init__(self, models, timeout=15, model=None, effort=None):
        self.models = models
        self.timeout = timeout
        self.fixed_model = model
        self.fixed_effort = effort
        self.histories = OrderedDict()
        self.current = {}
        self.pins = {}
        self.pending = {}

    def history(self, thread_id):
        if thread_id not in self.histories:
            if len(self.histories) >= 16:
                old, _ = self.histories.popitem(last=False)
                self.current.pop(old, None)
                self.pins.pop(old, None)
            self.histories[thread_id] = deque(maxlen=4)
        self.histories.move_to_end(thread_id)
        return self.histories[thread_id]

    def remember(self, thread_id, role, text):
        if text.strip():
            snippet = text if len(text) <= 300 else text[:200] + " ... " + text[-100:]
            self.history(thread_id).append((role, snippet))

    def select(self, thread_id, prompt, has_image=False):
        pin = self.pins.get(thread_id, {})
        model = pin.get("model") or self.fixed_model
        effort = pin.get("effort") or self.fixed_effort
        if model:
            return {"model": model, "effort": effort, "source": "manual", "reason": "手動指定"}
        history = tuple(self.history(thread_id))
        state = prompt
        if history:
            context = "\n".join(f"{role}: {text}" for role, text in history)
            state = f"直近の会話（今回の依頼を理解するための背景）:\n{context}\n\n今回の依頼:\n{prompt}"
        try:
            if has_image or not prompt.strip():
                raise RoutingError("画像を含む依頼は高性能側で扱います")
            kind, confidence, reason = assess(Ollaya(self.timeout).decide(state))
            source = "ollaya"
        except (RoutingError, OSError, ValueError, KeyError, TypeError, AttributeError):
            kind, confidence = "investigation", None
            reason, source = "判定を確認できないため高性能側を選択", "fallback"
        model, effort = choose_model(self.models, kind, effort)
        return {"model": model, "effort": effort, "source": source,
                "reason": reason, "confidence": confidence}

    def client_message(self, message):
        method = message.get("method")
        params = message.get("params") or {}
        thread_id = params.get("threadId")
        if method in ("thread/start", "thread/resume", "thread/fork", "thread/read",
                      "thread/rollback", "thread/settings/update", "turn/start") and "id" in message:
            if len(self.pending) >= 256:
                raise RoutingError("未完了の要求が多すぎます")
            self.pending[message["id"]] = (method, deepcopy(params))
        if method == "turn/steer":
            self.remember(thread_id, "user", text_input(params.get("input")))
            return message, None
        if method != "turn/start" or params.get("toolOutput") is not None:
            return message, None
        prompt = text_input(params.get("input"))
        has_image = any(item.get("type") in ("image", "localImage")
                        for item in params.get("input", []) if isinstance(item, dict))
        decision = self.select(thread_id, prompt, has_image)
        changed = deepcopy(message)
        changed["params"]["model"] = decision["model"]
        if decision["effort"]:
            changed["params"]["effort"] = decision["effort"]
        mode = changed["params"].get("collaborationMode")
        if mode and isinstance(mode.get("settings"), dict):
            mode["settings"]["model"] = decision["model"]
            if decision["effort"]:
                mode["settings"]["reasoning_effort"] = decision["effort"]
        if "id" in message:
            self.pending[message["id"]] = (method, deepcopy(changed["params"]))
        return changed, decision

    def server_message(self, message):
        if "id" in message and "method" not in message:
            pending = self.pending.pop(message["id"], None)
            if not pending or "error" in message:
                return
            method, params = pending
            result = message.get("result") or {}
            thread = result.get("thread") or {}
            thread_id = thread.get("id") or params.get("threadId")
            if method in ("thread/start", "thread/resume", "thread/fork", "thread/read", "thread/rollback"):
                if "turns" in thread:
                    self.history(thread_id).clear()
                    for turn in thread["turns"][-2:]:
                        for item in turn.get("items", []):
                            if item.get("type") == "userMessage":
                                self.remember(thread_id, "user", text_input(item.get("content")))
                            elif item.get("type") == "agentMessage":
                                self.remember(thread_id, "assistant", item.get("text", ""))
                self.current[thread_id] = (result.get("model"), result.get("reasoningEffort"))
            elif method == "turn/start" and params.get("toolOutput") is None:
                self.remember(thread_id, "user", text_input(params.get("input")))
                self.current[thread_id] = (params.get("model"), params.get("effort"))
            elif method == "thread/settings/update":
                previous = self.current.get(thread_id, (None, None))
                pin = self.pins.setdefault(thread_id, {})
                if params.get("model") and params["model"] != previous[0]:
                    pin["model"] = params["model"]
                if params.get("effort") and params["effort"] != previous[1]:
                    pin["effort"] = params["effort"]
        elif message.get("method") == "item/completed":
            params = message.get("params") or {}
            item = params.get("item") or {}
            if item.get("type") == "agentMessage" and item.get("phase") != "commentary":
                self.remember(params.get("threadId"), "assistant", item.get("text", ""))
