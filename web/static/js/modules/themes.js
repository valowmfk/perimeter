// Theme switching

export function switchTheme() {
    const selector = document.getElementById('themeSelector');
    const theme = selector.value;
    const body = document.body;

    const logo = document.getElementById('themeLogo');
    const title = document.getElementById('themeTitle');
    const subtitle = document.getElementById('themeSubtitle');
    const tag = document.getElementById('themeTag');

    if (theme === 'perimeter') {
        body.className = 'dark theme-perimeter';
        logo.src = '/static/assets/perimeter/perimeter-logo.svg';
        logo.alt = 'Perimeter';
        title.textContent = 'Perimeter';
        title.parentElement.style.alignSelf = '';
        subtitle.textContent = 'Automation Platform';
        subtitle.style.fontSize = '';
        tag.textContent = 'VM \u00b7 Ansible \u00b7 Certificates \u00b7 Live Telemetry';
    } else if (theme === 'a10') {
        body.className = 'dark theme-a10';
        logo.src = '/static/assets/a10-logo.png';
        logo.alt = 'A10 Networks';
        title.textContent = '';
        title.parentElement.style.alignSelf = 'flex-end';
        subtitle.textContent = 'Automation Platform';
        subtitle.style.fontSize = '1.1rem';
        tag.textContent = 'VM \u00b7 Ansible \u00b7 Certificates \u00b7 Enterprise Suite';
    }

    localStorage.setItem('preferred-theme', theme);
}

export function initTheme() {
    const savedTheme = localStorage.getItem('preferred-theme') || 'perimeter';
    document.getElementById('themeSelector').value = savedTheme;
    switchTheme();
}
