"""JavaScript-фрагменты, выполняемые внутри страницы.

Все обходят DOM вместе с открытыми shadow-корнями. Закрытые shadow-корни браузер
не отдаёт ни одной программе, это ограничение самой платформы.
"""

# Видимый текст страницы по строкам. Учитывает shadow DOM, слоты, закрытые <details>,
# подписи у картинок и кнопок без текста.
TEXT_JS = r"""
(arg) => {
  const maxChars = arg.maxChars, includeHidden = !!arg.includeHidden;
  const SKIP = new Set(['SCRIPT','STYLE','NOSCRIPT','TEMPLATE','HEAD','META','LINK','IFRAME','OBJECT','EMBED','SVG','CANVAS']);
  const BLOCK = new Set(['ADDRESS','ARTICLE','ASIDE','BLOCKQUOTE','BR','DD','DETAILS','DIV','DL','DT','FIELDSET','FIGCAPTION','FIGURE','FOOTER','FORM','H1','H2','H3','H4','H5','H6','HEADER','HR','LI','MAIN','NAV','OL','P','PRE','SECTION','SUMMARY','TABLE','TR','UL','BUTTON','LABEL','OPTION']);
  const lines = []; let cur = ''; let total = 0; let truncated = false;
  const flush = () => {
    const t = cur.replace(/[\s ]+/g, ' ').replace(/\s*\|\s*$/, '').trim();
    cur = '';
    if (t) { lines.push(t); total += t.length + 1; if (total > maxChars) truncated = true; }
  };
  const hidden = (el) => {
    if (includeHidden) return false;
    if (el.hidden) return true;
    const cs = getComputedStyle(el);
    return cs.display === 'none' || cs.visibility === 'hidden' || cs.visibility === 'collapse';
  };
  const walk = (node) => {
    if (truncated) return;
    if (node.nodeType === 3) { cur += node.nodeValue; return; }
    if (node.nodeType !== 1) return;
    const tag = node.tagName.toUpperCase();
    if (SKIP.has(tag)) return;
    if (hidden(node)) return;
    const block = BLOCK.has(tag);
    if (block) flush();
    if (tag === 'IMG') {
      const alt = node.getAttribute('alt');
      if (alt && alt.trim()) cur += ' [img: ' + alt.trim() + '] ';
      return;
    }
    if (tag === 'INPUT') {
      const type = (node.type || '').toLowerCase();
      const v = node.value || node.placeholder || '';
      if (v && ['button', 'submit', 'search', 'text'].includes(type)) {
        cur += ' [' + (type === 'text' || type === 'search' ? 'input' : 'button') + ': ' + v + '] ';
      }
      return;
    }
    if ((tag === 'A' || tag === 'BUTTON' || node.getAttribute('role') === 'button') && !(node.textContent || '').trim()) {
      const label = node.getAttribute('aria-label') || node.getAttribute('title');
      if (label && label.trim()) cur += ' [' + label.trim() + '] ';
    }
    let kids;
    if (node.shadowRoot) {
      kids = node.shadowRoot.childNodes;
    } else if (tag === 'SLOT') {
      const assigned = node.assignedNodes({flatten: true});
      kids = assigned.length ? assigned : node.childNodes;
    } else if (tag === 'DETAILS' && !node.open && !includeHidden) {
      kids = Array.from(node.children).filter(c => c.tagName === 'SUMMARY');
    } else {
      kids = node.childNodes;
    }
    for (const k of kids) walk(k);
    if (tag === 'TD' || tag === 'TH') cur += ' | ';
    if (block) flush();
  };
  if (document.body) walk(document.body);
  flush();
  const h1 = document.querySelector('h1');
  return {text: lines.join('\n'), truncated: truncated, h1: h1 ? (h1.innerText || '').trim().slice(0, 200) : ''};
}
"""

