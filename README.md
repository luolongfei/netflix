<div align="center">
<img src="https://alist.llf.app:1443/d/root/images/Netflix/netflix_logo.png?sign=KVLSUMCFw3v9JU_GMuoQsjzdeh_RgdMMp0j1JzOueLA=:0" width="300px" height="100%" alt="Netflix logo" />
</div>

# 监听奈飞（Netflix）密码变更邮件，自动重置密码

## 本人接与 Netflix 或其它平台相关的自动化脚本的单子，有需求的可以联系 luolongf@gmail.com 或加下面的微信
## 本人接与 Netflix 或其它平台相关的自动化脚本的单子，有需求的可以联系 luolongf@gmail.com 或加下面的微信
## 本人接与 Netflix 或其它平台相关的自动化脚本的单子，有需求的可以联系 luolongf@gmail.com 或加下面的微信

> 通过上方邮箱地址联系，或者直接加下方微信联系，添加时备注“奈飞”以便通过验证，成功添加好友后，直接留言说明你的需求，我会尽快回复。

<img src="https://alist.llf.app:1443/d/root/images/WeChat/IMG_9471.jpeg?sign=a70xMOfEoTLllOMD-mez765Un5Zwgs93__VE845CptE=:0" width="300px" height="100%" alt="WeChat" />

# 项目演示

**DEMO 环境地址：[https://demo.netflixadmin.com/admin](https://demo.netflixadmin.com/admin)**

## 奈飞全自动系统 Netflix-Admin

<img src="https://alist.llf.app:1443/d/root/images/Netflix/nf-2.png?sign=-KUzhCidoESEQZ1j573x8pxezw_nQcg-Coleo4_J56Q=:0" width="900px" height="100%" alt="Netflix-Admin" />

<img src="https://alist.llf.app:1443/d/root/images/Netflix/nf-3.png?sign=NFHi3JPMJlBHv4wyjJrnrHT9TgxjkEXSe73g2Df2fys=:0" width="900px" height="100%" alt="Netflix-Admin" />

<img src="https://alist.llf.app:1443/d/root/images/Netflix/nf-4.png?sign=_Ox0Vu_a7UwM3u7M_FthVdA3igRkCHLakTH_j25TrLw=:0" width="900px" height="100%" alt="Netflix-Admin" />

<img src="https://alist.llf.app:1443/d/root/images/Netflix/nf-5.png?sign=Ky5yyQgtcnRoJuhqvj0UnHCqEixsYvEqpRFYkdCF4fU=:0" width="900px" height="100%" alt="Netflix-Admin" />

<img src="https://alist.llf.app:1443/d/root/images/Netflix/nf-6.png?sign=oyMGCtWZSndnpZGPY7FiC7MC_GFH-QcVk8wpBUnlmCg=:0" width="900px" height="100%" alt="Netflix-Admin" />

## 奈飞自动取码，支持独立部署或接口调用的方式

<img src="https://alist.llf.app:1443/d/root/images/Netflix/verification.gif?sign=np9j1ufiAw818vHnwBblEcNpOFM05r2R0H5vAIVxmaM=:0" width="900px" height="100%" alt="Self-Service" />

*所有功能支持定制开发，支持与现有系统交互。*

# 写在前面

共享 Netflix 账户的用户，密码可能频繁被人修改，使大家无法登录。

本项目完美解决了这个问题，基本逻辑是监听 Netflix 密码变更邮件，自动重置密码。仅供 Netflix 账户主使用。

# 使用方法

*这里只说明如何在 Docker 中使用，按照步骤走即可。*

## 1、安装 Docker

升级源并安装软件（下面两行命令二选一，根据你自己的系统）

```shell
apt-get update && apt-get install -y wget vim git # Debian / Ubuntu
yum update && yum install -y wget vim git # CentOS
```

一句话命令安装 Docker

```shell
wget -qO- get.docker.com | bash
```

说明：请使用 KVM 架构的 VPS，OpenVZ 架构的 VPS 不支持安装 Docker，另外 CentOS 8 不支持用此脚本来安装 Docker。 更多关于
Docker
安装的内容参考 [Docker 官方安装指南](https://docs.docker.com/engine/install/) 。

启动 Docker

```shell
systemctl start docker
```

设置开机自动启动

```shell
sudo systemctl enable docker.service
sudo systemctl enable containerd.service
```

## 2、安装 Docker-compose

一句话命令安装 Docker-compose，如果想自定义版本，可以修改下面的版本号（`DOCKER_COMPOSE_VER`对应的值），否则保持默认就好。

```shell
DOCKER_COMPOSE_VER=1.29.2 && sudo curl -L "https://github.com/docker/compose/releases/download/${DOCKER_COMPOSE_VER}/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose && sudo chmod +x /usr/local/bin/docker-compose && sudo ln -snf /usr/local/bin/docker-compose /usr/bin/docker-compose && docker-compose --version
```

## 3、拉取源码

```shell
git clone https://github.com/luolongfei/netflix.git && cd netflix
```

## 4、修改 .env 配置

完成步骤 3 后，现在你应该正位于源码根目录，即 `.env.example` 文件所在目录，执行

```shell
cp .env.example .env
```

然后使用`vim`修改`.env`文件中的配置项。注意在 Docker 中运行的话，`DRIVER_EXECUTABLE_FILE`、`REDIS_HOST`以及`REDIS_PORT`
的值保持默认即可。

## 5、运行

直接执行

```shell
docker-compose up -d --build
```

执行完成后，项目便在后台跑起来了。

## Docker-compose 常用命令

查看程式的运行状态

```shell
docker-compose ps
```

输出程序日志

```shell
docker-compose logs
```

停用

```shell
docker-compose down
```

在后台启用

```shell
# 如果跟上 --build 参数，则会自动多一步重新构建所有容器的动作
docker-compose up -d
```

更多 Docker-compose 命令请参考： [Docker-compose 官方指南](https://docs.docker.com/compose/reference/) 。在官网能找到所有命令。

## 问答

> 如何升级到新版本呢？
>
请在`docker-compose.yml`文件所在目录，执行`git pull`拉取最新的代码，然后同样执行`docker-compose up -d --build`，Docker
会自动使用最新的代码进行构建，
构建完跑起来后，即是最新版本。

> 非 Netflix 账户主可以使用本项目吗？
>
不能。本项目仅供 Netflix 账户主使用，因为涉及到监听 Netflix 账户的邮件，而只有 Netflix 账户主才有 Netflix 邮箱以及其密码的权限，所以只有
Netflix 账户主有权使用。
