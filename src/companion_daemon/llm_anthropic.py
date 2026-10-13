"""Native Anthropic Messages transport behind the existing role-model interface.

No provider fallback, response repair, or monetary price is implied. A proxy's
model names and token counts are its declarations, not upstream attestation.
"""

from copy import deepcopy
import json
import math

import httpx

from .llm import DeepSeekChatModel, ProviderCircuitBreaker, ProviderResultFormatError, _expected_tool_name


def _usage(raw, *, final=True):
    raw = raw if isinstance(raw, dict) else {}
    result = {"_usage_final": final}
    inputs = [
        raw.get("input_tokens"),
        raw.get("cache_creation_input_tokens", 0),
        raw.get("cache_read_input_tokens", 0),
    ]
    if all(type(v) is int and v >= 0 for v in inputs):
        result.update(
            prompt_tokens=sum(inputs),
            prompt_cache_hit_tokens=inputs[2],
            prompt_cache_miss_tokens=inputs[0] + inputs[1],
            uncached_input_tokens=inputs[0],
            cache_creation_tokens=inputs[1],
        )
    output = raw.get("output_tokens")
    if type(output) is int and output >= 0:
        result["completion_tokens"] = output
        if "prompt_tokens" in result:
            result["total_tokens"] = result["prompt_tokens"] + output
    return result