# Все ссылки: <a>, <area>, data-href/data-url, onclick с адресом. Для каждой — окружающая карточка.
LINKS_JS = r"""
(arg) => {
  const all = [];
  const visit = (root) => root.querySelectorAll('*').forEach(el => { all.push(el); if (el.shadowRoot) visit(el.shadowRoot); });
  visit(document);
  const out = []; const seen = new Set();
  const abs = (h) => { try { return new URL(h, document.baseURI).href; } catch (e) { return null; } };
  const txt = (el) => {
    let t = ((el.innerText || el.textContent || '') + '').replace(/\s+/g, ' ').trim();
    if (!t) t = (el.getAttribute('aria-label') || el.getAttribute('title') || '').trim();
    if (!t && el.querySelector) { const im = el.querySelector('img[alt]'); if (im && im.getAttribute('alt').trim()) t = '[img: ' + im.getAttribute('alt').trim() + ']'; }
    return t.slice(0, 200);
  };
  const CARD = 'li, article, tr, [class*="card" i], [class*="job" i], [class*="position" i], [class*="opening" i], [class*="vacanc" i], [role="listitem"]';
  const ctx = (el) => { const c = el.closest ? el.closest(CARD) : null; if (!c) return ''; return ((c.innerText || '') + '').replace(/\s+/g, ' ').trim().slice(0, 220); };
  // Заголовок карточки: ближайший объемлющий блок с заголовком, в котором не больше трёх ссылок (иначе это уже весь список).
  const headingOf = (el) => {
    let e = el;
    for (let i = 0; i < 6 && e && e.nodeType === 1; i++) {
      if (e.querySelectorAll && e.querySelectorAll('a[href]').length > 3) return '';
      const h = e.querySelector ? e.querySelector('h1,h2,h3,h4,h5,h6,[class*="title" i],[class*="job-name" i],[class*="position-name" i]') : null;
      if (h && !h.contains(el)) { const t = ((h.innerText || '') + '').replace(/\s+/g, ' ').trim(); if (t && t.length <= 140) return t; }
      e = e.parentElement;
    }
    return '';
  };
  // Название карточки без тегов заголовка: первая содержательная строка самого крупного блока с 1-3 ссылками и небольшим текстом
  // («Art Manager / Bangalore / Department: ... / View Job Description / Apply Now»). Подписи полей («Department:») и кнопки пропускаются.
  const cardTitleOf = (el) => {
    const own = ((el.innerText || '') + '').replace(/\s+/g, ' ').trim().toLowerCase();
    const bad = /(:\s*$)|^(apply|view|read more|learn more|more details|details|job description|see details|click here)\b/i;
    let best = '';
    let e = el.parentElement;
    for (let i = 0; i < 6 && e && e.nodeType === 1; i++, e = e.parentElement) {
      if (e.querySelectorAll('a[href]').length > 3) break;
      const lines = ((e.innerText || '') + '').split(/\n+/).map(x => x.replace(/\s+/g, ' ').trim()).filter(Boolean);
      if (lines.length > 14) break;
      const first = lines.find(x => x.length >= 3 && x.length <= 120 && !bad.test(x) && x.toLowerCase() !== own);
      if (first) best = first;
    }
    return best;
  };
  const visible = (el) => { const cs = getComputedStyle(el); if (cs.display === 'none' || cs.visibility === 'hidden') return false; const r = el.getBoundingClientRect(); return r.width > 0 || r.height > 0; };
  for (const el of all) {
    let href = null, kind = null;
    const tag = el.tagName.toUpperCase();
    if ((tag === 'A' || tag === 'AREA') && el.hasAttribute('href')) { href = el.getAttribute('href'); kind = 'a'; }
    else {
      for (const a of ['data-href', 'data-url', 'data-link', 'data-to']) { if (el.hasAttribute && el.hasAttribute(a)) { href = el.getAttribute(a); kind = 'data'; break; } }
      if (!href && el.getAttribute) {
        const oc = el.getAttribute('onclick');
        if (oc) { const m = oc.match(/(?:location(?:\.href)?\s*=|location\.assign\(|window\.open\()\s*['"]([^'"]+)['"]/); if (m) { href = m[1]; kind = 'onclick'; } }
      }
    }
    if (!href) continue;
    href = href.trim();
    if (!href) continue;
    const full = href.toLowerCase().startsWith('javascript:') ? href : abs(href);
    if (!full) continue;
    const text = txt(el);
    const key = full + '|' + text;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({href: full, text: text, kind: kind, rel: el.getAttribute('rel') || '', target: el.getAttribute('target') || '',
              download: el.hasAttribute('download'), aria: el.getAttribute('aria-label') || '', context: ctx(el), heading: headingOf(el), cardTitle: cardTitleOf(el), visible: visible(el)});
    if (out.length >= arg.max) break;
  }
  return out;
}
"""

