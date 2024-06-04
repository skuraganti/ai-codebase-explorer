const $ = id => document.getElementById(id), el = (tag, text = '', parent) => { const node = document.createElement(tag); node.textContent = text; parent.append(node); return node; }, clear = node => node.replaceChildren();
const controls = $('controls'), workspace = $('workspace');
let data, visibleFiles = [], symbols = [];
function panel(title, id, wide = false) { const section = el('section', '', workspace); section.className = `panel${wide ? ' wide' : ''}`; const h = el('h2', title, section); h.id = `${id}-title`; section.setAttribute('aria-labelledby', h.id); return section; }
function btn(text, id, parent, action) { const b = el('button', text, parent); b.id = id; b.type = 'button'; b.addEventListener('click', action); return b; }
function setup() { controls.className = 'controls'; let label = el('label', 'Language', controls), lang = el('select', '', label); lang.id = 'language'; [['All languages', 'all'], ['Python', 'python'], ['TypeScript', 'typescript']].forEach(([text, value]) => lang.add(new Option(text, value))); label = el('label', 'Find symbol', controls); const query = el('input', '', label); query.id = 'query'; query.type = 'search'; query.maxLength = 100; query.placeholder = 'qualified symbol name'; label = el('label', 'Inspect file', controls); const file = el('select', '', label); file.id = 'file'; const action = btn('Show reverse impact', 'impact', controls, impact); const arch = panel('Saved architecture map', 'architecture', true); el('p', 'Static file links show direction from importer to imported file. Only saved exact resolved edges appear.', arch); const viz = el('div', '', arch); viz.id = 'architecture'; el('div', '', arch).id = 'counts'; const table = el('div', '', arch); table.id = 'edge-preview'; const sy = panel('Qualified symbol search', 'symbols'); el('div', 'Search a saved symbol name.', sy).id = 'symbols'; const im = panel('Reverse impact · exact edges', 'impact-output'); el('div', 'Select a file to inspect reverse reachability.', im).id = 'impact-output'; const details = panel('Pinned source file & definitions', 'file-details', true); el('div', '', details).id = 'file-details'; el('div', '', details).id = 'definitions'; const pre = el('pre', 'Choose a file to inspect source lines.', details); pre.id = 'source-lines'; const edges = panel('Saved resolved import edges', 'edges', true), scroll = el('div', '', edges); scroll.className = 'scroll'; const tableEl = el('table', '', scroll), cap = el('caption', 'Exact saved resolved imports', tableEl); cap.className = 'visually-hidden'; const head = el('thead', '', tableEl), row = el('tr', '', head); ['Importer', 'Imported file'].forEach(text => { const th = el('th', text, row); th.scope = 'col'; }); el('tbody', '', tableEl).id = 'edges'; lang.addEventListener('change', renderSymbols); query.addEventListener('input', renderSymbols); file.addEventListener('change', renderFile); }
function repoName(id) { return data.repositories.find(r => r.id === id)?.repo || id; }
function setupSelect() { const file = $('file'), previous = file.value; file.replaceChildren(); visibleFiles.forEach(f => file.add(new Option(`${repoName(f.repo_id)} · ${f.path}`, f.id))); if (visibleFiles.some(f => f.id === previous))
    file.value = previous; }
