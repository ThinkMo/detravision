# 工程交付加固设计

## 范围

- Python 直接依赖精确锁定，前端继续使用现有 `package-lock.json` 与 `npm ci`。
- GitHub Actions 在 Python 3.12、Node.js 24 上执行后端测试、前端测试、类型检查、构建和 Docker 镜像构建。
- Docker 使用前端/后端多阶段构建，生产容器启动前执行 Alembic 迁移并以非 root 用户运行。
- Compose 提供 MySQL 8.4、健康检查、持久卷和生产必要变量模板；不提交真实密码。
- 本地 Windows/Linux 一键启动仍使用 SQLite，生产 Compose 使用 MySQL。

## 边界

- GPU/CUDA 镜像、Atlas 200I DK 转换与实机性能验收不在本次范围。
- 数据治理继续按用户要求后置。
