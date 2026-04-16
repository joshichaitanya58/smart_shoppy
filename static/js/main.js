/* ═══════════════════════════════════════════════════
   Smart Shoppy  —  Main JavaScript
   Fixes: suggestion z-index, price display, stability
   ═══════════════════════════════════════════════════ */

/* ═══════════════════════════════════════════════════
   Smart Shoppy  —  Main JavaScript
   Adds a sleek top progress bar for better UX
   ═══════════════════════════════════════════════════ */

// ─── Top Progress Bar ────────────────────────────────────────────────────────
let topProgressInterval;
let topProgressWidth = 0;

function createTopProgressBar() {
  if (document.getElementById('ssTopProgress')) return;
  const bar = document.createElement('div');
  bar.id = 'ssTopProgress';
  bar.style.cssText = `
    position: fixed;
    top: 0;
    left: 0;
    width: 0%;
    height: 3px;
    background: linear-gradient(90deg, #0d6efd, #0dcaf0, #0d6efd);
    box-shadow: 0 0 10px #0d6efd;
    z-index: 9999;
    transition: width 0.2s ease;
    border-radius: 0 2px 2px 0;
  `;
  document.body.appendChild(bar);
}

function startTopProgress() {
  createTopProgressBar();
  const bar = document.getElementById('ssTopProgress');
  topProgressWidth = 10;
  bar.style.width = topProgressWidth + '%';
  clearInterval(topProgressInterval);
  topProgressInterval = setInterval(() => {
    if (topProgressWidth < 90) {
      topProgressWidth += Math.random() * 10;
      bar.style.width = Math.min(topProgressWidth, 90) + '%';
    }
  }, 300);
}

function finishTopProgress() {
  clearInterval(topProgressInterval);
  const bar = document.getElementById('ssTopProgress');
  if (bar) {
    bar.style.width = '100%';
    setTimeout(() => {
      bar.style.opacity = '0';
      setTimeout(() => {
        bar.remove();
      }, 300);
    }, 200);
  }
}

// ─── Override existing progress functions ────────────────────────────────────
let mainProgressInterval;
let mainProgressWidth = 0;

window.startProgressBar = function(message) {
  startTopProgress();  // also start the top bar
  const ov = document.getElementById('loadingOverlay');
  if (!ov) return;
  if (message) {
    const lbl = document.getElementById('overlayLabel');
    if (lbl) lbl.textContent = message;
  }
  ov.style.display = 'flex';

  const bar = document.getElementById('ssOverlayProgressBar');
  if (bar) {
    mainProgressWidth = 5;
    bar.style.width = mainProgressWidth + '%';
    clearInterval(mainProgressInterval);
    mainProgressInterval = setInterval(() => {
      if (mainProgressWidth < 95) {
        mainProgressWidth += (Math.random() * 8 + 2) * (1 - mainProgressWidth/100);
        bar.style.width = mainProgressWidth + '%';
      }
    }, 400);
  }
};

window.finishProgressBar = function() {
  finishTopProgress();
  clearInterval(mainProgressInterval);
  const ov = document.getElementById('loadingOverlay');
  const bar = document.getElementById('ssOverlayProgressBar');
  if (bar) bar.style.width = '100%';

  if (!ov) return;
  setTimeout(() => {
    ov.style.display = 'none';
    if (bar) bar.style.width = '0%';
  }, 400);
};


// ─── Theme Toggle ─────────────────────────────────────────────────────────────
const html        = document.documentElement;
const themeToggle = document.getElementById('themeToggle');

function setTheme(theme) {
  html.setAttribute('data-bs-theme', theme);
  try { localStorage.setItem('ss_theme', theme); } catch(e) {}
  const icon = themeToggle?.querySelector('.theme-icon');
  if (icon) {
    icon.className = theme === 'dark' ? 'bi bi-sun-fill theme-icon' : 'bi bi-moon-fill theme-icon';
  }
}

