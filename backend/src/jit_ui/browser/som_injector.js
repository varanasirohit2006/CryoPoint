({ limit = 80, epoch, validate = false, details = false, omit_changed = false }) => {
  const previousOverlay = document.querySelector('[data-jit-ui-overlay]');
  const omitted = new Set(previousOverlay?.jitEpoch === epoch ? previousOverlay.jitOmitted || [] : []);
  if (omit_changed) for (const selector of previousOverlay?.jitUnstableSelectors || []) omitted.add(selector);
  const roots = [];
  const collectRoots = root => {
    roots.push(root);
    for (const el of root.querySelectorAll('*')) if (el.shadowRoot) collectRoots(el.shadowRoot);
  };
  collectRoots(document);
  if (!validate) {
    previousOverlay?.remove();
    for (const root of roots) root.querySelectorAll('[data-jit-ui-mark]').forEach(el => el.removeAttribute('data-jit-ui-mark'));
  }
  const overlay = validate ? null : document.createElement('div');
  if (overlay) {
    overlay.setAttribute('data-jit-ui-overlay', 'true');
    overlay.setAttribute('aria-hidden', 'true');
    overlay.style.cssText = 'position:fixed;inset:0;pointer-events:none;z-index:2147483647;';
  }
  const parent = el => el?.parentElement || el?.getRootNode()?.host;
  const cssPath = el => {
    const parts = [];
    for (let current = el; current && current !== document.body; current = parent(current)) {
      const root = current.getRootNode();
      let part = current.tagName.toLowerCase();
      const uniqueId = current.id && root.querySelectorAll('#'+CSS.escape(current.id)).length === 1;
      if (uniqueId) part = '#' + CSS.escape(current.id);
      else {
        const siblings = [...(current.parentElement || root).children].filter(s => s.tagName === current.tagName);
        if (siblings.length > 1) part += ':nth-of-type(' + (siblings.indexOf(current) + 1) + ')';
      }
      parts.unshift(part);
      if (uniqueId && !root.host) break;
      if (root.host && !current.parentElement) parts.unshift(' ');
      else if (parent(current) && parent(current) !== document.body) parts.unshift(' > ');
    }
    return parts.join('');
  };
  const nativeRole = el => {
    if (el.getAttribute('role')) return el.getAttribute('role');
    const tag = el.tagName.toLowerCase();
    if (el.isContentEditable) return 'textbox';
    if (tag === 'button' || (tag === 'input' && ['button','submit','reset'].includes(el.type))) return 'button';
    if (tag === 'a') return 'link';
    if (tag === 'textarea') return 'textbox';
    if (tag === 'select') return 'combobox';
    if (tag === 'input') {
      if (el.type === 'range') return 'slider';
      if (el.type === 'search') return 'searchbox';
      if (el.type === 'checkbox') return 'checkbox';
      if (el.type === 'radio') return 'radio';
      if (el.type === 'number') return 'spinbutton';
      if (el.type === 'password') return '';
      if (el.type !== 'hidden') return 'textbox';
    }
    return '';
  };
  const accessibleName = el => {
    const root = el.getRootNode();
    const labelled = (el.getAttribute('aria-labelledby') || '').split(/\s+/).map(id => root.getElementById?.(id)?.textContent || '').join(' ').trim();
    const buttonValue = el.tagName === 'INPUT' && ['button','submit','reset'].includes(el.type) ? el.value : '';
    return (el.getAttribute('aria-label') || labelled || [...(el.labels || [])].map(label => label.textContent).join(' ') || buttonValue || el.getAttribute('placeholder') || el.textContent || el.getAttribute('alt') || el.getAttribute('title') || '').replace(/\s+/g,' ').trim().slice(0,160);
  };
  const amazonControlInfo = /* JIT_AMAZON_CONTROL_INFO */ null;
  const overlayContext = /* JIT_OVERLAY_CONTEXT */ null;
  const sliderInfo = /* JIT_SLIDER_INFO */ null;
  const selector = 'button,a[href],input:not([type="hidden"]),select,textarea,[role="button"],[role="link"],[role="textbox"],[role="searchbox"],[role="spinbutton"],[role="slider"],[role="combobox"],[role="checkbox"],[role="radio"],[role="option"],[role="tab"],[contenteditable="true"]';
  const visible = [];
  for (const root of roots) for (const el of root.querySelectorAll(selector)) {
    if (el.closest('.lab-dock,[data-jit-ui-overlay]')) continue;
    if (omitted.size && omitted.has(cssPath(el))) continue;
    const amazonInfo = amazonControlInfo ? amazonControlInfo(el) : null;
    if (amazonInfo && !amazonInfo.observable) continue;
    const rect = el.getBoundingClientRect(), style = getComputedStyle(el);
    if (rect.width <= 0 || rect.height <= 0 || rect.bottom <= 0 || rect.right <= 0 || rect.top >= innerHeight || rect.left >= innerWidth || style.visibility === 'hidden' || style.display === 'none' || Number(style.opacity) === 0 || el.disabled || el.getAttribute('aria-disabled') === 'true') continue;
    const role = nativeRole(el), slider = role === 'slider' && sliderInfo ? sliderInfo(el) : null;
    const bounds = slider ? slider.thumb : {x:rect.x,y:rect.y,width:rect.width,height:rect.height};
    const x = Math.max(0,Math.min(innerWidth-1,bounds.x+bounds.width/2)), y = Math.max(0,Math.min(innerHeight-1,bounds.y+bounds.height/2));
    let hit = document.elementFromPoint(x,y);
    while (hit?.shadowRoot) {
      const inner = hit.shadowRoot.elementFromPoint(x,y);
      if (!inner || inner === hit) break;
      hit = inner;
    }
    const overlappingRange = slider && hit?.matches('input[type="range"],[role="slider"]') && parent(hit) === parent(el);
    const pointerlessRange = slider && style.pointerEvents === 'none' && (hit === parent(el) || parent(hit) === parent(el));
    if (hit && hit !== el && !el.contains(hit) && !overlappingRange && !pointerlessRange) continue;
    visible.push({el,role,slider,bounds,amazonInfo});
  }
  // Keep form controls when product links exhaust the mark budget.
  const priority = item => item.role === 'slider' ? 0 : ['INPUT','SELECT','TEXTAREA'].includes(item.el.tagName) ? 1 : item.role === 'link' ? 3 : 2;
  visible.sort((a,b) => priority(a)-priority(b));
  const elements = [], targets = [], badges = [];
  for (const {el,role,slider,bounds,amazonInfo} of visible.slice(0,limit)) {
    const mark = elements.length+1, popup = overlayContext ? overlayContext(el) : {context:'',dismiss:false};
    const labelText = [...(el.labels || [])].map(label => label.textContent).join(' ').replace(/\s+/g,' ').trim().slice(0,160);
    const attrs = {};
    for (const key of ['id','type','name','placeholder','aria-label','data-testid','href']) {
      const value = el.getAttribute(key);
      if (value != null) attrs[key] = value.slice(0,300);
    }
    elements.push({mark,tag:el.tagName.toLowerCase(),role,
      name:accessibleName(el) || (slider?.parameter_hint ? slider.parameter_hint.replace('_',' ') : ''),
      label:labelText,placeholder:el.getAttribute('placeholder') || '',testid:el.getAttribute('data-testid') || '',css:cssPath(el),xpath:'',
      text:(el.textContent || '').replace(/\s+/g,' ').trim().slice(0,160),attributes:attrs,
      context:popup.context || slider?.context || '',readonly:!!el.readOnly,
      input_type:el.getAttribute('type') || '',parameter_hint:slider?.parameter_hint || '',value_text:slider?.value_text || '',
      value_min:slider?.minimum ?? null,value_max:slider?.maximum ?? null,value_step:slider?.step ?? null,
      options:el.tagName === 'SELECT' ? [...el.options].filter(o=>!o.disabled).slice(0,25).map(o=>({value:o.value,label:o.textContent.trim()})) : [],
      current_value:slider ? slider.value_text : ['INPUT','TEXTAREA','SELECT'].includes(el.tagName) && !['password','email','tel'].includes(el.type)
        && !/password|secret|token|card|phone|mobile|email|username/i.test((el.name||'')+' '+(el.id||'')+' '+(el.autocomplete||'')) ? String(el.value||'').slice(0,200) : null,
      checked:['checkbox','radio'].includes(el.type) ? el.checked : null,
      amazonDismiss:!!amazonInfo?.isDismiss || popup.dismiss,goal_control:amazonInfo?.purpose || (popup.dismiss ? 'dismiss' : null),bounds});
    targets.push(el);
    if (validate) continue;
    el.setAttribute('data-jit-ui-mark',epoch+':'+mark);
    const box = document.createElement('div');
    box.style.cssText = `position:fixed;left:${bounds.x}px;top:${bounds.y}px;width:${bounds.width}px;height:${bounds.height}px;border:2px solid #ec4899;box-sizing:border-box;pointer-events:none;`;
    overlay.appendChild(box);
    const label = document.createElement('span');
    label.textContent = String(mark);
    const width = 12+String(mark).length*8;
    let left = Math.max(0,Math.min(innerWidth-width,bounds.x)), top = Math.max(0,bounds.y-20);
    for (let tries=0;tries<12 && badges.some(b=>left<b.x+b.width && left+width>b.x && top<b.y+18 && top+18>b.y);tries++) {
      left = Math.min(innerWidth-width,left+width+2);
      if (tries%4 === 3) {left=Math.max(0,bounds.x-width-3);top=Math.min(innerHeight-18,top+20);}
    }
    badges.push({x:left,y:top,width});
    label.style.cssText = `position:fixed;left:${left}px;top:${top}px;background:#9d174d;color:white;font:bold 12px sans-serif;padding:1px 4px;line-height:16px;border-radius:3px;pointer-events:none;`;
    overlay.appendChild(label);
  }
  if (validate) {
    const old = previousOverlay?.jitSnapshot;
    const unstable = new Set();
    if (old) old.elements.forEach((before,i) => {
      const position = targets.indexOf(old.targets[i]), e = elements[position];
      const matches = !!e && old.targets[i].getAttribute('data-jit-ui-mark') === epoch+':'+before.mark
        && ['name','current_value','checked','role','parameter_hint','readonly'].every(key=>e[key] === before[key])
        && JSON.stringify(e.options) === JSON.stringify(before.options) && JSON.stringify(e.attributes) === JSON.stringify(before.attributes)
        && ['x','y','width','height'].every(key=>Math.abs(e.bounds[key]-before.bounds[key]) < 2);
      if (!matches) unstable.add(before.css);
    });
    elements.forEach((e,i) => {if (!old?.targets.includes(targets[i])) unstable.add(e.css);});
    const stable = !!old && old.elements.length === elements.length && !unstable.size;
    if (previousOverlay) previousOverlay.jitUnstableSelectors = [...unstable];
    return details ? {stable,unstable_count:unstable.size,omitted_count:omitted.size} : stable;
  }
  overlay.jitSnapshot = {elements,targets};
  overlay.jitEpoch = epoch;
  overlay.jitOmitted = [...omitted];
  document.documentElement.appendChild(overlay);
  return elements;
}
