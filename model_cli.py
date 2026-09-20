#!/usr/bin/env python3

import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


# ============================================================
# CONFIG
# ============================================================

HOME = Path.home()

DATA_DIR = HOME / ".local/share/model-cli"
STATE_FILE = DATA_DIR / "state.json"
SESSION_FILE = DATA_DIR / "session.json"
ARCHIVE_FILE = DATA_DIR / "history.jsonl"
LOG_FILE = DATA_DIR / "server.log"

HOST = "127.0.0.1"
PORT = 8080
BASE_URL = f"http://{HOST}:{PORT}"

# To jest miękki limit używany do ostrzegania.
# Nie obcina automatycznie historii.
SOFT_CONTEXT_LIMIT = 8192

REFRESH_FPS = 10


MODELS = {
    "4b": {
        "label": "Qwen3.5-4B Abliterated",
        "short": "4B",
        "target_rel":
            "mlx-community/"
            "Huihui-Qwen3.5-4B-Claude-4.6-Opus-abliterated-4bit",
        "draft_rel":
            "mlx-community/Qwen3.5-4B-MTP-4bit",
    },

    "9b": {
        "label": "Qwen3.5-9B Abliterated",
        "short": "9B",
        "target_rel":
            "huihui-ai/"
            "Huihui-Qwen3.5-9B-abliterated-mlx-4bit",
        "draft_rel":
            "mlx-community/Qwen3.5-9B-MTP-4bit",
    },
}


BASE_SYSTEM = """
Jesteś lokalnym asystentem użytkownika.

Odpowiadaj domyślnie po polsku, chyba że użytkownik wyraźnie
używa innego języka lub prosi o inny język.

Odpowiedzi mają być:
- konkretne,
- techniczne,
- precyzyjne,
- praktyczne,
- bez zbędnego lania wody.

Zachowuj ciągłość rozmowy i korzystaj z wcześniejszego kontekstu.
""".strip()


FAST_SYSTEM = """
TRYB FAST.

Odpowiadaj możliwie zwięźle, ale wystarczająco do rozwiązania zadania.

Zasady:
- proste pytanie faktograficzne lub obliczenie: odpowiedz w 1-3 zdaniach,
- nie pokazuj weryfikacji, alternatywnych metod ani rozumowania, jeśli użytkownik o nie nie prosi,
- nie powtarzaj wyniku kilka razy,
- nie dodawaj sekcji typu "Odpowiedź", "Weryfikacja", "Podsumowanie" przy prostych pytaniach,
- nie opisuj oczywistych kroków,
- dla trudniejszych pytań podaj najpierw konkretną odpowiedź, potem tylko potrzebne wyjaśnienie,
- pełną analizę wykonuj tylko wtedy, gdy użytkownik wyraźnie o nią poprosi.
""".strip()


CODE_SYSTEM = """
TRYB CODE.

Działaj jak precyzyjny software engineer.

Priorytety:
- poprawny i uruchamialny kod,
- nie wymyślaj nieistniejących API,
- zachowuj istniejące zachowanie kodu, jeśli użytkownik nie prosi
  o jego zmianę,
- przy debugowaniu szukaj przyczyny źródłowej,
- zwracaj gotowe rozwiązania,
- przy komendach shell unikaj destrukcyjnych operacji,
- kiedy jest kilka możliwości, preferuj rozwiązanie proste,
  stabilne i produkcyjne.
""".strip()


COMPACT_SYSTEM = """
Masz skompresować historię rozmowy do trwałej pamięci dla innego
modelu językowego.

Zachowaj wszystkie informacje potrzebne do dalszej pracy, ale usuń
powtórzenia, dygresje i niepotrzebne szczegóły.

Użyj dokładnie tej struktury:

USER GOALS
- ...

CURRENT SETUP
- ...

IMPORTANT FACTS
- ...

DECISIONS
- ...

TECHNICAL DETAILS
- ...

OPEN TASKS
- ...

Nie dodawaj komentarza ani wstępu. Zwróć tylko skompresowaną pamięć.
""".strip()


console = Console()


# ============================================================
# FILE HELPERS
# ============================================================

DATA_DIR.mkdir(parents=True, exist_ok=True)


