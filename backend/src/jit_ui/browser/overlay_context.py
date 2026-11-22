"""Read-only popup context for the model; never dismisses or selects a control."""

OVERLAY_CONTEXT_JS = r"""element => {
    const control = element.closest('button,a,input,select,textarea,[role="button"],[role="textbox"]') || element;
    const label = (control.getAttribute('aria-label') || control.innerText || control.getAttribute('title') || '').trim();
    let panel = control.closest('[role="dialog"],[aria-modal="true"],dialog');
    if (!panel) for (let parent = control.parentElement; parent && parent !== document.body; parent = parent.parentElement) {
        const style = getComputedStyle(parent), rect = parent.getBoundingClientRect();
        if (style.position === 'fixed' && rect.width * rect.height > innerWidth * innerHeight * .12 && Number(style.zIndex) > 1) {
            panel = parent; break;
        }
    }
    if (!panel) return {context:'', dismiss:false};
    const text = (panel.innerText || '').replace(/\s+/g,' ').trim();
    const heading = panel.querySelector('h1,h2,h3,[role="heading"]')?.textContent?.trim();
    const context = heading || (/\blog\s*in|\bsign\s*in/i.test(text) ? 'Login dialog' : text.slice(0,90) || 'Dialog');
    const dismiss = /^(?:[×✕✖✗x]|close(?:\s+(?:dialog|popup|window|modal))?|dismiss|not now|no thanks)$/i.test(label);
    return {context, dismiss};
}"""
