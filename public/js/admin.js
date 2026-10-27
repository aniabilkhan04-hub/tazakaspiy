const TYPE_LABELS = {
  dump: '🗑️ Несанкционированная свалка',
  cleaned: '✅ Место убрано волонтёром',
  recycling: '♻️ Пункт переработки',
  tree: '🌳 Место для посадки дерева',
  shop: '🛍 Эко-магазин',
};

let password = sessionStorage.getItem('eco_admin_password') || '';

const loginCard = document.getElementById('login-card');
const pendingSection = document.getElementById('pending-section');
const passwordInput = document.getElementById('password-input');
const loginBtn = document.getElementById('login-btn');
const loginError = document.getElementById('login-error');
const pendingList = document.getElementById('pending-list');
const refreshBtn = document.getElementById('refresh-btn');
const adminError = document.getElementById('admin-error');

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

// Пароль передаётся в заголовке, а не в URL — так он не попадает в историю и логи
function adminFetch(url, options = {}) {
  return fetch(url, {
    ...options,
    headers: { ...(options.headers || {}), 'X-Admin-Password': password },
  });
}

async function errorText(res) {
  try {
    return (await res.json()).error || `Ошибка ${res.status}`;
  } catch {
    return `Ошибка ${res.status}`;
  }
}

function showAdminError(text) {
  adminError.textContent = text || '';
}

async function tryLogin(pw) {
  password = pw;
  let res;
  try {
    res = await adminFetch('/api/admin/pending');
  } catch {
    loginError.textContent = 'Нет связи с сервером';
    return false;
  }
  if (!res.ok) {
    password = '';
    sessionStorage.removeItem('eco_admin_password');
    loginError.textContent = await errorText(res);
    return false;
  }
  sessionStorage.setItem('eco_admin_password', pw);
  loginError.textContent = '';
  loginCard.hidden = true;
  pendingSection.hidden = false;
  renderPending(await res.json());
  return true;
}

function renderPending(rows) {
  if (!rows.length) {
    pendingList.innerHTML = '<div class="pending-empty">Новых заявок нет 🎉</div>';
    return;
  }
  pendingList.innerHTML = rows
    .map((r) => {
      const author = r.author_name ? `Отправил: ${escapeHtml(r.author_name)}` : 'Автор неизвестен';
      // сервер отдаёт время в UTC в формате ISO с «Z» — браузер переводит его в местное
      const date = new Date(r.created_at).toLocaleString('ru-RU');
      return `
        <div class="pending-card" data-id="${r.id}">
          ${r.photo_path ? `<img src="${escapeHtml(r.photo_path)}" alt="Фото" />` : '<div></div>'}
          <div>
            <span class="pending-card__type">${TYPE_LABELS[r.type] || escapeHtml(r.type)}</span>
            <div class="pending-card__desc">${r.description ? escapeHtml(r.description) : '<em>без описания</em>'}</div>
            <div class="pending-card__meta">${author} · ${date}</div>
          </div>
          <div class="pending-card__actions">
            <button class="btn btn--approve" data-action="approve" data-id="${r.id}">Одобрить</button>
            <button class="btn btn--reject" data-action="reject" data-id="${r.id}">Отклонить</button>
          </div>
        </div>`;
    })
    .join('');
}

async function loadPending() {
  let res;
  try {
    res = await adminFetch('/api/admin/pending');
  } catch {
    showAdminError('Нет связи с сервером');
    return;
  }
  if (res.status === 401) {
    sessionStorage.removeItem('eco_admin_password');
    location.reload();
    return;
  }
  if (!res.ok) {
    showAdminError(await errorText(res));
    return;
  }
  showAdminError('');
  renderPending(await res.json());
}

pendingList.addEventListener('click', async (e) => {
  const btn = e.target.closest('button[data-action]');
  if (!btn) return;
  const { action, id } = btn.dataset;
  // блокируем обе кнопки карточки, чтобы нельзя было нажать «Одобрить» дважды
  btn.closest('.pending-card')
    .querySelectorAll('button')
    .forEach((b) => (b.disabled = true));
  try {
    const res = await adminFetch(`/api/admin/reports/${id}/${action}`, { method: 'POST' });
    if (!res.ok) showAdminError(await errorText(res));
    else showAdminError('');
  } catch {
    showAdminError('Нет связи с сервером');
  }
  loadPending();
});

loginBtn.addEventListener('click', async () => {
  loginBtn.disabled = true;
  try {
    await tryLogin(passwordInput.value);
  } finally {
    loginBtn.disabled = false;
  }
});

passwordInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') loginBtn.click();
});

refreshBtn.addEventListener('click', loadPending);

// автоматический вход, если пароль сохранён в этой сессии браузера
if (password) tryLogin(password);