# JSON-LD и встроенный JSON приложений (__NEXT_DATA__ и т. п.)
JSON_BLOCKS_JS = r"""
() => {
  const ld = [], app = [];
  document.querySelectorAll('script[type="application/ld+json"]').forEach(s => { const t = s.textContent || ''; if (t.length < 1500000) ld.push(t); });
  document.querySelectorAll('script#__NEXT_DATA__, script#__NUXT_DATA__, script[type="application/json"]').forEach(s => { const t = s.textContent || ''; if (t.length > 20 && t.length < 1500000) app.push(t); });
  return {ld: ld.slice(0, 30), app: app.slice(0, 10)};
}
"""

# Признак изменения содержимого: длина текста, высота, число элементов (с shadow DOM).
SIGNATURE_JS = r"""
() => {
  let n = 0;
  const visit = (root) => root.querySelectorAll('*').forEach(el => { n++; if (el.shadowRoot) visit(el.shadowRoot); });
  visit(document);
  const se = document.scrollingElement || document.documentElement;
  return [document.body ? (document.body.innerText || '').length : 0, se.scrollHeight, n];
}
"""

# Постепенная прокрутка страницы вниз и прокрутка внутренних прокручиваемых блоков.
SCROLL_JS = r"""
async () => {
  const se = document.scrollingElement || document.documentElement;
  const step = Math.max(300, Math.floor(window.innerHeight * 0.9));
  let y = se.scrollTop;
  for (let i = 0; i < 10; i++) {
    y += step;
    window.scrollTo(0, y);
    await new Promise(r => setTimeout(r, 80));
    if (y >= se.scrollHeight - window.innerHeight) break;
  }
  // Небольшой откат вверх и возврат вниз: сайты, которые догружают список по событию
  // прокрутки или по появлению нижнего маркера, не реагируют, если страница уже внизу.
  window.scrollTo(0, Math.max(0, se.scrollHeight - Math.floor(window.innerHeight * 1.5)));
  const els = [];
  const visit = (root) => root.querySelectorAll('*').forEach(el => { els.push(el); if (el.shadowRoot) visit(el.shadowRoot); });
  visit(document);
  const boxes = [];
  for (const el of els) {
    if (boxes.length >= 5) break;
    if (el === se || el === document.body) continue;
    const cs = getComputedStyle(el);
    if ((cs.overflowY === 'auto' || cs.overflowY === 'scroll') && el.scrollHeight > el.clientHeight + 40 && el.clientHeight > 80) {
      el.scrollTop = Math.max(0, el.scrollHeight - Math.floor(el.clientHeight * 1.5));
      boxes.push(el);
    }
  }
  await new Promise(r => setTimeout(r, 80));
  window.scrollTo(0, se.scrollHeight);
  for (const el of boxes) el.scrollTop = el.scrollHeight;
  return boxes.length;
}
"""

