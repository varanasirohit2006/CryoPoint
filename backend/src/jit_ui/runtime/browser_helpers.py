"""Shared deterministic browser semantics, also bundled into exported scripts.

Keep this module independent of jit_ui, providers, storage, and Pydantic.
"""

import os
import re
import ipaddress
import math
import time
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse, unquote_plus, urlsplit, urlunsplit


NETWORK_NAVIGATION_ERRORS = (
    "ERR_NAME_NOT_RESOLVED",
    "ERR_NAME_RESOLUTION_FAILED",
    "ERR_CONNECTION_REFUSED",
    "ERR_CONNECTION_TIMED_OUT",
    "ERR_INTERNET_DISCONNECTED",
)

DOMAIN_TYPO_MAP = {
    "youtbe.com": "youtube.com",
    "yotube.com": "youtube.com",
    "uoutube.com": "youtube.com",
    "youtub.com": "youtube.com",
    "goole.com": "google.com",
    "gogle.com": "google.com",
    "gooogle.com": "google.com",
    "amzon.com": "amazon.com",
    "amazn.com": "amazon.com",
    "amazone.com": "amazon.com",
    "amzon.in": "amazon.in",
    "amazn.in": "amazon.in",
    "amazone.in": "amazon.in",
    "flikart.com": "flipkart.com",
    "flikart.in": "flipkart.in",
    "githb.com": "github.com",
    "wikpedia.org": "wikipedia.org",
    "wikpedia.com": "wikipedia.org",
}


def correct_domain_typo(host: str) -> str:
    if not host:
        return host
    host_lower = host.lower()
    if host_lower in DOMAIN_TYPO_MAP:
        return DOMAIN_TYPO_MAP[host_lower]
    for typo, corrected in DOMAIN_TYPO_MAP.items():
        if host_lower.endswith("." + typo):
            prefix = host_lower[:-len(typo)]
            return prefix + corrected
    return host


def extract_net_error_code(exc: Exception) -> str | None:
    text = str(exc)
    for err in NETWORK_NAVIGATION_ERRORS:
        if err in text:
            match = re.search(r"(?:net::)?(ERR_[A-Z_]+)", text)
            return f"net::{match.group(1)}" if match else f"net::{err}"
    return None


class SiteBlockedError(RuntimeError):
    """The target requires authentication/challenge handling outside this goal."""


class StepExecutionError(RuntimeError):
    """A locator/action cannot be completed deterministically."""


class ActionOutsideGoalError(StepExecutionError):
    code = "ACTION_OUTSIDE_GOAL"


SLIDER_INFO_JS = r"""element => {
    const native = element.matches('input[type=range]');
    const number = (value, fallback=null) => { const n = Number(value); return value !== null && value !== '' && Number.isFinite(n) ? n : fallback; };
    const parent = e => e.parentElement || e.getRootNode()?.host;
    const identity = [element.getAttribute('aria-label'),element.id,element.getAttribute('name'),element.getAttribute('aria-labelledby')].join(' ').toLowerCase();
    let context = '', currencies = [];
    for (let e=parent(element), depth=0; e && depth<6; e=parent(e), depth++) {
        const text = (e.innerText || '').replace(/\s+/g,' ').trim();
        const money = [...text.matchAll(/[₹$€£]\s*\d[\d,]*(?:\.\d+)?\+?/g)].map(m=>m[0]);
        if (text.length < 600 && (money.length >= 2 || /\bprice\b/i.test(text))) {
            context = text;
            if (money.length >= 2) { currencies = money; break; }
        }
    }
    const price = /price/.test(identity) || /\bprice\b|[₹$€£]/i.test(context);
    const lower = /(?:^|[\W_])(min(?:imum)?|low(?:er)?|from)(?:$|[\W_])/.test(identity);
    const upper = /(?:^|[\W_])(max(?:imum)?|high(?:er)?|upper|to)(?:$|[\W_])/.test(identity);
    const hint = price && lower ? 'min_price' : price && upper ? 'max_price' : '';
    const value = number(native ? element.value : element.getAttribute('aria-valuenow'));
    const minimum = number(native ? element.min : element.getAttribute('aria-valuemin'), native ? 0 : null);
    const maximum = number(native ? element.max : element.getAttribute('aria-valuemax'), native ? 100 : null);
    const step = number(element.getAttribute('step'), 1);
    let text = element.getAttribute('aria-valuetext') || '';
    if (!text && /[₹$€£]/.test(element.getAttribute('aria-label') || '')) text = element.getAttribute('aria-label');
    if (!text && hint && currencies.length >= 2) text = currencies[hint === 'min_price' ? 0 : 1];
    if (!text && value !== null) text = String(value);
    const rect = element.getBoundingClientRect();
    let track = rect, thumb = rect;
    if (native && maximum > minimum && rect.width > rect.height*2) {
        const radius = Math.max(7,Math.min(12,rect.height/2));
        const x = rect.left + radius + (rect.width-2*radius)*(value-minimum)/(maximum-minimum);
        thumb = new DOMRect(x-radius,rect.top+rect.height/2-radius,radius*2,radius*2);
    } else if (!native) {
        for (let e=parent(element),depth=0;e&&depth<4;e=parent(e),depth++) {
            const r=e.getBoundingClientRect();
            if (r.width > rect.width*3 && r.height < 100) { track=r; break; }
        }
    }
    return {value,minimum,maximum,step,value_text:text,parameter_hint:hint,context,
        track:{x:track.x,y:track.y,width:track.width,height:track.height},
        thumb:{x:thumb.x,y:thumb.y,width:thumb.width,height:thumb.height}};
}"""


