// Development only. Never discard a form that may contain unsaved work.
(() => {
  let version, pending = false;
  async function check() {
    try {
      const response = await fetch('/api/dev-version', {cache:'no-store'});
      if (!response.ok) return;
      const current = (await response.json()).version;
      if (version && current !== version) pending = true;
      version = current;
      if (pending) {
        if (document.querySelector('#main form, dialog[open]')) {
          const notice = document.querySelector('#notice');
          notice.hidden = false;
          notice.textContent = 'A LocalBooks update is ready. Save your work and leave the editor to refresh.';
        } else location.reload();
      }
    } catch (_) { /* The server can briefly be unavailable during restart. */ }
  }
  setInterval(check, 1000);
  check();
})();