def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path, obj):
    tmp = path.with_suffix(path.suffix + ".tmp")

    tmp.write_text(
        json.dumps(
            obj,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    tmp.replace(path)


def default_session():
    return {
        "summary": "",
        "messages": [],
        "turns": 0,
        "compactions": 0,
        "last_context_tokens": 0,
        "last_metrics": {},
    }


def load_session():
    return read_json(
        SESSION_FILE,
        default_session(),
    )


def save_session(session):
    write_json(
        SESSION_FILE,
        session,
    )


def load_state():
    return read_json(
        STATE_FILE,
        {},
    )


def save_state(state):
    write_json(
        STATE_FILE,
        state,
    )


def append_archive(event):
    with ARCHIVE_FILE.open(
        "a",
        encoding="utf-8",
    ) as f:
        f.write(
            json.dumps(
                event,
                ensure_ascii=False,
            )
            + "\n"
        )


# ============================================================
# MODEL PATH RESOLUTION
# ============================================================

def resolve_model(relative_path):
    relative = Path(relative_path)

    candidates = [
        HOME / ".lmstudio/models" / relative,
        HOME / ".lmstudio/hub/models" / relative,
    ]

    for path in candidates:
        if (
            path.exists()
            and (path / "config.json").exists()
        ):
            return path

    # fallback dla niestandardowej struktury LM Studio
    lm_root = HOME / ".lmstudio"

    target_name = relative.name

    if lm_root.exists():
        for config in lm_root.rglob("config.json"):
            if config.parent.name == target_name:
                return config.parent

    raise FileNotFoundError(
        f"Nie znaleziono modelu:\n{relative_path}"
    )


# ============================================================
# SERVER MANAGEMENT
# ============================================================

def health():
    try:
        with urllib.request.urlopen(
            BASE_URL + "/health",
            timeout=0.5,
        ) as r:
            return 200 <= r.status < 300

    except Exception:
        return False


def kill_port_server():
    try:
        result = subprocess.run(
            [
                "lsof",
                "-tiTCP:8080",
                "-sTCP:LISTEN",
            ],
            capture_output=True,
            text=True,
        )

        for raw_pid in result.stdout.split():
            try:
                pid = int(raw_pid)

                cmd = subprocess.run(
                    [
                        "ps",
                        "-p",
                        str(pid),
                        "-o",
                        "command=",
                    ],
                    capture_output=True,
                    text=True,
                ).stdout

                if (
                    "mlx_vlm.server" in cmd
                    or "mlx-vlm" in cmd
                ):
                    os.kill(
                        pid,
                        signal.SIGTERM,
                    )

            except Exception:
                pass

    except Exception:
        pass


def stop_server(silent=False):
    state = load_state()

    pid = state.get("pid")

    if pid:
        try:
            os.killpg(
                int(pid),
                signal.SIGTERM,
            )
        except Exception:
            try:
                os.kill(
                    int(pid),
                    signal.SIGTERM,
                )
            except Exception:
                pass

    for _ in range(20):
        if not health():
            break
        time.sleep(0.1)

    if health():
        kill_port_server()

    try:
        STATE_FILE.unlink()
    except FileNotFoundError:
        pass

    if not silent:
        console.print(
            "[green]✓ Model rozładowany. RAM zwolniony.[/green]"
        )


def start_server(model_key, use_mtp=True):
    if model_key not in MODELS:
        raise ValueError(
            f"Nieznany model: {model_key}"
        )

    state = load_state()

    if (
        health()
        and state.get("model") == model_key
        and state.get("mtp") == use_mtp
    ):
        return

    if health():
        stop_server(silent=True)

    cfg = MODELS[model_key]

    target = resolve_model(
        cfg["target_rel"]
    )

    draft = resolve_model(
        cfg["draft_rel"]
    )

    command = [
        "uv",
        "run",
        "--with",
        "mlx-vlm",
        "python",
        "-m",
        "mlx_vlm.server",

        "--model",
        str(target),

        "--host",
        HOST,

        "--port",
        str(PORT),
    ]

    if use_mtp:
        command += [
            "--draft-model",
            str(draft),

            "--draft-kind",
            "mtp",

            "--draft-block-size",
            "2",
        ]

    LOG_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    log = LOG_FILE.open(
        "a",
        encoding="utf-8",
    )

    console.print(
        f"[cyan]Loading {cfg['label']}[/cyan]"
        + (
            " [magenta]+ MTP[/magenta]"
            if use_mtp
            else ""
        )
        + "..."
    )

    process = subprocess.Popen(
        command,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )

    save_state({
        "model": model_key,
        "mtp": use_mtp,
        "pid": process.pid,
        "started_at": time.time(),
        "target": str(target),
        "draft": (
            str(draft)
            if use_mtp
            else None
        ),
    })

    started = time.perf_counter()

    with console.status(
        "[cyan]Ładowanie modelu do unified memory...[/cyan]"
    ):
        while (
            time.perf_counter() - started
            < 120
        ):
            if health():
                elapsed = (
                    time.perf_counter()
                    - started
                )

                console.print(
                    f"[green]✓ Gotowe[/green] "
                    f"[dim]({elapsed:.1f}s)[/dim]"
                )

                return

            if process.poll() is not None:
                console.print(
                    "[red]✗ Serwer zakończył działanie.[/red]"
                )

                print_logs(30)

                raise RuntimeError(
                    "Nie udało się uruchomić modelu."
                )

            time.sleep(0.25)

    raise TimeoutError(
        "Timeout podczas ładowania modelu."
    )


# ============================================================
# OPENAI API
# ============================================================



# ============================================================
# CHAT
# ============================================================

def build_messages(
    model_key,
    session,
    prompt,
    code_mode,
    fast_mode,
):
    """
    Buduje historię rozmowy.

    Instrukcje systemowe są scalane z aktualnym user promptem,
    żeby uniknąć problemów customowego chat template w 4B VLM.
    """

    messages = []

    instruction_parts = [
        BASE_SYSTEM,
    ]

    if fast_mode:
        instruction_parts.append(
            FAST_SYSTEM
        )

    if code_mode:
        instruction_parts.append(
            CODE_SYSTEM
        )

    summary = session.get(
        "summary",
        "",
    ).strip()

    if summary:
        instruction_parts.append(
            "PAMIĘĆ Z WCZEŚNIEJSZEJ CZĘŚCI ROZMOWY:\n\n"
            + summary
        )

    # Poprzednia historia
    for msg in session.get(
        "messages",
        [],
    ):
        messages.append({
            "role": msg.get("role", "user"),
            "content": msg.get("content", ""),
        })

    prefix = "\n\n".join(
        part.strip()
        for part in instruction_parts
        if part.strip()
    )

    final_prompt = (
        prefix
        + "\n\nAKTUALNE PYTANIE UŻYTKOWNIKA:\n"
        + prompt
    )

    messages.append({
        "role": "user",
        "content": final_prompt,
    })

    return messages


def api_endpoint(model_key):
    """
    4B jest checkpointem VLM/Image-Text-to-Text.
    Dla niego używamy natywnego endpointu mlx-vlm.

    9B działa poprawnie przez OpenAI-compatible /v1.
    """
    if model_key == "4b":
        return BASE_URL + "/chat/completions"

    return BASE_URL + "/v1/chat/completions"


def api_request(payload, model_key, timeout=3600):
    request = urllib.request.Request(
        api_endpoint(model_key),

        data=json.dumps(
            payload
        ).encode("utf-8"),

        headers={
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },

        method="POST",
    )

    return urllib.request.urlopen(
        request,
        timeout=timeout,
    )


def chat(
    model_key,
    prompt,
    thinking=False,
    fast=True,
    code_mode=False,
    use_mtp=True,
):
    start_server(
        model_key,
        use_mtp=use_mtp,
    )

    session = load_session()

    cfg = MODELS[model_key]

    messages = build_messages(
        model_key,
        session,
        prompt,
        code_mode,
        fast,
    )

    # FAST oznacza greedy / deterministic.
    if fast:
        temperature = 0.0
        top_p = 1.0

    elif thinking:
        temperature = 0.6
        top_p = 0.95

    else:
        temperature = 0.7
        top_p = 0.8

    max_tokens = (
        8192
        if thinking
        else 4096
    )

    state = load_state()

    payload = {
        "model": state["target"],

        "messages": messages,

        "max_tokens": max_tokens,

        "temperature":
            temperature,

        "top_p":
            top_p,

        "top_k": 20,

        "enable_thinking":
            thinking,

        "chat_template_kwargs": {
            "enable_thinking": thinking,
        },

        "stream": True,

        "stream_options": {
            "include_usage": True
        },
    }

    mode_parts = []

    if code_mode:
        mode_parts.append("CODE")

    if fast:
        mode_parts.append("FAST")

    if thinking:
        mode_parts.append("THINK")

    if not mode_parts:
        mode_parts.append("CHAT")

    mode = "+".join(
        mode_parts
    )

    console.print()
    console.print(
        Text.assemble(
            ("❯ ", "bold cyan"),
            (prompt, "bold"),
        )
    )
    console.print()

    answer = ""
    reasoning = ""

    usage = {}
    timings = {}

    finish_reason = None

    started = time.perf_counter()
    first_token = None

    def render():
        elapsed = (
            time.perf_counter()
            - started
        )

        blocks = []

        if (
            thinking
            and reasoning.strip()
        ):
            blocks.append(
                Panel(
                    Markdown(
                        reasoning
                    ),

                    title=(
                        "[dim]"
                        "Reasoning"
                        "[/dim]"
                    ),

                    title_align="left",

                    border_style=
                        "bright_black",

                    padding=(0, 1),
                )
            )

        if answer:
            blocks.append(
                Markdown(answer)
            )

        elif thinking and reasoning:
            blocks.append(
                Text(
                    "Myślę…",
                    style="dim italic",
                )
            )

        else:
            blocks.append(
                Text(
                    "Generuję…",
                    style="dim italic",
                )
            )

        mtp_label = (
            "MTP"
            if use_mtp
            else "NO-MTP"
        )

        return Panel(
            Group(*blocks),

            title=(
                f"[bold cyan]"
                f" {cfg['label']} "
                f"[/bold cyan]"
                f"[magenta]• {mtp_label}[/magenta]"
                f"[dim] • {mode}[/dim]"
            ),

            title_align="left",

            subtitle=(
                f"[dim]"
                f"{elapsed:.1f}s"
                f"[/dim]"
            ),

            subtitle_align="right",

            border_style="cyan",

            padding=(1, 2),
        )

    try:
        with api_request(
            payload,
            model_key,
        ) as response:

            with Live(
                render(),

                console=console,

                refresh_per_second=
                    REFRESH_FPS,

                transient=False,

                vertical_overflow=
                    "visible",
            ) as live:

                while True:
                    raw = (
                        response.readline()
                    )

                    if not raw:
                        break

                    line = raw.decode(
                        "utf-8",
                        errors="replace",
                    ).strip()

                    if not line.startswith(
                        "data:"
                    ):
                        continue

                    data = (
                        line[5:].strip()
                    )

                    if not data:
                        continue

                    if data == "[DONE]":
                        break

                    try:
                        obj = (
                            json.loads(
                                data
                            )
                        )

                    except (
                        json.JSONDecodeError
                    ):
                        continue

                    incoming_usage = (
                        obj.get(
                            "usage"
                        )
                    )

                    if isinstance(
                        incoming_usage,
                        dict,
                    ):
                        usage.update(
                            incoming_usage
                        )

                    incoming_timings = (
                        obj.get(
                            "timings"
                        )
                    )

                    if isinstance(
                        incoming_timings,
                        dict,
                    ):
                        timings.update(
                            incoming_timings
                        )

                    choices = (
                        obj.get(
                            "choices"
                        )
                        or []
                    )

                    if not choices:
                        continue

                    choice = choices[0]

                    if choice.get(
                        "finish_reason"
                    ):
                        finish_reason = (
                            choice[
                                "finish_reason"
                            ]
                        )

                    delta = (
                        choice.get(
                            "delta"
                        )
                        or {}
                    )

                    reasoning_chunk = (
                        delta.get(
                            "reasoning_content"
                        )
                        or delta.get(
                            "reasoning"
                        )
                        or ""
                    )

                    content_chunk = (
                        delta.get(
                            "content"
                        )
                        or ""
                    )

                    if (
                        reasoning_chunk
                        or content_chunk
                    ):
                        if (
                            first_token
                            is None
                        ):
                            first_token = (
                                time.perf_counter()
                            )

                    if reasoning_chunk:
                        reasoning += str(
                            reasoning_chunk
                        )

                    if content_chunk:
                        answer += str(
                            content_chunk
                        )

                    live.update(
                        render()
                    )

    except KeyboardInterrupt:
        finish_reason = (
            "interrupted"
        )

        console.print(
            "\n[dim]"
            "Generowanie przerwane."
            "[/dim]"
        )

    except urllib.error.HTTPError as e:
        console.print(
            f"[red]"
            f"HTTP {e.code}"
            f"[/red]"
        )

        try:
            console.print(
                e.read().decode()
            )
        except Exception:
            pass

        return

    ended = time.perf_counter()

    elapsed = (
        ended - started
    )

    prompt_tokens = (
        usage.get(
            "prompt_tokens"
        )
    )

    completion_tokens = (
        usage.get(
            "completion_tokens"
        )
    )

    total_tokens = (
        usage.get(
            "total_tokens"
        )
    )

    tps = timings.get(
        "predicted_per_second"
    )

    prefill_tps = timings.get(
        "prompt_per_second"
    )

    peak_memory = timings.get(
        "peak_memory"
    )

    draft_n = timings.get(
        "draft_n"
    )

    draft_accepted = (
        timings.get(
            "draft_n_accepted"
        )
    )

    ttft = None

    if first_token is not None:
        ttft = (
            first_token
            - started
        )

    if (
        tps is None
        and completion_tokens
        and first_token is not None
    ):
        generation_time = max(
            ended - first_token,
            0.000001,
        )

        tps = (
            completion_tokens
            / generation_time
        )

    acceptance = None

    if (
        isinstance(
            draft_n,
            (int, float),
        )
        and draft_n > 0
        and isinstance(
            draft_accepted,
            (int, float),
        )
    ):
        acceptance = (
            draft_accepted
            / draft_n
            * 100
        )

    context_tokens = None

    if (
        prompt_tokens is not None
        and completion_tokens
        is not None
    ):
        context_tokens = (
            prompt_tokens
            + completion_tokens
        )

    # zapis rozmowy
    if answer.strip():
        session["messages"].append({
            "role": "user",
            "content": prompt,
        })

        session["messages"].append({
            "role": "assistant",
            "content": answer,
        })

        session["turns"] = (
            session.get(
                "turns",
                0,
            )
            + 1
        )

        if context_tokens:
            session[
                "last_context_tokens"
            ] = context_tokens

        session[
            "last_metrics"
        ] = {
            "model": model_key,
            "tps": tps,
            "ttft": ttft,
            "mtp_accept":
                acceptance,
            "peak_memory":
                peak_memory,
            "timestamp":
                time.time(),
        }

        save_session(
            session
        )

        append_archive({
            "timestamp":
                time.time(),

            "model":
                model_key,

            "mode":
                mode,

            "prompt":
                prompt,

            "answer":
                answer,

            "usage":
                usage,

            "timings":
                timings,
        })

    # ========================================================
    # FOOTER
    # ========================================================

    line1 = [
        f"[bold cyan]"
        f"{cfg['short']}"
        f"[/bold cyan]",

        (
            "[magenta]MTP[/magenta]"
            if use_mtp
            else "[dim]NO-MTP[/dim]"
        ),

        f"[yellow]{mode}[/yellow]",
    ]

    if isinstance(
        tps,
        (int, float),
    ):
        line1.append(
            f"[bold green]"
            f"{tps:.1f} tok/s"
            f"[/bold green]"
        )

    if (
        prompt_tokens is not None
        and completion_tokens
        is not None
    ):
        line1.append(
            f"↑ {prompt_tokens} "
            f"↓ {completion_tokens} tok"
        )

    if ttft is not None:
        line1.append(
            f"TTFT {ttft:.2f}s"
        )

    line1.append(
        f"{elapsed:.1f}s"
    )

    console.print(
        " • ".join(
            line1
        )
    )

    line2 = []

    if isinstance(
        prefill_tps,
        (int, float),
    ):
        line2.append(
            f"prefill "
            f"{prefill_tps:.0f} tok/s"
        )

    if acceptance is not None:
        line2.append(
            f"MTP accept "
            f"{acceptance:.1f}%"
        )

    if (
        draft_accepted
        is not None
        and draft_n is not None
    ):
        line2.append(
            f"draft "
            f"{int(draft_accepted)}"
            f"/"
            f"{int(draft_n)}"
        )

    if isinstance(
        peak_memory,
        (int, float),
    ):
        line2.append(
            f"peak "
            f"{peak_memory:.2f} GB"
        )

    if total_tokens is not None:
        line2.append(
            f"total "
            f"{total_tokens} tok"
        )

    if finish_reason == "length":
        line2.append(
            "[bold red]"
            "MAX TOKENS"
            "[/bold red]"
        )

    if line2:
        console.print(
            "[dim]"
            + " • ".join(
                line2
            )
            + "[/dim]"
        )

    if context_tokens:
        percent = (
            context_tokens
            / SOFT_CONTEXT_LIMIT
            * 100
        )

        ctx_line = (
            f"Context "
            f"{context_tokens:,}"
            f" / "
            f"{SOFT_CONTEXT_LIMIT:,}"
            f" • "
            f"{percent:.1f}%"
            f" • "
            f"{session['turns']} turns"
        )

        if percent >= 70:
            ctx_line += (
                " • "
                "[yellow]"
                "compact recommended"
                "[/yellow]"
            )

        console.print(
            "[dim]"
            + ctx_line
            + "[/dim]"
        )

    console.print()


# ============================================================
# MEMORY
# ============================================================

def clear_session():
    save_session(
        default_session()
    )

    console.print(
        "[green]"
        "✓ Aktualna rozmowa wyczyszczona."
        "[/green]"
    )


def compact_session():
    session = load_session()

    messages = session.get(
        "messages",
        [],
    )

    if not messages:
        console.print(
            "[yellow]"
            "Brak historii do kompresji."
            "[/yellow]"
        )
        return

    state = load_state()

    model_key = (
        state.get("model")
        if health()
        else "4b"
    )

    if model_key not in MODELS:
        model_key = "4b"

    start_server(
        model_key,
        use_mtp=True,
    )

    transcript = []

    if session.get(
        "summary"
    ):
        transcript.append(
            "POPRZEDNIA "
            "SKOMPRESOWANA PAMIĘĆ:\n"
            + session["summary"]
        )

    for msg in messages:
        role = (
            "USER"
            if msg["role"] == "user"
            else "ASSISTANT"
        )

        transcript.append(
            f"{role}:\n"
            f"{msg['content']}"
        )

    payload = {
        "model":
            load_state()["target"],

        "messages": [
            {
                "role": "user",
                "content":
                    COMPACT_SYSTEM
                    + "\n\n"
                    + "HISTORIA DO SKOMPRESOWANIA:\n\n"
                    + "\n\n".join(
                        transcript
                    ),
            },
        ],

        "temperature": 0,
        "top_p": 1.0,
        "max_tokens": 1500,
        "enable_thinking": False,
        "stream": False,
    }

    before = session.get(
        "last_context_tokens",
        0,
    )

    console.print(
        "[cyan]"
        "Kompresuję historię..."
        "[/cyan]"
    )

    with api_request(
        payload,
        model_key,
    ) as response:
        obj = json.loads(
            response.read().decode(
                "utf-8"
            )
        )

    summary = (
        obj["choices"][0]
        ["message"]["content"]
        .strip()
    )

    # Zachowujemy ostatnie dwie tury dosłownie.
    recent = messages[-4:]

    session["summary"] = summary
    session["messages"] = recent

    session["compactions"] = (
        session.get(
            "compactions",
            0,
        )
        + 1
    )

    # Dokładne użycie zobaczymy po następnym request.
    session[
        "last_context_tokens"
    ] = 0

    save_session(
        session
    )

    estimated = max(
        1,
        len(summary) // 4,
    )

    console.print(
        "[green]"
        "✓ Historia skompresowana."
        "[/green]"
    )

    if before:
        console.print(
            f"[dim]"
            f"Przed: ~{before:,} tok"
            f" → summary ~{estimated:,} tok"
            f" + ostatnie 2 tury"
            f"[/dim]"
        )


def context_status():
    session = load_session()

    ctx = session.get(
        "last_context_tokens",
        0,
    )

    turns = session.get(
        "turns",
        0,
    )

    compactions = session.get(
        "compactions",
        0,
    )

    summary = session.get(
        "summary",
        "",
    )

    table = Table(
        title="Conversation Context",
        show_header=False,
    )

    table.add_column(
        "Key",
        style="cyan",
    )

    table.add_column(
        "Value",
    )

    table.add_row(
        "Turns",
        str(turns),
    )

    if ctx:
        percent = (
            ctx
            / SOFT_CONTEXT_LIMIT
            * 100
        )

        table.add_row(
            "Context",
            f"{ctx:,} / "
            f"{SOFT_CONTEXT_LIMIT:,} "
            f"({percent:.1f}%)",
        )

    else:
        table.add_row(
            "Context",
            "zostanie policzony "
            "przy następnym request",
        )

    table.add_row(
        "Compactions",
        str(compactions),
    )

    table.add_row(
        "Summary",
        (
            f"yes (~{len(summary)//4} tok)"
            if summary
            else "no"
        ),
    )

    table.add_row(
        "Raw messages",
        str(
            len(
                session.get(
                    "messages",
                    [],
                )
            )
        ),
    )

    table.add_row(
        "Archive",
        str(ARCHIVE_FILE),
    )

    console.print(
        table
    )


# ============================================================
# STATUS / LOGS / HELP
# ============================================================

def status():
    state = load_state()
    session = load_session()

    table = Table(
        title="Model CLI",
        show_header=False,
    )

    table.add_column(
        "Key",
        style="cyan",
    )

    table.add_column(
        "Value",
    )

    if health():
        active = state.get(
            "model",
            "unknown",
        )

        table.add_row(
            "Server",
            "[green]healthy[/green]",
        )

        table.add_row(
            "Active model",
            MODELS.get(
                active,
                {}
            ).get(
                "label",
                active,
            ),
        )

        table.add_row(
            "MTP",
            (
                "ON"
                if state.get(
                    "mtp"
                )
                else "OFF"
            ),
        )

        started = state.get(
            "started_at"
        )

        if started:
            uptime = (
                time.time()
                - started
            )

            table.add_row(
                "Uptime",
                f"{uptime/60:.1f} min",
            )

    else:
        table.add_row(
            "Server",
            "[red]stopped[/red]",
        )

    table.add_row(
        "Turns",
        str(
            session.get(
                "turns",
                0,
            )
        ),
    )

    ctx = session.get(
        "last_context_tokens",
        0,
    )

    if ctx:
        table.add_row(
            "Context",
            f"{ctx:,} / "
            f"{SOFT_CONTEXT_LIMIT:,}",
        )

    metrics = session.get(
        "last_metrics",
        {},
    )

    if metrics.get("tps"):
        table.add_row(
            "Last speed",
            f"{metrics['tps']:.1f} tok/s",
        )

    if metrics.get(
        "mtp_accept"
    ) is not None:
        table.add_row(
            "Last MTP accept",
            f"{metrics['mtp_accept']:.1f}%",
        )

    console.print(
        table
    )


def print_logs(lines=80):
    if not LOG_FILE.exists():
        console.print(
            "[yellow]"
            "Brak logów."
            "[/yellow]"
        )
        return

    content = (
        LOG_FILE
        .read_text(
            encoding="utf-8",
            errors="replace",
        )
        .splitlines()
    )

    for line in content[-lines:]:
        print(line)


def help_screen():
    console.print(
        """
[bold cyan]MODEL CLI[/bold cyan]

[bold]Model management[/bold]

  model load --4b
  model load --9b
  model unload
  model status

[bold]Chat[/bold]

  model --q czym jest RAG?
  model --q --4b czym jest RAG?
  model --q --9b przeanalizuj ten problem

  model --q --think przeanalizuj to dokładnie
  model --q --fast --think przeanalizuj to

  model --q --code napisz API w Pythonie
  model --q --code --think znajdź błąd
  model --q --9b --code --think przeanalizuj projekt

[bold]Memory[/bold]

  model context
  model compact
  model clear

[bold]Utility[/bold]

  model logs
  model help

[bold]Zasady[/bold]

  brak --4b/--9b
      → aktualnie załadowany model
      → jeśli nic nie działa: 4B

  --code bez jawnego modelu
      → 9B

  --fast
      → temperature 0 / greedy

  --think
      → reasoning ON

  --fast --think
      → reasoning ON + greedy decoding

  tylko jeden target jest trzymany w RAM naraz.
  pamięć rozmowy jest wspólna między 4B i 9B.
"""
    )


# ============================================================
# ARGUMENT PARSER
# ============================================================

def parse_model_flags(args):
    use_4b = "--4b" in args
    use_9b = "--9b" in args

    if use_4b and use_9b:
        raise ValueError(
            "Nie można użyć "
            "--4b i --9b jednocześnie."
        )

    if use_4b:
        return "4b"

    if use_9b:
        return "9b"

    return None


def main():
    args = sys.argv[1:]

    if not args:
        help_screen()
        return

    # aliases dla convenience
    if args == ["--help"] or args == ["-h"]:
        help_screen()
        return

    if args[0] == "help":
        help_screen()
        return

    if args[0] == "load":
        try:
            model_key = (
                parse_model_flags(
                    args[1:]
                )
            )
        except ValueError as e:
            console.print(
                f"[red]{e}[/red]"
            )
            return

        if not model_key:
            console.print(
                "[red]"
                "Użycie: "
                "model load --4b "
                "lub "
                "model load --9b"
                "[/red]"
            )
            return

        use_mtp = (
            "--no-mtp"
            not in args
        )

        start_server(
            model_key,
            use_mtp=use_mtp,
        )

        return

    if (
        args[0] == "unload"
        or args == ["--unload"]
    ):
        stop_server()
        return

    if (
        args[0] == "status"
        or args == ["--status"]
    ):
        status()
        return

    if (
        args[0] == "clear"
        or args == ["--clear"]
    ):
        clear_session()
        return

    if (
        args[0] == "compact"
        or args == ["--compact"]
    ):
        compact_session()
        return

    if (
        args[0] == "context"
        or args == ["--context"]
    ):
        context_status()
        return

    if args[0] == "logs":
        print_logs()
        return

    # --------------------------------------------------------
    # CHAT FLAGS
    # --------------------------------------------------------

    if "--q" not in args:
        console.print(
            "[red]✗ Nieznana komenda lub brak --q.[/red]\n"
            "\n"
            "Do zadawania pytań użyj:\n"
            "  [cyan]model --q czym jest RAG?[/cyan]\n"
            "\n"
            "Pomoc:\n"
            "  [cyan]model --help[/cyan]"
        )
        return

    known_flags = {
        "--q",
        "--4b",
        "--9b",
        "--fast",
        "--think",
        "--code",
        "--no-mtp",
    }

    try:
        explicit_model = (
            parse_model_flags(
                args
            )
        )

    except ValueError as e:
        console.print(
            f"[red]{e}[/red]"
        )
        return

    thinking = (
        "--think" in args
    )

    fast = (
        "--fast" in args
    )

    code_mode = (
        "--code" in args
    )

    use_mtp = (
        "--no-mtp"
        not in args
    )

    prompt_parts = [
        x
        for x in args
        if x not in known_flags
    ]

    prompt = " ".join(
        prompt_parts
    ).strip()

    if not prompt:
        console.print(
            "[red]"
            "Brak pytania."
            "[/red]"
        )
        return

    # Bez jawnego --fast:
    # zwykły chat i code są deterministic.
    #
    # --think bez --fast korzysta z sampling
    # reasoning-friendly.
    if not thinking:
        fast = True

    # wybór modelu
    if explicit_model:
        model_key = explicit_model

    elif code_mode:
        model_key = "9b"

    else:
        state = load_state()

        if (
            health()
            and state.get("model")
            in MODELS
        ):
            model_key = state["model"]

        else:
            model_key = "4b"

    chat(
        model_key=model_key,
        prompt=prompt,
        thinking=thinking,
        fast=fast,
        code_mode=code_mode,
        use_mtp=use_mtp,
    )


if __name__ == "__main__":
    try:
        main()

    except FileNotFoundError as e:
        console.print(
            f"[bold red]"
            f"{e}"
            f"[/bold red]"
        )

    except KeyboardInterrupt:
        console.print(
            "\n[dim]Przerwano.[/dim]"
        )

    except Exception as e:
        console.print(
            f"[bold red]"
            f"Błąd: {e}"
            f"[/bold red]"
        )
