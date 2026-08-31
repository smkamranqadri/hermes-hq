# SSE Stream Shield (Standard) — planned 2026-08-31

Goal: a browser dropping the chat stream (phone lock, tab switch, nav) must not
kill the running turn. Today `stream_turn`'s generator closes the gateway
connection in its `finally` the moment the client disconnects; the Hermes
gateway sees its SSE client vanish and hard-interrupts the run
(`_INTERRUPT_REASON_SSE_DISCONNECT`, gateway/run.py:3254), and the engine
mislabels it "User sent a new message" → chat shows "Operation interrupted."

Owner approved 2026-08-31: backend-only shield, notifications unchanged, no
frontend work.

## Design (backend/chat.py `stream_turn` only)

- Move the gateway read-loop into a **daemon pump thread**: it reads
  `r.readline()` to stream end, parses `run.started`/`assistant.completed` as
  today, touches the gateway idle-timer, and feeds chunks into a `queue.Queue`
  (unbounded — chat turns are text-scale). On stream end or error it closes the
  gateway connection, runs `_notify_turn_done`, and enqueues a `None` sentinel.
- The response generator just drains the queue. On `GeneratorExit` (client
  disconnected) it sets `client_gone` and returns — it does NOT close the
  gateway connection; the pump keeps consuming (discarding chunks) until the
  turn finishes, bounded by the existing `TURN_TIMEOUT` (1 h socket timeout).
- Notification semantics unchanged: `_notify_turn_done` fires on the full
  reply; the device that watched marks it read by `source_key` — a disconnected
  device never does, so the phone alerts. That is the whole "lands as a
  notification" contract, for free.
- Pre-first-byte errors (409/502) unchanged: the request/response handshake
  stays outside the generator.

## Acceptance

1. Test: FakeGateway records stream completion; close the generator after the
   first chunks → gateway handler finishes writing every event (no BrokenPipe),
   chat notification row appears with the full reply text.
2. Watched-turn behavior unchanged: existing chat/notification tests green.
3. Suite green (112 + new).
4. Live :9010 against the real orchestrator gateway :8642: start a chat turn,
   kill the client connection mid-turn → run completes, gateway.log shows NO
   `_INTERRUPT_REASON_SSE_DISCONNECT` for it, transcript carries the full
   reply, notification row created (and pushed).

## Out of scope

Hermes engine interrupt labels; frontend affordances; dispatched-run sessions;
terminal/PTY streams.

## Status

COMPLETE 2026-08-31 — all acceptance checks passed; proof in `kis/state/current.md`.
