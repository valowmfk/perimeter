// Shared IP format validation — used by ipam.js and vms.js

/**
 * Validate an IP address against a subnet CIDR.
 *
 * @param {string} value     — The IP string to validate (e.g. "10.1.55.42")
 * @param {string} subnetCidr — The subnet CIDR (e.g. "10.1.55.0/24")
 * @returns {{ status: 'empty'|'typing'|'invalid'|'valid', error?: string, ip?: string }}
 */
export function validateSubnetIp(value, subnetCidr) {
    const val = (value || '').trim();

    if (!val) return { status: 'empty' };

    if (!subnetCidr) return { status: 'invalid', error: 'No subnet configured' };

    const subnetPrefix = subnetCidr.split('/')[0].replace(/\.0$/, '.');
    const prefixRegex = new RegExp('^' + subnetPrefix.replace(/\./g, '\\.') + '(\\d{1,3})$');
    const match = val.match(prefixRegex);

    if (!match) {
        // Still typing a valid prefix — don't show an error yet
        if ((val.length < subnetPrefix.length && subnetPrefix.startsWith(val)) || val.startsWith(subnetPrefix)) {
            return { status: 'typing' };
        }
        return { status: 'invalid', error: `Expected: ${subnetPrefix}x` };
    }

    const octet = parseInt(match[1], 10);
    if (octet < 1 || octet > 254) {
        return { status: 'invalid', error: 'Last octet must be 1\u2013254' };
    }

    return { status: 'valid', ip: val };
}