async def adjust_slider(page, locators, requested, reference, timeout_ms):
    """Seek a numeric slider value using trusted keys and observed UI values."""
    wanted = float(requested)
    if not math.isfinite(wanted):
        raise StepExecutionError("Slider values must be finite numbers.")
    target = await find_target(page, locators, timeout_ms)
    if not await target.evaluate("e => e.matches('input[type=range],[role=slider]')"):
        raise StepExecutionError("Adjust requires a slider control.")
    await target.focus()

    def displayed(info):
        if reference == "max_price" and info["value_text"].rstrip().endswith('+'):
            return math.inf
        return observed_number(info["value_text"])

    async def read_after_key(before):
        # Frameworks may replace the input or defer its displayed currency label.
        for attempt in range(5):
            await page.wait_for_timeout(40)
            current_target = await find_target(page, locators, timeout_ms)
            try:
                state = await current_target.evaluate(SLIDER_INFO_JS)
            except Exception:
                if attempt == 4:
                    raise
                continue
            if displayed(state) != before or attempt == 4:
                return current_target, state
        raise StepExecutionError("The slider was replaced before its value could be read.")

    previous = None
    use_jump = False
    for _ in range(80):
        target = await find_target(page, locators, timeout_ms)
        info = await target.evaluate(SLIDER_INFO_JS)
        actual = displayed(info)
        if actual is None:
            raise StepExecutionError("The slider does not expose a readable numeric value.")
        if actual == wanted:
            await target.press("Tab")
            return
        if previous and (previous[0]-wanted)*(actual-wanted) < 0 and not previous[1]:
            qualifies = actual >= wanted if reference == "min_price" else actual <= wanted if reference == "max_price" else False
            if qualifies:
                await target.press("Tab")
                return
            await target.press("ArrowDown" if actual > previous[0] else "ArrowUp")
            if reference in {"min_price", "max_price"}:
                target, state = await read_after_key(actual)
                restored = displayed(state)
                if restored is None or not (restored >= wanted if reference == "min_price" else restored <= wanted):
                    raise StepExecutionError("The slider could not maintain a bound satisfying the requested price.")
                await target.press("Tab")
                return
            raise StepExecutionError("The slider cannot represent the exact requested value.")
        increasing = actual < wanted
        key = ("PageUp" if increasing else "PageDown") if use_jump else ("ArrowUp" if increasing else "ArrowDown")
        await target.press(key)
        target, current = await read_after_key(actual)
        next_value = displayed(current)
        if next_value == actual or next_value is None:
            await target.press("ArrowRight" if increasing else "ArrowLeft")
            target, current = await read_after_key(actual)
            next_value = displayed(current)
            if next_value == actual or next_value is None:
                at_limit = info["value"] == (info["maximum"] if increasing else info["minimum"])
                if at_limit and (actual >= wanted if reference == "min_price" else actual <= wanted if reference == "max_price" else False):
                    await target.press("Tab")
                    return
                raise StepExecutionError("The slider did not respond to keyboard adjustment.")
        previous = (actual, use_jump)
        difference = abs(next_value-actual)
        use_jump = bool(difference and math.isfinite(difference) and abs(wanted-next_value) > difference*12 and (wanted-actual)*(wanted-next_value) > 0)
    raise StepExecutionError("The slider adjustment limit was reached without the requested value.")


# This classifier is shared with numbered observations and bundled exports.
AMAZON_CONTROL_INFO_JS = r"""element => {
    const control = element.closest('button,a,input,select,textarea,[role="button"],[role="textbox"],[role="searchbox"]') || element;
    const tag = control.tagName.toLowerCase();
    const type = (control.getAttribute('type') || '').toLowerCase();
    const labelled = (control.getAttribute('aria-labelledby') || '').split(/\s+/)
        .map(id => document.getElementById(id)?.textContent || '').join(' ').trim();
    const name = control.getAttribute('aria-label') || labelled || control.getAttribute('placeholder')
        || (tag === 'input' ? control.value : control.innerText) || control.getAttribute('title') || '';
    const label = name.replace(/\s+/g, ' ').trim().toLowerCase();
    const href = control.closest('a[href]')?.getAttribute('href') || '';
    const form = control.closest('form');
    const formAction = form?.getAttribute('action') || '';
    const url = value => { try { return new URL(value, location.href); } catch { return null; } };
    const searchURL = value => {
        if (!value || value.startsWith('#')) return false;
        const parsed = value && url(value);
        return !!parsed && parsed.origin === location.origin && (parsed.pathname === '/s' || parsed.pathname.startsWith('/s/'));
    };
    const restrictedURL = value => {
        const parsed = value && url(value);
        return !!parsed && /\/ap\/|\/(?:gp\/)?cart(?:\/|$)|checkout|\/gp\/(?:css|buy)(?:\/|$)|\/hz\/wishlist/i.test(parsed.pathname);
    };
    const publicURL = value => {
        if (!value || value.startsWith('#')) return false;
        const parsed = value && url(value);
        return !!parsed && parsed.origin === location.origin && !restrictedURL(value)
            && (parsed.pathname === '/' || parsed.pathname.startsWith('/ref=') || searchURL(value) || /\/dp\/|\/gp\/product\/|^\/b(?:\/|$)/.test(parsed.pathname));
    };
    const forbidden = ['add to cart','buy now','checkout','place order','sign in','signin','log in','subscribe','delete','add to wishlist','add to list','write a review']
        .some(term => label.includes(term)) || restrictedURL(href) || restrictedURL(formAction)
        || !!(formAction && url(formAction)?.origin !== location.origin)
        || !!control.closest('#nav-cart,#nav-link-accountList,#nav-orders,#nav-global-location-popover-link');
    const knownQuery = control.id === 'twotabsearchtextbox' || control.getAttribute('name') === 'field-keywords';
    const knownSubmit = ['nav-search-submit-button','nav-search-submit-text'].includes(control.id);
    const inSearch = knownQuery || knownSubmit || searchURL(formAction)
        || !!control.closest('#nav-search,#nav-search-bar-form');
    const overlay = control.closest('[role="dialog"],[aria-modal="true"],.a-popover,.a-modal,.a-popover-wrapper,#sp-cc,#hmenu-container');
    const dismissLabels = ['close','dismiss','cancel','not now','no thanks','maybe later','continue shopping',
        'continue browsing','accept cookies','accept all cookies','reject cookies','reject all cookies','continue without accepting'];
    const closeLabel = /^(close|dismiss)(?: (?:this |the )?(?:dialog|popup|pop-up|notification|notice|banner|window|menu|announcement|promotion|cookie notice))?$/;
    const isDismiss = (!!overlay && (dismissLabels.includes(label) || closeLabel.test(label)
        || control.matches('.a-popover-close,.a-button-close')))
        || (['continue shopping','continue browsing'].includes(label) && (tag === 'button' || type === 'submit'));
    const searchLink = searchURL(href);
    const priceInput = /low-price|high-price|min.*price|max.*price|lower.*price|upper.*price/i.test(control.id + ' ' + (control.name || ''));
    const queryInput = knownQuery || (inSearch && !priceInput && tag === 'input' && ['text','search',''].includes(type));
    const onResults = location.pathname === '/s' || location.pathname.startsWith('/s/');
    const sortControl = /s-result-sort-select|sort/i.test(control.id + ' ' + (control.name || '') + ' ' + label)
        || ['relevanceblender','price-asc-rank','price-desc-rank','review-rank','date-desc-rank'].includes(control.getAttribute('data-value'))
        || !!control.closest('.a-dropdown-container')?.querySelector('select[name="s"],#s-result-sort-select');
    const filterControl = priceInput || (inSearch && tag === 'select') || (onResults && !!control.closest('#s-refinements,#filters,#search'))
        || (searchURL(formAction) && !control.closest('#nav-search,#nav-search-bar-form'));
    const submitControl = knownSubmit || (inSearch && !filterControl && ((tag === 'input' && type === 'submit')
        || (tag === 'button' && ['','submit'].includes(type))));
    const productLink = !!href && publicURL(href) && /\/dp\/|\/gp\/product\//.test(url(href).pathname);
    const browseLink = !!href && publicURL(href);
    const allowed = !forbidden && (inSearch || searchLink || isDismiss || filterControl || sortControl || browseLink);
    const queryField = document.getElementById('twotabsearchtextbox')
        || form?.querySelector('input[name="field-keywords"],input[name="k"],input[type="search"],input[type="text"]');
    const queryValue = queryField ? queryField.value : null;
    const searchQuery = searchLink ? (url(href).searchParams.get('k') || url(href).searchParams.get('field-keywords') || '') : null;
    const purpose = isDismiss ? 'dismiss' : queryInput ? 'query' : submitControl ? 'submit' : sortControl ? 'sort'
        : filterControl ? 'filter' : productLink ? 'product' : searchLink ? 'search_link' : browseLink ? 'browse' : null;
    return {tag,id:control.id,type,name,href,inSearch,isDismiss,forbidden,allowed,
        queryValue,searchQuery,purpose,observable:allowed && !!purpose};
}"""


