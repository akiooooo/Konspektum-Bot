"""Проверяет индекс Git. Значения найденных секретов никогда не печатаются."""
from pathlib import PurePosixPath
import re
import subprocess
import sys


def main():
    paths=subprocess.check_output(['git','ls-files','-z']).decode('utf-8').split('\0')
    forbidden=[]
    patterns=[re.compile(rb'\b\d{6,14}:[A-Za-z0-9_-]{30,}\b'),re.compile(rb'\bgh[pousr]_[A-Za-z0-9]{30,}\b'),
              re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----')]
    for path in filter(None,paths):
        name=PurePosixPath(path).name
        if (name.startswith('.env') and name!='.env.example') or name.endswith(('.env','.sqlite3','.db')) or path.startswith(('data/','.venv/','artifacts/')):
            forbidden.append(path+': private/runtime file')
            continue
        content=subprocess.check_output(['git','show',':'+path])
        if b'\0' in content[:8192]:
            continue
        if any(pattern.search(content) for pattern in patterns):
            forbidden.append(path+': possible credential (value hidden)')
    if forbidden:
        print('\n'.join(forbidden))
        return 1
    print('Staged source check passed: no runtime data or matching credential patterns.')
    return 0


if __name__=='__main__':
    sys.exit(main())
