"""Build a source archive from an explicit allowlist, excluding private files."""
from pathlib import Path
import re
import zipfile


def main():
    root = Path(__file__).resolve().parents[1]
    files = []
    for folder in ('konspektum', 'tests', 'scripts', 'deploy', 'docs', '.github'):
        files.extend(p for p in (root / folder).rglob('*')
                     if p.is_file() and '__pycache__' not in p.parts)
    for pattern in ('*.md', 'requirements*.txt', 'requirements.lock',
                    'compose*.yaml', 'Dockerfile', 'run.py', '.env.example',
                    '.gitignore', '.gitattributes', '.dockerignore'):
        files.extend(root.glob(pattern))
    files = sorted(set(files))
    secret = re.compile(rb'\b\d{6,14}:[A-Za-z0-9_-]{30,}\b|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----')
    for path in files:
        if path.suffix in ('.pyc', '.db', '.sqlite3', '.log', '.zip') or secret.search(path.read_bytes()):
            raise SystemExit('Refusing release: inspect ' + str(path.relative_to(root)))
    output = root / 'dist' / 'konspektum-source.zip'
    output.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            content = path.read_bytes()
            if path.suffix in ('.sh', '.service', '.timer'):
                content = content.replace(b'\r\n', b'\n')
            archive.writestr(path.relative_to(root).as_posix(), content)
    print(f'{output}: {len(files)} source files; no .env, database, IDE files or logs')


if __name__ == '__main__':
    main()