function initTheme() {
  try {
    const saved = localStorage.getItem('ss_theme') ||
      (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    setTheme(saved);
  } catch(e) {
    setTheme('light');
  }
}

themeToggle?.addEventListener('click', () => {
  setTheme(html.getAttribute('data-bs-theme') === 'dark' ? 'light' : 'dark');
});

initTheme();

// ─── Toast Notifications ──────────────────────────────────────────────────────
function showToast(message, type = 'info', duration = 3500) {
  let container = document.querySelector('.ss-toast-container');
  if (!container) {
    container = document.createElement('div');
    container.className = 'ss-toast-container';
    document.body.appendChild(container);
  }
  const icons = {
    success: 'bi-check-circle-fill',
    danger:  'bi-exclamation-circle-fill',
    info:    'bi-info-circle-fill',
    warning: 'bi-exclamation-triangle-fill',
  };
  const toast = document.createElement('div');
  toast.className = `ss-toast ss-toast-${type}`;
  toast.innerHTML = `<i class="bi ${icons[type] || icons.info}"></i> ${message}`;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.animation = 'fadeOut 0.3s ease forwards';
    setTimeout(() => toast.remove(), 300);
  }, duration);
}
window.showToast = showToast;

// ─── Search Tabs ─────────────────────────────────────────────────────────────
document.querySelectorAll('.ss-tab').forEach(btn => {
  btn.addEventListener('click', () => {
    const tab = btn.dataset.tab;
    document.querySelectorAll('.ss-tab').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.ss-tab-panel').forEach(p => p.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById(`tab-${tab}`)?.classList.add('active');

    // Ensure only the active tab's input is submitted
    const searchInput = document.getElementById('searchInput');
    const urlInput = document.getElementById('urlInput');
    if (tab === 'text') {
      if (searchInput) searchInput.disabled = false;
      if (urlInput) urlInput.disabled = true;
    } else if (tab === 'url') {
      if (urlInput) urlInput.disabled = false;
      if (searchInput) searchInput.disabled = true;
    }
  });
});

// ─── Search Autocomplete ──────────────────────────────────────────────────────
const searchInput  = document.getElementById('searchInput');
const suggestionBox = document.getElementById('searchSuggestions');

// ─── Search Clear Button ──────────────────────────────────────────────────────
if (searchInput) {
  const wrapper = searchInput.closest('.ss-input-wrap');
  if (wrapper) {
    const clearBtn = document.createElement('i');
    clearBtn.className = 'bi bi-x ss-clear-btn';
    clearBtn.style.display = searchInput.value.length > 0 ? 'flex' : 'none';
    wrapper.appendChild(clearBtn);

    searchInput.addEventListener('input', () => {
      clearBtn.style.display = searchInput.value.length > 0 ? 'flex' : 'none';
    });

    clearBtn.addEventListener('click', () => {
      searchInput.value = '';
      clearBtn.style.display = 'none';
      hideSuggestions();
      searchInput.focus();
    });
  }
}

const popularSuggestions = [
  'iPhone 15', 'iPhone 15 Pro', 'Samsung Galaxy S24', 'OnePlus 12', 'Realme GT 6',
  'MacBook Air M2', 'MacBook Pro M3', 'Dell XPS 15', 'HP Spectre x360', 'Lenovo ThinkPad',
  'AirPods Pro', 'Sony WH-1000XM5', 'boAt Rockerz 550', 'Noise ColorFit Pro 4',
  'Samsung 4K TV', 'LG OLED TV', 'Mi Smart TV', 'Sony Bravia',
  'PlayStation 5', 'Xbox Series X', 'Nintendo Switch OLED',
  'Nike Air Max', 'Adidas Ultraboost', 'Puma RS-X',
  'Dyson V15', 'Philips Air Fryer', 'Instant Pot', 'Prestige Cooker',
  'Canon EOS R50', 'Sony Alpha A7 III', 'GoPro Hero 12',
  'Apple Watch Series 9', 'Samsung Galaxy Watch 6', 'Garmin Forerunner',
  'iPad Pro', 'iPad Air', 'Samsung Galaxy Tab S9',
];

let suggestionTimeout;