# Поиск кликабельных элементов нужного вида. Найденным присваивается data-wr-id,
# по нему Python потом находит элемент и нажимает.
# kind: loadmore | toggle | reject | close
FIND_JS = r"""
(arg) => {
  const re = new RegExp(arg.re, 'i');
  const skip = new RegExp(arg.skip, 'i');
  window.__wr = window.__wr || {n: 0};
  const all = [];
  const visit = (root) => root.querySelectorAll('*').forEach(el => { all.push(el); if (el.shadowRoot) visit(el.shadowRoot); });
  visit(document);
  const label = (el) => (((el.innerText || el.value || '') + '').replace(/\s+/g, ' ').trim()) || (el.getAttribute('aria-label') || el.getAttribute('title') || '').trim();
  const vis = (el) => { const cs = getComputedStyle(el); if (cs.display === 'none' || cs.visibility === 'hidden') return false; const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const path = (el) => {
    const parts = []; let e = el;
    for (let i = 0; i < 5 && e && e.nodeType === 1; i++) {
      let idx = 0, s = e; while ((s = s.previousElementSibling)) idx++;
      parts.push(e.tagName + idx);
      e = e.parentElement || (e.getRootNode && e.getRootNode().host) || null;
    }
    return parts.join('>');
  };
  const BANNER = '[role="dialog"], [aria-modal="true"], [id*="cookie" i], [class*="cookie" i], [id*="consent" i], [class*="consent" i], [id*="cmp" i], [class*="cmp" i], [id*="onetrust" i], [id*="gdpr" i], [class*="gdpr" i], [class*="privacy" i], [id*="privacy" i]';
  const MODAL = '[role="dialog"], [aria-modal="true"], [class*="modal" i], [class*="popup" i], [class*="overlay" i], [id*="popup" i], [id*="modal" i], [class*="newsletter" i], [class*="subscribe" i]';
  const out = [];
  for (const el of all) {
    if (out.length >= arg.limit) break;
    const tag = el.tagName.toUpperCase();
    const role = (el.getAttribute('role') || '').toLowerCase();
    const type = (el.type || '').toLowerCase();
    const interactive = tag === 'BUTTON' || tag === 'A' || tag === 'SUMMARY' || role === 'button' || role === 'tab' ||
                        (tag === 'INPUT' && (type === 'button' || type === 'submit')) || el.hasAttribute('aria-expanded') ||
                        (arg.kind === 'close' && (tag === 'SPAN' || tag === 'DIV' || tag === 'I') && (el.getAttribute('aria-label') || el.getAttribute('data-dismiss') || el.getAttribute('data-bs-dismiss')));
    if (!interactive) continue;
    if (el.disabled || el.getAttribute('aria-disabled') === 'true') continue;
    const text = label(el);
    const href = tag === 'A' ? (el.getAttribute('href') || '') : '';
    const realLink = tag === 'A' && href && !href.startsWith('#') && !href.toLowerCase().startsWith('javascript:');
    if (arg.kind === 'loadmore') {
      if (!text || text.length > 45 || !re.test(text) || skip.test(text) || realLink) continue;
    } else if (arg.kind === 'toggle') {
      const ae = el.getAttribute('aria-expanded'), sel = el.getAttribute('aria-selected');
      const isDetails = tag === 'SUMMARY' && el.parentElement && el.parentElement.tagName === 'DETAILS' && !el.parentElement.open;
      const collapsed = ae === 'false' || (role === 'tab' && sel === 'false') || isDetails;
      if (!collapsed || realLink) continue;
      if (el.closest('nav, header, footer, [role="navigation"], [role="banner"], [role="contentinfo"]')) continue;
      const meta = (el.id || '') + ' ' + (typeof el.className === 'string' ? el.className : '') + ' ' + (el.getAttribute('aria-controls') || '');
      if (/menu|hamburger|navbar|cookie|consent|language|locale|search|filter-reset/i.test(meta)) continue;
      if (skip.test(text)) continue;
    } else if (arg.kind === 'reject' || arg.kind === 'settings' || arg.kind === 'save') {
      if (!text || text.length > 60 || !re.test(text) || realLink) continue;
      if (!el.closest(BANNER)) continue;
    } else if (arg.kind === 'close') {
      const aria = (el.getAttribute('aria-label') || '') + ' ' + (el.getAttribute('data-dismiss') || '') + ' ' + (el.getAttribute('data-bs-dismiss') || '');
      if (!(re.test(text) || re.test(aria))) continue;
      if (realLink) continue;
      const box = el.closest(MODAL) || el.closest(BANNER);
      if (!box) continue;
      const pos = getComputedStyle(box).position;
      const modalLike = box.matches('[role="dialog"], [aria-modal="true"]') || pos === 'fixed' || pos === 'sticky' || pos === 'absolute';
      if (!modalLike) continue;
    }
    if (!vis(el)) continue;
    window.__wr.n += 1;
    const id = String(window.__wr.n);
    el.setAttribute('data-wr-id', id);
    out.push({id: id, text: text.slice(0, 80), tag: tag, role: role, sig: tag + '|' + text.slice(0, 40) + '|' + path(el)});
  }
  return out;
}
"""

