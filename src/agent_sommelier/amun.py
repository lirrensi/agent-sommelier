"""
FILE: src/agent_sommelier/amun.py
PURPOSE: CLI tool to send questions to configurable LLMs with streaming responses.
OWNS: Amun CLI tool (init, ask commands)
EXPORTS: main (Click entry point), load_config, parse_sse_lines
DOCS: docs/product.md (Tool: amun), docs/arch.md (Component: amun)
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

import click
import rich.markdown
from rich.console import Console

from agent_sommelier import __version__


# ─── constants ─────────────────────────────────────────────────────────────

CONFIG_DIR = ".amun"
CONFIG_FILE = "config.toml"

DEFAULT_SYSTEM_PROMPT = (
    "You are a senior architect and engineer. Think deeply before answering."
)

DEFAULT_CONFIG = """# Amun configuration
# Fill in your endpoint, model, and API key below.
endpoint = "https://api.openai.com/v1/chat/completions"
model = "o3-4h"
api_key = "$AMUN_API_KEY"
system_prompt = "You are a senior architect and engineer. Think deeply before answering."

[body]
reasoning_effort = "high"
"""


# ─── config ─────────────────────────────────────────────────────────────────


def _config_path() -> Path:
    """Return the path to the Amun config file."""
    return Path.home() / CONFIG_DIR / CONFIG_FILE


def _config_dir() -> Path:
    """Return the path to the Amun config directory."""
    return Path.home() / CONFIG_DIR


def _read_config_fallback(config_path: Path) -> dict:
    """Parse a minimal TOML-like config using stdlib only.

    Handles the subset of TOML that Amun's config uses:
    - key = "value" (string values, quotes stripped)
    - [section] headers
    - # comments
    - Empty lines
    """
    text = config_path.read_text(encoding="utf-8")
    result: dict = {}
    current_section: str | None = None

    for line in text.splitlines():
        stripped = line.strip()

        # Skip comments and empty lines
        if not stripped or stripped.startswith("#"):
            continue

        # Section header
        if stripped.startswith("[") and stripped.endswith("]"):
            current_section = stripped[1:-1].strip()
            if current_section not in result:
                result[current_section] = {}
            continue

        # Key = value
        if "=" in stripped:
            key, _, raw_value = stripped.partition("=")
            key = key.strip()
            value = raw_value.strip()

            # Strip surrounding quotes
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]

            if current_section:
                assert isinstance(result[current_section], dict)
                result[current_section][key] = value  # type: ignore[union-attr]
            else:
                result[key] = value

    return result


def _resolve_env(raw: str) -> str:
    """If value starts with '$', resolve it from environment variables."""
    if raw.startswith("$"):
        var = raw[1:]
        resolved = os.environ.get(var)
        if resolved is None:
            raise click.ClickException(
                f"Environment variable '${var}' referenced in config "
                f"but is not set. Set it or change the value in "
                f"~/.amun/config.toml"
            )
        return resolved
    return raw


def load_config() -> dict:
    """Load and resolve Amun config from ~/.amun/config.toml.

    Returns a dict with keys: endpoint, model, api_key, body.

    Raises ClickException if the file is missing or malformed.
    """
    path = _config_path()
    if not path.is_file():
        raise click.ClickException(
            "Config file not found at ~/.amun/config.toml.\n"
            "Run 'amun init' to create a default config first, "
            "then edit it with your endpoint, model, and API key."
        )

    # Try tomllib (stdlib >= 3.11), fall back to manual parser
    try:
        import tomllib  # type: ignore[import-untyped, import-not-found]

        with open(path, "rb") as f:
            raw = tomllib.load(f)
    except ImportError:
        raw = _read_config_fallback(path)
    except Exception as exc:
        raise click.ClickException(
            f"Failed to parse ~/.amun/config.toml: {exc}"
        )

    # Validate required top-level fields
    endpoint = raw.get("endpoint", "")
    if not endpoint:
        raise click.ClickException(
            "Missing 'endpoint' in ~/.amun/config.toml"
        )
    model = raw.get("model", "")
    if not model:
        raise click.ClickException(
            "Missing 'model' in ~/.amun/config.toml"
        )
    api_key = raw.get("api_key", "")
    if not api_key:
        raise click.ClickException(
            "Missing 'api_key' in ~/.amun/config.toml"
        )

    # Resolve env-var references
    endpoint = _resolve_env(str(endpoint))
    model = _resolve_env(str(model))
    api_key = _resolve_env(str(api_key))

    # System prompt from config (optional — CLI --system overrides this)
    system_prompt = raw.get("system_prompt")
    if system_prompt is not None:
        system_prompt = _resolve_env(str(system_prompt))

    # Extract and resolve the [body] section
    body_raw = raw.get("body", {})
    body: dict = {}
    if isinstance(body_raw, dict):
        for k, v in body_raw.items():
            body[k] = _resolve_env(str(v)) if isinstance(v, str) else v

    return {
        "endpoint": endpoint,
        "model": model,
        "api_key": api_key,
        "system_prompt": system_prompt,
        "body": body,
    }


def write_default_config() -> Path:
    """Write the default config to ~/.amun/config.toml (atomic write)."""
    config_dir = _config_dir()
    config_dir.mkdir(parents=True, exist_ok=True)

    path = config_dir / CONFIG_FILE
    tmp_path = config_dir / (CONFIG_FILE + ".tmp")

    tmp_path.write_text(DEFAULT_CONFIG, encoding="utf-8")
    os.replace(str(tmp_path), str(path))

    return path


# ─── SSE parser ─────────────────────────────────────────────────────────────


def parse_sse_lines(response):
    """Yield parsed JSON objects from an SSE stream.

    Reads ``data: {...}`` lines from an HTTP response body, handles
    the ``[DONE]`` sentinel, and yields decoded dicts.
    """
    buffer = ""
    for chunk in iter(lambda: response.read(4096), b""):
        buffer += chunk.decode("utf-8", errors="replace")
        while "\n\n" in buffer:
            block, buffer = buffer.split("\n\n", 1)
            for line in block.split("\n"):
                line = line.strip()
                if line.startswith("data: "):
                    payload = line[6:].strip()
                    if payload == "[DONE]":
                        return
                    if payload:
                        yield json.loads(payload)


# ─── HTTP ───────────────────────────────────────────────────────────────────


def _make_request(
    endpoint: str,
    api_key: str,
    body_bytes: bytes,
    timeout: int,
) -> object:
    """Send an HTTP POST to *endpoint* and return the response object.

    Handles HTTP errors, connection failures, and timeouts.
    """
    req = urllib.request.Request(
        endpoint,
        data=body_bytes,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        raise click.ClickException(
            f"API error {exc.code}: {err_body}"
        )
    except urllib.error.URLError as exc:
        raise click.ClickException(
            f"Connection error: {exc.reason}"
        )
    except (OSError, TimeoutError) as exc:
        raise click.ClickException(
            f"Request failed: {exc}"
        )


# ─── TTY helpers ────────────────────────────────────────────────────────────


def _is_tty() -> bool:
    """Whether stdout is connected to a terminal (PTY)."""
    return sys.stdout.isatty()


def _clear_line() -> None:
    """Carriage-return and clear the current terminal line."""
    sys.stdout.write("\r\u001b[2K")
    sys.stdout.flush()


def _indicator(msg: str) -> None:
    """Print a dim indicator (no newline, TTY only)."""
    if _is_tty():
        sys.stdout.write(click.style(msg, dim=True))
        sys.stdout.flush()


# ─── response handlers ──────────────────────────────────────────────────────


def _handle_streaming_response(response, json_output: bool = False) -> str:
    """Process an SSE stream, printing tokens as they arrive.

    **TTY mode:** Shows ``Thinking...`` indicator, clears on first token.
    Reasoning (``reasoning`` / ``reasoning_content``) in *dim yellow*.

    **Non-TTY mode:** Emits only the answer content — no indicators,
    no reasoning, no ANSI styling.  Still streams token by token.

    **JSON mode (json_output=True):** Emits one JSON line per content
    chunk: ``{"role": "assistant", "content": "<chunk>"}``.

    Returns the full accumulated content string.
    """
    tty = _is_tty() and not json_output
    content_text = ""
    saw_reasoning = False
    saw_any_output = False

    if tty:
        _indicator("Thinking...")

    try:
        for parsed in parse_sse_lines(response):
            # Check for API error embedded in stream
            if "error" in parsed:
                if tty:
                    _clear_line()
                err = parsed["error"]
                raise click.ClickException(
                    f"API error: {err.get('message', str(err))}"
                )

            choices = parsed.get("choices", [])
            if not choices:
                continue

            delta = choices[0].get("delta", {})

            # Reasoning from thinking models (skipped in JSON mode)
            reasoning_chunk = (
                delta.get("reasoning") or delta.get("reasoning_content") or ""
            )
            if reasoning_chunk and not json_output:
                if tty:
                    if not saw_any_output:
                        saw_any_output = True
                        _clear_line()
                    if not saw_reasoning:
                        saw_reasoning = True
                    styled = click.style(
                        reasoning_chunk, dim=True, fg="yellow"
                    )
                    sys.stdout.write(styled)
                    sys.stdout.flush()

            # Normal content
            content_chunk = delta.get("content") or ""
            if content_chunk:
                if json_output:
                    content_text += content_chunk
                    sys.stdout.write(
                        json.dumps({"role": "assistant", "content": content_chunk}) + "\n"
                    )
                    sys.stdout.flush()
                else:
                    if tty:
                        if not saw_any_output:
                            saw_any_output = True
                            _clear_line()
                        # Insert newline before first content token after reasoning
                        if saw_reasoning and not content_text:
                            sys.stdout.write("\n")
                            sys.stdout.flush()
                    content_text += content_chunk
                    sys.stdout.write(content_chunk)
                    sys.stdout.flush()

    except json.JSONDecodeError:
        # Skip any malformed SSE lines gracefully
        pass

    if tty and not saw_any_output:
        _clear_line()

    if not json_output:
        sys.stdout.write("\n")
        sys.stdout.flush()

    return content_text


def _handle_non_streaming_response(response, json_output: bool = False) -> str:
    """Process a complete (non-streaming) JSON response.

    **TTY mode:** Shows ``Thinking...`` indicator, clears when data arrives.
    Reasoning in *dim yellow*, answer rendered with ``rich.markdown.Markdown``.

    **Non-TTY mode:** Emits only the answer content as plain text.

    **JSON mode (json_output=True):** Emits a single JSON line:
    ``{"role": "assistant", "content": "<full text>"}``.
    """
    tty = _is_tty() and not json_output

    if tty:
        _indicator("Thinking...")

    try:
        data = json.loads(response.read().decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        if tty:
            _clear_line()
        raise click.ClickException(
            f"Failed to parse API response: {exc}"
        )
    if tty:
        _clear_line()

    choices = data.get("choices", [])
    if not choices:
        raise click.ClickException(
            "Unexpected API response: no choices in response"
        )

    message = choices[0].get("message", {})

    # Reasoning (TTY only, skipped in JSON mode)
    reasoning = (
        message.get("reasoning")
        or message.get("reasoning_content")
        or ""
    )
    if tty:
        c = Console()
        if reasoning:
            c.print(click.style(reasoning, dim=True, fg="yellow"))
            c.print()

    # Content
    content = message.get("content", "")
    if json_output:
        sys.stdout.write(json.dumps({"role": "assistant", "content": content}) + "\n")
        sys.stdout.flush()
    elif content:
        if tty:
            md = rich.markdown.Markdown(content)
            Console().print(md)
        else:
            sys.stdout.write(content)
            sys.stdout.write("\n")
            sys.stdout.flush()
    elif tty:
        Console().print("[dim]No content in response.[/dim]")

    return content


# ─── CLI ────────────────────────────────────────────────────────────────────


@click.group()
@click.version_option(__version__, prog_name="amun")
def main() -> None:
    """Amun — deep thinking LLM question-asker.

    Send questions to a configurable LLM endpoint and stream
    the response (including reasoning from thinking models).
    """


@main.command()
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Overwrite existing config if present.",
)
def init(force: bool) -> None:
    """Create default config at ~/.amun/config.toml.

    Refuses to overwrite an existing config unless --force is passed.
    """
    path = _config_path()
    if path.is_file() and not force:
        click.echo(
            f"Config already exists at {path}.\n"
            f"Use --force to overwrite."
        )
        return
    path = write_default_config()
    click.echo(f"Config created at {path}")
    click.echo("Edit this file with your endpoint, model, and API key.")


@main.command()
def doctor() -> None:
    """Check that Amun is ready to ask questions.

    Validates config file, required fields, environment variables,
    and endpoint reachability.
    """
    path = _config_path()

    if not path.is_file():
        click.echo(click.style("✗ Config file not found", fg="red"))
        click.echo(f"  Run 'amun init' to create {path}")
        return

    click.echo(click.style("✓ Config file exists", fg="green"))
    click.echo(f"  {path}")

    try:
        config = load_config()
    except click.ClickException as exc:
        click.echo(click.style(f"✗ Config error: {exc}", fg="red"))
        return

    click.echo(click.style("✓ Config parses correctly", fg="green"))

    # Check endpoint
    endpoint = config.get("endpoint", "")
    if endpoint:
        click.echo(click.style("✓ endpoint", fg="green"))
        click.echo(f"  {endpoint}")
    else:
        click.echo(click.style("✗ endpoint is missing", fg="red"))

    # Check model
    model = config.get("model", "")
    if model:
        click.echo(click.style("✓ model", fg="green"))
        click.echo(f"  {model}")
    else:
        click.echo(click.style("✗ model is missing", fg="red"))

    # Check api_key
    api_key = config.get("api_key", "")
    if api_key:
        click.echo(click.style("✓ api_key resolved", fg="green"))
        # Show source (env var or literal)
        raw = _read_config_raw().get("api_key", "")
        if raw.startswith("$"):
            click.echo(f"  from env var {raw}")
        else:
            click.echo("  from config file (literal)")
    else:
        click.echo(click.style("✗ api_key is missing or unresolvable", fg="red"))

    # Check system_prompt (optional, just report)
    sp = config.get("system_prompt")
    if sp:
        click.echo(click.style("✓ system_prompt", fg="green"))

    # Quick connectivity test — lightweight HEAD to endpoint
    click.echo()
    click.echo("Testing endpoint connectivity...", nl=False)
    sys.stdout.flush()
    try:
        req = urllib.request.Request(
            endpoint,
            method="HEAD",
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
        )
        urllib.request.urlopen(req, timeout=5)
        click.echo(click.style(" ✓", fg="green"))
    except urllib.error.HTTPError as exc:
        # 4xx/5xx means we reached the server — that's connectivity
        click.echo(click.style(" ✓ (server reached)", fg="green"))
    except Exception:
        click.echo(click.style(" ✗ (endpoint unreachable)", fg="yellow"))
        click.echo("  Check your network or endpoint URL.")

    click.echo()
    if endpoint and model and api_key:
        click.echo(click.style("Ready to ask.", fg="green", bold=True))
    else:
        click.echo(click.style("Fix the issues above, then try again.", fg="red"))


def _read_config_raw() -> dict:
    """Read config without env resolution — for display purposes."""
    path = _config_path()
    if not path.is_file():
        return {}
    try:
        import tomllib
        with open(path, "rb") as f:
            return tomllib.load(f)
    except ImportError:
        return _read_config_fallback(path)
    except Exception:
        return {}


@main.command()
@click.argument("question")
@click.option(
    "--system",
    "-s",
    default=None,
    help="System prompt for the model. Overrides system_prompt in config.",
)
@click.option(
    "--model",
    "-m",
    default=None,
    help="Override the configured model.",
)
@click.option(
    "--no-stream",
    is_flag=True,
    default=False,
    help="Disable streaming; collect full response then print.",
)
@click.option(
    "--timeout",
    type=int,
    default=120,
    show_default=True,
    help="Timeout in seconds for the HTTP request.",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    default=False,
    help="Output each response as a JSON object instead of plain text.",
)
def ask(
    question: str,
    system: str,
    model: str | None,
    no_stream: bool,
    timeout: int,
    json_output: bool,
) -> None:
    """Ask QUESTION to the configured LLM and stream the answer."""
    config = load_config()

    effective_model = model or config["model"]
    effective_system = system or config.get("system_prompt") or DEFAULT_SYSTEM_PROMPT

    # Build request body
    body: dict = {
        "model": effective_model,
        "messages": [
            {"role": "system", "content": effective_system},
            {"role": "user", "content": question},
        ],
        "stream": not no_stream,
    }
    # Merge in [body] config section (e.g. reasoning_effort)
    body.update(config["body"])
    # Ensure CLI flags take precedence over merged config
    body["stream"] = not no_stream
    body["model"] = effective_model

    body_bytes = json.dumps(body).encode("utf-8")

    if not json_output and _is_tty():
        _indicator("Requesting...")

    response = _make_request(
        config["endpoint"],
        config["api_key"],
        body_bytes,
        timeout,
    )

    if not json_output and _is_tty():
        _clear_line()

    if no_stream:
        _handle_non_streaming_response(response, json_output=json_output)
    else:
        _handle_streaming_response(response, json_output=json_output)


@main.command(hidden=True)
@click.argument("shell", type=click.Choice(["bash", "zsh", "fish", "powershell"]), default="bash")
@click.pass_context
def completions(ctx: click.Context, shell: str) -> None:
    """Print shell completion setup instructions.

    Use this to enable tab-completion for amun.

    Examples:

        amun completions bash   eval in .bashrc

        amun completions zsh   eval in .zshrc

        amun completions fish   source in config.fish

        amun completions powershell   add to $PROFILE
    """
    tool: str = ctx.parent.info_name if ctx.parent is not None and ctx.parent.info_name is not None else "amun"
    click.echo(f"# Enable shell completion for {tool}:")
    click.echo(f"# Add the following to your shell profile:")
    click.echo(f"eval $(_{tool.upper()}_COMPLETE={shell}_source {tool})")


if __name__ == "__main__":
    main()
