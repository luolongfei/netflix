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

python netflix.py -hl -f

exec "$@"