searchInput?.addEventListener('input', () => {
  clearTimeout(suggestionTimeout);
  const query = searchInput.value.trim();
  if (query.length < 2) { hideSuggestions(); return; }

  suggestionTimeout = setTimeout(async () => {
    try {
      const res = await fetch(`/api/suggestions?q=${encodeURIComponent(query)}`);
      if (res.ok) {
        const suggestions = await res.json();
        showSuggestions(suggestions);
      } else {
        console.warn('Suggestion API returned status:', res.status);
        hideSuggestions();
      }
    } catch(e) {
      console.warn('Suggestion fetch failed', e);
      hideSuggestions();
    }
  }, 300);
});

function showSuggestions(items) {
  if (!suggestionBox || items.length === 0) { hideSuggestions(); return; }
  const query = searchInput ? searchInput.value.trim() : '';
  suggestionBox.innerHTML = items.map(item =>
    `<div class="ss-suggestion-item" onclick="selectSuggestion('${item.replace(/'/g, "\\'")}')">
       <div class="d-flex align-items-center flex-grow-1">
         <i class="bi bi-search ss-suggestion-icon me-2"></i>
         <span class="ss-suggestion-text">${highlightMatch(item, query)}</span>
       </div>
       <i class="bi bi-arrow-up-left ss-suggestion-arrow"></i>
     </div>`
  ).join('');
  suggestionBox.style.display = 'block';
}

function hideSuggestions() {
  if (suggestionBox) suggestionBox.style.display = 'none';
}

function selectSuggestion(query) {
  if (searchInput) searchInput.value = query;
  hideSuggestions();
  const form = searchInput?.closest('form');
  if (form) {
    const btn = form.querySelector('button[type="submit"]');
    if (btn && !btn.disabled) {
      btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> Searching...';
      btn.disabled = true;
    }
    startProgressBar('Searching...');
    form.submit();
  }
}
window.selectSuggestion = selectSuggestion;

function highlightMatch(text, query) {
  if (!query) return escapeHtml(text);
  const escapedQuery = query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const regex = new RegExp(`(${escapedQuery})`, 'gi');
  return text.split(regex).map(part => 
    part.toLowerCase() === query.toLowerCase() 
      ? `<strong class="text-primary">${escapeHtml(part)}</strong>` 
      : escapeHtml(part)
  ).join('');
}

function escapeHtml(str) {
  const d = document.createElement('div');
  d.textContent = str;
  return d.innerHTML;
}

// Close suggestions when clicking outside
document.addEventListener('click', (e) => {
  if (!e.target.closest('.ss-input-wrap')) hideSuggestions();
});

// Keyboard navigation in suggestions
searchInput?.addEventListener('keydown', (e) => {
  if (!suggestionBox || suggestionBox.style.display === 'none') return;
  const items = suggestionBox.querySelectorAll('.ss-suggestion-item');
  const active = suggestionBox.querySelector('.ss-suggestion-item.active');
  let idx = Array.from(items).indexOf(active);

  if (e.key === 'ArrowDown') {
    e.preventDefault();
    idx = Math.min(idx + 1, items.length - 1);
    items.forEach(i => i.classList.remove('active'));
    items[idx]?.classList.add('active');
    if (items[idx]) searchInput.value = items[idx].textContent.trim();
  } else if (e.key === 'ArrowUp') {
    e.preventDefault();
    idx = Math.max(idx - 1, 0);
    items.forEach(i => i.classList.remove('active'));
    items[idx]?.classList.add('active');
    if (items[idx]) searchInput.value = items[idx].textContent.trim();
  } else if (e.key === 'Escape') {
    hideSuggestions();
  } else if (e.key === 'Enter') {
    hideSuggestions();
  }
});

// ─── Wishlist Count ───────────────────────────────────────────────────────────
async function updateWishlistCount() {
  try {
    const res = await fetch('/api/wishlist/count');
    if (!res.ok) return;
    const data  = await res.json();
    const badge = document.getElementById('wishlist-count');
    if (badge && data.count > 0) {
      badge.textContent = data.count > 99 ? '99+' : data.count;
      badge.style.display = 'inline';
    }
  } catch(e) { /* ignore */ }
}
updateWishlistCount();

