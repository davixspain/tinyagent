import os
import sys
import textwrap
import subprocess
import requests
import re
import datetime

# =========================================================
#   CONFIG (models and steps)
# =========================================================

MODEL_NAME_CONTROL = os.getenv("AGENT_MODEL_CONTROL", "llama3.2:3b")
MODEL_NAME_WRITER = os.getenv("AGENT_MODEL_WRITER", "qwen2.5:3b")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "outputs")
os.makedirs(OUTPUT_DIR, exist_ok=True)

LOG_PATH = os.path.join(OUTPUT_DIR, "session.log")

def log_line(text: str):
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(f"[{ts}] {text}\n")


# =========================================================
#   SYSTEM PROMPT
# =========================================================

SYSTEM_PROMPT = """
You are a local automation agent running on a Linux laptop.

The user gives you a GOAL.
You must reach that goal by calling one simple ACTION at a time.

You are NOT allowed to run arbitrary code.
You MUST reply with exactly ONE SINGLE LINE using one of these formats:

WEB_GET|<url>
WRITE_FILE|<filename>|<short text>
WRITE_LONG_FILE|<filename>|<instructions>
APPEND_FILE|<filename>|<short text>
READ_FILE|<filename>
LIST_FILES|
ASK_USER|<question>
DONE|<very short summary>

Rules:
- No explanations.
- No line breaks.
- No mixing multiple actions.
- No free text alone.
- Always strictly follow the format.
"""


# =========================================================
#   HELPER: FORMAT PROMPT
# =========================================================

def format_messages_for_prompt(messages):
    parts = [SYSTEM_PROMPT.strip(), "\n\nConversation so far:\n"]
    for m in messages:
        if m.get("role") == "system":
            continue
        prefix = "User" if m["role"] == "user" else "Assistant"
        parts.append(f"{prefix}: {m['content']}\n")
    parts.append("\nNow reply with ONE line only.")
    return "\n".join(parts)


# =========================================================
#   MODEL CALLS
# =========================================================

def call_control_model(messages):
    prompt = format_messages_for_prompt(messages)
    proc = subprocess.run(
        ["ollama", "run", MODEL_NAME_CONTROL],
        input=prompt,
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr)

    output = proc.stdout.strip()
    lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
    if not lines:
        raise RuntimeError("Empty reply")
    return lines[-1]


def call_control_model_with_retry(messages, max_attempts=3):
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            reply = call_control_model(messages)
            if "|" not in reply:
                raise ValueError("Missing '|' in reply")
            return reply
        except Exception as e:
            last_error = str(e)
            err = f"PARSER_ERROR_ATTEMPT_{attempt}: {last_error}"
            print(err)
            log_line(err)
            messages.append({"role": "user", "content": err})

    raise RuntimeError(f"Control model failed after {max_attempts} attempts: {last_error}")


def call_writer_model(instruction: str) -> str:
    writer_prompt = (
        "You are a helpful writer.\n\n"
        f"Instruction:\n{instruction}\n\n"
        "Write the text only.\n"
    )
    proc = subprocess.run(
        ["ollama", "run", MODEL_NAME_WRITER],
        input=writer_prompt,
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr)
    return proc.stdout.strip()


# =========================================================
#   SECURITY: FILENAME SANITIZATION
# =========================================================

def safe_filename(name: str) -> str:
    name = name.strip()
    safe = re.sub(r"[^a-zA-Z0-9_.-]", "", name)
    if not safe:
        raise ValueError("Invalid filename")
    if ".." in safe:
        raise ValueError("Unsafe filename")
    if "." not in safe:
        safe += ".txt"
    return safe[:255]


# =========================================================
#   ACTION IMPLEMENTATIONS
# =========================================================