def normalize_query(value):
    return " ".join(str(value).split()).casefold()


def resolve_value(spec, parameters, site):
    if spec.get("expected_template") is not None:
        def substitute(match):
            name = match.group(1)
            if name not in parameters:
                raise StepExecutionError("Missing completion parameter: " + name)
            return str(parameters[name])
        return re.sub(r"\{\{([^{}]+)\}\}", substitute, spec["expected_template"])
    reference = spec.get("value_ref")
    if reference:
        if reference in parameters:
            return parameters[reference]
        if reference in site.get("credentials", {}):
            return site["credentials"][reference]
        variable = site.get("credential_env", {}).get(reference)
        if variable and variable in os.environ:
            return os.environ[variable]
        raise StepExecutionError("Missing parameter or credential reference: " + reference)
    return spec.get("value", spec.get("expected"))


def make_locator(page, spec):
    strategy, value = spec["strategy"], spec["value"]
    exact = spec.get("exact", True)
    if strategy == "role":
        arguments = {"exact": exact}
        if spec.get("name"):
            arguments["name"] = spec["name"]
        return page.get_by_role(value, **arguments)
    if strategy == "testid":
        return page.get_by_test_id(value)
    if strategy == "label":
        return page.get_by_label(value, exact=exact)
    if strategy == "placeholder":
        return page.get_by_placeholder(value, exact=exact)
    if strategy == "text":
        return page.get_by_text(value, exact=exact)
    if strategy == "xpath":
        return page.locator("xpath=" + value)
    if strategy == "css":
        return page.locator(value)
    raise StepExecutionError("Unsupported locator strategy: " + strategy)


async def find_target(page, locators, timeout_ms=800):
    failures = []
    for spec in sorted(locators, key=lambda item: item.get("tier", 4)):
        target = make_locator(page, spec)
        try:
            if await target.count() == 0:
                await target.wait_for(state="visible", timeout=timeout_ms)
            if await target.count() != 1:
                failures.append(spec["strategy"] + ": ambiguous target")
                continue
            if not await target.is_visible() or not await target.is_enabled():
                failures.append(spec["strategy"] + ": not actionable")
                continue
            return target
        except Exception:
            failures.append(spec["strategy"] + ": unavailable")
    raise StepExecutionError("No unique actionable locator. " + "; ".join(failures))


async def detect_blocking(page, site=None):
    """Public shopping cannot authenticate; general web tasks still stop at verification challenges."""
    host = urlparse(page.url).hostname or ""
    amazon = host == "amazon.in" or host.endswith(".amazon.in")
    general_web = site is not None and site.get("site_id") == "web"
    if not amazon and not general_web:
        return
    address = page.url.lower()
    try:
        text = (await page.locator("body").inner_text(timeout=5000)).lower()
    except Exception:
        text = ""
    try:
        title = (await page.title()).lower()
    except Exception:
        title = ""
    if amazon and (any(fragment in address for fragment in ("validatecaptcha", "/ap/cvf"))
                   or ("/ap/signin" in address and not general_web)):
        raise SiteBlockedError("Amazon requires a CAPTCHA, authentication, or verification.")
    if general_web and host in {"google.com", "www.google.com", "google.co.in", "www.google.co.in"} and "/sorry/" in address:
        raise SiteBlockedError("Google requires an access verification challenge.")
    if any(fragment in title for fragment in (
        "robot check", "just a moment...", "attention required! | cloudflare", "security challenge",
        "human verification", "are you a robot", "i am not a robot", "i'm not a robot", "captcha", "ddos protection",
    )):
        if "robot" in title or "captcha" in title:
            raise SiteBlockedError("The website presented an 'I am not a robot' verification challenge." if general_web else "Amazon presented an 'I am not a robot' verification challenge.")
        raise SiteBlockedError("The website presented an access verification challenge." if general_web else "Amazon presented an access verification challenge.")
    if any(fragment in text for fragment in (
        "verify that you're not a robot", "verify that you are not a robot", "verify you are not a robot",
        "verify you're not a robot", "i'm not a robot", "i am not a robot", "i am not robot", "i'm not robot",
        "enter the characters you see", "robot check",
        "sorry, we just need to make sure you're not a robot", "automated access",
        "verify you are human", "verify that you are human", "confirm you are human",
        "confirm that you are human", "press and hold to show you're not a robot",
        "press & hold to confirm you are human", "unusual traffic from your computer network",
        "check the box to prove you're human", "complete the security check",
    )):
        if any(term in text for term in ("robot", "characters you see")):
            raise SiteBlockedError("The website presented an 'I am not a robot' verification challenge." if general_web else "Amazon presented an 'I am not a robot' verification challenge.")
        raise SiteBlockedError("The website presented an access verification challenge." if general_web else "Amazon presented an access verification challenge.")


