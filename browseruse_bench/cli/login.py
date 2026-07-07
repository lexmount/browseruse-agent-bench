"""`bubench login` subcommand — manage Lexmount login contexts.

Workflow:

1. ``bubench login add <site> --website https://www.xiaohongshu.com``
   - Creates a Lexmount persistent context, opens a read_write session,
     prints the inspect URL, waits for the user to log in manually, then
     closes the session so cookies persist into the base context.
   - Records the mapping in ``browser_data/login_contexts/index.json``.

2. ``bubench login list`` — show recorded login contexts.
3. ``bubench login remove <site>`` — drop a mapping entry (and optionally
   delete the Lexmount context via ``--delete-remote``).

Evaluations with ``login_required: true`` auto-resolve the context at run
time via ``target_website`` — see ``browseruse_bench/cli/run.py``.
"""
from __future__ import annotations

import argparse
import os
from datetime import UTC, datetime
from typing import Any

from browseruse_bench.browsers.login_contexts import (
    DEFAULT_PROFILE_KEY,
    INDEX_PATH,
    SITE_REGISTRY,
    get_profiles_for_site,
    load_index,
    remove_profile,
    resolve_site_for_url,
    upsert_profile,
)
from browseruse_bench.browsers.providers.lexmount import _build_debug_url, normalize_profile_keys
from browseruse_bench.utils import (
    REPO_ROOT,
    add_script_log_handler,
    handle_cli_errors,
    load_env_file,
    setup_logger,
)

load_env_file(REPO_ROOT / ".env")

logger = setup_logger("login", format_mode="plain")

_ANSI_GREEN = "\033[32m"
_ANSI_YELLOW = "\033[33m"
_ANSI_RESET = "\033[0m"


def _resolve_credential(cli_value: str | None, *env_keys: str) -> str | None:
    if cli_value:
        return cli_value
    for key in env_keys:
        val = os.getenv(key)
        if val:
            return val
    return None


def _as_bool(flag: bool | None, env_val: str | None, default: bool = True) -> bool:
    if flag is not None:
        return flag
    return _coerce_bool(env_val, default=default)


def _coerce_bool(value: Any, *, default: bool) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in ("false", "0", "no", "off")


