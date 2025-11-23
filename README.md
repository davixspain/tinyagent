# tinyagent

A lightweight local automation agent that delegates tasks to LLM tools one step at a time. It keeps a minimal control loop locally while using Ollama models for both control and writing.

## Requirements
- Python 3.9+
- [Ollama](https://ollama.com/) with access to the configured models (defaults: `llama3.2:3b` for control, `qwen2.5:3b` for writing)
- Python dependencies: `requests`

## Installation
1. Create and activate a virtual environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

## Usage
Run the agent with a goal sentence. The agent will iteratively decide actions and log results to `outputs/session.log`.

```bash
python agent.py "research the latest llama model release"
```

Environment variables:
- `AGENT_MODEL_CONTROL`: override control model name.
- `AGENT_MODEL_WRITER`: override writer model name.
- `AGENT_MAX_STEPS`: number of steps before stopping (default 25).

Outputs are written to the `outputs/` directory (created automatically).

## Actions the control model may emit
- `WEB_GET|<url>`
- `WRITE_FILE|<filename>|<short text>`
- `WRITE_LONG_FILE|<filename>|<instructions>`
- `APPEND_FILE|<filename>|<short text>`
- `READ_FILE|<filename>`
- `LIST_FILES|`
- `ASK_USER|<question>`
- `DONE|<very short summary>`
