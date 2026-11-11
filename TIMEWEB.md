# Запуск на Timeweb Cloud

Для этого проекта нужен VPS/VDS с SSH и постоянно работающим Python.
Эта инструкция рассчитана на **новый сервер Ubuntu 24.04**, отдельный для магазина.
Обычная загрузка файлов в каталог PHP-хостинга не запускает polling-бота.
Достаточно начать с 1–2 vCPU и 2 ГБ RAM; размер диска выбирайте с запасом для базы и копий.

## 1. Сервер и домен

В панели Timeweb Cloud создайте сервер Ubuntu 24.04, добавьте свой SSH-ключ.
Создайте DNS A-запись `shop.ваш-домен` с публичным IPv4 сервера.
Не добавляйте AAAA, если IPv6 не настроен. В сетевом экране разрешите TCP 80/443;
SSH 22 ограничьте своим IP. Порт 8080 наружу открывать не нужно.

## 2. Загрузка

На Windows соберите архив: `python scripts/package_release.py`.
Загрузите `dist/konspektum-source.zip` через SFTP или PowerShell:

```powershell
scp .\dist\konspektum-source.zip root@SERVER_IP:/tmp/
```

В SSH-терминале сервера:

```sh
apt-get update && apt-get install -y unzip
mkdir -p /opt/konspektum
unzip /tmp/konspektum-source.zip -d /opt/konspektum
cd /opt/konspektum
cp .env.example .env
nano .env
```

Заполните `BOT_TOKEN`, `ADMIN_IDS`, `SHOP_USERNAME`, `SHOP_DOMAIN`.
Укажите `WEBAPP_URL=https://shop.ваш-домен`,
`NOTIFY_CHAT_ID` и `LOG_CHAT_ID` — полные ID своих групп с префиксом `-100`.
Токен в исходный архив не входит: перенесите его в `.env` отдельно.

## 3. Перенос существующих заказов

Перед запуском сервера остановите **локальный** бот кнопкой Stop в PyCharm.
Создайте согласованную копию на Windows:

```powershell
.\.venv\Scripts\python.exe -m konspektum.maintenance --output artifacts/migration.sqlite3
scp .\artifacts\migration.sqlite3 root@SERVER_IP:/tmp/konspektum-migration.sqlite3
```

На новом сервере **до установки/первого запуска**:

```sh
mkdir -p /var/lib/konspektum
test ! -e /var/lib/konspektum/konspektum.sqlite3 && \
  cp /tmp/konspektum-migration.sqlite3 /var/lib/konspektum/konspektum.sqlite3
```

Если проверка `test` завершается ошибкой, база уже существует: не перезаписывайте её.
Пропустите перенос, если нужен пустой магазин. Копию из `/tmp` удалите после проверки.

## 4. Установка и HTTPS

```sh
cd /opt/konspektum
sudo bash deploy/install-timeweb.sh shop.ваш-домен
sudo certbot --nginx -d shop.ваш-домен
sudo systemctl restart konspektum
curl -f https://shop.ваш-домен/health
sudo journalctl -u konspektum -n 50 --no-pager
sudo certbot renew --dry-run
```

Для кириллического домена используйте ASCII/Punycode-имя в командах и `.env`.
После успешного старта в журнале есть `Run polling`, `/health` возвращает `ok`.
Откройте сайт, подтвердите вход через Telegram, проверьте каталог, тестовый заказ,
ответ в чате и изменение статуса. В админке нажмите «Проверить уведомления».
В BotFather можно задать Menu Button на тот же HTTPS-адрес.

## Обслуживание

```sh
sudo systemctl restart konspektum  # после изменения .env или кода
sudo systemctl stop konspektum
sudo systemctl start konspektum
sudo systemctl start konspektum-backup.service  # копия сейчас
sudo systemctl list-timers konspektum-backup.timer
```

Ночные копии создаются около 03:00 UTC в `/var/lib/konspektum/backups`.
Они не удаляются автоматически: следите за диском, переносите копии с сервера
в отдельное защищённое хранилище и проверяйте восстановление.
Перед обновлением сделайте копию, загрузите новые исходники, затем:

```sh
cd /opt/konspektum
sudo .venv/bin/pip install -r requirements.lock
sudo systemctl restart konspektum
```

Для восстановления остановите сервис, сохраните текущую базу вместе с WAL/SHM
в отдельный каталог, положите проверенную копию как `konspektum.sqlite3` и
выполните `chown konspektum:konspektum /var/lib/konspektum/konspektum.sqlite3`.
Запустите сервис и проверьте заказы. Не оставляйте старые WAL/SHM рядом с восстановленной базой.
Одновременно с сервером локальную копию с тем же токеном не запускайте.

Справка провайдера: https://timeweb.cloud/docs/cloud-servers .
Команды systemd/Nginx относятся к Ubuntu; установка на реальный VPS требует
доступа к серверу, домена и отдельной проверки сертификата.