function renderSymbols() { if (!data)
    return; const query = $('query').value.trim().toLocaleLowerCase(), lang = $('language').value; visibleFiles = data.files.filter(f => (lang === 'all' || f.language === lang)); setupSelect(); renderFile(); symbols = data.files.flatMap(file => (file.symbols || []).map(symbol => ({ file, symbol }))).filter(({ file, symbol }) => (lang === 'all' || file.language === lang) && (!query || `${symbol.qualname} ${symbol.search_text || ''}`.toLocaleLowerCase().includes(query))).slice(0, 100); const root = $('symbols'); clear(root); if (!symbols.length) {
    root.textContent = query ? 'No saved symbols match.' : 'No symbol records available.';
    return;
} for (const { file, symbol } of symbols) {
    const p = el('p', '', root), button = el('button', `${symbol.qualname} · ${symbol.kind} · ${file.path}:${symbol.start_line}`, p);
    button.type = 'button';
    button.className = 'secondary';
    button.addEventListener('click', () => { $('file').value = file.id; renderFile(); });
} }
function renderArchitecture() {
    const root = $('architecture');
    clear(root);
    const ns = 'http://www.w3.org/2000/svg', svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('viewBox', '0 0 800 900');
    svg.setAttribute('role', 'group');
    svg.setAttribute('aria-label', 'Directed file imports; each file node can be selected. Full edge table follows.');
    svg.style.cssText = 'width:100%;background:#f6f7fb;border-radius:8px';
    const defs = document.createElementNS(ns, 'defs'), marker = document.createElementNS(ns, 'marker');
    for (const [k, v] of Object.entries({ id: 'import-arrow', viewBox: '0 0 10 10', refX: 12, refY: 5, markerWidth: 5, markerHeight: 5, orient: 'auto-start-reverse' }))
        marker.setAttribute(k, v);
    const arrow = document.createElementNS(ns, 'path');
    arrow.setAttribute('d', 'M 0 0 L 10 5 L 0 10 z');
    arrow.setAttribute('fill', '#526789');
    marker.append(arrow);
    defs.append(marker);
    svg.append(defs);
    const positions = new Map();
    data.repositories.forEach((repo, ri) => {
        const label = document.createElementNS(ns, 'text');
        label.setAttribute('x', ri * 400 + 20);
        label.setAttribute('y', 30);
        label.setAttribute('fill', '#24324f');
        label.textContent = repo.repo;
        svg.append(label);
        data.files.filter(f => f.repo_id === repo.id).forEach((f, i) => positions.set(f.id, { x: ri * 400 + 55 + (i % 4) * 93, y: 75 + Math.floor(i / 4) * 74 }));
    });
    for (const edge of data.edges) {
        const a = positions.get(edge.source_file_id), b = positions.get(edge.target_file_id);
        if (!a || !b)
            continue;
        const line = document.createElementNS(ns, 'line');
        for (const [k, v] of Object.entries({ x1: a.x, y1: a.y, x2: b.x, y2: b.y, stroke: '#526789', 'stroke-width': 1, opacity: .32, 'marker-end': 'url(#import-arrow)' }))
            line.setAttribute(k, v);
        svg.append(line);
    }
    for (const f of data.files) {
        const pos = positions.get(f.id), circle = document.createElementNS(ns, 'circle');
        for (const [k, v] of Object.entries({ cx: pos.x, cy: pos.y, r: 9, fill: '#345baf', tabindex: 0, role: 'button', 'aria-label': `${repoName(f.repo_id)} ${f.path}`, 'data-file-id': f.id }))
            circle.setAttribute(k, v);
        const title = document.createElementNS(ns, 'title');
        title.textContent = f.path;
        circle.append(title);
        const choose = () => { $('language').value = 'all'; visibleFiles = data.files; setupSelect(); $('file').value = f.id; renderSymbols(); };
        circle.addEventListener('click', choose);
        circle.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            choose();
        } });
        svg.append(circle);
        const label = document.createElementNS(ns, 'text');
        label.setAttribute('x', pos.x);
        label.setAttribute('y', pos.y + 23);
        label.setAttribute('text-anchor', 'middle');
        label.setAttribute('font-size', 8);
        label.setAttribute('fill', '#24324f');
        label.textContent = f.path.split('/').pop().slice(0, 18);
        svg.append(label);
    }
    root.append(svg);
    const count = $('counts');
    clear(count);
    data.repositories.forEach(repo => el('p', `${repo.repo} · ${repo.tag} · ${repo.license} · ${repo.commit}`, count));
    el('p', `${data.counts.files} files · ${data.counts.symbols.toLocaleString()} symbols · ${data.counts.imports} imports · ${data.edges.length} distinct exact directed file edges · ${data.counts.parse_errors} parse errors. Unknown/dynamic imports are omitted.`, count);
}
function renderEdgeTable() { const body = $('edges'); clear(body); const fileNames = new Map(data.files.map(f => [f.id, f])); data.edges.filter(e => e.target_file_id).forEach(edge => { const a = fileNames.get(edge.source_file_id), b = fileNames.get(edge.target_file_id); if (!a || !b)
    return; const tr = el('tr', '', body); el('td', `${repoName(a.repo_id)} · ${a.path}:${edge.line || '—'}`, tr); el('td', `${repoName(b.repo_id)} · ${b.path}`, tr); }); }
