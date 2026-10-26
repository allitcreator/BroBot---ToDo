#!/usr/bin/env bash
# Выкладка мини-аппа: статика и конфиг nginx. Бэкенд выкатывается как
# раньше — git pull и docker compose на сервере (см. DEPLOY.md).
#
#   deploy/deploy-front.sh          — собрать фронт и выложить статику
#   deploy/deploy-front.sh nginx    — конфиг nginx поддомена (+ первый сертификат)
#   deploy/deploy-front.sh webhook  — вебхук на alxforbot.online только по точному пути
#
# Фронт собирается локально: на vps4 одно ядро, рядом VPN-нода и боты.
# Конфиг nginx подменяется только после nginx -t, с откатом при провале.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST="${DEPLOY_HOST:-vps4}"
WEB_DIR="/var/www/todobot"
SITE="/etc/nginx/sites-available/todo"
BACKUP="/root/nginx-backup"
TARGET="${1:-front}"

say() { printf "\n\033[1m==> %s\033[0m\n" "$1"; }

case "$TARGET" in
front)
    say "Тесты и сборка"
    (cd "$ROOT/miniapp" && npm ci --silent --ignore-scripts && npm test --silent && npm run build)

    say "Заливаю статику в $WEB_DIR"
    # Распаковываем рядом и подменяем каталог одним mv: нет момента, когда
    # мини-апп отдаёт пустоту.
    COPYFILE_DISABLE=1 tar -C "$ROOT/miniapp/dist" --exclude '._*' -czf - . | ssh "$HOST" "
        set -e
        rm -rf $WEB_DIR.new && mkdir -p $WEB_DIR.new
        tar -C $WEB_DIR.new -xzf -
        rm -rf $WEB_DIR.old
        [ -d $WEB_DIR ] && mv $WEB_DIR $WEB_DIR.old
        mv $WEB_DIR.new $WEB_DIR
        rm -rf $WEB_DIR.old
    "
    ;;
nginx)
    say "Ставлю $SITE"
    # Временные файлы и бэкапы — в каталоге root, а не в общем /tmp.
    ssh "$HOST" "mkdir -p -m 700 $BACKUP && cat > $BACKUP/todo.nginx.new" < "$ROOT/deploy/nginx-todo.conf"
    ssh "$HOST" "
        set -e
        NEW=$BACKUP/todo.nginx.new
        # Первый запуск: сертификата ещё нет, а полный конфиг на него
        # ссылается и не пройдёт nginx -t. Поднимаем только блок на 80 и
        # выпускаем сертификат; при провале убираем за собой всё.
        if [ ! -d /etc/letsencrypt/live/todo.alxforbot.online ]; then
            awk '/^server \\{/{n++} n==1' \$NEW > $SITE
            ln -sf $SITE /etc/nginx/sites-enabled/todo
            if ! nginx -t 2>$BACKUP/todo.nginx.err; then
                cat $BACKUP/todo.nginx.err; rm -f $SITE /etc/nginx/sites-enabled/todo
                echo 'nginx -t не прошёл на блоке :80, всё убрано' >&2; exit 1
            fi
            systemctl reload nginx
            if ! certbot certonly --webroot -w /var/www/html -d todo.alxforbot.online --non-interactive --agree-tos; then
                rm -f $SITE /etc/nginx/sites-enabled/todo; systemctl reload nginx
                echo 'сертификат не выпущен, конфиг убран' >&2; exit 1
            fi
        fi
        [ -f $SITE ] && cp $SITE $BACKUP/todo.nginx.bak || rm -f $BACKUP/todo.nginx.bak
        mv \$NEW $SITE
        ln -sf $SITE /etc/nginx/sites-enabled/todo
        if nginx -t 2>$BACKUP/todo.nginx.err; then
            systemctl reload nginx
            echo 'nginx перечитан'
        else
            cat $BACKUP/todo.nginx.err
            if [ -f $BACKUP/todo.nginx.bak ]; then mv $BACKUP/todo.nginx.bak $SITE; else rm -f $SITE /etc/nginx/sites-enabled/todo; fi
            echo 'nginx -t не прошёл, конфиг откатан' >&2
            exit 1
        fi
    "
    ;;
webhook)
    # nginx отрезал префикс /webhook/todobot/ — и любой путь под ним, в том
    # числе /webhook/todobot/api/..., уходил в бота. API должен быть доступен
    # только через поддомен мини-аппа, поэтому вебхук — строго один путь.
    BOTS=/etc/nginx/sites-available/bots
    say "Вебхук по точному пути в $BOTS"
    ssh "$HOST" "
        set -e
        mkdir -p -m 700 $BACKUP
        cp $BOTS $BACKUP/bots.nginx.bak
        sed -i 's|location /webhook/todobot/ {|location = /webhook/todobot/ {|' $BOTS
        if ! grep -q 'location = /webhook/todobot/ {' $BOTS; then
            cp $BACKUP/bots.nginx.bak $BOTS
            echo 'замена не применилась — строка в конфиге выглядит иначе' >&2; exit 1
        fi
        if nginx -t 2>$BACKUP/bots.nginx.err; then
            systemctl reload nginx && echo 'nginx перечитан'
        else
            cat $BACKUP/bots.nginx.err; cp $BACKUP/bots.nginx.bak $BOTS
            echo 'nginx -t не прошёл, конфиг откатан' >&2; exit 1
        fi
    "
    ;;
*)
    echo "Не знаю цели «$TARGET»: front, nginx или webhook." >&2
    exit 1
    ;;
esac
