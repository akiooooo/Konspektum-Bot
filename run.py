"""Запуск из любой папки: python /path/to/run.py [аргументы бота]."""
from pathlib import Path
import hashlib
import os
import shutil
import subprocess
import sys
import venv


def main():
    if sys.version_info<(3,12):
        raise SystemExit('Нужен Python 3.12 или новее.')
    project=Path(__file__).resolve().parent
    os.chdir(project)
    environment=project/'.venv'
    python=environment/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    if not python.exists():
        print('Подготавливаем отдельное окружение проекта…',flush=True)
        venv.EnvBuilder(with_pip=True).create(environment)
    lock=project/'requirements.lock'
    digest=hashlib.sha256(lock.read_bytes()).hexdigest()
    marker=environment/'.konspektum-dependencies'
    if not marker.exists() or marker.read_text()!=digest:
        subprocess.run([str(python),'-m','pip','install','-r',str(lock),'--disable-pip-version-check'],check=True)
        marker.write_text(digest)
    if not (project/'.env').exists() and not any(x in sys.argv for x in ('--preview','--demo')):
        shutil.copyfile(project/'.env.example',project/'.env')
        raise SystemExit('Создан .env. Заполните BOT_TOKEN, ADMIN_IDS и SHOP_USERNAME, затем повторите python run.py.')
    try:
        return subprocess.call([str(python),'-m','konspektum',*sys.argv[1:]],cwd=project)
    except KeyboardInterrupt:
        return 0


if __name__=='__main__':
    sys.exit(main())