async def temporary_page_error(page, site=None):
    """Observe availability failures without treating them as authentication."""
    if not page.url.startswith(("http://", "https://")):
        return None
    text = " ".join((await page.locator("body").inner_text(timeout=5000)).split())
    title = await page.title()
    headings = await page.locator("h1,h2").evaluate_all("elements => elements.slice(0,3).map(e=>e.innerText).join(' ')")
    status = (site or {}).get("_document_status")
    error_heading = re.search(r"^(?:error\s*)?(?:502|503|504)\b|^(?:service (?:temporarily )?unavailable|bad gateway|gateway timeout)\b", title.strip(), re.I)
    error_heading = error_heading or re.fullmatch(r"\s*(?:(?:error\s*)?(?:502|503|504)\s*[:\-]?\s*)?(?:service (?:temporarily )?unavailable|bad gateway|gateway timeout)\s*", headings, re.I)
    busy_page = (len(text) < 2000 and bool(re.search(r"traffic is piling up|it's rush hour", text, re.I))
                 and await page.locator('input:not([type="hidden"]),select,textarea').count() == 0)
    if status not in {502, 503, 504} and not error_heading and not busy_page:
        return None
    return {"code": "TEMPORARY_SITE_ERROR", "message": "The website returned a temporary server/traffic error. This page does not require login or manual verification.",
            "url": page.url, "status": status, "title": title, "details": text[:600]}


def permitted_url(address, site):
    parsed = urlparse(address)
    origin = parsed.scheme + "://" + parsed.netloc
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username is not None or parsed.password is not None:
        return False
    if site.get("site_id") != "web":
        return origin == site.get("allowed_origin") or origin in (site.get("allowed_origins") or [])
    explicit = site.get("allowed_origin") or origin_of(site.get("start_url", ""))
    allowed_origins = set(site.get("allowed_origins") or [])
    if explicit:
        allowed_origins.add(explicit)
    start = site.get("start_url") or site.get("target_url")
    if start:
        allowed_origins.add(origin_of(start))
    if origin in allowed_origins:
        return True
    host = parsed.hostname.lower().rstrip(".")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        return False
    try:
        address_ip = ipaddress.ip_address(host)
        return address_ip.is_global
    except ValueError:
        return "." in host and not re.fullmatch(r"[\d.]+|0x[\da-f]+", host)


def origin_of(address):
    parsed = urlparse(address)
    return parsed.scheme + "://" + parsed.netloc


AMAZON_URL_PARAMETERS = {"k": "query", "field-keywords": "query", "low-price": "min_price", "high-price": "max_price", "s": "sort_by"}
URL_PARAMETER_BINDINGS = {**AMAZON_URL_PARAMETERS, "q": "query", "query": "query", "language": "language", "hl": "language"}
LANGUAGE_NAMES = {"hindi": "hi", "english": "en", "tamil": "ta", "telugu": "te", "kannada": "kn",
                  "malayalam": "ml", "bengali": "bn", "marathi": "mr"}
AMAZON_LANGUAGE_LOCALES = {key: key + "_IN" for key in ("hi", "en", "ta", "te", "kn", "ml", "bn", "mr")}


def normalize_language(value):
    text = str(value or "").strip().casefold().replace("_", "-")
    return LANGUAGE_NAMES.get(text, text.split("-")[0])


def public_amazon_path(path):
    return (path in ("", "/", "/s", "/b") or path.startswith("/ref=") or path.startswith("/s/") or path.startswith("/b/")
            or bool(re.search(r"/dp/|/gp/product/", path))) and not re.search(r"/ap/|/cart|checkout|/gp/buy", path)


def parameterize_search_navigation(action, parameters):
    result = dict(action)
    if result.get("action") != "goto" or not result.get("url"):
        return result
    parsed = urlparse(result["url"])
    query = parse_qs(parsed.query, keep_blank_values=True)
    refs = dict(result.get("url_refs") or {})
    for key in ("q", "query", "language", "hl"):
        reference = URL_PARAMETER_BINDINGS[key]
        if key not in query or parameters.get(reference) is None:
            continue
        actual, expected = query[key][0], parameters[reference]
        matching = normalize_language(actual) == normalize_language(expected) if reference == "language" else normalize_query(actual) == normalize_query(expected)
        if matching:
            refs[key] = reference
            del query[key]
    result["url"] = urlunparse(parsed._replace(query=urlencode(query, doseq=True)))
    result["url_refs"] = refs
    if parsed.path != "/s" and not parsed.path.startswith("/s/"):
        return result
    for key, reference in AMAZON_URL_PARAMETERS.items():
        if parameters.get(reference) is None or key not in query:
            continue
        actual, expected = query[key][0], parameters[reference]
        try:
            matches = float(actual) == float(expected) if reference in {"min_price", "max_price"} else normalize_query(actual) == normalize_query(expected)
        except (ValueError, TypeError):
            matches = False
        if matches:
            refs[key] = reference
            del query[key]
    # Price facets use paise. Convert their variable bounds to public rupee URL parameters.
    if "rh" in query:
        facets = query["rh"][0].split(",")
        kept = []
        for facet in facets:
            match = re.fullmatch(r"p_36:(\d*)-(\d*)", facet)
            if match and any(parameters.get(key) is not None for key in ("min_price", "max_price")):
                lower, upper = match.groups()
                valid = ((not lower and parameters.get("min_price") is None) or (lower and parameters.get("min_price") is not None and float(lower) / 100 == float(parameters["min_price"])))
                valid = valid and ((not upper and parameters.get("max_price") is None) or (upper and parameters.get("max_price") is not None and float(upper) / 100 == float(parameters["max_price"])))
                if valid:
                    for key, reference in (("low-price", "min_price"), ("high-price", "max_price")):
                        if parameters.get(reference) is not None:
                            refs[key] = reference
                    continue
            kept.append(facet)
        if kept:
            query["rh"] = [",".join(kept)]
        else:
            del query["rh"]
    result["url"] = urlunparse(parsed._replace(query=urlencode(query, doseq=True)))
    result["url_refs"] = refs
    return result


