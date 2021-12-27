#!/usr/bin/env bash

#===================================================================#
#   Author: mybsdc <mybsdc@gmail.com>                               #
#   Intro: https://github.com/luolongfei/freenom                    #
#===================================================================#

set -e

# 生成配置文件
if [ ! -f /conf/.env ]; then
    cp /app/.env.example /conf/.env
    echo "[Info] 已生成 .env 文件，请将 .env 文件中的配置项改为你自己的，然后重启容器"
fi

# 为配置文件建立软链接
if [ ! -f /app/.env ]; then
    ln -s /conf/.env /app/.env
fi

# 等待 redis 就绪才执行 netflix 脚本
# https://docs.docker.com/compose/startup-order/
# https://github.com/vishnubob/wait-for-it
chmod +x ./wait-for-it.sh
./wait-for-it.sh redis_for_netflix:6379 --strict --timeout=24 -- python netflix.py -hl -f

exec "$@"