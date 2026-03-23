// DNS record management

import { showToast } from '../utils/dom.js';
import { withBusy } from '../utils/busy.js';

export function toggleDnsFields() {
    const recordType = document.getElementById('dnsRecordType').value;
    const aFields = document.getElementById('dnsAFields');
    const cnameFields = document.getElementById('dnsCnameFields');

    if (recordType === 'A') {
        aFields.style.display = 'block';
        cnameFields.style.display = 'none';
    } else {
        aFields.style.display = 'none';
        cnameFields.style.display = 'block';
    }
}

export function createDnsRecord() {
    const recordType = document.getElementById('dnsRecordType').value;
    const hostname = document.getElementById('dnsHostname').value.trim();

    if (!hostname) {
        showToast('Please enter a hostname');
        return;
    }

    let payload = { record_type: recordType, hostname: hostname };

    if (recordType === 'A') {
        const ip = document.getElementById('dnsIpAddress').value.trim();
        if (!ip) {
            showToast('Please enter an IP address');
            return;
        }
        payload.ip = ip;
    } else {
        const target = document.getElementById('dnsCnameTarget').value.trim();
        if (!target) {
            showToast('Please enter a CNAME target');
            return;
        }
        payload.target = target;
    }

    const done = withBusy('create-dns');

    document.getElementById('dnsSpinner').style.display = 'block';
    document.getElementById('dnsCreateStatus').textContent = 'Creating DNS record...';
    document.getElementById('dnsCreateStatus').style.color = '';

    fetch('/api/dns/create', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    })
    .then(resp => resp.json().then(data => ({ status: resp.status, data })))
    .then(({ status, data }) => {
        done();
        document.getElementById('dnsSpinner').style.display = 'none';

        if (data.error) {
            document.getElementById('dnsCreateStatus').textContent = data.error;
            document.getElementById('dnsCreateStatus').style.color = '#ff4444';
            showToast('DNS record creation failed');
            return;
        }

        let msg;
        if (data.record_type === 'A') {
            msg = `A record created: ${data.hostname} \u2192 ${data.ip}`;
        } else {
            msg = `CNAME record created: ${data.hostname} \u2192 ${data.target}`;
        }

        document.getElementById('dnsCreateStatus').textContent = msg;
        document.getElementById('dnsCreateStatus').style.color = '#00ff00';
        showToast(msg);

        document.getElementById('dnsHostname').value = '';
        document.getElementById('dnsIpAddress').value = '';
        document.getElementById('dnsCnameTarget').value = '';
    })
    .catch(err => {
        done();
        document.getElementById('dnsSpinner').style.display = 'none';
        document.getElementById('dnsCreateStatus').textContent = `Error: ${err.message}`;
        document.getElementById('dnsCreateStatus').style.color = '#ff4444';
        showToast(`DNS error: ${err.message}`);
    });
}
