// Carvalhaes Iluminação — Main JS

// Mobile menu toggle
(function () {
    const toggle = document.getElementById('menu-toggle');
    const menu = document.getElementById('mobile-menu');
    if (toggle && menu) {
        toggle.addEventListener('click', () => {
            menu.classList.toggle('hidden');
        });
    }
})();

// Auto-dismiss flash messages (if any)
(function () {
    const alerts = document.querySelectorAll('[data-auto-dismiss]');
    alerts.forEach(el => {
        setTimeout(() => {
            el.style.opacity = '0';
            el.style.transition = 'opacity 0.5s';
            setTimeout(() => el.remove(), 500);
        }, 4000);
    });
})();

// Confirm delete buttons
(function () {
    const deleteForms = document.querySelectorAll('form[data-confirm]');
    deleteForms.forEach(form => {
        form.addEventListener('submit', (e) => {
            const msg = form.dataset.confirm || 'Tem certeza?';
            if (!window.confirm(msg)) {
                e.preventDefault();
            }
        });
    });
})();
