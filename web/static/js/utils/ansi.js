// ANSI escape code to HTML converter

const ansiMap = {
    '0': '</span>',
    '30': '<span class="ansi-black">',
    '31': '<span class="ansi-red">',
    '32': '<span class="ansi-green">',
    '33': '<span class="ansi-yellow">',
    '34': '<span class="ansi-blue">',
    '35': '<span class="ansi-magenta">',
    '36': '<span class="ansi-cyan">',
    '37': '<span class="ansi-white">',
    '90': '<span class="ansi-bright-black">',
    '91': '<span class="ansi-bright-red">',
    '92': '<span class="ansi-bright-green">',
    '93': '<span class="ansi-bright-yellow">',
    '94': '<span class="ansi-bright-blue">',
    '95': '<span class="ansi-bright-magenta">',
    '96': '<span class="ansi-bright-cyan">',
    '97': '<span class="ansi-bright-white">',
    '0;30': '<span class="ansi-black">',
    '0;31': '<span class="ansi-red">',
    '0;32': '<span class="ansi-green">',
    '0;33': '<span class="ansi-yellow">',
    '0;34': '<span class="ansi-blue">',
    '0;35': '<span class="ansi-magenta">',
    '0;36': '<span class="ansi-cyan">',
    '0;37': '<span class="ansi-white">',
    '1;30': '<span class="ansi-bright-black">',
    '1;31': '<span class="ansi-bright-red">',
    '1;32': '<span class="ansi-bright-green">',
    '1;33': '<span class="ansi-bright-yellow">',
    '1;34': '<span class="ansi-bright-blue">',
    '1;35': '<span class="ansi-bright-magenta">',
    '1;36': '<span class="ansi-bright-cyan">',
    '1;37': '<span class="ansi-bright-white">',
};

export function ansiToHtml(text) {
    if (!text) return '';

    let html = text
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');

    html = html.replace(/\x1b\[([0-9;]+)m/g, (match, code) => {
        return ansiMap[code] || '';
    });

    html = html.replace(/\x1b\[m/g, '</span>');

    return html;
}
