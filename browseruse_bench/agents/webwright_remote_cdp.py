"""Remote CDP environment for Webwright.

Webwright's bundled ``local_browser`` environment assumes a local Chrome CDP
HTTP endpoint and uses /json/* probes. Browseruse-bench owns browser lifecycle
through backends like Lexmount, which provide a ready browser-level websocket.
This environment plugs that websocket into Webwright without local Chrome
startup or local CDP HTTP target management.
"""

from __future__ import annotations

from contextlib import suppress
from typing import Any

from webwright.environments.local_browser import (
    LocalBrowserEnvironment,
    LocalBrowserEnvironmentConfig,
)


class RemoteCDPEnvironmentConfig(LocalBrowserEnvironmentConfig):
    """Config for a browseruse-bench managed remote CDP session."""

    browser_mode: str = "local_cdp"
    remote_cdp_url: str = ""
    remote_cdp_new_page: bool = True
    remote_cdp_close_page_on_exit: bool = False


class RemoteCDPEnvironment(LocalBrowserEnvironment):
    """Webwright live-browser environment backed by a remote CDP websocket."""

    def __init__(self, *, config_class: type = RemoteCDPEnvironmentConfig, **kwargs: Any):
        super().__init__(config_class=config_class, **kwargs)

    @property
    def remote_config(self) -> RemoteCDPEnvironmentConfig:
        return self.config

    def _browser_connected(self) -> bool:
        browser = self._browser
        if browser is None:
            return False
        is_connected = getattr(browser, "is_connected", None)
        if callable(is_connected):
            return bool(is_connected())
        return True

    def _page_open(self) -> bool:
        page = self._page
        if page is None:
            return False
        is_closed = getattr(page, "is_closed", None)
        if callable(is_closed):
            return not bool(is_closed())
        return True

    async def _prepare_async(self) -> None:
        await self._ensure_remote_browser()

    async def _ensure_remote_browser(self) -> None:
        from playwright.async_api import async_playwright

        if self._browser_connected() and self._context is not None and self._page_open():
            return

        old_playwright = self._playwright
        self._page = None
        self._local_cdp_page = None
        self._context = None
        self._browser = None
        self._playwright = None
        self._connected_over_cdp = False
        if old_playwright is not None:
            with suppress(RuntimeError, OSError, TimeoutError):
                await old_playwright.stop()

        cdp_url = self.remote_config.remote_cdp_url or self.config.local_cdp_url
        if not cdp_url:
            raise ValueError("remote_cdp_url is required for RemoteCDPEnvironment")

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.connect_over_cdp(cdp_url)
        self._connected_over_cdp = True

        self._context = (
            self._browser.contexts[0]
            if self._browser.contexts
            else await self._browser.new_context(
                viewport={
                    "width": self.config.browser_width,
                    "height": self.config.browser_height,
                }
            )
        )

        if self.remote_config.remote_cdp_new_page or not self._context.pages:
            self._page = await self._context.new_page()
            self._local_cdp_page = self._page
        else:
            self._page = self._context.pages[0]

        self._context.set_default_timeout(self.config.browser_timeout_ms)
        self._context.set_default_navigation_timeout(self.config.browser_navigation_timeout_ms)
        self._attach_page_listeners(self._page)
        if self.config.start_url:
            await self._page.goto(self.config.start_url, wait_until="domcontentloaded")

    async def _execute_async(self, action: dict[str, Any]) -> dict[str, Any]:
        await self._ensure_remote_browser()
        return await super()._execute_async(action)

    async def _close_async(self) -> None:
        page = self._local_cdp_page
        playwright = self._playwright

        self._page = None
        self._local_cdp_page = None
        self._context = None
        self._browser = None
        self._playwright = None
        self._connected_over_cdp = False
        self._local_cdp_process = None

        try:
            if page is not None and self.remote_config.remote_cdp_close_page_on_exit:
                with suppress(RuntimeError, OSError, TimeoutError):
                    await page.close()
        finally:
            if playwright is not None:
                await playwright.stop()