# Выключает необязательные переключатели в панели настроек cookie (обязательные неактивны и не трогаются).
UNCHECK_OPTIONAL_JS = r"""
() => {
  const BANNER = '[role="dialog"], [aria-modal="true"], [id*="cookie" i], [class*="cookie" i], [id*="consent" i], [class*="consent" i], [id*="cmp" i], [class*="cmp" i], [id*="onetrust" i], [id*="gdpr" i], [class*="gdpr" i], [class*="privacy" i], [id*="privacy" i]';
  let n = 0, total = 0;
  const visit = (root) => root.querySelectorAll('input[type="checkbox"], [role="switch"], [role="checkbox"]').forEach(el => {
    if (!el.closest(BANNER) || el.disabled || el.getAttribute('aria-disabled') === 'true') return;
    total += 1;
    const on = el.checked === true || el.getAttribute('aria-checked') === 'true';
    if (on) { el.click(); n += 1; }
  });
  visit(document);
  document.querySelectorAll('*').forEach(el => { if (el.shadowRoot) visit(el.shadowRoot); });
  return [n, total];
}
"""

# Последнее средство для баннера cookie без отказа: скрыть его на странице. Согласие при этом не даётся.
HIDE_COOKIE_BANNER_JS = r"""
(hide) => {
  const out = [];
  const vw = window.innerWidth, vh = window.innerHeight;
  document.querySelectorAll('*').forEach(el => {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') return;
    const isDialog = el.matches('[role="dialog"], [aria-modal="true"], dialog[open]');
    const fixed = cs.position === 'fixed' || cs.position === 'sticky';
    if (!isDialog && !fixed) return;
    const t = (el.innerText || '').replace(/\s+/g, ' ').trim();
    if (t.length < 20 || t.length > 1500 || !/cookie|consent|gdpr|куки|кукі|cookies/i.test(t)) return;
    const r = el.getBoundingClientRect();
    if (r.width < 200 || r.height < 40 || r.width * r.height > vw * vh * 0.98) return;
    if (hide) el.style.setProperty('display', 'none', 'important');
    out.push(t.slice(0, 80));
  });
  return out;
}
"""

# Окна и баннеры, которые всё ещё закрывают заметную часть экрана.
OBSTRUCTION_JS = r"""
() => {
  const out = [];
  const vw = window.innerWidth, vh = window.innerHeight;
  const all = [];
  const visit = (root) => root.querySelectorAll('*').forEach(el => { all.push(el); if (el.shadowRoot) visit(el.shadowRoot); });
  visit(document);
  for (const el of all) {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') continue;
    const isDialog = el.matches('[role="dialog"], [aria-modal="true"], dialog[open]');
    const fixed = cs.position === 'fixed' && parseInt(cs.zIndex || '0', 10) >= 100;
    if (!isDialog && !fixed) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 280 || r.height < 160) continue;
    const area = (Math.min(r.right, vw) - Math.max(r.left, 0)) * (Math.min(r.bottom, vh) - Math.max(r.top, 0));
    if (area < 0.25 * vw * vh) continue;
    out.push(((el.innerText || '') + '').replace(/\s+/g, ' ').trim().slice(0, 140));
    if (out.length >= 3) break;
  }
  return out;
}
"""

# Регулярные выражения (как строки для new RegExp) для поиска кнопок.
LOADMORE_RE = (
    r"(load|show|see|view|display|get)\s+(more|all|additional)|more\s+(jobs|results|positions|openings|roles)|"
    r"\bshow all\b|\bsee all\b|\bview all\b|"
    r"(cargar|mostrar|ver)\s+(más|mas|todo|todos|todas)|más\s+(resultados|ofertas|empleos)|"
    r"(voir|afficher|charger)\s+(plus|tout|tous|davantage)|"
    r"(mehr|weitere)\s*(laden|anzeigen|stellen|ergebnisse)?|alle\s+anzeigen|"
    r"(carregar|mostrar|ver)\s+(mais|tudo|todas)|(mostra|carica)\s+(altri|più|tutti)|"
    r"(показать|загрузить|ещё|еще)\s*(ещё|еще|больше|все|всё)?|показати\s*(ще|більше|все)|завантажити\s*(ще|більше)|^\s*ещё\s*$|^\s*ще\s*$|"
    r"^\s*\+?\s*more\s*$|^\s*more\s+\w+\s*$"
)
LOADMORE_RE = LOADMORE_RE  # одна строка для new RegExp