def navigation_url(action, parameters, site):
    address = urljoin(site["start_url"], action.get("url") or site["start_url"])
    parsed = urlparse(address)
    query = parse_qs(parsed.query, keep_blank_values=True)
    for key, reference in (action.get("url_refs") or {}).items():
        bindings = URL_PARAMETER_BINDINGS if site.get("site_id") == "web" else AMAZON_URL_PARAMETERS
        if bindings.get(key) != reference:
            raise StepExecutionError("Unsupported navigation parameter binding.")
        query.pop(key, None)
        value = parameters.get(reference)
        if value is not None:
            if reference == "language" and key == "language" and (parsed.hostname or "").lower() in {"amazon.in", "www.amazon.in"}:
                value = AMAZON_LANGUAGE_LOCALES.get(normalize_language(value))
                if value is None:
                    raise StepExecutionError("Unsupported Amazon language code.")
            query[key] = [str(value)]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


async def enforce_amazon_search(action, target, site, parameters=None):
    if site.get("site_id") != "amazon_in":
        return
    kind = action["action"]
    if kind in ("scroll", "wait"):
        return
    if kind == "goto":
        path = urlparse(action.get("url") or site["start_url"]).path
        if not public_amazon_path(path):
            raise ActionOutsideGoalError("Amazon navigation is limited to public shopping and product pages.")
        return
    information = await target.evaluate(AMAZON_CONTROL_INFO_JS)
    if information["forbidden"]:
        raise ActionOutsideGoalError("Account, cart, and purchase actions are outside the Amazon search goal.")
    if kind in ("fill", "press", "select", "adjust") and not (information["inSearch"] or information["purpose"] in {"filter", "sort"}):
        raise ActionOutsideGoalError("Use search or product-filter controls for this shopping task.")
    if kind == "click" and not information["allowed"]:
        label = str(information["name"]).strip()[:160]
        raise ActionOutsideGoalError("This Amazon control is outside product search and overlay dismissal: " + label)
    if parameters and "query" in parameters:
        submitting = ((kind == "click" and information["purpose"] == "submit")
                      or (kind == "press" and action.get("value") == "Enter" and information["purpose"] == "query"))
        if submitting and (information["queryValue"] is None
                           or normalize_query(information["queryValue"]) != normalize_query(parameters["query"])):
            raise ActionOutsideGoalError("Enter the requested query in the numbered search input before submitting the search.")
        if kind == "click" and information["purpose"] == "search_link" and normalize_query(information["searchQuery"]) != normalize_query(parameters["query"]):
            raise ActionOutsideGoalError("This suggested search does not match the requested query. Use the main search input and submit control.")


async def apply_action(page, action, parameters, site, timeout_ms=800):
    await detect_blocking(page, site)
    record_search_evidence(page, parameters, site)
    kind = action["action"]
    if kind == "handoff":
        raise StepExecutionError("A model-requested manual step must be completed in the dashboard.")
    if kind == "goto":
        address = navigation_url(action, parameters, site)
        if not permitted_url(address, site):
            raise StepExecutionError("Navigation outside the registered site is not permitted.")
        await enforce_amazon_search({**action, "url": address}, None, site)
        try:
            response = await page.goto(address, wait_until="domcontentloaded", timeout=30000)
        except Exception as exc:
            err_code = extract_net_error_code(exc)
            if err_code:
                hostname = urlsplit(address).hostname or address
                corrected_host = correct_domain_typo(hostname)
                if corrected_host and corrected_host != hostname:
                    parsed = urlsplit(address)
                    corrected_authority = corrected_host + (f":{parsed.port}" if parsed.port and parsed.port not in (80, 443) else "")
                    corrected_address = urlunsplit((parsed.scheme, corrected_authority, parsed.path or "/", parsed.query, parsed.fragment))
                    try:
                        if site.get("allowed_origins") is not None:
                            site["allowed_origins"].append(origin_of(corrected_address))
                        site["allowed_origin"] = origin_of(corrected_address)
                        site["target_url"] = corrected_address
                        if "start_url" in site:
                            site["start_url"] = corrected_address
                        action["url"] = corrected_address
                        response = await page.goto(corrected_address, wait_until="domcontentloaded", timeout=30000)
                    except Exception as retry_exc:
                        retry_code = extract_net_error_code(retry_exc) or err_code
                        raise StepExecutionError(
                            f"Unable to resolve domain '{hostname}' ({retry_code}). Check the URL spelling and your internet connection."
                        ) from retry_exc
                else:
                    raise StepExecutionError(
                        f"Unable to resolve domain '{hostname}' ({err_code}). Check the URL spelling and your internet connection."
                    ) from exc
            else:
                raise
        site["_document_status"] = response.status if response else None
        if site.get("site_id") == "amazon_in" and not await temporary_page_error(page, site):
            await page.locator("input,button,a,select").first.wait_for(state="attached", timeout=5000)
        await detect_blocking(page, site)
        record_search_evidence(page, parameters, site)
        return
    if page.url and page.url != "about:blank" and not permitted_url(page.url, site):
        raise StepExecutionError("The browser left the registered site.")
    if kind == "scroll":
        amount = int(action.get("value") or 500)
        await page.mouse.wheel(0, max(-2000, min(2000, amount)))
        return
    if kind == "wait" and not action.get("locators"):
        await page.wait_for_timeout(min(10000, max(0, int(action.get("value") or 250))))
        return
    try:
        target = await find_target(page, action.get("locators", []), timeout_ms)
    except StepExecutionError:
        if action.get("optional"):
            return
        raise
    if site.get("site_id") in {"shop", "admin"} and await target.evaluate("element => Boolean(element.closest('.lab-dock'))"):
        raise StepExecutionError("Testbed reset and mutation controls are outside the registered browser goal.")
    await enforce_amazon_search(action, target, site, parameters)
    if site.get("site_id") == "web" and (kind == "click" or (kind == "press" and action.get("value") == "Enter")):
        destination = await target.evaluate("""element => {
            const anchor = element.closest('a[href]');
            if (anchor) {
                const href = anchor.getAttribute('href') || anchor.getAttribute('xlink:href');
                return href !== null ? href : (typeof anchor.href === 'string' ? anchor.href : (anchor.href?.baseVal || null));
            }
            const form = element.form || element.closest('form');
            const submitting = element.matches('button:not([type]),button[type="submit"],input[type="submit"]')
                || element.matches('input,textarea');
            if (submitting) {
                const formaction = element.getAttribute && element.getAttribute('formaction');
                if (formaction !== null) return formaction;
            }
            if (form && submitting) {
                const actionAttr = form.getAttribute('action');
                if (actionAttr !== null) return actionAttr;
                if (typeof form.action === 'string') return form.action;
                return '';
            }
            return null;
        }""")
        if destination is not None and isinstance(destination, str):
            dest = destination.strip()
            # In-page actions (empty href, # anchors, javascript:) and non-HTTP schemes
            # (mailto:, tel:, blob:) must never be blocked.
            if dest and not dest.startswith("#") and not dest.lower().startswith("javascript:"):
                base_url = page.url if page.url and page.url != "about:blank" else (site.get("start_url") or site.get("target_url") or "")
                resolved = urljoin(base_url, dest)
                parsed_dest = urlparse(resolved)
                if parsed_dest.scheme in ("http", "https") and not permitted_url(resolved, site):
                    raise StepExecutionError("This link or form points outside permitted public navigation.")
    value = resolve_value(action, parameters, site)
    activated_name = None
    if kind == "click" or (kind == "press" and value in {"Enter", "Space"}):
        activated_name = await target.evaluate("""(element, operation) => {
            if (operation === 'click' && !element.matches('button,a[href],[role=button],[role=link],[role=tab],[role=checkbox],[role=radio],input[type=submit],input[type=button],input[type=checkbox],input[type=radio]')) return '';
            const labels = (element.getAttribute('aria-labelledby') || '').split(/\\s+/).map(id=>document.getElementById(id)?.textContent || '').join(' ');
            return element.getAttribute('aria-label') || labels || [...(element.labels || [])].map(e=>e.textContent).join(' ')
                || element.innerText || element.getAttribute('title') || (element.matches('input[type=submit],input[type=button]') ? element.value : '') || '';
        }""", kind)
    if kind == "click":
        await target.click(timeout=5000)
    elif kind == "fill":
        await target.fill(str(value if value is not None else ""), timeout=5000)
    elif kind == "select":
        await target.select_option(str(value), timeout=5000)
    elif kind == "adjust":
        await adjust_slider(page, action["locators"], value, action.get("value_ref"), timeout_ms)
    elif kind == "press":
        await target.press(str(value or "Enter"), timeout=5000)
    elif kind == "wait":
        await target.wait_for(state="visible", timeout=5000)
    else:
        raise StepExecutionError("Unsupported action: " + kind)
    if activated_name:
        site.setdefault("_activated_controls", []).append({"action": kind, "name": normalize_query(activated_name)})
    record_search_evidence(page, parameters, site)