class AnthropicChatModel(DeepSeekChatModel):
    provider = "anthropic"
    supports_strict_tool_choice = False  # A compatible proxy is not a strict-schema guarantee.

    def __init__(self, api_key, base_url, model, *, thinking_enabled=False,
                 tool_result_carrier="native", **kwargs):
        if thinking_enabled:
            raise ValueError("Anthropic thinking controls require a separately qualified route")
        if tool_result_carrier not in {"native", "native_or_json_object"}:
            raise ValueError("unknown Anthropic tool result carrier")
        self._tool_result_carrier = tool_result_carrier
        if tool_result_carrier != "native":
            self.model_version = model + ":json-object-carrier.1"
        # Do not share circuit state with the retired DeepSeek route.
        kwargs.setdefault("circuit_breaker", ProviderCircuitBreaker())
        super().__init__(api_key, base_url, model, thinking_enabled=False, **kwargs)

    @property
    def single_tool_response_carrier(self):
        return self._tool_result_carrier

    def _response_tool_content(self, message, *, tools, tool_choice, expected_tool_name):
        native = super()._response_tool_content(
            message, tools=tools, tool_choice=tool_choice, expected_tool_name=expected_tool_name,
        )
        if native is not None or message.get("tool_calls"):
            return native
        if self._tool_result_carrier == "native":
            return None
        if (len(tools) != 1 or expected_tool_name is None
                or tools[0].get("function", {}).get("name") != expected_tool_name
                or not isinstance(tool_choice, dict) or tool_choice.get("type") != "function"
                or tool_choice.get("function", {}).get("name") != expected_tool_name
                or message.get("native_stop_reason") != "end_turn"):
            raise ValueError("JSON text carrier requires one explicitly named schema and end_turn")
        text = message.get("content")
        if not isinstance(text, str):
            raise ValueError("JSON text carrier requires text")

        def members(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("JSON text carrier contains duplicate keys")
                result[key] = value
            return result

        def constant(value):
            raise ValueError("JSON text carrier contains nonfinite values")

        def finite_float(value):
            number = float(value)
            if not math.isfinite(number):
                raise ValueError("JSON text carrier contains a nonfinite number")
            return number

        try:
            value = json.loads(text, object_pairs_hook=members, parse_constant=constant,
                               parse_float=finite_float)
            if not isinstance(value, dict):
                raise ValueError("JSON text carrier requires one complete object")
        except (ValueError, RecursionError) as exc:
            raise ProviderResultFormatError(raw_result=text,
                detail=f"invalid JSON object response carrier: {exc}") from exc
        # Decode a declared carrier, not a successful native tool invocation.
        # The caller's original schema/source validator still receives the exact
        # bytes, including invalid fields so its same-role correction can run.
        return text

    def _completion_url(self, tools):
        base = self.base_url.rstrip("/")
        return base + ("/messages" if base.endswith("/v1") else "/v1/messages")

    def _completion_headers(self):
        return {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

    def _stream_request(self, payload):
        return {**payload, "stream": True}

    def _expected_response_tool(self, tools, tool_choice):
        if tools and tool_choice == "none":
            raise ValueError("single-tool JSON completion does not support tool_choice=none")
        return _expected_tool_name(
            tools, "auto" if tools and tool_choice in (None, "required") else tool_choice
        )

    def request_payload(
        self, messages, *, temperature, json_object=False, tools=None, tool_choice=None
    ):
        system, turns = [], []
        for message in messages:
            role, content = message.get("role"), message.get("content")
            if not isinstance(content, str):
                raise ValueError("Anthropic text adapter requires exact text messages")
            if role == "system" and not turns:
                system.append({"type": "text", "text": content})
            elif role in {"user", "assistant"}:
                turns.append({"role": role, "content": content})
            else:
                raise ValueError(
                    "Anthropic adapter cannot reorder system messages or infer tool results"
                )
        if not turns:
            raise ValueError("Anthropic request requires conversation input")
        payload = {
            "model": self.model,
            "messages": turns,
            "max_tokens": self.max_completion_tokens,
            "temperature": temperature,
        }
        if tools:
            self._expected_response_tool(tools, tool_choice)
            native = []
            for tool in tools:
                if tool.get("type") != "function" or not isinstance(tool.get("function"), dict):
                    raise ValueError("Anthropic adapter requires declared function tools")
                fn = tool["function"]
                if set(fn) - {"name", "description", "parameters", "strict"}:
                    raise ValueError("unsupported function metadata")
                entry = {"name": fn["name"], "input_schema": deepcopy(fn["parameters"])}
                for key in ("description", "strict"):
                    if key in fn:
                        entry[key] = fn[key]
                native.append(entry)
            payload["tools"] = native
            if isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
                choice = {"type": "tool", "name": tool_choice["function"]["name"]}
            elif tool_choice in (None, "auto", "required"):
                choice = {"type": {None: "auto", "auto": "auto", "required": "any"}[tool_choice]}
            else:
                raise ValueError("unsupported Anthropic tool choice")
            choice["disable_parallel_tool_use"] = True
            payload["tool_choice"] = choice
        elif json_object:
            system.append(
                {
                    "type": "text",
                    "text": "Return exactly one valid JSON object, without Markdown fences or surrounding prose.",
                }
            )
        if system:
            payload["system"] = system
        return payload

    def _response_usage(self, payload):
        return _usage(payload.get("usage") if isinstance(payload, dict) else None)

    def _response_message(self, payload):
        if not isinstance(payload, dict) or payload.get("type") != "message":
            raise ValueError("Anthropic response must be a Message")
        if payload.get("model") != self.model:
            raise ValueError("Anthropic response model differs from the requested identifier")
        if payload.get("stop_reason") not in {"end_turn", "tool_use", "stop_sequence"}:
            raise ValueError(
                f"Anthropic response did not finish normally: {payload.get('stop_reason')!r}"
            )
        blocks = payload.get("content")
        if not isinstance(blocks, list):
            raise ValueError("Anthropic response content must be a block list")
        text, calls = [], []
        for block in blocks:
            if not isinstance(block, dict):
                raise ValueError("Anthropic response block must be an object")
            if block.get("type") == "text":
                if not isinstance(block.get("text"), str):
                    raise ValueError("Anthropic text block is invalid")
                text.append(block["text"])
            elif block.get("type") == "tool_use":
                if not isinstance(block.get("input"), dict):
                    raise ValueError("Anthropic tool input must be an object")
                calls.append(
                    {
                        "function": {
                            "name": block.get("name"),
                            "arguments": json.dumps(
                                block["input"], ensure_ascii=False, separators=(",", ":")
                            ),
                        }
                    }
                )
            elif block.get("type") not in {"thinking", "redacted_thinking"}:
                raise ValueError("unsupported Anthropic response block")
        return {"content": "".join(text), "tool_calls": calls,
                "native_stop_reason": payload.get("stop_reason")}

    def _stream_decoder(self):
        return _MessageStream(self.model)


class _MessageStream:
    """Per-invocation state; never mix concurrent streams or sum cumulative usage."""

    def __init__(self, model):
        self.model = model
        self.raw_usage = {}
        self.message_id = None
        self.started = self.stopped = False
        self.stop_reason = None
        self.final_output_reported = False
        self.blocks = {}
        self.tool_indexes = {}
        self.tool_has_arguments = set()

    def __call__(self, event):
        kind = event.get("type")
        delta = None
        if kind == "error":
            raise httpx.RemoteProtocolError("Anthropic stream reported an error")
        if self.stopped:
            raise ValueError("Anthropic stream continued after message_stop")
        if kind == "message_start":
            message = event.get("message", {})
            if self.started or message.get("model") != self.model:
                raise ValueError("Anthropic stream identity is invalid")
            self.started, self.message_id = True, message.get("id")
            self.raw_usage.update(message.get("usage", {}))
        elif kind == "ping":
            pass
        elif not self.started:
            raise ValueError("Anthropic stream has no message_start")
        elif kind == "content_block_start":
            index, block = event["index"], event["content_block"]
            if index in self.blocks:
                raise ValueError("Anthropic stream repeated a content block")
            self.blocks[index] = block["type"]
            if block["type"] == "tool_use":
                self.tool_indexes[index] = len(self.tool_indexes)
                initial = block.get("input", {})
                if not isinstance(initial, dict):
                    raise ValueError("Anthropic initial tool input must be an object")
                args = (
                    json.dumps(initial, ensure_ascii=False, separators=(",", ":"))
                    if initial
                    else ""
                )
                if args:
                    self.tool_has_arguments.add(index)
                delta = self._tool_delta(index, name=block.get("name"), arguments=args)
            elif block["type"] == "text":
                if not isinstance(block.get("text"), str):
                    raise ValueError("Anthropic stream text block must contain text")
                delta = {"content": block.get("text", "")}
            elif block["type"] not in {"thinking", "redacted_thinking"}:
                raise ValueError("unsupported Anthropic stream block")
        elif kind == "content_block_delta":
            index, part = event["index"], event["delta"]
            block_type = self.blocks.get(index)
            if part.get("type") == "input_json_delta" and block_type == "tool_use":
                args = part["partial_json"]
                if not isinstance(args, str):
                    raise ValueError("Anthropic JSON delta must be text")
                if args:
                    self.tool_has_arguments.add(index)
                delta = self._tool_delta(index, arguments=args)
            elif part.get("type") == "text_delta" and block_type == "text":
                if not isinstance(part.get("text"), str):
                    raise ValueError("Anthropic stream text delta must contain text")
                delta = {"content": part["text"]}
            elif not (
                block_type == "thinking"
                and part.get("type") in {"thinking_delta", "signature_delta"}
            ):
                raise ValueError("Anthropic delta does not match its block")
        elif kind == "content_block_stop":
            index = event["index"]
            if index not in self.blocks:
                raise ValueError("Anthropic stream stopped an unknown block")
            if self.blocks[index] == "tool_use" and index not in self.tool_has_arguments:
                delta = self._tool_delta(index, arguments="{}")
            self.blocks[index] = "closed"
        elif kind == "message_delta":
            reported = event.get("usage", {})
            self.raw_usage.update(reported)
            reason = event.get("delta", {}).get("stop_reason")
            if reason is not None:
                self.stop_reason = reason
                output = reported.get("output_tokens")
                self.final_output_reported = type(output) is int and output >= 0
        elif kind == "message_stop":
            self.stopped = True
        else:
            raise ValueError("unsupported Anthropic stream event")
        return {
            "id": self.message_id,
            "usage": _usage(self.raw_usage, final=self.stopped and self.final_output_reported),
            "choices": [{"delta": delta}] if delta is not None else [],
        }

    def _tool_delta(self, index, **function):
        return {"tool_calls": [{"index": self.tool_indexes[index], "function": function}]}

    def finish(self):
        if not self.stopped or self.stop_reason not in {"end_turn", "tool_use", "stop_sequence"}:
            raise ValueError("Anthropic stream ended without a complete message")
        if any(kind != "closed" for kind in self.blocks.values()):
            raise ValueError("Anthropic stream ended with an open content block")