// ─── Navbar Scroll Effect ─────────────────────────────────────────────────────
const navbar = document.getElementById('mainNavbar');
window.addEventListener('scroll', () => {
  if (navbar) {
    navbar.style.boxShadow = window.scrollY > 50 ? '0 2px 20px rgba(0,0,0,.12)' : '';
  }
}, { passive: true });

// ─── Loading State on Form Submit ─────────────────────────────────────────────
document.querySelectorAll('form').forEach(form => {
  const action = form.getAttribute('action') || '';
  if (form.id === 'mainSearchForm' || action.includes('search') || action.includes('results') || action === '/' || form.querySelector('#searchInput') || form.querySelector('.ss-search-input')) {
    form.addEventListener('submit', async (e) => {
    // --- 1. Async URL Extraction Logic ---
    const urlInput = document.getElementById('urlInput');
    if (form.id === 'mainSearchForm' && urlInput && !urlInput.disabled && urlInput.value.trim() !== '') {
      e.preventDefault();
      const url = urlInput.value.trim();
      
      if (!url.startsWith('http')) {
        showToast('Please enter a valid URL starting with http:// or https://', 'warning');
        return;
      }

      const btn = form.querySelector('button[type="submit"]');
      if (btn) {
        btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> Analyzing URL...';
        btn.disabled = true;
      }
      startProgressBar('Extracting product details from URL...');
      
      try {
        const res = await fetch('/api/fetch-url', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url })
        });
        const data = await res.json();
        
        if (data.success && data.product_name) {
          // Redirect to compare page with extracted name
          window.location.href = `/results?q=${encodeURIComponent(data.product_name)}`;
        } else {
          finishProgressBar();
          showToast(data.error || 'Failed to extract product. Try text search.', 'danger');
          if (btn) {
            btn.disabled = false;
            btn.innerHTML = '<i class="bi bi-arrow-right me-1"></i> Compare';
          }
        }
      } catch(err) {
        finishProgressBar();
        showToast('Network error while analyzing URL.', 'danger');
        if (btn) {
          btn.disabled = false;
          btn.innerHTML = '<i class="bi bi-arrow-right me-1"></i> Compare';
        }
      }
      return; // Stop standard form submission
    }

    // --- 2. Standard Text Search Logic ---
    if (form.dataset.submitting === '1') {
      e.preventDefault();
      return;
    }
    form.dataset.submitting = '1';
    const btn = form.querySelector('button[type="submit"]');
    startProgressBar('Searching...'); // Trigger progress bar
    if (btn && !btn.disabled) {
      btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> Searching...';
      btn.disabled  = true;
      // Re-enable after 15s to prevent permanent lock
      setTimeout(() => {
        form.dataset.submitting = '0';
        btn.disabled  = false;
        btn.innerHTML = '<i class="bi bi-search me-1"></i> Search';
      }, 15000);
    }
  });
  }
});

// ─── Keyboard Shortcuts ───────────────────────────────────────────────────────
document.addEventListener('keydown', (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
    e.preventDefault();
    searchInput?.focus();
    searchInput?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }
  if (e.key === 'Escape') {
    hideSuggestions();
    document.getElementById('alertModal')?.style && (document.getElementById('alertModal').style.display = 'none');
  }
});

// ─── Scroll Animations ────────────────────────────────────────────────────────
if ('IntersectionObserver' in window) {
  const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        entry.target.style.opacity    = '1';
        entry.target.style.transform  = 'translateY(0)';
        observer.unobserve(entry.target);
      }
    });
  }, { threshold: 0.06, rootMargin: '0px 0px -30px 0px' });

  document.querySelectorAll('.ss-product-card, .ss-offer-card, .ss-feature-card, .ss-trending-card, .ss-wishlist-card, .ss-alert-card').forEach(el => {
    el.style.opacity   = '0';
    el.style.transform = 'translateY(14px)';
    el.style.transition = 'opacity 0.4s ease, transform 0.4s ease';
    observer.observe(el);
  });
}

