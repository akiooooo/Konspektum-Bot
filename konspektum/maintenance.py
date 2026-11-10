import argparse
import sqlite3
from datetime import datetime,timezone
from pathlib import Path
import os
from contextlib import closing

from .config import load_env


def backup(source,target):
    source=Path(source).resolve()
    target=Path(target).resolve()
    if not source.is_file():
        raise ValueError('Рабочая база не найдена.')
    if source==target or target.exists():
        raise ValueError('Выберите новый файл резервной копии.')
    target.parent.mkdir(parents=True,exist_ok=True)
    # mode=ro не создаёт пустую базу при опечатке и не меняет исходник.
    with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as src,closing(sqlite3.connect(target)) as dst:
        src.backup(dst)
        if dst.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
            raise ValueError('Резервная копия не прошла проверку целостности.')
    return target


if __name__=='__main__':
    load_env()
    parser=argparse.ArgumentParser(description='Резервная копия базы Конспектума без остановки магазина')
    parser.add_argument('--db',default=os.getenv('DB_PATH','data/konspektum.sqlite3'))
    parser.add_argument('--output',default='data/backups/konspektum-'+datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')+'.sqlite3')
    args=parser.parse_args()
    try:
        print('Копия создана:',backup(args.db,args.output))
    except (ValueError,sqlite3.Error) as exc:
        raise SystemExit(str(exc))
