# Model CLI

A lightweight terminal interface for running and chatting with local LLMs using **MLX** and `mlx-vlm` on Apple Silicon.

Model CLI manages a local inference server, streams responses directly to the terminal, keeps persistent conversation history, supports reasoning and coding modes, and can use **MTP draft models** for faster generation.

Everything runs locally on your machine.

## Features

- Local LLM inference with `mlx-vlm`
- Optimized for Apple Silicon / MLX
- Streaming terminal responses
- Persistent conversation history
- Automatic local server management
- Fast and reasoning modes
- Dedicated coding mode
- MTP speculative decoding support
- Runtime model switching
- Context usage tracking
- Conversation compaction
- Persistent conversation archive
- Server and generation statistics
- No cloud API required
- Localhost-only inference server

---

## How It Works

Model CLI starts an `mlx-vlm` server on:

```text
127.0.0.1:8080
```

The CLI communicates with this local server, streams generated tokens to the terminal and stores conversation state on disk.

A typical workflow looks like:

```text
Terminal
   │
   ▼
Model CLI
   │
   ▼
mlx-vlm server
   │
   ├── Target model
   │
   └── MTP draft model
```

The model itself is not bundled with this project.

---

## Requirements

- Python 3
- `uv`
- `rich`
- `mlx-vlm`
- `lsof`
- `ps`
- macOS with an environment capable of running the selected MLX models
- Local model files containing `config.json`

The script expects models to be available locally, typically through LM Studio.

Model CLI does **not** automatically download models.

You can run it without permanently installing `rich`:

```bash
uv run --with rich python model.py --help
```

---

## Optional Shell Command

The examples below use the shorter:

```bash
model
```

command.

To make it available in the current shell session from the repository directory:

```bash
MODEL_CLI_PATH="$PWD/model.py"

model() {
    uv run --with rich python "$MODEL_CLI_PATH" "$@"
}
```

You can then use:

```bash
model status
model --q "Explain how RAG works"
```

Alternatively, run the script directly:

```bash
uv run --with rich python model.py ...
```

---

## Commands

| Command | Description |
|---|---|
| `model help` | Show CLI help |
| `model --help` | Show CLI help |
| `model load --4b` | Start the server using the model assigned to `4b` |
| `model load --9b` | Start the server using the model assigned to `9b` |
| `model load --4b --no-mtp` | Start the selected model without MTP acceleration |
| `model unload` | Stop the inference server and remove its runtime state |
| `model status` | Show server state, active model and latest generation statistics |
| `model --q "..."` | Send a prompt to the currently loaded model |
| `model --q --4b "..."` | Send a prompt using the configured `4b` model |
| `model --q --9b "..."` | Send a prompt using the configured `9b` model |
| `model --q --code "..."` | Enable coding-oriented instructions |
| `model --q --think "..."` | Enable reasoning mode |
| `model --q --fast --think "..."` | Use reasoning mode with deterministic generation |
| `model --q --no-mtp "..."` | Disable MTP for the selected run |
| `model context` | Show context usage and compaction information |
| `model compact` | Summarize older conversation history while preserving the two latest turns |
| `model clear` | Clear the active conversation without deleting the archive |
| `model logs` | Display the last 80 lines of the inference server log |

---

## Basic Usage

Ask a question:

```bash
model --q "Explain how transformer attention works"
```

Use the larger configured model:

```bash
model --q --9b "Explain embeddings and vector databases"
```

Use coding mode:

```bash
model --q --code "Review this Python architecture"
```

Enable reasoning:

```bash
model --q --think "Compare two possible implementations"
```

Explicitly disable MTP:

```bash
model --q --9b --no-mtp "Explain speculative decoding"
```

---

## Model Selection

`--4b` and `--9b` are mutually exclusive.

When no model is explicitly selected:

- regular queries use the currently running model
- if no model is running, the CLI defaults to `4b`
- `--code` defaults to `9b`

Regular chat operates in **fast mode** unless `--think` is enabled.

Using:

```bash
--fast
```

without `--think` does not change the default behavior.

Prompts can contain multiple words:

```bash
model --q --9b "Explain how RAG works"
```

---

## Reasoning Mode

Reasoning can be enabled with:

```bash
model --q --think "..."
```

It can also be combined with deterministic generation:

```bash
model --q --fast --think "..."
```

In this configuration, generation uses:

```text
temperature = 0
```

This can be useful when reproducibility is preferred over output diversity.

---

## Coding Mode

Coding mode adds dedicated instructions intended for software-development tasks.

Example:

```bash
model --q --code "Analyze the architecture of this application"
```

Without an explicit model selection, coding mode uses the configured `9b` model.

