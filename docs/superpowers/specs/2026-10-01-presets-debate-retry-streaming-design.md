# Presets, debate rounds, retry and streaming

Agreed with the host on 2026-10-01.

## Goal

Make the room useful for structured discussion, not only one round of replies: pick a ready-made setup of seat roles, let models debate over several rounds, recover from a single failed reply, and watch replies arrive live.

## Presets

- A preset holds a prompt for every seat, the number of rounds (1–5), whether round 1 is blind, and an optional verdict seat.
- Built-ins: Best answer, Pro vs con, Stress-test my idea, Independent answers, Free discussion. Role prompts extend the safe default prompt.
- "My prompts" is created from the host's existing prompts so nothing is lost; switching presets never overwrites another preset.
- Chosen from a dropdown beside the topic. The editor (Room controls → Preset & seat prompts…) saves, saves as new, or resets a built-in.
- Stored in `room_settings.json` (`presets`, `active_preset`). Saved edits override built-ins of the same name.

## Rounds

- Sending in the public room runs the active preset as a plan of steps (`room_rounds.plan`): every invited non-verdict seat once per round, then the verdict seat once.
- Each step appends a short instruction (blind answer, revise or defend in under 120 words, final verdict).
- Blind rounds hide other models' same-round replies and tool results (`round_key` on entries).
- A preset making more than one call per invited model asks once per chat and preset, stating the call count.
- Private chats are always one ordinary reply.

## Retry

- Every model reply has "↻ Retry" in the transcript. It re-runs that model with the conversation up to that entry and the same step (round, blindness, job), replacing the entry in place. Tool output from the retry is inserted before it; the old text goes to the replay log.

## Streaming

- OpenAI-compatible providers use server-sent events; Claude uses the SDK's `messages.stream`. A streaming reply may run 180 s while text arrives and stops after 30 s of silence.
- If a provider refuses streaming (non-event-stream 4xx/5xx), the existing non-streaming request with its retry runs instead. No retry happens once text has been shown.
- `room.visible_text` decodes the `"text"` value of a partial `{"text": ...}` reply; tool calls show nothing.
- Live text appears in the transcript (after a `live_start` mark) and on the seat card, at most about seven updates a second.
- Pause closes the stream at once; the partial reply is kept, marked "[Stopped by host]".

## Testing

Synthetic responses only: round plans and blind context, preset load/save, the partial-JSON decoder under random splits, the event-stream reader (truncation, mid-stream error, fallback, pause), Claude streaming, and GUI runs of a debate, retry, preset persistence, the preset editor and pausing a live reply.
