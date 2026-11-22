"""One isolated Chromium context with bounded observations and action telemetry."""

from __future__ import annotations

import base64
import asyncio
import hashlib
import json
import time
import re
from contextlib import suppress
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from jit_ui.config.settings import Settings
from jit_ui.domain.models import ActionFrame, ActionSpec, ConditionSpec, ElementFingerprint, LocatorSpec, Observation
from jit_ui.tracing.network import NetworkTracer, redact_url


class SiteBlocked(RuntimeError):
    code = "SITE_BLOCKED"


class BrowserActionError(RuntimeError):
    code = "BROWSER_ACTION_FAILED"

    def __init__(self, message: str, frame: ActionFrame | None = None) -> None:
        super().__init__(message)
        self.frame = frame


def fingerprint_locators(element: ElementFingerprint) -> list[LocatorSpec]:
    result: list[LocatorSpec] = []
    if element.testid:
        result.append(LocatorSpec(strategy="testid", value=element.testid, tier=1))
    if element.role and element.name:
        result.append(LocatorSpec(strategy="role", value=element.role, name=element.name, tier=1))
    if element.label:
        result.append(LocatorSpec(strategy="label", value=element.label, tier=2))
    if element.placeholder:
        result.append(LocatorSpec(strategy="placeholder", value=element.placeholder, tier=2))
    if element.text and element.tag not in {"input", "textarea", "select"}:
        result.append(LocatorSpec(strategy="text", value=element.text, tier=2))
    if element.xpath:
        result.append(LocatorSpec(strategy="xpath", value=element.xpath, tier=3))
    if element.css:
        result.append(LocatorSpec(strategy="css", value=element.css, tier=4))
    return result


