<div align="center">
<h1>Netflix</h1>
监听奈飞（Netflix）密码变更邮件，自动重置密码。
</div>

### 简介

共享 Netflix 账户的用户，最大的烦恼莫过于密码频繁被不良人修改，本项目完美解决了这个问题。基本逻辑是监听 Netflix 密码变更邮件，自动重置密码。
仅供 Netflix 账户主使用。

### 使用方法

*这里只说明如何在 docker 中使用，按照步骤走即可。*

#### 1、安装 docker

升级源并安装软件（下面两行命令二选一，根据你自己的系统）

Debian / Ubuntu

```shell
apt-get update && apt-get install -y wget vim git
```

CentOS

```shell
yum update && yum install -y wget vim git
```

一句话命令安装 docker

```shell
wget -qO- get.docker.com | bash
```

说明：请使用 KVM 架构的 VPS，OpenVZ 架构的 VPS 不支持安装 Docker，另外 CentOS 8 不支持用此脚本来安装 Docker。 更多关于 Docker
安装的内容参考 [Docker 官方安装指南](https://docs.docker.com/engine/install/) 。

启动 docker

```shell
systemctl start docker
```

设置开机自动启动

```shell
sudo systemctl enable docker.service
sudo systemctl enable containerd.service
```

#### 2、安装 docker-compose

一句话命令安装 docker-compose，如果想自定义版本，可以修改下面的版本号（`DOCKER_COMPOSE_VER`对应的值），否则保持默认就好。

```shell
DOCKER_COMPOSE_VER=1.29.2 && sudo curl -L "https://github.com/docker/compose/releases/download/${DOCKER_COMPOSE_VER}/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose && sudo chmod +x /usr/local/bin/docker-compose && sudo ln -snf /usr/local/bin/docker-compose /usr/bin/docker-compose && docker-compose --version
```

#### 3、拉取源码

```shell
git clone https://github.com/luolongfei/netflix.git && cd netflix
```

#### 4、修改 .env 配置

完成步骤 3 后，现在你应该正位于源码根目录，即 `.env.example` 文件所在目录，执行
```shell
cp .env.example .env
```
然后使用`vim`修改`.env`文件中的配置项。注意在 docker 中运行的话，`DRIVER_EXECUTABLE_FILE`、`REDIS_HOST`以及`REDIS_PORT`的值保持默认即可。

#### 5、运行

直接执行
```shell
docker-compose up -d
```
执行完成后，项目便在后台跑起来了，再执行 `docker-compose ps` 可以看到程式的运行状态。