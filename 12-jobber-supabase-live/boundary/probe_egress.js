'use strict';

const net = require('net');
const dns = require('dns');
const fs = require('fs');
const os = require('os');
const crypto = require('crypto');

function options(args) {
  if (args.length !== 8 || args[0] !== '--proxy' || args[2] !== '--target' ||
      args[4] !== '--mock-port' || args[6] !== '--canary-port' ||
      !/^apc-proxy-[0-9a-f]{12}:3128$/.test(args[1]) ||
      !/^[a-z0-9.-]+$/.test(args[3])) {
    throw new Error('expected --proxy HOST:3128 --target HOST --mock-port N --canary-port N');
  }
  const ports = [args[5], args[7]].map((value) => {
    if (!/^\d+$/.test(value) || Number(value) < 1 || Number(value) > 65535) {
      throw new Error('invalid port');
    }
    return Number(value);
  });
  return {proxy: args[1].split(':')[0], target: args[3], mockPort: ports[0], canaryPort: ports[1]};
}

function tcp(host, port, expected) {
  return new Promise((resolve) => {
    const socket = new net.Socket();
    let finished = false;
    const finish = (actual, detail) => {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      socket.destroy();
      resolve({host, port, expected, actual, detail});
    };
    const timer = setTimeout(() => finish('FAIL', 'timeout'), 2000);
    socket.once('connect', () => finish('OK', 'connected'));
    socket.once('error', (error) => finish('FAIL', error.code || 'error'));
    socket.connect({host, port});
  });
}

function connect(proxy, host, expected) {
  return new Promise((resolve) => {
    const socket = new net.Socket();
    let finished = false;
    let header = '';
    const finish = (actual, detail) => {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      // No TLS or application bytes are sent after a successful CONNECT.
      socket.destroy();
      resolve({proxy, host, port: 443, expected, actual, detail});
    };
    const timer = setTimeout(() => finish('FAIL', 'timeout'), 2000);
    socket.once('connect', () => {
      socket.write('CONNECT ' + host + ':443 HTTP/1.1\r\nHost: ' + host + ':443\r\n\r\n');
    });
    socket.on('data', (data) => {
      header += data.toString('ascii');
      if (header.length > 16384) {
        finish('FAIL', 'oversized_headers');
      } else if (header.includes('\r\n\r\n')) {
        const match = /^HTTP\/1\.[01] ([0-9]{3})(?: |\r\n)/.exec(header);
        finish(match ? Number(match[1]) : 'FAIL', match ? 'response' : 'invalid_response');
      }
    });
    socket.once('end', () => finish('FAIL', 'peer_closed'));
    socket.once('error', (error) => finish('FAIL', error.code || 'error'));
    socket.connect({host: proxy, port: 3128});
  });
}

function lookup() {
  return new Promise((resolve) => {
    let finished = false;
    const finish = (actual, detail) => {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      resolve({host: 'example.com', actual, detail});
    };
    const timer = setTimeout(() => finish('FAIL', 'timeout'), 2000);
    dns.lookup('example.com', (error, address) => {
      finish(error ? 'FAIL' : 'OK', error ? error.code : address);
    });
  });
}

function writable(directory, expected) {
  const path = directory.replace(/\/$/, '') + '/apc-probe-' + crypto.randomBytes(12).toString('hex');
  let actual = 'FAIL';
  let detail = '';
  let created = false;
  try {
    fs.writeFileSync(path, '', {flag: 'wx'});
    created = true;
    actual = 'OK';
  } catch (error) {
    detail = error.code || 'error';
  } finally {
    if (created) {
      try {
        fs.unlinkSync(path);
      } catch (error) {
        actual = 'FAIL';
        detail = error.code || 'cleanup_error';
      }
    }
  }
  return {path: directory, expected, actual, detail};
}

async function main() {
  const {proxy, target, mockPort, canaryPort} = options(process.argv.slice(2));
  const checks = [];
  checks.push(await tcp('127.0.0.1', mockPort, 'OK'));
  checks.push(await tcp(target, 443, 'FAIL'));
  checks.push(await tcp('1.1.1.1', 443, 'FAIL'));
  for (const host of ['host.docker.internal', 'gateway.docker.internal', '192.168.65.254']) {
    checks.push(await tcp(host, canaryPort, 'FAIL'));
  }
  checks.push(await connect(proxy, 'example.com', 403));
  checks.push(await connect(proxy, '1.1.1.1', 403));
  checks.push(await connect(proxy, target, 200));
  for (const path of ['/var/run/docker.sock', '/mnt/c', '/mnt/wsl']) {
    let actual;
    try {
      fs.lstatSync(path);
      actual = 'present';
    } catch (error) {
      actual = error.code === 'ENOENT' ? 'absent' : 'error';
    }
    checks.push({path, expected: 'absent', actual});
  }
  checks.push(writable('/', 'FAIL'), writable('/in', 'FAIL'), writable('/tmp', 'OK'));
  let routes;
  let defaultRoute = null;
  try {
    routes = fs.readFileSync('/proc/net/route', 'utf8');
    const lines = routes.trim().split('\n');
    if (!/^Iface\s+Destination\s+Gateway\s/.test(lines[0])) throw new Error('invalid_routes');
    const rows = lines.slice(1).map((line) => line.trim().split(/\s+/));
    if (rows.some((row) => row.length < 11 || !/^[0-9a-f]{8}$/i.test(row[1]))) {
      throw new Error('invalid_routes');
    }
    defaultRoute = rows.some((row) => row[1] === '00000000');
  } catch (error) {
    routes = {error: error.code || 'invalid_routes'};
  }
  checks.push({name: 'default_route', expected: false, actual: defaultRoute});
  checks.push({name: 'uid', expected: 1000, actual: process.getuid()});
  checks.push({name: 'gid', expected: 1000, actual: process.getgid()});
  const dnsLookup = await lookup();
  const allExpected = checks.every((check) => check.expected === check.actual);
  console.log(JSON.stringify({network_interfaces: os.networkInterfaces(), routes,
    dns_lookup: dnsLookup, default_route: defaultRoute,
    uid: process.getuid(), gid: process.getgid(), checks, all_expected: allExpected}));
  if (!allExpected) process.exitCode = 1;
}

main().catch(() => {
  console.log(JSON.stringify({all_expected: false, error: 'probe failed'}));
  process.exitCode = 1;
});