function renderFile() { if (!data)
    return; const file = data.files.find(f => f.id === $('file').value), details = $('file-details'); clear(details); clear($('definitions')); if (!file) {
    details.textContent = 'No source file selected.';
    $('source-lines').textContent = '';
    $('impact-output').textContent = 'No file selected.';
    return;
} const repo = data.repositories.find(r => r.id === file.repo_id); el('h3', `${repo?.repo || file.repo_id} · ${file.path}`, details); const p = el('p', '', details); el('span', `${file.language} · ${file.lines} lines · ${file.bytes} bytes · parse ${file.parse_status} · SHA-256 ${file.sha256} · `, p); const link = el('a', 'Pinned original source', p); link.href = file.url; link.target = '_blank'; link.rel = 'noreferrer'; if (file.diagnostics !== '[]')
    el('p', `Saved diagnostics: ${file.diagnostics}`, details); const definitions = $('definitions'); el('h3', 'Saved definitions', definitions); const defs = file.symbols || []; if (!defs.length)
    el('p', 'No definition records for this file.', definitions); defs.slice(0, 100).forEach(symbol => el('p', `${symbol.kind} ${symbol.qualname} · lines ${symbol.start_line}–${symbol.end_line} · ${symbol.signature || ''}`, definitions)); $('source-lines').textContent = (file.source || '').split('\n').map((line, i) => `${String(i + 1).padStart(4, ' ')}  ${line}`).join('\n'); impact(); document.querySelectorAll('[data-file-id]').forEach(node => node.setAttribute('fill', node.getAttribute('data-file-id') === file.id ? '#a63c23' : '#345baf')); }
function impact() { const start = $('file').value, out = $('impact-output'); clear(out); if (!start) {
    out.textContent = 'Select a source file first.';
    return;
} const incoming = new Map(); data.edges.forEach(edge => { if (!edge.target_file_id)
    return; if (!incoming.has(edge.target_file_id))
    incoming.set(edge.target_file_id, []); incoming.get(edge.target_file_id).push(edge.source_file_id); }); const visited = new Set([start]), queue = [[start, [start]]], paths = []; while (queue.length && paths.length < 100) {
    const [current, path] = queue.shift();
    for (const parent of incoming.get(current) || []) {
        if (visited.has(parent))
            continue;
        visited.add(parent);
        const next = [...path, parent];
        paths.push(next);
        queue.push([parent, next]);
    }
} const fileById = new Map(data.files.map(f => [f.id, f])); if (!paths.length) {
    el('p', 'No upstream importer is reachable through saved resolved edges.', out);
    return;
} el('p', `${paths.length} reverse-BFS paths shown · exact saved edges only; dynamic/unknown imports are not resolved.`, out); paths.forEach(path => { const row = el('p', '', out); path.slice().reverse().forEach((id, index) => { const f = fileById.get(id); if (index)
    row.append(document.createTextNode(' → ')); if (f) {
    const a = el('a', `${repoName(f.repo_id)}:${f.path}`, row);
    a.href = f.url;
    a.target = '_blank';
    a.rel = 'noreferrer';
} }); }); }
setup();
fetch('./demo-data.json').then(response => { if (!response.ok)
    throw Error('Fixture unavailable'); return response.json(); }).then(json => { if (!Array.isArray(json.files) || !Array.isArray(json.edges) || !Array.isArray(json.repositories) || !json.files.every(f => typeof f.source === 'string' && Array.isArray(f.symbols)))
    throw Error('Unexpected source atlas structure'); data = json; const attribution = $('source-attribution'); clear(attribution); json.repositories.forEach((repo, index) => { if (index)
    attribution.append(document.createTextNode(' · ')); const link = el('a', `${repo.repo} ${repo.tag} (${repo.license})`, attribution); link.href = `https://github.com/${repo.repo}/tree/${repo.commit}`; link.target = '_blank'; link.rel = 'noreferrer'; }); renderSymbols(); renderArchitecture(); renderEdgeTable(); renderFile(); $('status').textContent = `${data.files.length} pinned source files, ${data.counts.symbols.toLocaleString()} saved symbols and ${data.edges.length} exact edges loaded. No source execution.`; }).catch(error => { $('status').textContent = `Cannot load code atlas: ${error.message}. Reload to try again.`; $('status').className = 'status error'; });
function labelScrollableContent() {
    document.querySelectorAll('.scroll, pre').forEach(node => {
        node.tabIndex = 0;
        if (!node.hasAttribute('role'))
            node.setAttribute('role', 'region');
        if (!node.hasAttribute('aria-label'))
            node.setAttribute('aria-label', `${node.closest('section')?.querySelector('h2')?.textContent || 'Recorded'} scrollable content`);
    });
}
new MutationObserver(labelScrollableContent).observe(document.getElementById('main'), { childList: true, subtree: true });
labelScrollableContent();