SEARCH_QUERY_KEYS = ("q", "query", "search_query", "k", "field-keywords")


def record_search_evidence(page, parameters, site):
    """Retain a matched search URL before a model-chosen result navigation."""
    expected = parameters.get("query", parameters.get("product_name"))
    if expected is None or not str(expected).strip() or (site.get("_document_status") or 0) >= 400:
        return
    address = urlparse(page.url)
    query = parse_qs(address.query)
    for key in SEARCH_QUERY_KEYS:
        actual = query.get(key, [None])[0]
        if actual is not None and normalize_query(actual) == normalize_query(expected):
            evidence = {"query": actual, "url": urlunparse(address._replace(query=urlencode({key:actual}), fragment=""))}
            searches = site.setdefault("_submitted_searches", [])
            if evidence not in searches:
                searches.append(evidence)
                del searches[:-20]


MEDIA_INFO_JS = r"""el => {
    if (!el.matches('video,audio')) return null;
    const rect=el.getBoundingClientRect(), style=getComputedStyle(el);
    const visible=rect.width>0 && rect.height>0 && rect.bottom>0 && rect.top<innerHeight
        && rect.right>0 && rect.left<innerWidth && style.visibility!=='hidden' && style.display!=='none';
    if (el.tagName==='VIDEO' && !visible) return null;
    return {tag:el.tagName.toLowerCase(),paused:el.paused,ended:el.ended,
        ready_state:el.readyState,time:Number.isFinite(el.currentTime)?el.currentTime:0,
        duration:Number.isFinite(el.duration)?el.duration:null,visible};
}"""

MEDIA_STATE_JS = r"""() => {
    const info = """ + MEDIA_INFO_JS + r""";
    const roots = [document], media = [];
    for (let i=0;i<roots.length;i++) {
        for (const el of roots[i].querySelectorAll('*')) if (el.shadowRoot) roots.push(el.shadowRoot);
        for (const el of roots[i].querySelectorAll('video,audio')) {
            const state=info(el); if (state) media.push(state);
        }
    }
    return media.slice(0,8);
}"""


async def read_media_state(page, candidates=None):
    if candidates:
        for spec in sorted(candidates, key=lambda item:item.get("tier", 4)):
            try:
                result = await make_locator(page, spec).evaluate_all("elements => { const info=" + MEDIA_INFO_JS + "; return elements.map(info).filter(Boolean); }")
                if result:
                    return result
            except Exception:
                continue
        return []
    result = []
    for index, frame in enumerate(getattr(page, "frames", None) or [page]):
        try:
            if frame is not getattr(page, "main_frame", page) and hasattr(frame, "frame_element"):
                if not await (await frame.frame_element()).is_visible():
                    continue
            result.extend({**item,"frame":index} for item in await frame.evaluate(MEDIA_STATE_JS))
        except Exception:
            continue
    return result[:8]


def observed_number(value):
    """Read a currency amount or a standalone number, never arbitrary prose/dates."""
    text = str(value if value is not None else "").strip()
    currency = re.search(r"[₹$€£]\s*([+-]?\d[\d,]*(?:\.\d+)?)", text)
    number = currency.group(1) if currency else text
    if not re.fullmatch(r"[+-]?(?:\d[\d,]*(?:\.\d+)?|\.\d+)", number):
        return None
    try:
        result = float(number.replace(",", ""))
        return result if math.isfinite(result) else None
    except ValueError:
        return None


async def numeric_condition_values(page, candidates):
    """The first visible locator alternative supplies all values to verify."""
    for spec in sorted(candidates, key=lambda item: item.get("tier", 4)):
        target = make_locator(page, spec)
        values = await target.evaluate_all("""elements => { const sliderInfo = """ + SLIDER_INFO_JS + """; return elements
            .filter(e => e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden'
                && getComputedStyle(e).visibility !== 'collapse')
            .map(e => { if (e.matches('input[type=range],[role=slider]')) {
                const info=sliderInfo(e); return info.value_text.trim().endsWith('+') ? null : info.value_text;
            } return /^(INPUT|SELECT|TEXTAREA)$/.test(e.tagName) ? e.value : e.innerText; }); }""")
        if values:
            return [observed_number(value) for value in values]
    return []