// ─── PWA Service Worker ───────────────────────────────────────────────────────
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {});
  });
}

// ─── Auto-dismiss flash messages ──────────────────────────────────────────────
setTimeout(() => {
  document.querySelectorAll('#flash-container .alert').forEach(el => {
    el.style.transition = 'opacity 0.5s';
    el.style.opacity    = '0';
    setTimeout(() => el.remove(), 500);
  });
}, 5000);

// ─── Global error handler (prevent uncaught errors from crashing UI) ──────────
window.addEventListener('unhandledrejection', (e) => {
  console.warn('Unhandled promise rejection:', e.reason);
  e.preventDefault();   // Don't bubble to browser
});
window.addEventListener('error', (e) => {
  console.warn('Global error:', e.message);
});

// ─── Email Product Details Feature ────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  // Inject "Email Details" button if on product page
  const header = document.querySelector('.ss-product-header');
  if (header && !document.getElementById('btnShareEmail')) {
    const btn = document.createElement('button');
    btn.id = 'btnShareEmail';
    btn.className = 'btn btn-outline-primary mt-3 w-100';
    btn.innerHTML = '<i class="bi bi-envelope-at-fill me-2"></i> Email Me Analysis & Links';
    btn.onclick = handleEmailShare;
    
    // Insert after the image or at the end of header
    header.appendChild(btn);
  }
});

async function handleEmailShare() {
  const pathParts = window.location.pathname.split('/');
  const productId = pathParts[pathParts.length - 1];
  
  // Check auth status first
  try {
    const res = await fetch('/api/auth-status');
    const auth = await res.json();
    
    if (auth.logged_in) {
      // Logged in: Send immediately
      sendEmailRequest(productId, null);
    } else {
      // Guest: Show custom modal to ask for email
      showEmailPrompt(productId);
    }
  } catch(e) {
    console.error(e);
    showToast('Error checking status', 'danger');
  }
}