class BrowserSession:
    def __init__(self, settings: Settings, artifact_dir: Path, site: dict[str, Any]) -> None:
        self.settings = settings
        self.artifact_dir = Path(artifact_dir)
        self.site = site
        self.page: Any = None
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._cdp: Any = None
        self._tracer: NetworkTracer | None = None
        self._last_observation: Observation | None = None
        self._password_indices: set[int] = set()
        self._amazon_dismiss_indices: set[int] = set()
        self._observation_number = 0
        self.defer_navigation = False
        self.confirmation_handler = None
        self.manual_handler = None

    async def open(self) -> "BrowserSession":
        try:
            from playwright.async_api import async_playwright

            self.artifact_dir.mkdir(parents=True, exist_ok=True)
            self._playwright = await async_playwright().start()
            launch: dict[str, Any] = {"headless": self.settings.browser_headless}
            if self.settings.chromium_path:
                launch["executable_path"] = self.settings.chromium_path
            self._browser = await self._playwright.chromium.launch(**launch)
            self._context = await self._browser.new_context(viewport={"width": 1280, "height": 800}, locale="en-IN")
            self.page = await self._context.new_page()
            def document_response(response):
                if response.request.is_navigation_request() and response.frame == self.page.main_frame:
                    self.site["_document_status"] = response.status
            self.page.on("response", document_response)
            self.page.set_default_timeout(self.settings.condition_timeout_ms)
            self._cdp = await self._context.new_cdp_session(self.page)
            self._tracer = NetworkTracer(self._cdp)
            await self._tracer.start()
            if not self.defer_navigation:
                await self.page.goto(self.site["start_url"], wait_until="domcontentloaded", timeout=30000)
            if not self.defer_navigation and self.site.get("site_id") in {"shop", "admin"}:
                await self.page.locator("input,button,a,select,textarea").first.wait_for(state="visible", timeout=5000)
            elif not self.defer_navigation and self.site.get("site_id") == "amazon_in":
                from jit_ui.runtime.browser_helpers import temporary_page_error
                if not await temporary_page_error(self.page, self.site):
                    await self.page.locator("input,button,a,select").first.wait_for(state="attached", timeout=5000)
            await self._check_blocked()
            return self
        except SiteBlocked:
            await self.close()
            raise
        except Exception as exc:
            await self.close()
            raise BrowserActionError("Unable to open Chromium or the target site. Install Playwright Chromium and verify the local testbed.") from exc

    async def close(self) -> None:
        if self._cdp:
            with suppress(Exception):
                await asyncio.wait_for(self._cdp.detach(), self.settings.browser_close_timeout_seconds)
        if self._browser:
            with suppress(Exception):
                await asyncio.wait_for(self._browser.close(), self.settings.browser_close_timeout_seconds)
        if self._playwright:
            with suppress(Exception):
                await asyncio.wait_for(self._playwright.stop(), self.settings.browser_close_timeout_seconds)
        self.page = None

    async def _check_blocked(self) -> None:
        from jit_ui.runtime.browser_helpers import SiteBlockedError, detect_blocking

        resumed = False
        while True:
            try:
                await detect_blocking(self.page, self.site)
                return
            except SiteBlockedError as exc:
                if self.manual_handler is None:
                    message = str(exc) + (" Enable 'Show browser for manual steps' to complete verification and resume." if self.site.get("site_id") == "web" else "")
                    raise SiteBlocked(message) from exc
                message = ("Verification is still pending. " if resumed else "") + str(exc)
                await self._manual_step(message, "Verification still pending" if resumed else "Manual verification required")
                resumed = True

    async def _manual_step(self, message, label="Manual verification required"):
        self._observation_number += 1
        path = self.artifact_dir / f"manual-step-{self._observation_number:03d}.jpg"
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        await self.page.screenshot(path=str(path), type="jpeg", quality=65,
                                   mask=[self.page.locator('input[type="password"],input[autocomplete^="cc-"],input[name="email"]')])
        await self.manual_handler({"manual": True, "label": label, "url": redact_url(self.page.url),
                                   "details": message + " Complete the required step in the browser window, then resume.",
                                   "screenshot_path": str(path)})

    def _redact(self, value: str) -> str:
        for secret in sorted(self.site.get("credentials", {}).values(), key=lambda value: len(str(value)), reverse=True):
            if isinstance(secret, str) and secret:
                value = value.replace(secret, "[credential]")
        return value

    async def _stable_read(self, reader):
        """Retry read-only observations when a normal navigation replaces the document."""
        for attempt in range(3):
            try:
                return await reader()
            except Exception as exc:
                if (attempt == 2 or self.page.is_closed()
                        or not any(text in str(exc).lower() for text in ("execution context was destroyed", "cannot find context"))):
                    raise
                with suppress(Exception):
                    await self.page.wait_for_load_state("domcontentloaded", timeout=5000)
                await self.page.wait_for_timeout(100)

    async def _state(self) -> tuple[str, str]:
        return await self._stable_read(self._state_once)

    async def _state_once(self) -> tuple[str, str]:
        state = await self.page.evaluate("""() => {
          const parts = [location.href, String(scrollX), String(scrollY)];
          const nodes = [];
          const walk = root => {
            for (const el of root.children || []) {
              if (nodes.length >= 3000) return;
              if (el.matches('[data-jit-ui-overlay],script,style,noscript')) continue;
              nodes.push(el);
              if (el.shadowRoot) walk(el.shadowRoot);
              walk(el);
            }
          };
          walk(document.body);
          for (const el of nodes) {
            if (el.closest('[data-jit-ui-overlay]') || ['SCRIPT','STYLE','NOSCRIPT'].includes(el.tagName)) continue;
            const text = [...el.childNodes].filter(n=>n.nodeType===Node.TEXT_NODE).map(n=>n.textContent).join(' ').replace(/\\s+/g,' ').trim().slice(0,120);
            const value = ['INPUT','TEXTAREA','SELECT'].includes(el.tagName) && el.type!=='password' && !['username','email'].includes(el.name) ? String(el.value||'').slice(0,160) : '';
            const goal = ['data-query','data-max-price','data-sort-by','data-order-id','data-product-name','data-price','data-rating'].map(key=>el.getAttribute(key)||'').join('|');
            const control = ['aria-valuenow','aria-valuetext','aria-valuemin','aria-valuemax','aria-checked','aria-selected'].map(key=>el.getAttribute(key)||'').join('|');
            parts.push([el.tagName,el.id,el.getAttribute('role')||'',el.getAttribute('data-testid')||'',text,value,goal,control].join('|'));
          }
          return parts.join('\\n');
        }""")
        return redact_url(self.page.url), hashlib.sha256(self._redact(state).encode()).hexdigest()

    async def _goal_attributes(self) -> dict[str, str | None]:
        state: dict[str, str | None] = {}
        if self.site.get("site_id") == "shop":
            results = self.page.get_by_test_id("search-results")
            if await results.count() == 1:
                for reference, attribute in {"product_name": "data-query", "max_price": "data-max-price", "sort_by": "data-sort-by"}.items():
                    state[reference] = await results.get_attribute(attribute)
        elif self.site.get("site_id") == "admin":
            detail = self.page.get_by_test_id("order-detail")
            if await detail.count() == 1:
                state["order_id"] = await detail.get_attribute("data-order-id")
            search = self.page.get_by_role("searchbox", name="Search order ID", exact=True)
            state["admin_search_present"] = "true" if await search.count() == 1 else "false"
            status = self.page.get_by_test_id("order-status")
            if await status.count() == 1:
                state["order_status"] = (await status.inner_text()).strip()
        return state

    def _observed_conditions(self, before: dict[str, str | None], after: dict[str, str | None], parameters: dict[str, Any]) -> list[ConditionSpec]:
        conditions: list[ConditionSpec] = []
        marker = "search-results" if self.site.get("site_id") == "shop" else "order-detail"
        attributes = {"product_name": "data-query", "max_price": "data-max-price", "sort_by": "data-sort-by", "order_id": "data-order-id"}
        for reference, attribute in attributes.items():
            actual = after.get(reference)
            if actual is not None and actual != before.get(reference) and reference in parameters and str(parameters[reference]) == str(actual):
                locator = LocatorSpec(strategy="testid", value=marker)
                conditions.append(ConditionSpec(kind="attribute_equals", locator=locator, locators=[locator], attribute=attribute, value_ref=reference))
        if before.get("admin_search_present") == "false" and after.get("admin_search_present") == "true":
            locator = LocatorSpec(strategy="role", value="searchbox", name="Search order ID")
            conditions.append(ConditionSpec(kind="visible", locator=locator, locators=[locator]))
        if after.get("order_status") == "Shipped" and before.get("order_status") != "Shipped":
            locator = LocatorSpec(strategy="testid", value="order-status")
            conditions.append(ConditionSpec(kind="text_equals", locator=locator, locators=[locator], expected="Shipped"))
        return conditions

    async def observe(self) -> Observation:
        return await self._stable_read(self._observe_once)

    async def _observe_once(self) -> Observation:
        await self._check_blocked()
        from jit_ui.runtime.browser_helpers import temporary_page_error
        observation = Observation(url=redact_url(self.page.url), page_issue=await temporary_page_error(self.page, self.site))
        self._observation_number += 1
        script = Path(__file__).with_name("som_injector.js").read_text(encoding="utf-8")
        from jit_ui.runtime.browser_helpers import AMAZON_CONTROL_INFO_JS, SLIDER_INFO_JS, read_media_state
        from jit_ui.browser.overlay_context import OVERLAY_CONTEXT_JS

        script = script.replace("/* JIT_AMAZON_CONTROL_INFO */ null",
                                AMAZON_CONTROL_INFO_JS if self.site.get("site_id") == "amazon_in" else "null")
        script = script.replace("/* JIT_OVERLAY_CONTEXT */ null", OVERLAY_CONTEXT_JS)
        script = script.replace("/* JIT_SLIDER_INFO */ null", SLIDER_INFO_JS)
        try:
            observation.aria_summary = self._redact((await self.page.locator("body").aria_snapshot(timeout=5000))[:14000])
        except Exception:
            pass
        observation.media = await read_media_state(self.page)
        observation.submitted_searches = list(self.site.get("_submitted_searches", []))
        path = self.artifact_dir / f"observation-{self._observation_number:03d}.jpg"
        try:
            for attempt in range(6):
                raw = await self.page.evaluate(script, {"limit": 80, "epoch": observation.observation_id, "omit_changed": attempt >= 3})
                screenshot = await self.page.screenshot(type="jpeg", quality=65, full_page=False,
                    animations="disabled", caret="hide", mask=[self.page.locator('input[type="password"], input[autocomplete="username"], input[name="username"], input[name="email"], input[autocomplete^="cc-"], .demo-credentials, .lab-dock')])
                capture = await self.page.evaluate(script, {"limit": 80, "epoch": observation.observation_id, "validate": True, "details": True})
                if capture["stable"]:
                    if capture["omitted_count"]:
                        observation.capture_note = f"{capture['omitted_count']} changing controls were temporarily omitted. Their unnumbered appearance is not actionable. Wait for a new observation if a required control is missing."
                    break
                if attempt == 5:
                    raise BrowserActionError("The page controls changed during screenshot capture. Retry when the page settles.")
                await self.page.wait_for_timeout(min(1000, 100 * (attempt + 1)))
            self._password_indices = {e["mark"] for e in raw if e.get("attributes", {}).get("type") == "password"}
            self._amazon_dismiss_indices = {e["mark"] for e in raw if e.get("amazonDismiss")}
            fields = {"tag":"", "role":"", "name":"", "testid":"", "label":"", "placeholder":"", "text":"", "css":"", "xpath":"", "bounds":{}, "goal_control":None, "context":"", "readonly":False, "options":[], "current_value":None, "checked":None,
                      "input_type":"", "parameter_hint":"", "value_text":"", "value_min":None, "value_max":None, "value_step":None}
            observation.elements = [ElementFingerprint(index=e["mark"], **{key: e.get(key, default) for key, default in fields.items()}) for e in raw]
            if not observation.aria_summary:
                observation.aria_summary = json.dumps([{ "role": e.role, "name": e.name, "index": e.index } for e in observation.elements], ensure_ascii=False)
            observation.url = redact_url(self.page.url)
            path.write_bytes(screenshot)
            observation.screenshot_base64 = base64.b64encode(screenshot).decode("ascii")
            observation.screenshot_path = str(path)
        finally:
            await self.page.evaluate("document.querySelector('[data-jit-ui-overlay]')?.remove()")
        _, observation.state_hash = await self._state()
        path.with_suffix(".json").write_text(observation.model_dump_json(exclude={"screenshot_base64"}), encoding="utf-8")
        self._last_observation = observation
        return observation

    def _safe_action(self, action: ActionSpec, element: ElementFingerprint | None) -> ActionSpec:
        recorded = action.model_copy(deep=True)
        recorded.target_index = None
        if element:
            recorded.locators = fingerprint_locators(element)
            if recorded.action == "click" and element.index in self._amazon_dismiss_indices:
                recorded.optional = True
        credentials = self.site.get("credentials", {})
        for key in ("username", "password"):
            if recorded.action == "fill" and (recorded.value_ref == key or (credentials.get(key) and recorded.value == credentials[key])):
                recorded.value_ref, recorded.value = key, None
        if element and element.index in self._password_indices and recorded.action == "fill":
            recorded.value_ref, recorded.value = "password", None
        return recorded

    async def execute(self, action: ActionSpec, parameters: dict[str, Any]) -> ActionFrame:
        from jit_ui.runtime.browser_helpers import ActionOutsideGoalError, SiteBlockedError, apply_action

        await self._check_blocked()
        element: ElementFingerprint | None = None
        execution = action.model_copy(deep=True)
        if action.target_index is not None and not action.locators:
            if self._last_observation is None:
                raise BrowserActionError("An indexed action requires a current observation.")
            element = next((e for e in self._last_observation.elements if e.index == action.target_index), None)
            if element is None:
                raise BrowserActionError("The proposed element index is absent from the current observation.")
            execution.locators = [LocatorSpec(strategy="css", value=f'[data-jit-ui-mark="{self._last_observation.observation_id}:{element.index}"]', tier=1)]
            if action.action == "adjust":
                execution.locators.extend(fingerprint_locators(element))
        before_url, before_hash = await self._state()
        before_goal = await self._goal_attributes()
        recorded = self._safe_action(action, element)
        cursor = self._tracer.begin_action() if self._tracer else 0
        started = time.perf_counter()
        frame = ActionFrame(action=recorded, element=element, before_url=before_url, before_hash=before_hash,
            screenshot_path=self._last_observation.screenshot_path if self._last_observation else None)
        try:
            if recorded.action == "handoff":
                if self.manual_handler is None:
                    raise SiteBlocked("The model requested a manual step. Enable 'Show browser for manual steps' and retry.")
                await self._manual_step(recorded.intent or "Complete the required manual step.", "Manual step requested by the model")
                await self._check_blocked()
                frame.after_url, frame.after_hash = await self._state()
                return frame
            if self.site.get("site_id") == "web" and recorded.action in {"click", "press"}:
                from jit_ui.runtime.browser_helpers import find_target
                target = await find_target(self.page, execution.model_dump(mode="json").get("locators", []), self.settings.locator_timeout_ms)
                label = await target.evaluate("""(element, operation) => {
                    const control = element.closest('button,a,input,[role="button"]') || element;
                    const submit = control.closest('form')?.querySelector('button[type="submit"],input[type="submit"]');
                    const selected = operation === 'press' && control.tagName === 'INPUT' && control.type !== 'submit' ? (submit || control) : control;
                    return (selected.getAttribute('aria-label') || selected.innerText || selected.value || '').trim();
                }""", recorded.action)
                sensitive = bool(re.fullmatch(r"(?:book(?:\s+now)?|confirm(?:\s+(?:booking|order|purchase|reservation)(?:\s+.*)?)?|pay(?:\s+.*)?|purchase(?:\s+.*)?|buy\s+now(?:\s+.*)?|place\s+order(?:\s+.*)?|send(?:\s+(?:message|email))?|submit\s+(?:booking|order|payment))", label, re.I))
                if sensitive or recorded.requires_confirmation:
                    recorded.requires_confirmation, recorded.effect = True, "mutation"
                    if self.confirmation_handler is None:
                        raise BrowserActionError("This final action requires user review and approval.")
                    review = {"label": label, "url": self.page.url, "details": self._redact((await self.page.locator('body').inner_text())[:2500]),
                              "action": recorded.model_dump(mode="json"), "screenshot_path": frame.screenshot_path}
                    await self.confirmation_handler(review)
            await apply_action(self.page, execution.model_dump(mode="json"), parameters, self.site, timeout_ms=self.settings.locator_timeout_ms)
            if self._tracer and recorded.action in {"click", "press", "select", "adjust", "goto"}:
                await self._tracer.settle(cursor)
            await self._check_blocked()
            frame.after_url, frame.after_hash = await self._state()
            from jit_ui.runtime.browser_helpers import record_search_evidence
            record_search_evidence(self.page, parameters, self.site)
            if recorded.action in {"fill", "select"} and recorded.locators:
                frame.post_conditions = [ConditionSpec(kind="value_equals", locator=recorded.locators[0], locators=recorded.locators, expected=recorded.value, value_ref=recorded.value_ref)]
            elif recorded.action == "adjust" and recorded.locators:
                direction = "number_at_least" if recorded.value_ref == "min_price" else "number_at_most" if recorded.value_ref == "max_price" else "number_at_least"
                frame.post_conditions = [ConditionSpec(kind=direction, locator=recorded.locators[0], locators=recorded.locators, expected=recorded.value, value_ref=recorded.value_ref)]
                if recorded.value_ref not in {"min_price", "max_price"}:
                    frame.post_conditions.append(frame.post_conditions[0].model_copy(update={"kind": "number_at_most"}))
            elif frame.after_url != frame.before_url:
                frame.post_conditions = [ConditionSpec(kind="url_contains", expected=urlsplit(frame.after_url).path)]
            frame.post_conditions.extend(self._observed_conditions(before_goal, await self._goal_attributes(), parameters))
            return frame
        except SiteBlocked:
            raise
        except SiteBlockedError as exc:
            if self.manual_handler is None:
                raise SiteBlocked(str(exc) + (" Enable 'Show browser for manual steps' to complete verification and resume." if self.site.get("site_id") == "web" else "")) from exc
            await self._check_blocked()
            frame.after_url, frame.after_hash = await self._state()
            if recorded.action in {"fill", "select"} and recorded.locators:
                frame.post_conditions = [ConditionSpec(kind="value_equals", locator=recorded.locators[0], locators=recorded.locators, expected=recorded.value, value_ref=recorded.value_ref)]
            elif frame.after_url != frame.before_url:
                frame.post_conditions = [ConditionSpec(kind="url_contains", expected=urlsplit(frame.after_url).path)]
            return frame
        except Exception as exc:
            frame.error = self._redact(str(exc))[:1500]
            with suppress(Exception):
                frame.after_url, frame.after_hash = await self._state()
            failure = BrowserActionError(frame.error, frame)
            if isinstance(exc, ActionOutsideGoalError):
                failure.code = exc.code
            raise failure from exc
        finally:
            frame.duration_ms = (time.perf_counter() - started) * 1000
            frame.network = self._tracer.finish_action(cursor) if self._tracer else []
            frame.safe_to_prune = recorded.effect == "read" and recorded.action in {"scroll", "wait"}

    async def verify_goal(self, goal_id: str, parameters: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        from jit_ui.runtime.browser_helpers import verify_goal

        await self._check_blocked()
        return await self._stable_read(lambda: verify_goal(self.page, self.site, goal_id, parameters))

    async def check_conditions(self, conditions: list[ConditionSpec], parameters: dict[str, Any]) -> bool:
        from jit_ui.runtime.browser_helpers import check_conditions

        return await self._stable_read(lambda: check_conditions(self.page, [c.model_dump(mode="json") for c in conditions], parameters, self.site))
