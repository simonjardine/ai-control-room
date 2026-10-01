# AI Control Room

A desktop room for a human host and up to six language models. Invite the room to discuss a topic, speak privately to one model, choose individual prompts, and review real file and web tool results alongside their replies.

![Council chamber artwork](assets/room/council-chamber-v1.png)

_Background artwork used by the interactive Tkinter interface. Model badges, controls and conversations are drawn by the app._

This is an experimental Windows desktop application. Hosted models need your own provider API keys; local models need a separately running OpenAI-compatible server. The app does not include models or a hosted service.

## Download the Windows app

Download `AI-Control-Room.exe` from the [latest release](https://github.com/simonjardine/ai-control-room/releases/latest) and put it in its own folder, for example `C:\AI Control Room\`. No Python installation is needed. The app keeps `config.json`, `chats/`, `replays/` and `workspace/` next to the exe, so choose a folder you can write to (not `Program Files`). Windows SmartScreen may warn about an unsigned download; choose **More info → Run anyway** only if you got it from this repository.

To build the exe yourself:

```powershell
powershell -ExecutionPolicy Bypass -File build_exe.ps1
```

The result is `dist\AI-Control-Room.exe`.

## Quick start from source on Windows

Install Python 3.12 or newer with Tcl/Tk support. From this repository's directory in PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

The app opens without credentials and does not contact any provider on startup. To configure it:

1. Open **Settings & APIs → APIs**. Enter the API key for a provider you want to use. Click **Test / refresh**.
2. Open **Models & individual prompts**. Choose a provider and a model for one or more seats. Leave unused model fields blank. You can also type a provider's exact model ID.
3. Edit each seat's prompt if wanted, then click **Save settings & prompts**. You can save API settings before choosing any models.
4. Click the table to address the public room, or a model's chair for a private chat. Use the invitation checkboxes to choose who responds. Send a message with **Send** or **Ctrl+Enter**.

Settings are saved locally to `config.json`; the supplied `config.example.json` contains empty keys and no model assignments. A ChatGPT subscription or desktop login is not used as an API credential.

### Providers and local models

- OpenAI, OpenRouter, xAI and Moonshot/Kimi use their configured OpenAI-compatible endpoints.
- Local chat defaults to `http://127.0.0.1:18434/v1` for Hermes. An explicit local key can be entered in Settings.
- For Hermes on Windows, the app can read the current user's `%LOCALAPPDATA%\hermes\runtimes\llamacpp\server.json`. No runtime credential is included in the repository.
- **Local LLM · Load / unload** manages models already registered with a running Hermes router. Other OpenAI-compatible servers can support chat and model discovery without supporting these Hermes-specific controls.
- Loading can use significant RAM/VRAM. Unloading releases model memory without removing its files. A later request may cause Hermes to load the model again.

Local provider requests have a **90-second** deadline; hosted model and web-search requests have **60 seconds**. Quick local probes use a shorter deadline. Provider work runs outside the Tk event loop. A temporary provider failure can be retried once within the original deadline, using the same model.

OpenRouter, Kimi and local models keep their default reasoning mode. Supported OpenAI reasoning models use low effort. Only the final answer is shown. Invalid or failed responses are reported explicitly; there is no mock-model fallback.

## Conversation controls

- **Public room:** invited models reply in speaking order. Later speakers see earlier public replies. This is a discussion, not an independent voting experiment.
- **Private chat:** only that seat's matching model receives the private channel. **Share latest private reply with room** explicitly copies a reply into the public discussion.
- **Room controls:** edit individual prompts, speaking order and the optional GPT-last reviewer preset. Prompt and order changes apply to the next round.
- **Pause:** holds the remaining reply queue and cancels pending write approvals. An already running provider request can finish.
- **New chat / Chat history:** start a fresh conversation or resume a saved one. The latest chat is restored when the app opens.
- **Memory / recap:** host-edited notes, separately scoped to public or private conversations. A model does not automatically create long-term memory. Turn **Use saved memory** off for a fresh start without those notes.

History is bounded to recent entries and text size. A long conversation is not an unlimited context window. Private messages are separated in model context, but they are stored as local files, not encrypted by this application.

## Files and internet

**Files & internet** selects a shared workspace and enables file reading, proposed writes and web access. The default is this repository's ignored `workspace/` folder.

Available tools:

| Tool             | Purpose                                              |
| ---------------- | ---------------------------------------------------- |
| `list_directory` | List workspace entries                               |
| `read_file`      | Read a bounded UTF-8 text excerpt                    |
| `search_files`   | Search text in the workspace                         |
| `write_file`     | Propose complete file contents                       |
| `edit_file`      | Propose one exact text replacement                   |
| `web_search`     | Search using the OpenAI Responses web-search service |
| `fetch_web_page` | Read a public HTTPS text page                        |

Every write requires a host preview and **Approve / Decline**. Changes are checked again after approval; existing files are backed up, writes are atomic, and the saved result is read back. Closing the preview or pausing declines it. There is no shell or code-execution tool.

Files are limited to 128 KiB. Tools reject traversal, symlinks, junctions, hard links, known credential paths and detected secret-like content. These checks are not a substitute for choosing a suitable workspace. File excerpts go to the responding model's provider. The workspace is shared across public and private chats, so files are not private to one seat.

Search uses your configured OpenAI key with `gpt-4.1-mini` and web search; normal API charges apply. Only the search query is sent to that service. Direct page reading needs no search API key, is limited to public HTTPS text, checks DNS and redirects, and has a 20-second deadline. Source links and tool results appear in **Tool activity**.

Each reply has at most eight tool calls. Example tests:

> Create hello.txt containing Hello from the control room, then read it back.

> Search for the official Python pathlib documentation and give me its link.

## Local data

These files and directories are excluded from Git:

| Location             | Contents                                               |
| -------------------- | ------------------------------------------------------ |
| `config.json`        | Your provider keys and model assignments               |
| `room_settings.json` | Personal prompts, speaking order and workspace choices |
| `room_memory.json`   | Host-saved notes                                       |
| `chats/`, `replays/` | Saved conversations and tool activity                  |
| `workspace/`         | Shared working files                                   |
| `tool_backups/`      | Backups of approved file changes                       |
| `*.log`              | Local diagnostics, including startup failures          |

Keep backups of this local data separately if you need it. Do not force-add credentials or private conversations to Git.

## Development and checks

```powershell
.\.venv\Scripts\python.exe -m compileall -q main.py config.py providers.py room.py room_gui.py room_scene.py room_dialogs.py room_settings.py room_store.py room_tools.py room_tool_dialogs.py room_web.py tests
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Tests use synthetic provider responses and temporary workspaces. They do not need API keys or make paid model calls. GUI tests require a working Windows desktop/Tk environment. Automated checks are separate from live provider verification, which depends on your selected models and credentials.

The entry point is `main.py`. `room_gui.py` owns Tk callbacks and background jobs, `room.py` builds conversation context and runs the tool loop, `providers.py` handles model APIs, `room_store.py` persists sessions, and `room_tools.py` / `room_web.py` implement the bounded tools. The former ship/race implementation and local repair scripts are not part of this repository.

## Project status and credits

Private development repository; no open-source licence has been selected for the application. Preserve third-party notices when redistributing assets. Model icons come with their own licence and attribution in [`assets/logos/`](assets/logos/NOTICE.md). The chamber artwork and its generation prompt are recorded in [`assets/room/ARTWORK.md`](assets/room/ARTWORK.md).
