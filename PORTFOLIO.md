# Описание проекта

## GitHub About

Telegram shop for custom study notes: web storefront, bot checkout, shared admin panel,
order messaging and status notifications. Python, aiogram, aiohttp, SQLite.

Topics: `python`, `telegram-bot`, `aiogram`, `aiohttp`, `sqlite`, `ecommerce`.

## LinkedIn

Конспектум — магазин конспектов с веб-витриной и Telegram-ботом.
Покупатель собирает заказ на сайте, подтверждает его в боте и обсуждает материалы
в чате заказа. Администратор управляет каталогом, перепиской и статусами через
бота или веб-панель; данные хранятся в общей SQLite-базе.

В проекте реализованы подтверждение входа через Telegram, разграничение доступа,
защита CSRF, серверный расчёт цен, идемпотентное создание заказов и очередь
уведомлений о статусах. Для VPS подготовлены systemd-сервисы, Nginx и резервное
копирование. Стек: Python, aiogram, aiohttp, SQLite, JavaScript и CSS.

Ссылка на репозиторий: https://github.com/akiooooo/Konspektum-Bot
Ссылка на демо: добавьте HTTPS-адрес после развёртывания.

## Что показать

Для снимков используйте `python -m tests.serve_fixture`: это отдельная база
с тестовыми данными на localhost:8092. Покажите каталог, корзину и панель заказов.
Рабочие заказы, реальные имена, Telegram ID, токен и переписку не публикуйте.
Не заявляйте показатели нагрузки, рост продаж или эксплуатацию на хостинге,
пока они не измерены или не проверены на работающем сервере.