function showEmailPrompt(productId) {
  // Create modal HTML
  const modalId = 'emailPromptModal';
  let modal = document.getElementById(modalId);
  if (modal) modal.remove();

  modal = document.createElement('div');
  modal.id = modalId;
  modal.className = 'ss-modal-overlay';
  modal.innerHTML = `
    <div class="ss-modal">
      <div class="ss-modal-header">
        <h5 style="margin:0">Get Full Analysis</h5>
        <button type="button" class="btn-close" onclick="document.getElementById('${modalId}').remove()"></button>
      </div>
      <div class="ss-modal-body">
        <p class="small text-muted mb-3">Enter your email to receive the AI analysis, price comparison, and direct buy links.</p>
        <input type="email" id="guestEmailInput" class="form-control mb-3" placeholder="name@example.com">
        <button class="btn btn-primary w-100" onclick="submitGuestEmail('${productId}')">Send Details</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
}

window.submitGuestEmail = function(productId) {
  const email = document.getElementById('guestEmailInput').value;
  if (!email || !email.includes('@')) {
    showToast('Please enter a valid email', 'warning');
    return;
  }
  document.getElementById('emailPromptModal').remove();
  sendEmailRequest(productId, email);
};

async function sendEmailRequest(productId, email) {
  startProgressBar('Sending email...');
  const btn = document.getElementById('btnShareEmail');

  try {
    const res = await fetch('/api/share/product', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ product_id: productId, email: email })
    });
    const data = await res.json();
    finishProgressBar();
    
    if (data.success) {
      showToast(data.message, 'success');
      if (btn) {
        const originalHtml = btn.innerHTML;
        btn.innerHTML = '<i class="bi bi-check-circle-fill me-2"></i>Sent Successfully!';
        btn.classList.remove('btn-outline-primary');
        btn.classList.add('btn-success');
        setTimeout(() => {
          btn.innerHTML = originalHtml;
          btn.classList.remove('btn-success');
          btn.classList.add('btn-outline-primary');
        }, 3000);
      }
    } else {
      showToast(data.error || 'Failed to send email', 'danger');
    }
  } catch(e) {
    finishProgressBar();
    showToast('Network error. Try again.', 'danger');
  }
}

function openAuthCard(mode) {
  const modal = new bootstrap.Modal(document.getElementById('authModal'));

  const title = document.getElementById('authTitle');
  const userBtn = document.getElementById('userBtn');
  const adminBtn = document.getElementById('adminBtn');

  if (mode === 'login') {
    title.textContent = "Login As";

    userBtn.textContent = "User Login";
    userBtn.href = "/login?type=user";

    adminBtn.textContent = "Admin Login";
    adminBtn.href = "/login?type=admin";

  } else {
    title.textContent = "Signup As";

    userBtn.textContent = "User Signup";
    userBtn.href = "/register?type=user";

    adminBtn.textContent = "Admin Signup";
    adminBtn.href = "/register?type=admin";
  }

  modal.show();
}

// ─── Similar Products Feature ────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  const path = window.location.pathname;
  let apiUrl = null;
  let containerTitle = "Similar Products";
  
  if (path.startsWith('/product/')) {
    const slug = path.split('/').pop();
    apiUrl = `/api/recommendations?type=similar&slug=${slug}`;
    containerTitle = "Similar Alternatives From Other Brands";
  } else if (path === '/results') {
    const urlParams = new URLSearchParams(window.location.search);
    const q = urlParams.get('q');
    if (q) apiUrl = `/api/recommendations?type=similar&q=${encodeURIComponent(q)}`;
    containerTitle = "You May Also Like";
  }
  
  if (apiUrl) {
     fetch(apiUrl)
       .then(res => res.json())
       .then(data => {
          if (data && data.length > 0) {
             renderSimilarProducts(data, containerTitle);
          }
       })
       .catch(e => console.warn('Failed to load similar products:', e));
  }
});

function renderSimilarProducts(products, title) {
   const container = document.querySelector('.container.py-4') || document.querySelector('.container');
   if (!container) return;
   
   const section = document.createElement('div');
   section.className = 'mt-5 pt-4 border-top';
   
   const isAi = products.length > 0 && products[0].is_ai;
   const badgeHtml = isAi ? `<span class="badge bg-primary bg-opacity-10 text-primary ms-2 border border-primary border-opacity-25" style="font-size:0.7rem; vertical-align:middle; position:relative; top:-2px;"><i class="bi bi-robot me-1"></i>AI Specs Match</span>` : '';
   
   let html = `<h5 class="fw-bold mb-4 d-flex align-items-center flex-wrap gap-2"><i class="bi bi-stars text-warning"></i>${title}${badgeHtml}</h5><div class="row g-3 mb-4">`;
   
   products.forEach(p => {
      const priceHtml = p.price > 0 ? `<div class="ss-price mt-2">₹${p.price.toLocaleString('en-IN')}</div>` : '<div class="ss-price mt-2 text-muted fs-6">Check Price</div>';
      const ratingHtml = p.rating > 0 ? `<div class="small text-warning mb-1"><i class="bi bi-star-fill"></i> ${p.rating.toFixed(1)}</div>` : '';
      const imgHtml = p.image ? `<img src="${p.image}" alt="Product" class="ss-product-img">` : `<div class="ss-img-ph"><i class="bi bi-image text-muted fs-3"></i></div>`;
      
      html += `
      <div class="col-6 col-md-4 col-lg-2">
         <a href="/product/${p.slug}" class="text-decoration-none">
            <div class="ss-product-card h-100 p-2 text-center d-flex flex-column shadow-sm">
               <div class="ss-product-img-wrap mb-2" style="height:120px; background:transparent;">
                  ${imgHtml}
               </div>
               <div class="ss-product-body p-2 border-top-0 d-flex flex-column flex-grow-1 justify-content-between">
                  <div>
                    ${ratingHtml}
                    <div class="ss-product-name small text-dark fw-medium" style="-webkit-line-clamp: 2; line-height: 1.3;">${p.name}</div>
                  </div>
                  ${priceHtml}
               </div>
            </div>
         </a>
      </div>`;
   });
   
   html += `</div>`;
   section.innerHTML = html;
   container.appendChild(section);
}