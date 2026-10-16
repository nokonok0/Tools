from collections import OrderedDict, deque
from copy import deepcopy

from .cli import BANNED_MODELS, Ollaya, RoutingError, classify, check_allowed_model, choose_model


def text_input(items):
    return "\n".join(item["text"] for item in (items or [])
                     if isinstance(item, dict) and item.get("type") == "text"
                     and isinstance(item.get("text"), str))


class TurnRouter:
    def __init__(self, models, timeout=15, model=None, effort=None):
        check_allowed_model(model)
        self.models = models
        self.timeout = timeout
        self.fixed_model = model
        self.fixed_effort = effort
        self.histories = OrderedDict()
        self.current = {}
        self.pins = {}
        self.pending = {}
        self.manual_turns = {}

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
            check_allowed_model(model)
            return {"model": model, "effort": effort, "source": "manual", "reason": "手動指定"}
        history = tuple(self.history(thread_id))
        try:
            kind, confidence, reason, source = classify(prompt, Ollaya(self.timeout).decide, history, has_image)
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
        if method in ("thread/start", "thread/resume", "thread/fork", "thread/settings/update", "turn/settings/update") or (
                method == "turn/start" and params.get("toolOutput") is not None):
            check_allowed_model(params.get("model"))
            mode = params.get("collaborationMode") or {}
            check_allowed_model((mode.get("settings") or {}).get("model"))
        if method in ("thread/start", "thread/resume", "thread/fork", "thread/read",
                      "thread/rollback", "thread/settings/update", "turn/start", "model/list", "thread/list") and "id" in message:
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
            if thread_id in self.pins:
                self.manual_turns[message["id"]] = (thread_id, self.pins[thread_id])
        return changed, decision

    def server_message(self, message):
        if "id" in message and "method" not in message:
            pending = self.pending.pop(message["id"], None)
            manual = self.manual_turns.pop(message["id"], None)
            if not pending or "error" in message:
                return
            if manual and self.pins.get(manual[0]) is manual[1]:
                self.pins.pop(manual[0], None)
            method, params = pending
            result = message.get("result") or {}
            if method == "thread/list":
                seen = set()
                unique = []
                for record in result.get("data", []):
                    identifier = record.get("id")
                    if identifier is None or identifier not in seen:
                        unique.append(record)
                        if identifier is not None:
                            seen.add(identifier)
                result["data"] = unique
                return
            if method == "model/list":
                result["data"] = [record for record in result.get("data", [])
                                  if (record.get("slug") or record.get("model") or record.get("id")) not in BANNED_MODELS]
                return
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
                settings = (params.get("collaborationMode") or {}).get("settings") or {}
                model = settings.get("model") or params.get("model")
                effort = settings.get("reasoning_effort") or params.get("effort")
                pin = dict(self.pins.get(thread_id, {}))
                if model:
                    pin["model"] = model
                if effort:
                    pin["effort"] = effort
                if pin:
                    self.pins[thread_id] = pin
                self.current[thread_id] = (model or previous[0], effort or previous[1])
        elif message.get("method") == "item/completed":
            params = message.get("params") or {}
            item = params.get("item") or {}
            if item.get("type") == "agentMessage" and item.get("phase") != "commentary":
                self.remember(params.get("threadId"), "assistant", item.get("text", ""))
