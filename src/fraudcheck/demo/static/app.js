/* The control desk's page.

   It keeps the session's documents as the server describes them and draws three things from
   that state: the list on the left, the open document, and its panel (choose the type, follow
   the run, read the result, decide). Every text of a document's result is put on the page as
   text, never as HTML. Nothing is loaded from another site. */
'use strict';

(() => {
  const STAGES = [
    { key: 'prepare', label: 'Préparer', icon: 'pages',
      text: 'Le fichier est découpé en une image par page. Deux lecteurs relèvent ses codes et ses mots imprimés, sur cet ordinateur.' },
    { key: 'read', label: 'Lire', icon: 'lines',
      text: "L'IA recopie chaque information imprimée, caractère par caractère, sans rien corriger." },
    { key: 'inspect', label: 'Regarder', icon: 'search',
      text: "L'IA compare l'image au formulaire officiel de ce type de document : zones attendues, lettrage, netteté." },
    { key: 'check', label: 'Contrôler', icon: 'list',
      text: "Chaque contrôle répond à une seule question : traces laissées dans le fichier, règles sur les valeurs lues, constats sur l'image." },
    { key: 'decide', label: 'Décider', icon: 'scale',
      text: "Le document n'est signalé que si un contrôle de confiance échoue. Un doute ne suffit pas." },
  ];
  const PERSON = { key: 'validate', label: 'Valider', icon: 'user-check',
    text: "L'outil signale et explique. Vous confirmez ou vous corrigez : votre décision remplace la sienne." };
  const SKIPPED = {
    no_saved_answer: "Étape non faite : ce document n'a pas de réponse enregistrée, et aucun appel réseau n'est permis ici.",
    azure_not_configured: "Étape non faite : l'accès à Azure n'est pas configuré sur ce poste.",
    azure_unreachable: "Étape non faite : Azure n'a pas répondu (réseau ou droits).",
    azure_no_answer: "Étape non faite : Azure n'a pas donné de réponse.",
    no_page: "Étape non faite : le fichier n'a pas pu être ouvert.",
  };
  const KINDS = { file: 'Fichier', values: 'Valeurs lues', picture: 'Image' };
  const FILE_KINDS = { pdf_text: 'PDF contenant du texte', pdf_scan: 'PDF scanné', picture: 'image' };
  const ZOOMS = [0, 1, 1.5, 2, 3]; // 0: fitted to the panel; then the page's width as a share of the panel's
  const SVG = 'http://www.w3.org/2000/svg';
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const stepHold = () => (reducedMotion.matches ? 0 : 750); // each step stays on screen at least this long, in ms

  const state = {
    setup: null,
    docs: [],
    selected: null,
    blur: true, // real people's papers: blurred until the handler asks to see them
    zoom: 0,
    page: 1,
    turns: {}, // "key:page" -> degrees chosen in the viewer
    play: null, // the run being followed step by step: { key, shown, since }
    chosen: {}, // key -> nature picked in the list, before the run starts
    retyping: null, // key of a finished document whose type is being changed
    drafts: {}, // key -> the decision being written
  };

  // ---- Small helpers ------------------------------------------------------------------

  const $ = (id) => document.getElementById(id);

  function el(tag, props, ...children) {
    const node = document.createElement(tag);
    for (const [name, value] of Object.entries(props || {})) {
      if (value === null || value === undefined || value === false) continue;
      if (name === 'class') node.className = value;
      else if (name === 'text') node.textContent = value;
      else if (name.startsWith('on')) node.addEventListener(name.slice(2), value);
      else if (value === true) node[name] = true;
      else node.setAttribute(name, value);
    }
    for (const child of children.flat(3)) {
      if (child === null || child === undefined || child === false) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  function icon(name) {
    const svg = document.createElementNS(SVG, 'svg');
    svg.setAttribute('class', 'icon');
    svg.setAttribute('aria-hidden', 'true');
    const use = document.createElementNS(SVG, 'use');
    use.setAttribute('href', `#i-${name}`);
    svg.append(use);
    return svg;
  }

  const tag = (text, kind, iconName) => el('span', { class: `tag${kind ? ` tag--${kind}` : ''}` }, iconName && icon(iconName), text);
  const plural = (count, one, many) => `${count} ${count > 1 ? many : one}`;
  const seconds = (value) => `${Number(value).toLocaleString('fr-FR', { maximumFractionDigits: 1 })} s`;
  const find = (key) => state.docs.find((doc) => doc.key === key) || null;
  const busy = (doc) => doc.status === 'queued' || doc.status === 'running';
  const finished = (doc) => doc.stages.filter((stage) => stage.status === 'done' || stage.status === 'skipped').length;
  const playing = (doc) => (state.play && state.play.key === doc.key ? state.play : null);
  // Documents are analysed one at a time: a queued one waits only while another is running.
  const waiting = (doc) => doc.status === 'queued' && state.docs.some((other) => other.status === 'running');

  function upsert(doc) {
    const index = state.docs.findIndex((known) => known.key === doc.key);
    if (index < 0) state.docs.push(doc);
    else state.docs[index] = doc;
  }

  async function api(method, url, body, headers) {
    const options = { method, headers: { ...(headers || {}) } };
    if (body instanceof Blob) options.body = body;
    else if (body !== undefined) {
      options.body = JSON.stringify(body);
      options.headers['Content-Type'] = 'application/json';
    }
    let response;
    try {
      response = await fetch(url, options);
    } catch (error) {
      throw new Error("L'outil ne répond plus. Vérifiez qu'il est toujours lancé.");
    }
    if (response.status === 204) return null;
    let data = null;
    try {
      data = await response.json();
    } catch (error) { /* an answer without a body */ }
    if (!response.ok) {
      throw new Error(data && typeof data.detail === 'string' ? data.detail : `La demande a échoué (${response.status}).`);
    }
    return data;
  }

  function toast(message, kind) {
    const node = el('div', { class: `toast${kind ? ` toast--${kind}` : ''}`, text: message });
    $('toasts').append(node);
    setTimeout(() => node.remove(), 7000);
  }

  function shortNature(nature) {
    for (const family of state.setup ? state.setup.families : []) {
      const option = family.natures.find((known) => known.nature === nature);
      if (option) return option.label === nature ? nature : `${family.label} · ${option.label}`;
    }
    return nature || '';
  }

  // ---- What the handler does ----------------------------------------------------------

  async function addFiles(files) {
    let first = null;
    for (const file of Array.from(files || [])) {
      try {
        const doc = await api('POST', '/api/documents', file,
          { 'Content-Type': 'application/octet-stream', 'X-File-Name': encodeURIComponent(file.name) });
        upsert(doc);
        if (doc.duplicate) toast(`« ${file.name} » est déjà dans la liste.`, 'info');
        first = first || doc.key;
      } catch (error) {
        toast(`${file.name} : ${error.message}`);
      }
    }
    if (first) select(first);
    else render();
  }

  function select(key) {
    const doc = find(key);
    state.selected = doc ? key : null;
    state.page = 1;
    state.zoom = 0;
    state.retyping = null;
    state.play = doc && busy(doc) ? { key, shown: finished(doc), since: performance.now() } : null;
    history.replaceState(null, '', doc ? `#${key}` : location.pathname); // a reloaded page opens the same document
    render();
  }

  const chosenNature = (doc) => state.chosen[doc.key] || doc.nature || doc.suggested_nature || null;

  async function startRun(doc) {
    const nature = chosenNature(doc);
    if (!nature) return;
    try {
      upsert(await api('POST', `/api/documents/${doc.key}/analysis`, { nature }));
      delete state.drafts[doc.key];
      state.retyping = null;
      state.play = { key: doc.key, shown: 0, since: performance.now() };
      render();
    } catch (error) {
      toast(error.message);
    }
  }

  async function removeDocument(doc) {
    const lost = doc.review ? ' Sa décision ne sera plus dans le journal.' : '';
    if (!window.confirm(`Retirer « ${doc.name} » de la session ?${lost}`)) return;
    try {
      await api('DELETE', `/api/documents/${doc.key}`);
      state.docs = state.docs.filter((known) => known.key !== doc.key);
      select(null);
    } catch (error) {
      toast(error.message);
    }
  }

  async function saveDecision(doc) {
    const draft = state.drafts[doc.key];
    if (!draft || !draft.choice) return;
    if (draft.choice === 'fake' && !draft.reason.trim()) {
      draft.error = 'Un document jugé faux demande un motif.';
      renderPanel(doc);
      return;
    }
    try {
      upsert(await api('POST', `/api/documents/${doc.key}/decision`,
        { fake: draft.choice === 'fake', reason: draft.reason, generated: draft.generated }));
      delete state.drafts[doc.key];
      render();
    } catch (error) {
      draft.error = error.message;
      renderPanel(doc);
    }
  }

  async function reopenDecision(doc) {
    const review = doc.review;
    try {
      upsert(await api('DELETE', `/api/documents/${doc.key}/decision`));
      state.drafts[doc.key] = { choice: review.fake ? 'fake' : 'genuine', reason: review.reason, generated: review.generated, error: '' };
      render();
    } catch (error) {
      toast(error.message);
    }
  }

  // ---- Following the runs -------------------------------------------------------------

  let polling = false;

  async function poll() {
    if (polling) return;
    polling = true;
    try {
      for (const doc of state.docs.filter(busy)) {
        const before = JSON.stringify([doc.status, doc.stages]);
        const fresh = await api('GET', `/api/documents/${doc.key}`);
        upsert(fresh);
        if (before !== JSON.stringify([fresh.status, fresh.stages])) {
          renderSide();
          if (fresh.key === state.selected && !playing(fresh)) renderMain();
        }
      }
    } catch (error) { /* the next round tries again; a lost server shows on the next action */ } finally {
      polling = false;
    }
  }

  /* The steps shown follow the real ones, one at a time, each held on screen a moment. */
  function tick() {
    const play = state.play;
    if (!play) return;
    const doc = find(play.key);
    if (!doc) {
      state.play = null;
      return;
    }
    const now = performance.now();
    if (doc.status === 'failed' || (play.shown >= STAGES.length && !busy(doc))) {
      state.play = null;
      render();
      return;
    }
    if (play.shown < finished(doc) && now - play.since >= stepHold()) {
      play.shown += 1;
      play.since = now;
      if (play.shown >= STAGES.length && !busy(doc)) state.play = null;
      render();
      return;
    }
    const clock = $('elapsed');
    if (clock) clock.textContent = seconds((now - play.since) / 1000);
  }

  /* Each stage as it is shown: the server's report, held back while an earlier step is still on screen. */
  function shownStages(doc) {
    const play = playing(doc);
    return STAGES.map((stage, index) => {
      const real = doc.stages.find((known) => known.key === stage.key) || { key: stage.key, status: 'pending', facts: {} };
      if (!play || index < play.shown) return real;
      if (index === play.shown) return { ...real, status: waiting(doc) ? 'pending' : 'running' };
      return { ...real, status: 'pending' };
    });
  }

  const resultShown = (doc) => doc.status === 'done' && doc.result && !playing(doc);

  // ---- Drawing: the side ---------------------------------------------------------------

  function verdictTag(doc) {
    if (doc.review) return doc.review.fake ? tag('Faux', 'alert', 'user-check') : tag('Authentique', 'ok', 'user-check');
    const decision = doc.result.decision;
    if (decision.flagged) return tag('Signalé', 'alert');
    return decision.line === 'unreadable' ? tag('Illisible', 'warn') : tag('Non signalé', 'ok');
  }

  function stateTag(doc) {
    const play = playing(doc); // the list says what the open document shows, not more
    if (play) return tag(`En cours ${Math.min(play.shown + 1, STAGES.length)}/${STAGES.length}`);
    if (doc.status === 'uploaded') return tag('À lancer', 'action');
    if (doc.status === 'queued') return tag(waiting(doc) ? 'En attente' : 'En cours');
    if (doc.status === 'running') return tag(`En cours ${Math.min(finished(doc) + 1, STAGES.length)}/${STAGES.length}`);
    if (doc.status === 'failed') return tag('Échec', 'alert');
    return verdictTag(doc);
  }

  function stateLine(doc) {
    const type = doc.nature ? shortNature(doc.nature) : 'Type à choisir';
    if (doc.status !== 'done' || playing(doc)) return type;
    return `${doc.review ? 'Validé' : 'À valider'} · ${type}`;
  }

  function renderSide() {
    $('doc-count').textContent = state.docs.length ? String(state.docs.length) : '';
    $('doc-empty').hidden = state.docs.length > 0;
    $('doc-list').replaceChildren(...state.docs.map((doc) => el('li', null,
      el('button', { type: 'button', class: 'doc-item', 'aria-current': doc.key === state.selected ? 'true' : null,
        onclick: () => select(doc.key) },
      el('span', { class: 'doc-item__name', text: doc.name }),
      el('span', { class: 'doc-item__type', text: stateLine(doc) }),
      el('span', { class: 'doc-item__state' }, stateTag(doc))))));
    $('journal').hidden = !state.docs.some((doc) => doc.review);
  }

  function renderMode() {
    const mode = state.setup.mode;
    let name = 'archive';
    let title = 'Sans réseau';
    let text = "Les réponses de l'IA déjà enregistrées sont rejouées. Tout autre document reçoit les contrôles faits sur ce poste.";
    if (mode.live) {
      name = 'bolt';
      title = 'IA en direct';
      text = "Un document sans réponse enregistrée est lu par le service Azure de l'équipe.";
    } else if (mode.live_asked) {
      title = 'IA en direct indisponible';
      text = "L'accès à Azure n'est pas configuré : seules les réponses enregistrées sont rejouées.";
    }
    $('mode').replaceChildren(icon(name), el('span', null, el('strong', { text: title }), text));
    $('privacy').replaceChildren(icon('lock'), mode.live
      ? "Les fichiers restent sur cet ordinateur et sont effacés à la fermeture de l'outil. Seule la lecture par l'IA passe par le service Azure de l'équipe."
      : "Les fichiers restent sur cet ordinateur et sont effacés à la fermeture de l'outil. Rien n'est envoyé sur le réseau.");
  }

  function renderHow() {
    const total = state.setup.checks.total;
    const steps = [...STAGES, PERSON].map((stage, index) => el('li', { class: `how__step${stage === PERSON ? ' how__step--person' : ''}` },
      el('span', { class: 'how__badge' }, el('span', { class: 'how__number', text: String(index + 1) }), stage.label),
      el('p', { text: stage.key === 'check' ? `${total} contrôles, chacun pour une seule question : traces laissées dans le fichier, règles sur les valeurs lues, constats sur l'image.` : stage.text })));
    $('how').replaceChildren(...steps);
  }

  // ---- Drawing: the open document -------------------------------------------------------

  function renderMain() {
    const doc = find(state.selected);
    $('view-add').hidden = Boolean(doc);
    $('view-doc').hidden = !doc;
    if (!doc) return;
    $('doc-name').textContent = doc.name;
    const parts = [doc.doc_id !== 'document' ? doc.doc_id : null, doc.nature || 'type à choisir',
      plural(doc.pages, 'page', 'pages'), doc.format.toUpperCase()];
    $('doc-sub').textContent = parts.filter(Boolean).join(' · ');
    $('doc-retype').hidden = !(doc.status === 'done' || doc.status === 'failed') || state.retyping === doc.key;
    $('doc-remove').disabled = busy(doc);
    renderStepper(doc);
    renderViewer(doc);
    renderPanel(doc);
  }

  function renderStepper(doc) {
    const shown = shownStages(doc);
    const steps = STAGES.map((stage, index) => {
      const status = shown[index].status;
      const mark = status === 'done' ? icon('check') : status === 'skipped' ? icon('minus')
        : status === 'running' ? el('span', { class: 'spinner' }) : String(index + 1);
      return el('li', { class: `step step--${status}`, 'aria-current': status === 'running' ? 'step' : null },
        el('span', { class: 'step__dot' }, mark), el('span', { class: 'step__label', text: stage.label }));
    });
    const personState = doc.review ? 'done' : resultShown(doc) ? 'current' : 'pending';
    steps.push(el('li', { class: `step step--person step--${personState}`, 'aria-current': personState === 'current' ? 'step' : null },
      el('span', { class: 'step__dot' }, icon(doc.review ? 'check' : 'user-check')),
      el('span', { class: 'step__label', text: doc.review ? 'Validé par vous' : 'À vous de valider' })));
    $('stepper').replaceChildren(...steps);
  }

  function pageTurn(doc) {
    const chosen = state.turns[`${doc.key}:${state.page}`];
    if (chosen !== undefined) return chosen;
    const read = doc.stages.find((stage) => stage.key === 'read');
    const turns = (read && read.facts && read.facts.turns) || {};
    return Number(turns[String(state.page)] || 0); // the pipeline's own reading says which pages are sideways
  }

  function renderViewer(doc) {
    const viewer = $('viewer');
    const image = $('page-img');
    state.page = Math.min(Math.max(1, state.page), doc.pages);
    const source = `/api/documents/${doc.key}/pages/${state.page}?turn=${pageTurn(doc)}`;
    if (image.dataset.source !== source) {
      image.dataset.source = source;
      viewer.classList.remove('viewer--empty');
      image.src = source;
    }
    image.alt = `Page ${state.page} du document ${doc.name}${state.blur ? ' (floutée)' : ''}`;
    const zoom = ZOOMS[state.zoom];
    image.style.width = zoom ? `${zoom * 100}%` : '';
    viewer.classList.toggle('viewer--zoomed', zoom > 0);
    viewer.classList.toggle('viewer--blurred', state.blur);
    viewer.classList.toggle('viewer--busy', busy(doc) || Boolean(playing(doc)));
    $('zoom-label').textContent = zoom ? `${zoom * 100} %` : 'Ajusté';
    $('zoom-out').disabled = state.zoom === 0;
    $('zoom-in').disabled = state.zoom === ZOOMS.length - 1;
    $('pager').hidden = doc.pages < 2;
    $('page-label').textContent = `Page ${state.page} / ${doc.pages}`;
    $('page-prev').disabled = state.page <= 1;
    $('page-next').disabled = state.page >= doc.pages;
    const toggle = $('blur-toggle');
    toggle.setAttribute('aria-pressed', String(state.blur));
    toggle.replaceChildren(icon(state.blur ? 'eye' : 'eye-off'), state.blur ? 'Afficher le document' : 'Flouter le document');
    renderStamp(doc);
  }

  function renderStamp(doc) {
    const stamp = $('stamp');
    let word = null;
    let kind = '';
    let note = "avis de l'outil";
    if (doc.review) {
      word = doc.review.fake ? 'Faux' : 'Authentique';
      kind = doc.review.fake ? 'alert' : '';
      note = 'décision validée par une personne';
    } else if (resultShown(doc)) {
      const decision = doc.result.decision;
      word = decision.flagged ? 'Signalé' : decision.line === 'unreadable' ? 'Illisible' : 'Non signalé';
      kind = decision.flagged ? 'alert' : decision.line === 'unreadable' ? 'warn' : '';
    }
    const signature = word ? `${doc.key}|${word}|${note}` : '';
    if (stamp.dataset.signature === signature) return;
    stamp.dataset.signature = signature;
    stamp.hidden = !word;
    if (!word) return;
    stamp.className = `stamp${kind ? ` stamp--${kind}` : ''}`;
    stamp.replaceChildren(word, el('small', { text: note }));
  }

  // ---- Drawing: the panel ---------------------------------------------------------------

  function renderPanel(doc) {
    const panel = $('panel');
    let mode = 'run';
    if (doc.status === 'uploaded' || state.retyping === doc.key) mode = 'choose';
    else if (resultShown(doc)) mode = 'result';
    else if (doc.status === 'failed' && !playing(doc)) mode = 'failed';
    // Redrawing the same part of the same document keeps the reader where they were.
    const same = panel.dataset.shown === `${doc.key}:${mode}` && panel.firstElementChild;
    const keepScroll = same ? panel.firstElementChild.scrollTop : 0;
    const parts = { choose: chooser, result: (shown) => [resultPart(shown), decisionPart(shown)], failed: failedPart, run: runPart };
    panel.replaceChildren(...[parts[mode](doc)].flat());
    panel.dataset.shown = `${doc.key}:${mode}`;
    if (keepScroll) panel.firstElementChild.scrollTop = keepScroll;
  }

  const scroll = (...children) => el('div', { class: 'panel__scroll' }, children);
  const foot = (...children) => el('div', { class: 'panel__foot' }, children);

  function chooser(doc) {
    const chosen = chosenNature(doc);
    const start = el('button', { type: 'button', class: 'button', disabled: !chosen, onclick: () => startRun(doc) },
      icon('check'), doc.status === 'uploaded' ? 'Lancer le contrôle' : 'Relancer avec ce type');
    const families = state.setup.families.map((family) => el('fieldset', { class: 'family' },
      el('legend', { text: family.label }),
      el('div', { class: 'choices' }, family.natures.map((option) => el('label', { class: 'choice' },
        el('input', { type: 'radio', name: 'nature', value: option.nature, checked: option.nature === chosen,
          onchange: () => {
            state.chosen[doc.key] = option.nature;
            start.disabled = false;
          } }),
        el('span', { text: option.label }))))));
    const known = doc.known && doc.suggested_nature && el('p', { class: 'note' }, icon('archive'),
      'Ce fichier fait partie du jeu de documents fourni : son type vient du tableur du jeu. Vous pouvez le changer.');
    const cancel = state.retyping === doc.key && el('button', { type: 'button', class: 'button button--quiet',
      onclick: () => {
        state.retyping = null;
        delete state.chosen[doc.key];
        renderMain();
      } }, 'Annuler');
    return [
      scroll(
        el('h2', { text: "De quel document s'agit-il ?" }),
        el('p', { class: 'muted', text: "Le type indique à l'outil quelles informations lire et quelles règles appliquer." }),
        known,
        el('div', { class: 'families' }, families)),
      foot(el('div', { class: 'actions' }, start, cancel)), // always in view, whatever the height of the window
    ];
  }

  function factTags(stage) {
    const facts = stage.facts || {};
    const tags = [];
    if (stage.key === 'prepare') {
      if (facts.error) tags.push(tag('fichier illisible', 'warn'));
      if (facts.pages !== undefined) tags.push(tag(plural(facts.pages, 'page', 'pages')));
      if (facts.kind) tags.push(tag(FILE_KINDS[facts.kind] || facts.kind));
      if (facts.codes !== null && facts.codes !== undefined) tags.push(tag(plural(facts.codes, 'code lu', 'codes lus')));
      if (facts.words !== null && facts.words !== undefined) tags.push(tag(plural(facts.words, 'mot imprimé relevé', 'mots imprimés relevés')));
    } else if (stage.key === 'read') {
      if (facts.read === true) tags.push(tag(plural(facts.fields, 'information relevée', 'informations relevées')));
      if (facts.read === false) tags.push(tag("l'IA n'a pas pu lire ce document", 'warn'));
      if (facts.turned) tags.push(tag(plural(facts.turned, 'page redressée', 'pages redressées')));
    } else if (stage.key === 'inspect') {
      if (facts.inspected === true) {
        tags.push(tag(plural(facts.zones, 'zone du formulaire comparée', 'zones du formulaire comparées')));
        if (facts.tiles) tags.push(tag(plural(facts.tiles, 'zoom examiné', 'zooms examinés')));
        tags.push(tag(plural(facts.observations, 'remarque sur le lettrage', 'remarques sur le lettrage')));
      }
      if (facts.inspected === false) tags.push(tag("l'IA n'a pas pu regarder ce document", 'warn'));
    } else if (stage.key === 'check' && facts.applied !== undefined) {
      tags.push(tag(`${facts.applied} contrôles appliqués sur ${facts.total}`));
      tags.push(tag(plural(facts.pass, 'conforme', 'conformes'), 'ok'));
      if (facts.fail) tags.push(tag(`${facts.fail} en échec`, 'alert'));
      if (facts.cannot_tell) tags.push(tag(`${facts.cannot_tell} sans réponse`, 'warn'));
    } else if (stage.key === 'decide' && facts.flagged !== undefined) {
      tags.push(facts.flagged ? tag('signalé : un contrôle de confiance a échoué', 'alert') : tag('non signalé', 'ok'));
    }
    if (stage.source === 'live') tags.push(tag('appel en direct', 'action', 'bolt'));
    else if (stage.source) tags.push(tag('réponse enregistrée', null, 'archive'));
    return tags;
  }

  function traceRow(stage, report) {
    const status = report.status;
    const mark = status === 'done' ? icon('check') : status === 'skipped' ? icon('skip')
      : status === 'running' ? el('span', { class: 'spinner' }) : icon(stage.icon);
    const time = status === 'running' ? el('span', { class: 'trace__time', id: 'elapsed' })
      : status === 'done' && report.seconds >= 0.1 && el('span', { class: 'trace__time', text: seconds(report.seconds) });
    const finishedRow = status === 'done' || status === 'skipped';
    return el('li', { class: `trace__row trace__row--${status}` },
      el('span', { class: 'trace__dot' }, mark),
      el('span', { class: 'trace__title' }, stage.label, time),
      el('p', { class: 'trace__text', text: status === 'skipped' ? SKIPPED[report.note] || 'Étape non faite.' : stage.text }),
      finishedRow && el('div', { class: 'trace__facts' }, factTags(report)));
  }

  function trace(doc) {
    const shown = shownStages(doc);
    const rows = STAGES.map((stage, index) => traceRow(stage, shown[index]));
    const personState = doc.review ? 'done' : 'pending';
    rows.push(el('li', { class: `trace__row trace__row--person trace__row--${personState}` },
      el('span', { class: 'trace__dot' }, icon(doc.review ? 'check' : 'user-check')),
      el('span', { class: 'trace__title' }, 'Valider — par vous'),
      el('p', { class: 'trace__text', text: PERSON.text })));
    return el('ol', { class: 'trace' }, rows);
  }

  function runPart(doc) {
    return scroll(
      el('h2', { text: 'Contrôle en cours' }),
      waiting(doc) && el('p', { class: 'note' }, icon('clock'), 'En attente : un autre document est en cours de contrôle.'),
      trace(doc));
  }

  function failedPart(doc) {
    return scroll(
      el('h2', { text: "Le contrôle n'a pas pu aboutir" }),
      el('p', { class: 'note note--alert' }, icon('alert'),
        `L'outil s'est arrêté sur ce document (${doc.error || 'erreur'}). Rien n'a été décidé. Vous pouvez relancer le contrôle ou changer le type.`),
      el('div', { class: 'actions' },
        el('button', { type: 'button', class: 'button', onclick: () => startRun(doc) }, 'Relancer le contrôle')));
  }

  function verdictCard(doc) {
    const decision = doc.result.decision;
    const unreadable = decision.line === 'unreadable';
    const kind = decision.flagged ? 'alert' : unreadable ? 'warn' : '';
    const title = decision.flagged ? 'Signalé comme faux' : unreadable ? 'Document illisible' : 'Non signalé';
    const tags = [];
    if (decision.fraud_type) tags.push(tag(decision.fraud_type, 'alert'));
    if (decision.generated) tags.push(tag('image produite ou redessinée par une IA', 'alert'));
    const text = decision.flagged ? null
      : unreadable ? "L'outil n'a pas pu le lire : il ne le signale pas, et le laisse à une personne."
        : "Aucun contrôle de confiance n'a échoué sur ce document.";
    const confidence = decision.flagged && decision.confidence !== null && el('div', { class: 'confidence' },
      el('div', { class: 'confidence__value', text: `${Math.round(decision.confidence * 100)} %` }),
      el('div', { class: 'confidence__label', text: 'de confiance' }),
      el('div', { class: 'confidence__bar' }, confidenceFill(decision.confidence)));
    return el('div', { class: `verdict${kind ? ` verdict--${kind}` : ''}` },
      el('span', { class: 'verdict__icon' }, icon(decision.flagged ? 'alert' : unreadable ? 'help' : 'check')),
      el('div', null,
        el('div', { class: 'verdict__over', text: doc.review ? "Avis de l'outil (votre décision le remplace)" : "Avis de l'outil" }),
        el('div', { class: 'verdict__title', text: title }),
        tags.length > 0 && el('div', { class: 'verdict__tags' }, tags)),
      confidence,
      text && el('p', { class: 'verdict__text', text }));
  }

  function confidenceFill(confidence) {
    const fill = el('div', { class: 'confidence__fill' });
    fill.style.width = `${Math.round(confidence * 100)}%`;
    return fill;
  }

  function checkGroup(title, hint, rows, kind, open) {
    if (!rows.length) return null;
    const mark = { fail: 'x', cannot_tell: 'help', pass: 'check', score: 'check' };
    return el('details', { class: `group group--${kind}`, open },
      el('summary', null, el('span', { class: 'group__count', text: String(rows.length) }),
        el('span', null, title, hint && el('small', { text: ` — ${hint}` })), icon('down')),
      el('ul', { class: 'checks' }, rows.map((row) => el('li', { class: `check--${row.outcome}` },
        icon(mark[row.outcome] || 'check'),
        el('span', { text: row.evidence || '' }),
        el('span', { class: 'checks__meta' }, tag(KINDS[row.kind] || row.kind), tag(row.name, 'code'))))));
  }

  function resultPart(doc) {
    const result = doc.result;
    const decision = result.decision;
    const rows = result.checks;
    const failed = rows.filter((row) => row.outcome === 'fail');
    const applied = rows.filter((row) => row.outcome !== 'not_applicable').length;
    const partial = result.skipped.length > 0 && el('p', { class: 'note note--warn' }, icon('alert'),
      el('span', null, el('strong', { text: 'Contrôle partiel. ' }),
        `L'IA ${result.skipped.length > 1 ? "n'a ni lu ni regardé" : result.skipped[0] === 'read' ? "n'a pas lu" : "n'a pas regardé"} ce document : `
        + 'seuls les autres contrôles ont répondu.'
        + (decision.flagged ? '' : ' « Non signalé » ne veut donc pas dire « vérifié ».')));
    const unreadable = decision.line === 'unreadable' && decision.review_reasons.length > 0
      && el('ul', { class: 'clues' }, decision.review_reasons.map((reason) => el('li', null, icon('help'), reason)));
    return scroll(
      verdictCard(doc),
      partial,
      decision.justification && [el('h3', { text: 'Pourquoi' }), el('p', { class: 'why', text: decision.justification })],
      decision.clues.length > 0 && [el('h3', { text: 'Indices' }),
        el('ul', { class: 'clues' }, decision.clues.map((clue) => el('li', null, icon('alert'), clue)))],
      unreadable,
      el('h3', { text: `Les contrôles : ${applied} appliqués à ce document, sur ${rows.length}` }),
      el('div', null,
        checkGroup('En échec, de confiance', 'ce sont eux qui signalent le document', failed.filter((row) => row.trusted), 'alert', true),
        checkGroup('En échec, à titre indicatif', 'à regarder, sans suffire à signaler', failed.filter((row) => !row.trusted), 'warn', true),
        checkGroup("N'ont pas pu répondre", "information illisible ou absente : ce n'est pas un indice de fraude",
          rows.filter((row) => row.outcome === 'cannot_tell'), 'warn', failed.length === 0),
        checkGroup('Conformes', null, rows.filter((row) => row.outcome === 'pass' || row.outcome === 'score'), 'ok', false)),
      el('details', { class: 'group' },
        el('summary', null, el('span', null, 'Déroulé du contrôle', el('small', { text: ' — les étapes, et ce que chacune a fait' })), icon('down')),
        el('div', { class: 'panel__scroll' }, trace(doc))));
  }

  function decisionPart(doc) {
    if (doc.review) return decidedCard(doc);
    const flagged = doc.result.decision.flagged;
    const draft = state.drafts[doc.key] || (state.drafts[doc.key] = { choice: null, reason: '', generated: false, error: '' });
    const redraw = () => renderPanel(doc);
    const option = (value, title, hint, iconName) => el('label', { class: `option option--${value}` },
      el('input', { type: 'radio', name: 'decision', value, checked: draft.choice === value,
        onchange: () => {
          draft.choice = value;
          draft.error = '';
          redraw();
        } }),
      el('span', { class: 'option__box' }, icon(iconName), el('span', null, el('strong', { text: title }), el('small', { text: hint }))));
    const reason = draft.choice === 'fake' && el('div', { class: 'field' },
      el('label', { for: 'reason', text: 'Motif (ne recopiez aucun nom, numéro ou date du document)' }),
      el('textarea', { id: 'reason', rows: '2', maxlength: '600', oninput: (event) => {
        draft.reason = event.target.value;
      } }, draft.reason),
      el('label', { class: 'checkbox' }, el('input', { type: 'checkbox', checked: draft.generated,
        onchange: (event) => {
          draft.generated = event.target.checked;
        } }), 'Document généré ou redessiné par une IA'));
    return el('form', { class: 'decision', onsubmit: (event) => {
      event.preventDefault();
      saveDecision(doc);
    } },
    el('div', { class: 'decision__head' },
      el('h2', null, icon('user-check'), 'Votre décision'),
      el('p', { text: "L'outil propose, vous décidez : votre décision remplace la sienne." })),
    el('div', { class: 'decision__choices' },
      option('fake', 'Faux', flagged ? "je confirme l'avis de l'outil" : "je corrige l'avis de l'outil", 'alert'),
      option('genuine', 'Authentique', flagged ? "je corrige l'avis de l'outil" : "je confirme l'avis de l'outil", 'check')),
    reason,
    draft.error && el('p', { class: 'field__error', role: 'alert', text: draft.error }),
    el('div', { class: 'actions' },
      el('button', { type: 'submit', class: 'button', disabled: !draft.choice }, 'Enregistrer ma décision')));
  }

  function decidedCard(doc) {
    const review = doc.review;
    const next = state.docs.find((other) => other.key !== doc.key && !other.review && other.status !== 'failed');
    const time = new Date(review.at).toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
    return el('div', { class: 'decision' },
      el('div', { class: 'decided' },
        el('span', { class: 'decided__icon' }, icon('user-check')),
        el('strong', { text: `Votre décision : ${review.fake ? 'faux' : 'authentique'}` }),
        el('div', { class: 'actions' },
          el('button', { type: 'button', class: 'button button--quiet', onclick: () => reopenDecision(doc) }, 'Modifier'),
          next && el('button', { type: 'button', class: 'button', onclick: () => select(next.key) }, 'Document suivant')),
        el('p', { text: `${review.agrees ? "Vous confirmez l'avis de l'outil." : "Vous corrigez l'avis de l'outil : votre décision le remplace."} Enregistrée à ${time}.` }),
        review.reason && el('p', { text: `Motif : ${review.reason}` })));
  }

  function render() {
    renderSide();
    renderMain();
  }

  // ---- Wiring ---------------------------------------------------------------------------

  function wire() {
    const input = $('file-input');
    const browse = () => input.click();
    $('dropzone').addEventListener('click', browse);
    $('add-button').addEventListener('click', () => {
      if (state.selected === null) browse();
      else select(null);
    });
    input.addEventListener('change', () => {
      addFiles(input.files);
      input.value = '';
    });

    // Files can be dropped anywhere on the window.
    let depth = 0;
    const carriesFiles = (event) => event.dataTransfer && Array.from(event.dataTransfer.types || []).includes('Files');
    window.addEventListener('dragenter', (event) => {
      if (!carriesFiles(event)) return;
      event.preventDefault();
      depth += 1;
      $('drop-veil').hidden = false;
    });
    window.addEventListener('dragover', (event) => {
      if (carriesFiles(event)) event.preventDefault();
    });
    window.addEventListener('dragleave', (event) => {
      if (!carriesFiles(event)) return;
      depth = Math.max(0, depth - 1);
      if (depth === 0) $('drop-veil').hidden = true;
    });
    window.addEventListener('drop', (event) => {
      if (!carriesFiles(event)) return;
      event.preventDefault();
      depth = 0;
      $('drop-veil').hidden = true;
      addFiles(event.dataTransfer.files);
    });

    const current = () => find(state.selected);
    const viewer = (change) => () => {
      const doc = current();
      if (!doc) return;
      change(doc);
      renderViewer(doc);
    };
    $('blur-toggle').addEventListener('click', viewer(() => {
      state.blur = !state.blur;
    }));
    $('unblur').addEventListener('click', viewer(() => {
      state.blur = false;
    }));
    $('zoom-in').addEventListener('click', viewer(() => {
      state.zoom = Math.min(ZOOMS.length - 1, state.zoom + 1);
    }));
    $('zoom-out').addEventListener('click', viewer(() => {
      state.zoom = Math.max(0, state.zoom - 1);
    }));
    $('turn').addEventListener('click', viewer((doc) => {
      state.turns[`${doc.key}:${state.page}`] = (pageTurn(doc) + 90) % 360;
    }));
    $('page-prev').addEventListener('click', viewer(() => {
      state.page -= 1;
    }));
    $('page-next').addEventListener('click', viewer(() => {
      state.page += 1;
    }));
    $('page-img').addEventListener('error', () => $('viewer').classList.add('viewer--empty'));
    $('doc-remove').addEventListener('click', () => current() && removeDocument(current()));
    $('doc-retype').addEventListener('click', () => {
      state.retyping = state.selected;
      renderMain();
    });
  }

  async function init() {
    wire();
    try {
      state.setup = await api('GET', '/api/setup');
      state.docs = await api('GET', '/api/documents'); // a reloaded page finds the session again
    } catch (error) {
      toast(error.message);
      return;
    }
    renderMode();
    renderHow();
    select(location.hash.slice(1) || null);
    setInterval(poll, 450);
    setInterval(tick, 120);
  }

  init();
})();
