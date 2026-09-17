# vphone-mcp

MCP server for programmatic control of vphone-cli iOS VMs.

## Quick Reference

- **Install:** `uv sync`
- **Run (stdio, default):** `uv run vphone-mcp`
- **Run (remote):** `VPHONE_MCP_AUTH_TOKEN=... uv run vphone-mcp --transport http --host 0.0.0.0`
- **Test:** `uv run python -c "from vphone_mcp.client import VPhoneClient; print(VPhoneClient('/path/to/vm/vphone.sock').screenshot('/tmp/test.png'))"`
- **Env probe:** `vphone_status` tool reports CLI binary, socket, VM list, and running launches without raising.

## Architecture

```
Claude Code / Claude Desktop
    ↓ MCP — stdio (local) or streamable HTTP/SSE + bearer token (remote)
vphone-mcp (Python)
    ├── Unix socket (JSON)  → vphone-cli hostctl (running VM)
    └── subprocess           → vphone-cli (VM lifecycle, firmware)
    ↓
vphone-cli (Swift, vm/vphone.sock)
    ↓ Virtualization.framework
iOS VM
```

## Tool Layers

1. **Hardware keys** — `go_home`, `press_power`, `volume_up`, `volume_down` (each takes `screen=True, delay=500`)
2. **Screenshots, clipboard & files** — `screenshot` (full-res PNG + compact preview; defaults to `$VPHONE_ROOT/vphone-mcp/screenshots/screen.png`), `set_clipboard(text)` — sets the guest clipboard, NOT keyboard typing — `get_file_url(path)` (network transport only)
3. **Pre-mapped navigation** — `open_app`, `tap_back`, `scroll_down`, `scroll_up`, `open_notification_center`, `open_control_center`, `open_app_switcher`, `open_search`, `swipe_to_next_page`, `swipe_to_previous_page`
4. **Raw interaction** — `tap(x, y, screen=True, delay=500)`, `swipe(x1, y1, x2, y2, duration_ms=300, screen=True, delay=500)`
5. **Environment status** — `vphone_status` (binary, socket, VM list, running launches; never raises)
6. **vphone-cli command wrappers (21)** — `vm_list`, `vm_info`, `vm_new`, `vm_config`, `vm_rename`, `vm_delete`, `vm_clone`, `vm_export`, `vm_import`, `vm_launch`, `vm_stop`, `vm_create`, `fw_catalog`, `fw_prepare`, `fw_patch`, `restore`, `cfw_install`, `patch_firmware`, `patch_component`, `setup_env`, `boot` — one tool per vphone-cli subcommand. Destructive ones (`vm_delete`, `vm_stop`, `vm_create`, `restore`, `cfw_install`) require `confirm=True` (or `force=True` for `vm_delete`); `vm_launch`/`boot` run in the background with log files under `$VPHONE_ROOT/vphone-mcp/logs/`.

Every socket action also returns an inline compact grayscale screenshot; socket errors surface as clean messages, never tracebacks.

## Transports and remote file access

`main()` defers to `remote.parse_config()` / `remote.run_server()`. stdio stays
the default; `--transport http` (or `sse`) serves the same tool set over the
network, with the whole ASGI app wrapped in `remote.AuthMiddleware`
(constant-time compare, `GET /healthz` exempt). A network transport refuses to
start without `VPHONE_MCP_AUTH_TOKEN` unless `--allow-anonymous` is passed —
the tools launch VMs and run sudo, so the token is the only gate. The token is
env-only by design (never argv). Binding a non-loopback host replaces FastMCP's
import-time localhost DNS-rebinding allowlist, which would otherwise 421 every
real Host header.

`vphone_mcp/files.py` makes server-side files fetchable by remote clients over
`GET /files/<path under files.root()>` (e.g. `/files/screenshots/screen.png`).
Auth is HTTP Basic (the only scheme that fits inside a URL) but introduces no
second secret: the password is the MCP token, the user is `sha256(<resolved
absolute path>)`. Serving is stateless — `files.resolve()` joins the relative
path onto the root, normalises it, and returns a file only when it stayed
inside the root, is the spelling `relative_path()` would mint (so each file has
exactly one URL — a `..` detour is a 404), hashes to the presented user, and is
a regular file. Traversal, absolute paths and symlinks out of scope therefore
fail without ever being opened, and links survive a restart. `files.root()` is
`$VPHONE_ROOT/vphone-mcp` and nothing else is served: the VM library and the
rest of the vphone root stay unreachable. `files.url_for()` returns None on
stdio, so tools silently skip the link when it is meaningless.
`cli.run_background()` and `server._guest_network()` take the log directory
from `files.log_dir()`, so a custom `$VPHONE_ROOT` keeps launch logs in scope.

## Configuration

- `VPHONE_SOCK` — explicit hostctl socket path (auto-discovered from the VM library otherwise)
- `VPHONE_CLI_BIN` — vphone-cli binary override (default: PATH lookup, then the app bundle)
- `VPHONE_LIBRARY_ROOT` — VM library root override (default `~/.vphone/VMs`)
- `VPHONE_ROOT` — vphone project root override (default `~/.vphone`)
- `VPHONE_SUDO_PASSWORD` — sudo password fallback for `vm_create` (keeps it out of agent context)
- `VPHONE_MCP_AUTH_TOKEN` / `VPHONE_MCP_AUTH_TOKEN_FILE` — bearer token for network transports
- `VPHONE_MCP_TRANSPORT`, `_HOST`, `_PORT`, `_PATH`, `_ALLOW_ANONYMOUS`, `_JSON_RESPONSE`, `_STATELESS`, `_ALLOWED_HOSTS`, `_ALLOWED_ORIGINS`, `_SSL_CERTFILE`, `_SSL_KEYFILE`, `_PUBLIC_URL`, `_DISABLE_FILES` — transport settings, each with a matching CLI flag (`vphone-mcp --help`)

## Coordinate System

All pixel coordinates are for the default 1290x2796 screen (3x scale). Use `screenshot()` to see the current display and derive coordinates for `tap()`.
