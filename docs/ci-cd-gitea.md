# Gitea 私有仓库 CI/CD 配置

本项目使用 Gitea Actions 执行 CI，并在 `master` 分支推送后通过 SSH 部署到服务器。

## 工作流

- `.gitea/workflows/ci.yml`：面向 `master` 的 pull request、手动触发时运行。
- `.gitea/workflows/deploy.yml`：在 `master` 推送时运行，先执行同一组测试和镜像构建，成功后部署。

CI 步骤：

1. 拉取仓库代码。
2. 使用 Python 3.12 和 `uv sync --frozen` 安装依赖。
3. 执行 `PYTHONPATH=. uv run pytest -q`。
4. 构建 `python-strm` 和 `pansou_src` 两个 Docker 镜像。

部署步骤：

1. 校验 Gitea Secrets 是否存在。
2. 使用 SSH 登录部署服务器。
3. 在 `DEPLOY_PATH` 执行 `git pull --ff-only origin master`。
4. 执行 `docker compose build`。
5. 执行 `docker compose up -d --remove-orphans`。

## Gitea Runner 要求

仓库或实例需要启用 Gitea Actions，并注册可用标签为 `ubuntu-latest` 的 runner。Gitea 1.19 开始提供 Actions；如果实例版本较旧或关闭了 Actions，需要先在实例配置中启用。

该 runner 需要满足：

- 可以访问 `actions/checkout@v4`、`actions/setup-python@v5`、`astral-sh/setup-uv@v5`。
- 可以运行 Docker CLI。
- 可以访问 Docker daemon，例如挂载 `/var/run/docker.sock`，或使用具备 Docker 权限的宿主机 runner。
- 可以访问 Python 依赖源、Docker 基础镜像源、Gitea 仓库地址和部署服务器 SSH 端口。

如果你的 Gitea 是完全离线环境，需要在实例里配置 Actions 镜像源，或者把 workflow 中的外部 action 换成内部镜像地址。

官方参考：

- [Gitea Actions Quick Start](https://docs.gitea.com/usage/actions/quickstart)
- [Gitea Actions Overview](https://docs.gitea.com/usage/actions/overview)
- [Gitea Act Runner](https://docs.gitea.com/usage/actions/act-runner)

## Secrets

在仓库设置的 Actions Secrets 中添加：

- `DEPLOY_HOST`：部署服务器地址。
- `DEPLOY_PORT`：SSH 端口，可不填，默认使用 `22`。
- `DEPLOY_USER`：部署服务器 SSH 用户。
- `DEPLOY_SSH_KEY`：可登录部署服务器的私钥。
- `DEPLOY_PATH`：部署服务器上的仓库目录，例如 `/vol1/docker-data/python-strm`。

## 部署服务器准备

部署服务器需要提前完成：

1. 安装 Docker 和 Docker Compose plugin。
2. 在 `DEPLOY_PATH` 克隆当前私有仓库。
3. 配置该 clone 的 `origin`，确保 `DEPLOY_USER` 登录后可以 `git pull` 私有仓库。
4. 保留生产配置和运行数据在 `.gitignore` 已忽略的位置，例如 `config.yaml`、`data/`、`strm/`。
5. 确认工作区没有未提交的受 Git 管理文件，否则 `git pull --ff-only origin master` 会失败。

私有仓库拉取建议使用部署密钥或只读访问令牌，不要把个人主账号密码写入服务器。

## 首次验证

1. 提交一个面向 `master` 的 pull request，确认 `CI` 工作流启动。
2. 推送或合并到 `master`，确认 `Deploy` 工作流启动。
3. 如果 Docker 构建失败，先检查 runner 是否具备 Docker CLI 和 Docker daemon 权限。
4. 如果部署失败，先检查 Secrets、服务器 SSH 连通性、`DEPLOY_PATH` 和私有仓库拉取权限。
