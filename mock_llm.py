"""Throwaway OpenAI-compatible server: exercise the jobpipe agent offline.

    python3 mock_llm.py                       # serves 127.0.0.1:8111
    LLM_PROVIDER=custom LLM_BASE_URL=http://127.0.0.1:8111/v1 LLM_MODEL=mock \\
        python3 -m jobpipe agent "what is in the queue?"

Scripted behaviour, so a full tool loop runs with no API key and no network:
1st agent call -> a list_jobs tool call, 2nd -> a show_job tool call,
3rd -> a final answer. Also serves GET /v1/models (provider probe) and plain
completions (llm-test / extraction / email-body calls). Stdlib only.
"""
from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _completion(message: dict, finish: str) -> dict:
    return {"id": "chatcmpl-mock", "object": "chat.completion", "created": 0,
            "model": "mock",
            "choices": [{"index": 0, "finish_reason": finish, "message": message}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            self._send({"object": "list", "data": [{"id": "mock", "object": "model"}]})
        else:
            self._send({"error": "not found"}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        req = json.loads(self.rfile.read(n) or b"{}")
        msgs = req.get("messages", [])
        system = next((m.get("content") or "" for m in msgs
                       if m.get("role") == "system"), "")

        # Non-agent completions: triage/extraction expect JSON, llm-test a word.
        if not req.get("tools"):
            if "triage ONE job" in system:
                payload = '{"decision": "skip", "reason": "mock verdict", "confidence": 0.5}'
            elif "extraction engine" in system:
                payload = "{}"
            else:
                payload = "pong"
            self._send(_completion({"role": "assistant", "content": payload}, "stop"))
            return

        # Agent loop: tool call -> tool call -> final answer.
        tool_msgs = [m for m in msgs if m.get("role") == "tool"]
        if not tool_msgs:
            call = {"id": "call_1", "type": "function",
                    "function": {"name": "list_jobs",
                                 "arguments": json.dumps({"limit": 5})}}
            self._send(_completion(
                {"role": "assistant", "content": None, "tool_calls": [call]},
                "tool_calls"))
        elif len(tool_msgs) == 1:
            call = {"id": "call_2", "type": "function",
                    "function": {"name": "show_job",
                                 "arguments": json.dumps({"job_id": 1})}}
            self._send(_completion(
                {"role": "assistant", "content": None, "tool_calls": [call]},
                "tool_calls"))
        else:
            self._send(_completion(
                {"role": "assistant",
                 "content": "Mock agent: I listed the queue and inspected job 1. "
                            "Ask me for a recommendation."},
                "stop"))


def main(port: int = 8111):
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"mock OpenAI-compatible LLM on http://127.0.0.1:{port}/v1 (model: mock)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 8111)