You can still explicitly select a model:

```bash
model --q --4b --code "Explain this Python function"
```

---

## MTP Acceleration

Model CLI supports an optional **MTP draft model** alongside the main target model.

The target model generates the final output while the compatible draft model can accelerate decoding.

MTP can be disabled with:

```bash
--no-mtp
```

For example:

```bash
model load --9b --no-mtp
```

or:

```bash
model --q --9b --no-mtp "Hello"
```

Both target and draft models must be compatible with the inference server and the selected MTP implementation.

Changing only the model path does not guarantee compatibility.

---

## Conversation Context

Model CLI keeps conversation history between queries.

To inspect the current context:

```bash
model context
```

The command shows information such as:

- conversation turn count
- latest context usage
- number of context compactions
- archive location

---

## Context Compaction

Long conversations can be compressed using:

```bash
model compact
```

The CLI summarizes older parts of the conversation while preserving the **two most recent turns verbatim**.

This allows longer sessions without keeping the entire conversation directly inside the active context window.

---

## Clearing the Conversation

To start a fresh active conversation:

```bash
model clear
```

This clears the current session state.

It does **not** delete:

```text
history.jsonl
```

The full conversation archive therefore remains available on disk.

---

## Model Configuration

Model definitions are located near the beginning of:

[`model.py`](https://github.com/kikk-i/model-cli/blob/main/model.py)

inside the `MODELS` dictionary.

The current implementation exposes two model slots:

```python
"4b"
"9b"
```

Each model entry contains:

| Field | Description |
|---|---|
| `label` | Full model name displayed by the CLI |
| `short` | Short identifier used in statistics |
| `target_rel` | Path to the main model relative to the LM Studio models directory |
| `draft_rel` | Path to the MTP draft model relative to the same directory |

Example:

```python
"4b": {
    "label": "My Model",
    "short": "MYMODEL",
    "target_rel": "publisher/model-name",
    "draft_rel": "publisher/model-name-draft",
},
```

Replace these values with directories that actually exist on your system.

---

## Model Discovery

By default, Model CLI searches for models under:

```text
~/.lmstudio/models/
```

and:

```text
~/.lmstudio/hub/models/
```

If no direct match is found, it also searches within:

```text
~/.lmstudio/
```

using the final directory name.

A valid model directory must contain:

```text
config.json
```

To use models stored elsewhere, modify the `candidates` list inside:

```python
resolve_model()
```

---

## Changing Models

After modifying the model configuration, restart the server:

```bash
model unload
```

then load the required model:

```bash
model load --4b
```

or:

```bash
model load --9b
```

---

## Current Limitations

The current version has several implementation-specific limitations.

### Fixed model slots

Only two model selectors currently exist:

```text
--4b
--9b
```

The names remain unchanged even if you configure models with different parameter counts.

### Draft model requirement

The current implementation checks for the configured `draft_rel` directory even when:

```text
--no-mtp
```

is used.

As a result, the configured draft-model directory currently needs to exist even if MTP is disabled.

### MTP compatibility

The target model and draft model must both be compatible with the selected `mlx-vlm` server and MTP implementation.

Changing model paths alone does not guarantee compatibility.

### API endpoints

The current `api_endpoint()` implementation uses:

```text
/chat/completions
```

for the `4b` slot and:

```text
/v1/chat/completions
```

for the `9b` slot.

If a configured model or server requires a different endpoint, update:

```python
api_endpoint()
```

accordingly.

---

## Local Data

Model CLI stores its runtime data under:

```text
~/.local/share/model-cli/
```

Files include:

| File | Contents |
|---|---|
| `session.json` | Active conversation, summary and statistics |
| `history.jsonl` | Persistent archive of full prompts and responses |
| `state.json` | Server process ID and active model paths |
| `server.log` | Local inference server log |

---

## Privacy & Security

Inference is performed locally.

The server listens only on:

```text
127.0.0.1:8080
```

and does not require authentication.

Model CLI does not require a cloud LLM API.

However, prompts supplied directly through:

```bash
model --q "..."
```

may also appear in:

- shell history
- process command-line arguments
- local Model CLI history

Do not pass passwords, API keys, access tokens or other secrets directly through command-line arguments.

The conversation archive:

```text
history.jsonl
```

is persistent and is **not removed by `model clear`**.

---

## Intended Use

Model CLI is designed for experimenting with local language models directly from the terminal, including:

- local AI development
- coding assistance
- model benchmarking
- MLX experimentation
- testing MTP/speculative decoding
- private local conversations
- reasoning experiments
- comparing different local models
- development workflows without cloud inference

It is primarily designed around local MLX models running on macOS / Apple Silicon.