def action_web_get(url: str) -> str:
    headers = {"User-Agent": "Mozilla/5.0 (LocalAgent)"}
    try:
        r = requests.get(url, headers=headers, timeout=20)
        r.raise_for_status()
    except Exception as e:
        return f"WEB_GET_ERROR: {e}"

    html = r.text
    snippet = textwrap.shorten(html.replace("\n", " "), width=1500, placeholder="...")

    path = os.path.join(OUTPUT_DIR, "last_page.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)

    return f"WEB_GET_OK: downloaded {url}. Snippet: {snippet}"


def action_write_file(filename: str, content: str) -> str:
    filename = safe_filename(filename)
    with open(os.path.join(OUTPUT_DIR, filename), "w", encoding="utf-8") as f:
        f.write(content)
    return f"WRITE_FILE_OK: {filename}"


def action_write_long_file(filename: str, instructions: str) -> str:
    filename = safe_filename(filename)
    text = call_writer_model(instructions)
    with open(os.path.join(OUTPUT_DIR, filename), "w", encoding="utf-8") as f:
        f.write(text)
    return f"WRITE_LONG_FILE_OK: {filename}"


def action_append_file(filename: str, content: str) -> str:
    filename = safe_filename(filename)
    path = os.path.join(OUTPUT_DIR, filename)
    with open(path, "a", encoding="utf-8") as f:
        f.write(content + "\n")
    return f"APPEND_FILE_OK: {filename}"


def action_read_file(filename: str) -> str:
    filename = safe_filename(filename)
    path = os.path.join(OUTPUT_DIR, filename)
    if not os.path.exists(path):
        return f"READ_FILE_ERROR: {filename} missing"
    with open(path, "r", encoding="utf-8") as f:
        txt = f.read()
    snippet = textwrap.shorten(txt.replace("\n", " "), width=1500, placeholder="...")
    return f"READ_FILE_OK: {snippet}"


def action_list_files() -> str:
    files = sorted(os.listdir(OUTPUT_DIR))
    if not files:
        return "LIST_FILES_OK: empty"
    return "LIST_FILES_OK: " + ", ".join(files)


# =========================================================
#   PARSE MODEL RESPONSE
# =========================================================

def parse_reply(reply: str):
    parts = reply.split("|", 2)
    parts += [""] * (3 - len(parts))
    cmd = parts[0].upper()
    a1 = parts[1].strip()
    a2 = parts[2].strip()

    if cmd == "DONE": return ("DONE", a1)
    if cmd == "WEB_GET": return ("WEB_GET", a1)
    if cmd == "WRITE_FILE": return ("WRITE_FILE", a1, a2)
    if cmd == "WRITE_LONG_FILE": return ("WRITE_LONG_FILE", a1, a2)
    if cmd == "APPEND_FILE": return ("APPEND_FILE", a1, a2)
    if cmd == "READ_FILE": return ("READ_FILE", a1)
    if cmd == "LIST_FILES": return ("LIST_FILES",)
    if cmd == "ASK_USER": return ("ASK_USER", a1)

    raise ValueError(f"Unrecognized command: {reply}")


# =========================================================
#   MAIN LOOP
# =========================================================

def main():
    if len(sys.argv) < 2:
        print("Usage: python agent.py \"your goal here\"")
        sys.exit(1)

    task = " ".join(sys.argv[1:])
    print(f"🎯 Goal: {task}")
    log_line(f"NEW GOAL: {task}")

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"GOAL: {task}"},
    ]

    max_steps = int(os.getenv("AGENT_MAX_STEPS", "25"))

    for step in range(1, max_steps + 1):
        print(f"\n--- STEP {step} ---")
        log_line(f"STEP {step} START")

        try:
            reply = call_control_model_with_retry(messages)
        except RuntimeError as e:
            print(f"❌ {e}")
            log_line(f"FATAL: {e}")
            return

        print(f"🧠 Control model replied: {reply}")
        log_line(f"CONTROL_REPLY: {reply}")
        messages.append({"role": "assistant", "content": reply})

        try:
            cmd = parse_reply(reply)
        except Exception as e:
            obs = f"PARSER_ERROR: {e}"
            print(obs)
            log_line(obs)
            messages.append({"role": "user", "content": obs})
            continue

        # Execute action ----------------------------------------------------

        if cmd[0] == "DONE":
            summary = cmd[1]
            print(f"✅ DONE: {summary}")
            log_line(f"DONE: {summary}")
            with open(os.path.join(OUTPUT_DIR, "final.log"), "a", encoding="utf-8") as f:
                f.write(f"GOAL: {task}\nSUMMARY: {summary}\n\n")
            break

        elif cmd[0] == "WEB_GET":
            obs = action_web_get(cmd[1])

        elif cmd[0] == "WRITE_FILE":
            obs = action_write_file(cmd[1], cmd[2])

        elif cmd[0] == "WRITE_LONG_FILE":
            obs = action_write_long_file(cmd[1], cmd[2])

        elif cmd[0] == "APPEND_FILE":
            obs = action_append_file(cmd[1], cmd[2])

        elif cmd[0] == "READ_FILE":
            obs = action_read_file(cmd[1])

        elif cmd[0] == "LIST_FILES":
            obs = action_list_files()

        elif cmd[0] == "ASK_USER":
            print(f"\n❓ Agent asks: {cmd[1]}")
            user_input = input("> ")
            obs = f"USER_REPLY: {user_input}"

        else:
            obs = f"UNKNOWN_COMMAND: {cmd}"

        # End action dispatch ---------------------------------------------

        print(f"🔧 ACTION RESULT: {obs}")
        log_line(f"ACTION_RESULT: {obs}")
        messages.append({"role": "user", "content": f"ACTION_RESULT: {obs}"})


if __name__ == "__main__":
    main()