async def check_conditions(page, conditions, parameters, site):
    for condition in conditions:
        if hasattr(condition, "model_dump"):
            condition = condition.model_dump(mode="json")
        kind = condition["kind"]
        if condition.get("value_ref") or condition.get("expected_template") is not None:
            expected = resolve_value(condition, parameters, site)
        else:
            expected = condition.get("expected")
        if kind == "goal":
            if site.get("site_id") == "web":
                return False
            passed, _ = await verify_goal(page, site, site.get("goal_id", "search_products"), parameters)
            if not passed:
                return False
            continue
        if kind == "control_activated":
            wanted = normalize_query(expected)
            if not wanted or not any(item["name"] == wanted or re.search(r"(?<!\w)" + re.escape(wanted) + r"(?!\w)", item["name"])
                                     for item in site.get("_activated_controls", [])):
                return False
            continue
        if kind == "search_submitted":
            record_search_evidence(page, parameters, site)
            if expected is None or not str(expected).strip() or not any(normalize_query(item["query"]) == normalize_query(expected)
                    for item in site.get("_submitted_searches", [])):
                return False
            continue
        if kind == "media_playing":
            candidates = condition.get("locators") or ([condition["locator"]] if condition.get("locator") else [])
            deadline = time.monotonic() + max(0, site.get("condition_timeout_ms", 5000))/1000
            while True:
                media = await read_media_state(page, candidates)
                if any(not item["paused"] and not item["ended"] and item["ready_state"] >= 3 and item["time"] > 0 for item in media):
                    break
                remaining = deadline-time.monotonic()
                if remaining <= 0:
                    return False
                await page.wait_for_timeout(min(100, remaining*1000))
            continue
        if kind == "url_contains":
            address = unquote_plus(page.url) if condition.get("expected_template") is not None else page.url
            if str(expected) not in address:
                return False
            continue
        if kind == "query_equals":
            query = parse_qs(urlparse(page.url).query).get("k", [""])[0]
            if normalize_query(query) != normalize_query(expected):
                return False
            continue
        if kind == "url_query_equals":
            language = condition.get("value_ref") == "language"
            key = condition.get("attribute") or ("language" if language else "q")
            query = parse_qs(urlparse(page.url).query, keep_blank_values=True).get(key, [None])[0]
            normalize = normalize_language if language else normalize_query
            if query is None or normalize(query) != normalize(expected):
                return False
            continue
        if kind == "language_equals":
            actual = await page.locator("html").get_attribute("lang")
            if not actual or not normalize_language(expected) or normalize_language(actual) != normalize_language(expected):
                return False
            continue
        if kind == "title_contains":
            if expected is None or str(expected).casefold() not in (await page.title()).casefold():
                return False
            continue
        if kind not in {"visible", "attribute_equals", "value_equals", "text_equals", "text_contains", "number_at_most", "number_at_least"}:
            return False
        try:
            candidates = condition.get("locators") or ([condition["locator"]] if condition.get("locator") else [])
            if kind in {"number_at_most", "number_at_least"}:
                bound = observed_number(expected)
                values = await numeric_condition_values(page, candidates)
                if bound is None or not values or any(value is None for value in values):
                    return False
                if kind == "number_at_most" and any(value > bound for value in values):
                    return False
                if kind == "number_at_least" and any(value < bound for value in values):
                    return False
                continue
            target = await find_target(page, candidates, 800)
            if kind == "visible":
                continue
            if kind == "attribute_equals":
                actual = await target.get_attribute(condition["attribute"])
            else:
                actual = await target.input_value() if kind == "value_equals" else await target.inner_text()
            if kind in ("value_equals", "text_equals", "attribute_equals"):
                if kind == "value_equals" and isinstance(expected, (int, float)) and not isinstance(expected, bool):
                    if observed_number(actual) != observed_number(expected) or observed_number(expected) is None:
                        return False
                elif str(actual).strip() != str(expected).strip():
                    return False
            if kind == "text_contains" and str(expected) not in actual:
                return False
        except Exception:
            return False
    return True


