// Координаты центра города Атырау
const ATYRAU_CENTER = [47.1167, 51.9167];

const TYPE_META = {
  dump: { emoji: '🗑️', color: '#B3452F', label: 'Несанкционированная свалка' },
  cleaned: { emoji: '✅', color: '#2F6F62', label: 'Место убрано' },
  recycling: { emoji: '♻️', color: '#2F6F62', label: 'Пункт переработки' },
  tree: { emoji: '🌳', color: '#3E7D3A', label: 'Место для посадки дерева' },
  shop: { emoji: '🛍', color: '#C97B3D', label: 'Эко-магазин' },
};

const map = L.map('map').setView(ATYRAU_CENTER, 13);

L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 19,
  attribution: '&copy; OpenStreetMap contributors',
}).addTo(map);

let markersLayer = L.layerGroup().addTo(map);
let currentFilter = '';

function makeIcon(type) {
  const meta = TYPE_META[type] || { emoji: '📍', color: '#2F6F62' };
  return L.divIcon({
    className: '',
    html: `<div class="marker-badge" style="background:${meta.color}"><span>${meta.emoji}</span></div>`,
    iconSize: [34, 34],
    iconAnchor: [17, 30],
    popupAnchor: [0, -30],
  });
}

// id заявки -> маркер. Нужен, чтобы при автообновлении не пересоздавать все маркеры
// (иначе открытый попап закрывается каждые 30 секунд).
const markersById = new Map();
let loadSeq = 0;

async function loadReports(type = '') {
  const seq = ++loadSeq;
  let reports;
  try {
    const url = type ? `/api/reports?type=${encodeURIComponent(type)}` : '/api/reports';
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    reports = await res.json();
  } catch (err) {
    console.error('Не удалось загрузить точки', err);
    return;
  }
  // быстрое переключение фильтров: устаревший ответ не должен перезаписать свежий
  if (seq !== loadSeq) return;

  const incoming = new Set(reports.map((r) => r.id));
  for (const [id, marker] of markersById) {
    if (!incoming.has(id)) {
      markersLayer.removeLayer(marker);
      markersById.delete(id);
    }
  }

  reports.forEach((r) => {
    if (markersById.has(r.id)) return;
    const meta = TYPE_META[r.type] || {};
    const marker = L.marker([r.lat, r.lon], { icon: makeIcon(r.type) });

    const authorLine =
      r.type === 'cleaned' && r.author_name
        ? `<div class="popup__author">🙌 Убрал(а): ${escapeHtml(r.author_name)}</div>`
        : '';

    marker.bindPopup(`
      <div class="popup">
        ${r.photo_path ? `<img src="${r.photo_path}" alt="Фото" />` : ''}
        <div class="popup__type">${meta.label || r.type}</div>
        ${r.description ? `<div class="popup__desc">${escapeHtml(r.description)}</div>` : ''}
        ${authorLine}
      </div>
    `);

    markersLayer.addLayer(marker);
    markersById.set(r.id, marker);
  });
}

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

async function loadLeaderboard() {
  const list = document.getElementById('leaderboard');
  let rows;
  try {
    const res = await fetch('/api/leaderboard');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    rows = await res.json();
  } catch (err) {
    console.error('Не удалось загрузить рейтинг', err);
    if (list.textContent.trim() === 'Загрузка…') {
      list.innerHTML = '<li class="leaderboard__empty">Не удалось загрузить рейтинг</li>';
    }
    return;
  }

  if (!rows.length) {
    list.innerHTML = '<li class="leaderboard__empty">Пока никто не убирал мусор. Будьте первым! 🌱</li>';
    return;
  }

  list.innerHTML = rows
    .map((u, i) => {
      const name = u.first_name || u.username || 'Волонтёр';
      return `
        <li class="leaderboard__item">
          <span class="leaderboard__rank">${i + 1}</span>
          <span class="leaderboard__name">${escapeHtml(name)}</span>
          <span class="leaderboard__score">${u.rating}</span>
        </li>`;
    })
    .join('');
}

document.querySelectorAll('.filter-chip').forEach((btn) => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.filter-chip').forEach((b) => b.classList.remove('is-active'));
    btn.classList.add('is-active');
    currentFilter = btn.dataset.type || '';
    loadReports(currentFilter);
  });
});

// Ссылка на бота берётся с сервера (BOT_USERNAME в .env); если не задана — кнопка скрывается
async function setupBotLink() {
  const link = document.getElementById('bot-link');
  try {
    const res = await fetch('/api/config');
    const { bot_username } = await res.json();
    if (bot_username) {
      link.href = `https://t.me/${bot_username}`;
      return;
    }
  } catch (err) {
    console.error('Не удалось загрузить настройки', err);
  }
  link.style.display = 'none';
}

setupBotLink();
loadReports();
loadLeaderboard();
setInterval(loadLeaderboard, 30000);
setInterval(() => loadReports(currentFilter), 30000);
