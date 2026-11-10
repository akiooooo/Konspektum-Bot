"""Запуск из любой папки: python /path/to/run.py [аргументы бота]."""
from pathlib import Path
import hashlib
import os
import shutil
import subprocess
import sys
import sysconfig
import venv


def is_msys_python(platform_tag=None, base_prefix=None):
    tag=(platform_tag or sysconfig.get_platform()).lower()
    base=str(base_prefix or sys.base_prefix).lower().replace('\\','/')
    return any(name in tag for name in ('mingw','msys','cygwin')) or any(part in {'msys64','msys32'} for part in base.split('/'))


def environment_python(environment, windows=None):
    """Reuse an active project environment; never recreate it around its interpreter."""
    windows=os.name=='nt' if windows is None else windows
    environment=environment.resolve()
    if sys.prefix!=sys.base_prefix and Path(sys.prefix).resolve()==environment:
        return Path(sys.executable)
    python=environment/('Scripts/python.exe' if windows else 'bin/python')
    if python.is_file():
        return python
    if environment.exists() and (not environment.is_dir() or any(environment.iterdir())):
        raise SystemExit('Папка .venv уже есть, но в ней нет подходящего Python. Не пересоздаём её поверх старой. '
                         'Остановите запуск, переименуйте .venv в .venv-old и выберите обычный Python 3.13 в PyCharm. Затем запустите run.py снова.')
    print('Подготавливаем отдельное окружение проекта…',flush=True)
    try:
        venv.EnvBuilder(with_pip=True).create(environment)
    except (OSError,subprocess.SubprocessError) as exc:
        raise SystemExit('Не удалось создать окружение. Выберите обычный Python с python.org '
                         '(рекомендуется 3.13), а не Python из MSYS2. После этого переименуйте неполную .venv и повторите запуск.') from exc
    if not python.is_file():
        raise SystemExit('Python создал окружение с неподходящей структурой. Выберите обычный Python с python.org, '
                         'переименуйте неполную .venv и повторите запуск.')
    return python


def validate_environment(python, environment):
    # A copied environment may exist but point to a missing or incompatible base Python.
    import json
    probe="import json,sys,sysconfig; print(json.dumps([list(sys.version_info[:2]),sys.prefix,sys.base_prefix,sysconfig.get_platform()]))"
    try:
        result=subprocess.run([str(python),'-I','-c',probe],capture_output=True,text=True,check=True,timeout=15)
        version,prefix,base,platform_tag=json.loads(result.stdout)
        if tuple(version)<(3,12) or Path(prefix).resolve()!=environment.resolve() or Path(prefix).resolve()==Path(base).resolve() or is_msys_python(platform_tag,base):
            raise ValueError('incompatible environment')
    except (OSError,subprocess.SubprocessError,ValueError,TypeError) as exc:
        raise SystemExit('Окружение .venv не подходит или повреждено. Остановите запуск, переименуйте .venv '
                         'в .venv-old и выберите обычный Python 3.13 с python.org в PyCharm. Затем повторите запуск run.py.') from exc


def main():
    if sys.version_info<(3,12):
        raise SystemExit('Нужен Python 3.12 или новее.')
    if is_msys_python():
        raise SystemExit('Выбран Python из MSYS2/MinGW. Он не подходит для автоматического запуска этого проекта. '
                         'В PyCharm выберите обычный Python с python.org (рекомендуется 3.13). '
                         'Остановите запуск и переименуйте старую .venv в .venv-old; run.py создаст новое окружение. '
                         'Файл .env и базу данных сохраняйте.')
    project=Path(__file__).resolve().parent
    os.chdir(project)
    environment=project/'.venv'
    python=environment_python(environment)
    validate_environment(python,environment)
    lock=project/'requirements.lock'
    digest=hashlib.sha256(lock.read_bytes()).hexdigest()
    marker=environment/'.konspektum-dependencies'
    if not marker.exists() or marker.read_text()!=digest:
        try:
            subprocess.run([str(python),'-m','pip','install','-r',str(lock),'--disable-pip-version-check'],check=True)
        except (OSError,subprocess.SubprocessError) as exc:
            raise SystemExit('Не удалось установить зависимости. Проверьте сообщение установщика выше, доступ к интернету '
                             'и выбранный Python. Рекомендуется обычный Python 3.13 с python.org.') from exc
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