async def verify_goal(page, site, goal_id, parameters):
    await detect_blocking(page, site)
    issue = await temporary_page_error(page, site)
    if issue:
        return False, issue
    site_id = site["site_id"]
    if site_id == "web":
        evidence = {"url": page.url, "title": await page.title(), "conditions": []}
        if not permitted_url(page.url, site):
            return False, {**evidence, "reason": "The browser reached a URL outside permitted public navigation."}
        conditions = site.get("completion_conditions") or []
        if not conditions:
            return False, {**evidence, "reason": "This browser task requires explicit completion conditions."}
        complete = True
        for condition in conditions:
            condition = condition.model_dump(mode="json") if hasattr(condition, "model_dump") else condition
            passed = await check_conditions(page, [condition], parameters, site)
            checked = {"kind": condition.get("kind"), "passed": passed}
            if condition.get("value_ref"):
                checked["value_ref"] = condition["value_ref"]
            if condition.get("kind") == "control_activated":
                checked["expected"] = resolve_value(condition, parameters, site)
                checked["activated_controls"] = site.get("_activated_controls", [])
            if condition.get("kind") == "search_submitted":
                checked["submitted_searches"] = site.get("_submitted_searches", [])
            if condition.get("kind") == "media_playing":
                checked["media"] = await read_media_state(page)
            if condition.get("objective_index") is not None:
                checked["objective_index"] = condition["objective_index"]
            if condition.get("kind") in {"number_at_most", "number_at_least"}:
                candidates = condition.get("locators") or ([condition["locator"]] if condition.get("locator") else [])
                checked["observed_values"] = await numeric_condition_values(page, candidates)
                checked["expected"] = resolve_value(condition, parameters, site)
            evidence["conditions"].append(checked)
            complete = complete and passed
        host = (urlparse(page.url).hostname or "").lower()
        if goal_id == "web_search" and re.fullmatch(r"(?:[\w-]+\.)*amazon\.(?:com|in)", host):
            # Observing an edited handle does not prove that its filter was submitted.
            applied = amazon_url_filters(page.url)
            evidence["applied_filters"] = applied
            for bound in ("min_price", "max_price"):
                if parameters.get(bound) is None:
                    continue
                actual = applied.get(bound)
                passed = actual is not None and (float(actual) >= float(parameters[bound]) if bound == "min_price" else float(actual) <= float(parameters[bound]))
                complete = complete and passed
                evidence["conditions"].append({"kind": "applied_price_filter", "value_ref": bound, "passed": passed, "observed_value": actual})
        if not complete:
            evidence["reason"] = "The browser state does not meet every completion condition."
        return complete, evidence
    if site_id == "shop":
        results = page.get_by_test_id("search-results")
        if await results.count() != 1:
            return False, {}
        query = await results.get_attribute("data-query") or ""
        ceiling = await results.get_attribute("data-max-price")
        ordering = await results.get_attribute("data-sort-by")
        observed = {"query": query, "max_price": ceiling, "sort_by": ordering}
        if normalize_query(query) != normalize_query(parameters["product_name"]):
            return False, observed
        try:
            if float(ceiling) != float(parameters["max_price"]):
                return False, observed
        except (ValueError, TypeError):
            return False, observed
        if ordering != parameters.get("sort_by", "rating_desc"):
            return False, observed
        products = await page.get_by_test_id("product-card").evaluate_all("""elements => elements.map(e => ({
          name:e.dataset.productName, price:Number(e.dataset.price), rating:Number(e.dataset.rating)
        }))""")
        if any(item["price"] > float(parameters["max_price"]) for item in products):
            return False, {}
        key = "rating" if ordering == "rating_desc" else "price"
        numbers = [item[key] for item in products]
        if numbers != sorted(numbers, reverse=ordering == "rating_desc"):
            return False, {}
        return True, {"query": query, "max_price": float(ceiling), "sort_by": ordering,
                      "count": len(products), "products": products}
    if site_id == "admin":
        details, status = page.get_by_test_id("order-detail"), page.get_by_test_id("order-status")
        if await details.count() != 1 or await status.count() != 1:
            return False, {}
        order_id = await details.get_attribute("data-order-id")
        state = (await status.inner_text()).strip()
        result = {"order_id": order_id, "status": state}
        return str(order_id) == str(parameters["order_id"]) and state.lower() == "shipped", result
    if site_id == "amazon_in":
        query = parameters["query"]
        address = parse_qs(urlparse(page.url).query)
        submitted = address.get("k", address.get("field-keywords", [""]))[0]
        requested = {key: parameters[key] for key in ("min_price", "max_price", "sort_by", "min_rating") if parameters.get(key) is not None}
        applied = amazon_url_filters(page.url)
        observed = {"query": submitted, "requested_filters": requested, "applied_filters": applied}
        if normalize_query(submitted) != normalize_query(query):
            return False, observed
        search = page.locator("#twotabsearchtextbox")
        if await search.count() == 1 and normalize_query(await search.input_value()) != normalize_query(query):
            return False, observed
        if parameters.get("min_rating") is not None:
            selected = await page.locator('#s-refinements [aria-checked="true"], #s-refinements input:checked, #s-refinements .a-text-bold, #filters [aria-checked="true"]').evaluate_all("""elements => elements.map(e => (e.getAttribute('aria-label') || e.closest('li')?.innerText || e.innerText || '')).join(' ')""")
            rating = re.search(r"([1-5](?:\.\d+)?)\s*(?:stars?|out of 5)", selected, re.I)
            if rating and "p_72:" in address.get("rh", [""])[0]:
                applied["min_rating"] = float(rating.group(1))
        for name, expected in requested.items():
            actual = applied.get(name)
            target = min(4, int(float(expected))) if name == "min_rating" else expected
            if actual is None or (str(actual) != str(target) if name == "sort_by" else abs(float(actual) - float(target)) > .01):
                observed["reason"] = "Requested filter has not been applied: " + name
                return False, observed
        cards = page.locator('[data-component-type="s-search-result"][data-asin]')
        items = await cards.evaluate_all("""elements => elements.filter(e => e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden').slice(0,50).map(e => ({
          name:[...e.querySelectorAll('h2')].map(h=>h.innerText.trim()).sort((a,b)=>b.length-a.length)[0] || '',
          url:e.querySelector('h2 a, a.a-link-normal.s-line-clamp-2, a.a-link-normal.s-no-outline')?.href || null,
          price:e.querySelector('.a-price .a-offscreen')?.innerText?.trim() || null,
          sponsored:/sponsored/i.test(e.innerText),
          rating_text:e.querySelector('[aria-label*="out of 5"], .a-icon-alt')?.getAttribute('aria-label') || e.querySelector('.a-icon-alt')?.innerText || null
        }))""")
        eligible = []
        excluded = 0
        for item in items:
            price = money_value(item.get("price"))
            rating = money_value(item.get("rating_text"))
            if ((parameters.get("max_price") is not None and (price is None or price > float(parameters["max_price"])))
                    or (parameters.get("min_price") is not None and (price is None or price < float(parameters["min_price"])))):
                excluded += 1
                continue
            if parameters.get("min_rating") is not None and (rating is None or rating < float(parameters["min_rating"])):
                excluded += 1
                continue
            if rating is not None:
                item["rating"] = rating
            item.pop("rating_text", None)
            eligible.append(item)
        text = await page.locator("body").inner_text()
        empty = bool(re.search(r"no results for|couldn't find any results", text, re.I))
        complete = bool(items) or empty
        if items and not eligible and not empty:
            return False, {**observed, "reason": "Loaded results do not contain any product meeting the requested price/rating constraints.",
                           "products": [], "excluded_outside_budget": excluded}
        return complete, {"query": query, "products": eligible[:5], "empty": not eligible and complete,
                          "requested_filters": requested, "applied_filters": applied,
                          "excluded_outside_budget": excluded}
    raise StepExecutionError("No registered goal verifier for site: " + site_id)


def money_value(text):
    match = re.search(r"\d[\d,]*(?:\.\d+)?", str(text or ""))
    return float(match.group().replace(",", "")) if match else None


def amazon_url_filters(address):
    query = parse_qs(urlparse(address).query)
    result = {}
    for name, key in (("min_price", "low-price"), ("max_price", "high-price")):
        if query.get(key):
            result[name] = money_value(query[key][0])
    for facet in query.get("rh", [""])[0].split(","):
        match = re.fullmatch(r"p_36:(\d*)-(\d*)", facet)
        if match:
            if match.group(1):
                result.setdefault("min_price", float(match.group(1)) / 100)
            if match.group(2):
                result.setdefault("max_price", float(match.group(2)) / 100)
    if query.get("s"):
        result["sort_by"] = query["s"][0]
    return result
