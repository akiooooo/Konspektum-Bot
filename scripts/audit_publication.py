"""Inspect reachable Git blobs for credential patterns without printing values."""
import re
import subprocess


def main():
    patterns = [re.compile(rb'\b\d{6,14}:[A-Za-z0-9_-]{30,}\b'),
                re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
                re.compile(rb'\bgh[pousr]_[A-Za-z0-9]{30,}\b')]
    objects = subprocess.check_output(['git', 'rev-list', '--objects', '--all']).splitlines()
    checked = 0
    found = []
    for line in objects:
        oid, _, path = line.partition(b' ')
        if not path:
            continue
        if subprocess.check_output(['git', 'cat-file', '-t', oid]).strip() != b'blob':
            continue
        content = subprocess.check_output(['git', 'cat-file', '-p', oid])
        checked += 1
        name = path.decode('utf-8', 'replace')
        private = name.endswith(('.sqlite3', '.db', '.env')) or name.startswith(('.venv/', 'data/'))
        if private or any(p.search(content) for p in patterns):
            found.append(name)
    if found:
        print('Review before publishing (values hidden): ' + ', '.join(sorted(set(found))))
        raise SystemExit(1)
    print(f'History scan passed: {checked} reachable blobs; no matching secrets/runtime data.')


if __name__ == '__main__':
    main()
