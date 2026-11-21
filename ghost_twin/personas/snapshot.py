"""Extract a ``PageSnapshot`` (links, forms, buttons) from a live page via injected JS."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ghost_twin.models import PageSnapshot

if TYPE_CHECKING:  # pragma: no cover
    from playwright.async_api import Page

SNAPSHOT_JS = r"""
() => {
  window.__gtN = window.__gtN || 0;
  const sel = (el) => {
    if (!el.dataset.gtId) el.dataset.gtId = 'gt' + (window.__gtN++);
    return `[data-gt-id="${el.dataset.gtId}"]`;
  };
  const visible = (el) => {
    if (el.type === 'hidden') return true;
    const r = el.getClientRects();
    if (!r.length) return false;
    const s = getComputedStyle(el);
    return s.visibility !== 'hidden' && s.display !== 'none';
  };
  const txt = (s) => (s || '').replace(/\s+/g, ' ').trim().slice(0, 80);
  const labelFor = (el) => {
    if (el.id) {
      const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (l) return txt(l.innerText);
    }
    const wrap = el.closest('label');
    if (wrap) return txt(wrap.innerText);
    return txt(el.getAttribute('aria-label') || '');
  };
  const inputInfo = (el) => ({
    selector: sel(el),
    name: el.name || el.id || '',
    type: (el.tagName === 'INPUT' ? (el.getAttribute('type') || 'text') : el.tagName.toLowerCase()).toLowerCase(),
    tag: el.tagName.toLowerCase(),
    placeholder: el.getAttribute('placeholder') || '',
    label: labelFor(el),
    required: !!el.required,
    options: el.tagName === 'SELECT' ? Array.from(el.options).map(o => o.value) : [],
    maxlength: el.maxLength > 0 ? el.maxLength : null,
    min: el.getAttribute('min'),
    max: el.getAttribute('max'),
  });
  const fieldSel = 'input, textarea, select';
  const usable = (el) => !el.disabled && !el.readOnly && visible(el);
  const submitOf = (root) => root.querySelector(
    'button[type=submit], input[type=submit], button:not([type])');

  const forms = [];
  for (const f of Array.from(document.forms)) {
    if (!visible(f) && !f.querySelector(fieldSel)) continue;
    const inputs = Array.from(f.querySelectorAll(fieldSel)).filter(usable).map(inputInfo);
    if (!inputs.length) continue;
    const btn = submitOf(f);
    forms.push({
      selector: sel(f),
      action: f.action || location.href,
      method: (f.getAttribute('method') || 'get').toLowerCase(),
      inputs,
      submit_selector: btn && usable(btn) ? sel(btn) : null,
      submit_label: btn ? txt(btn.innerText || btn.value || '') : '',
    });
  }
  // Inputs outside <form> (common in SPAs) become one pseudo-form.
  const orphans = Array.from(document.querySelectorAll(fieldSel))
    .filter(el => !el.form && usable(el));
  const looseButtons = Array.from(document.querySelectorAll('button, [role=button], input[type=button]'))
    .filter(b => !b.form && !b.closest('form') && usable(b));
  if (orphans.length) {
    const btn = looseButtons[0];
    forms.push({
      selector: null, action: location.href, method: 'js',
      inputs: orphans.map(inputInfo),
      submit_selector: btn ? sel(btn) : null,
      submit_label: btn ? txt(btn.innerText || btn.value || '') : '',
    });
  }
  const links = Array.from(document.querySelectorAll('a[href]'))
    .filter(a => visible(a) && a.href && !a.href.startsWith('javascript:') && !a.href.startsWith('mailto:') && !a.href.startsWith('tel:'))
    .slice(0, 200)
    .map(a => ({ href: a.href.split('#')[0], text: txt(a.innerText || a.title || ''), selector: sel(a) }));
  const buttons = looseButtons.slice(0, 50).map(b => ({ selector: sel(b), label: txt(b.innerText || b.value || b.getAttribute('aria-label') || '') }));
  return { url: location.href, title: document.title, links, forms, buttons };
}
"""


async def take_snapshot(page: Page) -> PageSnapshot:
    data = await page.evaluate(SNAPSHOT_JS)
    return PageSnapshot.from_dict(data)
