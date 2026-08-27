(() => {
  'use strict';
  const status = document.getElementById('status');
  const token = location.hash.slice(1);
  history.replaceState(null, '', '/share');
  if (!token) {
    status.textContent = 'Freigabelink unvollständig.';
    return;
  }
  fetch('/share/resolve', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token }),
  }).then((response) => {
    if (!response.ok) {
      status.textContent = 'Freigabe ungültig oder abgelaufen.';
      return;
    }
    location.replace('/share/view');
  }).catch(() => {
    status.textContent = 'Freigabe konnte nicht geladen werden.';
  });
})();
