# src/vba/perceive/snapshot.py
from .elements import Observation, elements_from_records
from .fingerprint import fingerprint

EXTRACT_JS = """
() => {
  const SEL = ['a','button','input','select','textarea','[role="button"]',
               '[role="link"]','[onclick]','[tabindex="0"]'];

  function queryDeep(root) {
    const out = Array.from(root.querySelectorAll(SEL.join(',')));
    for (const el of Array.from(root.querySelectorAll('*'))) {
      if (el.shadowRoot) out.push(...queryDeep(el.shadowRoot));
    }
    return out;
  }

  function visible(el) {
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    const s = getComputedStyle(el);
    return s.visibility !== 'hidden' && s.display !== 'none' && s.opacity !== '0';
  }

  function topmost(el) {
    const r = el.getBoundingClientRect();
    const cx = r.x + r.width / 2, cy = r.y + r.height / 2;
    if (cx < 0 || cy < 0 || cx > innerWidth || cy > innerHeight) return false;
    const top = document.elementFromPoint(cx, cy);
    return !!top && (el === top || el.contains(top) || top.contains(el));
  }

  function accName(el) {
    if (el.getAttribute('aria-label')) return el.getAttribute('aria-label').trim();
    if (el.id) {
      const lab = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (lab) return lab.innerText.trim();
    }
    const own = el.closest('label');
    if (own && el.tagName !== 'LABEL') {
      return own.innerText.trim();
    }
    return (el.innerText || el.value || el.placeholder || '').trim();
  }

  function uniq(el) {
    if (el.id) return '#' + CSS.escape(el.id);
    if (el.getAttribute('name')) {
      return el.tagName.toLowerCase() + '[name="' + el.getAttribute('name') + '"]';
    }
    const p = el.parentElement;
    if (!p) return el.tagName.toLowerCase();
    const same = Array.from(p.children).filter(c => c.tagName === el.tagName);
    return uniq(p) + ' > ' + el.tagName.toLowerCase()
           + ':nth-of-type(' + (same.indexOf(el) + 1) + ')';
  }

  const seen = new Set();
  const out = [];
  for (const el of queryDeep(document)) {
    if (seen.has(el) || !visible(el) || !topmost(el)) continue;
    seen.add(el);
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    out.push({
      tag: tag,
      role: el.getAttribute('role') || (tag === 'a' ? 'link'
            : tag === 'button' ? 'button'
            : tag === 'select' ? 'combobox'
            : type === 'checkbox' ? 'checkbox' : 'textbox'),
      name: accName(el),
      element_id: el.id || '',
      name_attr: el.getAttribute('name') || '',
      input_type: type,
      is_submit: (tag === 'button' && type !== 'button')
                 || (tag === 'input' && type === 'submit'),
      selector: uniq(el),
    });
  }
  return out;
}
"""


async def snapshot(page, epoch: int, contract: str, step_key: str) -> Observation:
    records = await page.evaluate(EXTRACT_JS)
    elements = elements_from_records(records)
    return Observation(
        url=page.url,
        epoch=epoch,
        elements=elements,
        text=await page.inner_text("body"),
        fingerprint=fingerprint(contract, step_key, page.url, elements),
    )
