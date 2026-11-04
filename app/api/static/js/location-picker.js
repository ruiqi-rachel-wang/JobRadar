/* Select stable geographic places; typing text never makes unknown jobs match. */
(() => {
  'use strict';
  let input, list, status, selection = null, options = [], active = -1;
  let timer, controller, generation = 0;
  const cache = new Map();
  const kindLabel = p => p.kind === 'region' ? 'State / region' : p.kind === 'city' ? 'City' : p.kind === 'country' ? 'Country' : 'Workplace';
  function close() {
    if (!list) return;
    list.hidden = true;
    input.setAttribute('aria-expanded', 'false');
    input.removeAttribute('aria-activedescendant');
    active = -1;
  }
  function set(place) {
    generation++;
    clearTimeout(timer);
    controller?.abort();
    selection = place;
    input.value = place.label;
    input.removeAttribute('aria-invalid');
    status.textContent = kindLabel(place) + ' selected';
    close();
  }
  function paint() {
    list.replaceChildren();
    options.forEach((place, index) => {
      const option = document.createElement('button');
      option.type = 'button';
      option.id = 'location-option-' + index;
      option.className = 'location-option';
      option.setAttribute('role', 'option');
      option.setAttribute('aria-selected', String(index === active));
      const label = document.createElement('span'); label.textContent = place.label;
      const kind = document.createElement('small'); kind.textContent = kindLabel(place);
      option.append(label, kind);
      option.addEventListener('mousedown', e => e.preventDefault());
      option.addEventListener('click', () => { set(place); if (typeof applyFilters === 'function') applyFilters(null, 1); });
      list.append(option);
    });
    const credit = document.createElement('a');
    credit.className = 'location-credit'; credit.href = 'https://www.geonames.org/';
    credit.target = '_blank'; credit.rel = 'noopener'; credit.textContent = 'Place names: GeoNames';
    list.append(credit);
    list.hidden = false;
    input.setAttribute('aria-expanded', 'true');
    if (active >= 0) input.setAttribute('aria-activedescendant', 'location-option-' + active);
    else input.removeAttribute('aria-activedescendant');
  }
  async function lookup(query, signal) {
    if (cache.has(query)) return cache.get(query);
    const response = await fetch('/api/locations?q=' + encodeURIComponent(query), { signal });
    if (!response.ok) throw new Error('Location search unavailable');
    const data = await response.json();
    if (cache.size > 80) cache.clear();
    cache.set(query, data);
    return data;
  }
  async function suggest() {
    const query = input.value.trim();
    const version = ++generation;
    controller?.abort(); controller = new AbortController();
    status.textContent = 'Finding locations…';
    try {
      const data = await lookup(query, controller.signal);
      if (version !== generation) return;
      options = data.locations || []; active = -1;
      status.textContent = options.length ? 'Choose a city, state, or country' : 'No matching place. Try a city and state or country.';
      paint();
    } catch (error) {
      if (error.name !== 'AbortError' && version === generation) status.textContent = 'Could not load locations. Try again.';
    }
  }
  window.jobdropLocationPicker = {
    async value() {
      input ||= document.getElementById('f-location');
      const query = input.value.trim();
      if (!query) { selection = null; return ''; }
      if (selection && selection.label === query) return selection.id;
      try {
        const data = await lookup(query);
        if (input.value.trim() !== query) return null;
        if (data.resolved) { set(data.resolved); return data.resolved.id; }
        options = data.locations || []; active = -1; paint();
        input.setAttribute('aria-invalid', 'true');
        status.textContent = 'Choose the city, state, or country you mean.';
        input.focus();
      } catch { status.textContent = 'Could not load locations. Try again.'; }
      return null;
    },
    clear() { selection = null; close(); }
  };
  document.addEventListener('DOMContentLoaded', () => {
    input = document.getElementById('f-location');
    list = document.getElementById('location-options');
    status = document.getElementById('location-status');
    if (!input || !list || !status) return;
    input.addEventListener('input', () => {
      selection = null; generation++; clearTimeout(timer);
      options = []; active = -1; close();
      input.removeAttribute('aria-invalid');
      if (!input.value.trim()) { close(); status.textContent = 'City, state, or country'; return; }
      timer = setTimeout(suggest, 180);
    });
    input.addEventListener('focus', () => { if (!selection) suggest(); });
    input.addEventListener('keydown', e => {
      if (e.key === 'Escape') { close(); return; }
      if ((e.key === 'ArrowDown' || e.key === 'ArrowUp') && options.length && !list.hidden) {
        e.preventDefault();
        active = (active + (e.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length;
        paint(); list.querySelector('#location-option-' + active)?.scrollIntoView({ block:'nearest' });
      }
      if (e.key === 'Enter' && !list.hidden && active >= 0) {
        e.preventDefault(); e.stopImmediatePropagation(); set(options[active]);
        if (typeof applyFilters === 'function') applyFilters(null, 1);
      }
    });
    document.addEventListener('click', e => { if (!e.target.closest('.location-filter')) close(); });
  });
})();
