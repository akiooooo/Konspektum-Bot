"""Запуск из любой папки: python /path/to/run.py [аргументы бота]."""
from pathlib import Path
import hashlib
import os
import re
import shutil
import subprocess
import sys
import sysconfig
import venv


def pip_environment(direct=False):
    environment=os.environ.copy()
    environment.update(PIP_DISABLE_PIP_VERSION_CHECK='1',PYTHONIOENCODING='utf-8')
    if direct:
        for key in list(environment):
            if key.lower() in {'http_proxy','https_proxy','all_proxy','no_proxy','pip_proxy','pip_config_file'}:
                environment.pop(key)
        # Applies to this pip process only, including proxies from Windows settings.
        environment.update(NO_PROXY='*',no_proxy='*',PIP_CONFIG_FILE=os.devnull)
    return environment


def pip_attempt(command, environment):
    missing_socks=False
    with subprocess.Popen(command,env=environment,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                          text=True,encoding='utf-8',errors='replace') as process:
        for line in process.stdout:
            missing_socks|='missing dependencies for socks support' in line.lower()
            # Never echo proxy credentials supplied by an installer or proxy error.
            line=re.sub(r'(?i)((?:https?|socks\w*)://)[^/\s@]+@',r'\1***@',line)
            print(line,end='',flush=True)
        return process.wait(),missing_socks


def install_dependencies(python, lock, direct=False):
    command=[str(python),'-m','pip','install','-r',str(lock),'--disable-pip-version-check']
    try:
        code,missing_socks=pip_attempt(command,pip_environment(direct))
        if code and missing_socks and not direct:
            print('Установщику не хватает поддержки SOCKS. Повторяем установку напрямую по HTTPS. '
                  'Настройки прокси компьютера и бота не меняются.',flush=True)
            code,_=pip_attempt(command,pip_environment(True))
        if code:
            raise SystemExit('Не удалось установить зависимости. Проверьте сообщение выше и доступ к PyPI. '
                             'Для прямого подключения только на время установки: python run.py --direct.')
    except (OSError,subprocess.SubprocessError) as exc:
        raise SystemExit('Не удалось запустить установщик зависимостей. Проверьте Python и окружение .venv.') from exc


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
    arguments=sys.argv[1:]
    direct='--direct' in arguments
    arguments=[arg for arg in arguments if arg!='--direct']
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
        install_dependencies(python,lock,direct)
        marker.write_text(digest)
    if not (project/'.env').exists() and not any(x in arguments for x in ('--preview','--demo','--help','-h')):
        shutil.copyfile(project/'.env.example',project/'.env')
        raise SystemExit('Создан .env. Заполните BOT_TOKEN, ADMIN_IDS и SHOP_USERNAME, затем повторите python run.py.')
    try:
        return subprocess.call([str(python),'-m','konspektum',*arguments],cwd=project)
    except KeyboardInterrupt:
        return 0


if __name__=='__main__':
    sys.exit(main())