def _first_present(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def configure_login_parser(parser: argparse.ArgumentParser, _config: dict[str, Any]) -> None:
    sub = parser.add_subparsers(dest="login_action", required=True)

    add_p = sub.add_parser("add", help="Create a new login context (interactive)")
    add_p.add_argument("site", help="Short site key, e.g. 'xiaohongshu', 'jd', 'taobao'")
    add_p.add_argument(
        "--website",
        default=None,
        help="Website URL the user will open/login to (used for target_website matching). "
        "Defaults to inferring from `site`.",
    )
    add_p.add_argument("--api-key", default=None, help="Lexmount API key (or set LEXMOUNT_API_KEY / LEXMOUNT_API)")
    add_p.add_argument("--project-id", default=None, help="Lexmount project id (or set LEXMOUNT_PROJECT_ID)")
    add_p.add_argument("--base-url", default=None, help="Lexmount base URL (or set LEXMOUNT_BASE_URL)")
    add_p.add_argument("--browser-mode", default="normal", choices=("normal", "light"))
    add_p.add_argument(
        "--verify-ssl",
        dest="verify_ssl",
        action="store_true",
        default=None,
        help="Enable SSL verification on the Lexmount client (default).",
    )
    add_p.add_argument(
        "--no-verify-ssl",
        dest="verify_ssl",
        action="store_false",
        default=None,
        help="Disable SSL verification (for self-signed endpoints).",
    )
    add_p.add_argument(
        "--profile",
        default=None,
        help="Restrict login to one profile key (matches `lexmount_profiles.<key>` in config). "
        "Without this flag, the command iterates every configured profile.",
    )

    list_p = sub.add_parser("list", help="List recorded login contexts")
    list_p.add_argument("--json", action="store_true", help="Emit raw JSON")

    rm_p = sub.add_parser("remove", help="Remove a login context mapping")
    rm_p.add_argument("site")
    rm_p.add_argument(
        "--delete-remote",
        action="store_true",
        help="Also call Lexmount contexts.delete to drop the remote context.",
    )
    rm_p.add_argument("--api-key", default=None)
    rm_p.add_argument("--project-id", default=None)
    rm_p.add_argument("--base-url", default=None)
    rm_p.add_argument(
        "--profile",
        default=None,
        help="Drop only the named profile entry. Without this flag, all profile "
        "entries for the site are removed.",
    )


def _build_client(
    api_key: str | None,
    project_id: str | None,
    base_url: str | None,
    verify_ssl: bool,
):
    # Imported lazily: avoids forcing lexmount load on unrelated subcommands.
    from lexmount import Lexmount

    # Ensure base_url has a protocol prefix (env vars often omit it).
    if base_url and "://" not in base_url:
        base_url = "https://" + base_url

    kwargs: dict[str, Any] = {}
    if api_key:
        kwargs["api_key"] = api_key
    if project_id:
        kwargs["project_id"] = project_id
    if base_url:
        kwargs["base_url"] = base_url
    client = Lexmount(**kwargs)
    if not verify_ssl:
        # TODO: upstream a `verify=` parameter to the Lexmount SDK constructor
        # so we don't have to swap the private `_http_client`. Current SDK
        # (0.4.9) exposes no way to disable SSL verification at init time.
        import httpx

        client._http_client = httpx.Client(timeout=client._http_client.timeout, verify=False)
    return client


def _get_page_target(client, session):
    """Return the first 'page' target for *session*, or None."""
    sid = str(getattr(session, "session_id", "") or getattr(session, "id", "") or "")
    if not sid:
        return None
    try:
        targets = client.sessions.list_targets(sid)
        return next((t for t in targets if t.type == "page"), None)
    except Exception as exc:  # noqa: BLE001
        logger.warning("list_targets failed: %s", exc)
        return None


def _navigate_page(page_ws_url: str, website: str) -> bool:
    """Send ``Page.navigate`` over a page-level CDP WS URL. Best-effort."""
    if not page_ws_url or not website:
        return False
    try:
        import json as _json

        from websockets.sync.client import connect
    except ImportError as exc:
        logger.warning("websockets unavailable: %s", exc)
        return False
    try:
        import time as _time

        with connect(page_ws_url, open_timeout=10, close_timeout=5) as ws:
            ws.send(_json.dumps(
                {"id": 1, "method": "Page.navigate", "params": {"url": website}}
            ))
            # CDP may emit events (e.g. Page.frameStartedLoading) before the
            # Page.navigate reply arrives. Loop until we get our reply or timeout.
            deadline = _time.monotonic() + 10
            while _time.monotonic() < deadline:
                remaining = max(0.5, deadline - _time.monotonic())
                reply = _json.loads(ws.recv(timeout=remaining))
                if reply.get("id") == 1:
                    if "result" in reply:
                        logger.info("Navigated remote page to %s", website)
                        return True
                    logger.warning("Page.navigate error reply: %s", str(reply)[:200])
                    return False
            logger.warning("Page.navigate timed out waiting for reply")
            return False
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to navigate remote page to %s: %s", website, exc)
        return False


def _print_site_registry() -> None:
    """Print all built-in site → URL mappings."""
    print(f"\nBuilt-in site registry ({len(SITE_REGISTRY)} sites):\n")
    # Group by section comments in SITE_REGISTRY order isn't preserved across Python dicts,
    # just sort alphabetically for clean output.
    col = max(len(k) for k in SITE_REGISTRY)
    for site_key in sorted(SITE_REGISTRY):
        registered = "✓" if get_profiles_for_site(site_key) else " "
        print(f"  {registered} {site_key.ljust(col)}  {SITE_REGISTRY[site_key]}")
    print("\n✓ = already logged in   Usage: bubench login add <site>\n")


def _profile_keys_for_run(args: argparse.Namespace, _config: dict[str, Any]) -> list[str]:
    """Return the ordered list of profile keys this invocation should target."""
    if getattr(args, "profile", None):
        return [str(args.profile).strip().lower()]
    keys: list[str] = []
    seen: set[str] = set()
    for browser in _iter_lexmount_browser_configs(_config):
        profiles = normalize_profile_keys(
            browser.get("lexmount_profiles") if isinstance(browser, dict) else None
        )
        for kn in profiles:
            if kn and kn not in seen:
                seen.add(kn)
                keys.append(kn)
    return keys


def _iter_lexmount_browser_configs(_config: dict[str, Any]) -> list[dict[str, Any]]:
    configs: list[dict[str, Any]] = []
    browsers_cfg = (_config or {}).get("browsers") or {}
    if isinstance(browsers_cfg, dict):
        preferred = browsers_cfg.get("lexmount")
        if isinstance(preferred, dict):
            configs.append(preferred)
        for key, browser in browsers_cfg.items():
            if key == "lexmount" or not isinstance(browser, dict):
                continue
            if str(browser.get("browser_id") or "").lower() == "lexmount":
                configs.append(browser)

    agents_cfg = (_config or {}).get("agents") or {}
    if isinstance(agents_cfg, dict):
        for agent_cfg in agents_cfg.values():
            if not isinstance(agent_cfg, dict):
                continue
            browser = agent_cfg.get("browser")
            if isinstance(browser, dict):
                configs.append(browser)
    return configs


def _lexmount_browser_config(_config: dict[str, Any]) -> dict[str, Any]:
    configs = _iter_lexmount_browser_configs(_config)
    return configs[0] if configs else {}


def _profile_cfg_from_config(_config: dict[str, Any], profile_key: str) -> dict[str, Any]:
    """Find the per-profile dict in any agent's `lexmount_profiles`, case-insensitive."""
    if not profile_key:
        return {}
    norm_key = profile_key.strip().lower()
    for browser in _iter_lexmount_browser_configs(_config):
        profiles = normalize_profile_keys(
            browser.get("lexmount_profiles") if isinstance(browser, dict) else None
        )
        if norm_key in profiles:
            return profiles[norm_key]
    return {}


def _resolve_creds_for_profile(
    args: argparse.Namespace,
    _config: dict[str, Any],
    profile_key: str | None,
) -> dict[str, Any] | None:
    """Resolve creds for one profile. Returns None when api_key or project_id missing."""
    cfg_profile = _profile_cfg_from_config(_config, profile_key) if profile_key else {}
    browser_cfg = _lexmount_browser_config(_config)
    upper = (profile_key or "").upper().replace("-", "_")
    api_env = (f"LEXMOUNT_API_KEY_{upper}", f"LEXMOUNT_API_{upper}") if upper else ()
    project_env = (f"LEXMOUNT_PROJECT_ID_{upper}",) if upper else ()
    base_env = (f"LEXMOUNT_BASE_URL_{upper}",) if upper else ()
    api_key = (
        args.api_key
        or _resolve_credential(None, *api_env)
        or cfg_profile.get("api_key")
        or browser_cfg.get("lexmount_api_key")
        or _resolve_credential(None, "LEXMOUNT_API_KEY", "LEXMOUNT_API")
    )
    project_id = (
        args.project_id
        or _resolve_credential(None, *project_env)
        or cfg_profile.get("project_id")
        or browser_cfg.get("lexmount_project_id")
        or _resolve_credential(None, "LEXMOUNT_PROJECT_ID")
    )
    base_url = (
        args.base_url
        or _resolve_credential(None, *base_env)
        or cfg_profile.get("base_url")
        or browser_cfg.get("lexmount_base_url")
        or _resolve_credential(None, "LEXMOUNT_BASE_URL")
    )
    if not api_key or not project_id:
        return None
    verify_ssl = _as_bool(args.verify_ssl, os.getenv("LEXMOUNT_VERIFY_SSL"), default=True)
    official_proxy = _coerce_bool(
        _first_present(
            cfg_profile.get("official_proxy"),
            browser_cfg.get("lexmount_official_proxy"),
            os.getenv("LEXMOUNT_OFFICIAL_PROXY"),
        ),
        default=False,
    )
    return {
        "api_key": api_key,
        "project_id": project_id,
        "base_url": base_url,
        "verify_ssl": verify_ssl,
        "official_proxy": official_proxy,
    }


def _print_profile_banner(profile_key: str | None) -> None:
    label = profile_key or "(default)"
    print()
    print(f"{_ANSI_GREEN}== Profile: {label} =={_ANSI_RESET}")


def _resolve_inspect_url(client, session, target) -> str:
    """Pick a page-level inspect URL with a session-level fallback."""
    inspect_url = (getattr(target, "inspect_url", None) or "") if target else ""
    if inspect_url:
        return inspect_url
    raw_inspect = str(getattr(session, "inspect_url", "") or "")
    if raw_inspect:
        return raw_inspect
    return _build_debug_url(
        base_url=str(getattr(client, "base_url", "") or ""),
        session_id=str(getattr(session, "session_id", "") or getattr(session, "id", "") or ""),
    )


def _wait_for_user_login(inspect_url: str, website: str, site: str, base_ctx_id: str) -> None:
    print()
    print(
        f"{_ANSI_GREEN}== Open the URL below in a browser and log in to the remote Chrome "
        f"(在浏览器打开下面的 URL 完成登录) =={_ANSI_RESET}"
    )
    print()
    print(f"  {inspect_url or '(inspect_url unavailable — check session_id in logs)'}")
    print()
    print(f"  website   : {website}")
    print(f"  site key  : {site}")
    print(f"  context   : {base_ctx_id}")
    print()
    try:
        input(
            f"{_ANSI_YELLOW}Press Enter after login (登录完成后回车); "
            f"Ctrl-C to cancel: {_ANSI_RESET}"
        )
    except EOFError:
        logger.error("stdin closed; cannot confirm login — aborting and deleting context")
        raise KeyboardInterrupt from None


def _persist_login_entry(
    site: str,
    profile_key: str | None,
    base_ctx_id: str,
    creds: dict[str, Any],
    args: argparse.Namespace,
    website: str,
) -> None:
    entry = {
        "context_id": base_ctx_id,
        "website": website,
        "created_at": datetime.now(UTC).isoformat(),
        "login_type": "manual",
        "base_url": creds.get("base_url") or None,
        "browser_mode": args.browser_mode,
        "verify_ssl": creds.get("verify_ssl", True),
    }
    upsert_profile(site, profile_key or DEFAULT_PROFILE_KEY, entry)
    print(
        f"\n{_ANSI_GREEN}✓ Login state saved (登录态已保存){_ANSI_RESET} "
        f"site={site} profile={profile_key or '(default)'} "
        f"context={base_ctx_id} → {INDEX_PATH.relative_to(REPO_ROOT)}"
    )


def _login_one_profile(
    args: argparse.Namespace,
    _config: dict[str, Any],
    site: str,
    website: str,
    profile_key: str | None,
) -> int:
    creds = _resolve_creds_for_profile(args, _config, profile_key)
    if creds is None:
        logger.info(
            "Skipping site=%s profile=%s: missing api_key or project_id "
            "(set per-profile env vars, --api-key/--project-id, or fill the profile in config.yaml).",
            site, profile_key or "(default)",
        )
        return 0

    _print_profile_banner(profile_key)

    from lexmount.exceptions import LexmountError

    client = _build_client(creds["api_key"], creds["project_id"], creds["base_url"], creds["verify_ssl"])
    base_ctx = None
    session = None
    base_ctx_id = ""
    try:
        base_ctx = client.contexts.create(metadata={
            "site": site, "kind": "login", "website": website,
            "profile": profile_key or DEFAULT_PROFILE_KEY,
        })
        base_ctx_id = str(getattr(base_ctx, "id", "") or "")
        if not base_ctx_id:
            logger.error("contexts.create returned no id (profile=%s)", profile_key or "(default)")
            return 1
        logger.info("Created base login context: %s (profile=%s)", base_ctx_id, profile_key or "(default)")

        session = client.sessions.create(
            context={"id": base_ctx_id, "mode": "read_write"},
            browser_mode=args.browser_mode,
            official_proxy=creds["official_proxy"],
        )
        target = _get_page_target(client, session)
        page_ws_url = (
            getattr(target, "web_socket_debugger_url_transformed", None)
            or getattr(target, "web_socket_debugger_url", None)
            or ""
        ) if target else ""
        inspect_url = _resolve_inspect_url(client, session, target)
        if page_ws_url:
            _navigate_page(page_ws_url, website)
        else:
            logger.warning("No page-level WS URL; browser will open on about:blank")
        _wait_for_user_login(inspect_url, website, site, base_ctx_id)

    except KeyboardInterrupt:
        logger.warning("Canceled by user; cleaning up (profile=%s)", profile_key or "(default)")
        if session is not None:
            try:
                session.close()
            except (LexmountError, OSError, RuntimeError, TimeoutError) as exc:
                logger.debug("session.close ignored during cancel: %s", exc)
        if base_ctx is not None:
            try:
                client.contexts.delete(str(getattr(base_ctx, "id", "")))
            except LexmountError as exc:
                logger.debug("contexts.delete ignored during cancel: %s", exc)
        return 130

    except LexmountError as exc:
        logger.error("Lexmount error (profile=%s): %s", profile_key or "(default)", exc)
        if session is not None:
            try:
                session.close()
            except (LexmountError, OSError, RuntimeError, TimeoutError) as cleanup_exc:
                logger.debug("session.close ignored during error: %s", cleanup_exc)
        if base_ctx is not None:
            try:
                client.contexts.delete(str(getattr(base_ctx, "id", "")))
            except LexmountError as cleanup_exc:
                logger.debug("contexts.delete ignored during error: %s", cleanup_exc)
        return 1

    try:
        session.close()
    except (LexmountError, OSError, RuntimeError, TimeoutError) as exc:
        logger.warning("session.close failed (profile=%s): %s", profile_key or "(default)", exc)
    sid = str(getattr(session, "session_id", "") or getattr(session, "id", "") or "")
    if sid:
        try:
            client.sessions.delete(session_id=sid)
        except LexmountError as exc:
            logger.debug("sessions.delete skipped: %s", exc)

    _persist_login_entry(site, profile_key, base_ctx_id, creds, args, website)
    return 0


def _cmd_add(args: argparse.Namespace, _config: dict[str, Any]) -> int:
    site = args.site.strip().lower()

    # Special keyword: show available sites.
    if site == "help":
        _print_site_registry()
        return 0

    # Auto-fill website from built-in registry if not provided.
    website = args.website or SITE_REGISTRY.get(site) or f"https://www.{site}.com"
    if not args.website and site in SITE_REGISTRY:
        logger.info("Using built-in URL for %s: %s", site, website)

    # Sanity-warn if the supplied website won't match this site key at eval time.
    derived = resolve_site_for_url(website)
    if derived and derived != site:
        logger.warning(
            "Site key '%s' does not match target_website '%s' → derives to '%s'. "
            "Consider using '%s' so auto-matching works.",
            site, website, derived, derived,
        )

    profile_keys: list[str | None] = list(_profile_keys_for_run(args, _config))
    if not profile_keys:
        # No profiles configured and no --profile flag — register a single
        # default entry (legacy behavior, backward compatible).
        profile_keys = [None]

    rc = 0
    for profile_key in profile_keys:
        existing = get_profiles_for_site(site)
        if profile_key and profile_key in existing:
            logger.warning(
                "Site '%s' profile=%s already has a login context — it will be overwritten.",
                site, profile_key,
            )
        elif profile_key is None and DEFAULT_PROFILE_KEY in existing:
            logger.warning(
                "Site '%s' already has a default login context — it will be overwritten.", site,
            )
        rc_one = _login_one_profile(args, _config, site, website, profile_key)
        if rc_one != 0:
            rc = rc_one
            if rc_one == 130:  # user-canceled — abort the loop
                break
    return rc


def _cmd_list(args: argparse.Namespace) -> int:
    index = load_index()
    if args.json:
        import json as _json

        print(_json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if not index:
        print("(no login contexts — run `bubench login add <site> --website ...`)")
        return 0
    width = max(len(site) for site in index)
    for site in sorted(index):
        profiles = get_profiles_for_site(site)
        for profile_key in sorted(profiles):
            entry = profiles[profile_key]
            label = profile_key if profile_key != DEFAULT_PROFILE_KEY else "(default)"
            print(
                f"  {site.ljust(width)}  profile={label.ljust(10)}  "
                f"context={str(entry.get('context_id'))[:12]}…  "
                f"website={entry.get('website', '?')}  "
                f"created={entry.get('created_at', '?')}"
            )
    return 0


def _delete_remote_entry(
    args: argparse.Namespace,
    entry: dict[str, Any],
    profile_key: str | None,
    _config: dict[str, Any],
) -> int:
    """Best-effort remote delete for one persisted entry. Returns 0 on success/no-op.

    Resolves credentials per-profile so a multi-profile remove targets the
    endpoint that actually owns the context. The entry's stored ``base_url``
    wins over any configured fallback because the context lives there. Falls
    through to legacy ``LEXMOUNT_*`` env vars when ``profile_key`` is None or
    the default-fallback bucket.
    """
    lookup_key = profile_key if profile_key and profile_key != DEFAULT_PROFILE_KEY else None
    creds = _resolve_creds_for_profile(args, _config, lookup_key)
    if creds is None:
        logger.error(
            "Missing credentials for --delete-remote profile=%s (need api_key + project_id)",
            profile_key or "(default)",
        )
        return 1
    base_url = entry.get("base_url") or creds.get("base_url")
    verify_ssl = bool(entry.get("verify_ssl", creds.get("verify_ssl", True)))
    from lexmount.exceptions import ContextLockedError, ContextNotFoundError, LexmountError

    client = _build_client(creds["api_key"], creds["project_id"], base_url, verify_ssl=verify_ssl)
    ctx_id = str(entry.get("context_id") or "")
    try:
        client.contexts.delete(ctx_id)
        logger.info("Remote context deleted: %s (profile=%s)", ctx_id, profile_key or "(default)")
        return 0
    except ContextNotFoundError:
        logger.info("Remote context already gone: %s (profile=%s)", ctx_id, profile_key or "(default)")
        return 0
    except ContextLockedError as exc:
        logger.error(
            "Context locked by session %s — retry later or force_release", exc.active_session_id
        )
        return 1
    except LexmountError as exc:
        logger.error("Failed to delete remote context (profile=%s): %s", profile_key or "(default)", exc)
        return 1


def _cmd_remove(args: argparse.Namespace, _config: dict[str, Any]) -> int:
    site = args.site.strip().lower()
    profiles = get_profiles_for_site(site)
    if not profiles:
        logger.error("No login context mapping for site=%s", site)
        return 1

    targets: list[str]
    if getattr(args, "profile", None):
        target = args.profile.strip().lower()
        if target not in profiles:
            logger.error("No login context mapping for site=%s profile=%s", site, target)
            return 1
        targets = [target]
    else:
        targets = list(profiles)

    rc = 0
    for profile_key in targets:
        entry = profiles[profile_key]
        if args.delete_remote:
            rc_remote = _delete_remote_entry(args, entry, profile_key, _config)
            if rc_remote != 0:
                rc = rc_remote
                continue
        remove_profile(site, profile_key)
        label = profile_key if profile_key != DEFAULT_PROFILE_KEY else "(default)"
        print(f"✓ Removed login context mapping for site={site} profile={label}")
    return rc


@handle_cli_errors
def login_command(args: argparse.Namespace, _config: dict[str, Any]) -> int:
    add_script_log_handler(
        logger, REPO_ROOT / "output" / "logs", "login", format_mode="plain"
    )
    action = getattr(args, "login_action", None)
    if action == "add":
        return _cmd_add(args, _config)
    if action == "list":
        return _cmd_list(args)
    if action == "remove":
        return _cmd_remove(args, _config)
    logger.error("Unknown login action: %s", action)
    return 1


if __name__ == "__main__":
    raise SystemExit(login_command(argparse.Namespace(), {}))