REJECT_RE = (
    r"^(reject( all)?|decline( all)?|deny( all)?|refuse( all)?|only (necessary|essential)( cookies)?|necessary only|essential only|"
    r"continue without (accepting|agreeing)|no,? thanks|"
    r"rechazar( todo| todas| todas las cookies)?|solo (necesarias|esenciales)|continuar sin aceptar|"
    r"refuser( tout)?|tout refuser|continuer sans accepter|"
    r"ablehnen|alle ablehnen|nur notwendige|nur erforderliche|"
    r"rifiuta( tutto| tutti)?|solo necessari|rejeitar( tudo| todos)?|recusar|apenas necess\w+|"
    r"odmítnout|відхилити( все| всі)?|відмовитися|тільки необхідні|"
    r"отклонить( все)?|отказаться|только необходимые|"
    r"(use |allow |accept )?(strictly |only )?(necessary|essential|required) cookies( only)?|"
    r"i do not accept|do not accept|continue with(out)? (necessary|essential)( cookies)?( only)?|"
    r"deny cookies|no cookies|reject non-?essential( cookies)?|decline non-?essential( cookies)?|"
    r"rechazar (las )?no esenciales|refuser les cookies non essentiels)$"
)

CLOSE_RE = (
    r"^(×|✕|✖|x|close|dismiss|no,? thanks|not now|maybe later|skip|cerrar|no,? gracias|ahora no|"
    r"fermer|non merci|plus tard|schließen|nein,? danke|später|chiudi|fechar|"
    r"закрыть|закрити|не сейчас|не зараз|нет,? спасибо|ні,? дякую)$|close|dismiss|cerrar|fermer|schließen|закры|закри"
)

# Нажимать эти кнопки программа не станет никогда: они меняют данные, а не раскрывают их.
SKIP_RE = (
    r"assistant|chatbot|chat with|live chat|ai recruit|virtual agent|ask me|"
    r"delete|remove|logout|log out|sign out|sign in|log in|login|sign up|register|submit|send|buy|pay|checkout|"
    r"subscribe|unsubscribe|apply|save|donate|accept|agree|confirm|upload|download|share|print|reset|clear|cancel|"
    r"eliminar|borrar|cerrar sesión|iniciar sesión|enviar|comprar|pagar|suscri|solicitar|aplicar|guardar|"
    r"supprimer|déconnexion|connexion|envoyer|acheter|payer|postuler|"
    r"löschen|abmelden|anmelden|senden|kaufen|bewerben|"
    r"удалить|выйти|войти|отправить|купить|оплатить|подписаться|откликнуться|сохранить|принять|"
    r"видалити|вийти|увійти|надіслати|купити|сплатити|підписатися|відгукнутися|зберегти|прийняти"
)

# Баннер без кнопки отказа: открываем «Настройки cookie», выключаем необязательное и сохраняем выбор.
SETTINGS_RE = (
    r"^(cookie settings|cookies settings|cookie preferences|manage( cookies| preferences| settings| options)?|customi[sz]e( cookies| settings)?|"
    r"preferences|settings|more options|configure|"
    r"configurar( cookies)?|gestionar( cookies| preferencias)?|personalizar|paramétrer|gérer( les cookies)?|personnalisation|"
    r"einstellungen|cookie-einstellungen|anpassen|налаштування( cookie)?|налаштувати|управління|настройки( cookie)?|настроить)$"
)
SAVE_RE = (
    r"^(save( my)?( preferences| settings| choices| selection)?|confirm( my)?( choices| selection| preferences)?|save and close|"
    r"allow selection|apply|done|"
    r"guardar( preferencias| configuración| selección)?|confirmar( selección| mi selección)?|enregistrer( mes choix)?|valider( mes choix)?|"
    r"speichern|auswahl (speichern|bestätigen)|зберегти( вибір)?|підтвердити( вибір)?|сохранить( выбор)?|подтвердить( выбор)?)$"
)

KNOWN_REJECT_SELECTORS = [
    "#onetrust-reject-all-handler",
    "#CybotCookiebotDialogBodyButtonDecline",
    "#didomi-notice-disagree-button",
    "[data-testid='uc-deny-all-button']",
    ".osano-cm-denyAll",
    ".cmplz-deny",
    ".cky-btn-reject",
    "#cn-refuse-cookie",
    ".iubenda-cs-reject-btn",
    "[data-tid='banner-decline']",
    "button.js-cookie-reject",
]
