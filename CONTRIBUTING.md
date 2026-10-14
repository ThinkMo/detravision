# Contributing / 贡献指南

[English README](README.md) · [简体中文 README](README.zh-CN.md) · [简体中文](#中文)

## English

Bug fixes, usability improvements, documentation, and reproducible field feedback are welcome.

Include your environment, reproduction steps, expected/actual behavior, and sanitized logs in issues. Discuss substantial features before a large PR. Never commit credentials, private images, unauthorized datasets, model weights, or generated local artifacts.

Keep UI changes accessible by keyboard, respect roles, and check narrow layouts. Describe roadmap items as plans. Use Python 3.12+ and Node.js 24.x; the development commands are shown below. Real training, GPU, MySQL, and hardware acceptance need explicit environments and data.

```powershell
.\.venv\Scripts\python.exe -m pytest -q
cd frontend
npm run test:run
npm run typecheck
npm run build
```

PRs should explain the problem, resulting behavior, and validation. Keep both README files concise and aligned; place detailed explanations in `docs/`.

You must have the right to contribute under the project's current `AGPL-3.0-only` license. Preserve third-party attribution. See [licensing](docs/licensing.md#english).

## 中文

欢迎提交缺陷修复、使用体验改进、文档和可复现的场景反馈。

- 提交问题时说明运行环境、复现步骤、预期与实际行为，附脱敏日志或示例。
- 新功能或较大改动建议先开 Issue 讨论，说明使用场景与验收方式。
- 不提交 RTSP 凭据、云密钥、私有图片、未获授权的数据、模型权重或本地运行产物。
- 前端改动保持键盘可用、角色权限和窄屏布局；新增规划不应描述为已有功能。

### 开发检查

环境要求：Python 3.12+、Node.js 24.x。

命令见上方英文部分。

只运行与你的改动及项目门禁相关的检查；真实训练、GPU、MySQL 和目标硬件验收需明确环境与数据，不以模拟测试替代。

提交 PR 时简述问题、改后行为和验证结果。文档入口是中英文 README，细节放到 `docs/`。

### 贡献授权

提交贡献时，你应有权按项目当前的 `AGPL-3.0-only` 提供代码或文档。保留第三方来源与必要通知。详见[授权说明](docs/licensing.md)。
