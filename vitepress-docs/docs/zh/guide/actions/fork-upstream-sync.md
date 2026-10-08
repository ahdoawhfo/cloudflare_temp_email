# Fork 上游同步与升级检查

## 本次审查范围

基线为 fork `2d584e41`，上游 `dreamhunter2333/cloudflare_temp_email:main` 为 `be7ba25a`：fork 独有 5 个提交，上游独有 60 个提交，涉及 227 个文件，上游版本从 v1.10.0 更新到 v1.13.0。这里只审查并调整同步工作流，**不直接合并或部署上游代码**。

5 个 fork 提交主要修改部署工作流、WASM 可选补丁处理、配置写入与错误诊断，以及同步策略；没有需要整文件冻结的业务源码。`worker/src/common.ts` 应继续接收上游改进，而不是保留旧版整体覆盖。

## 配置兼容性

现有域名、D1/KV 绑定、JWT 密钥、管理员密码、Cloudflare 凭据和部署 Secrets 的名称没有在这 60 个提交中被替换。仓库同步不会修改 GitHub Secrets 或 Cloudflare 控制台配置；部署仍使用 `BACKEND_TOML`、`FRONTEND_ENV`、`PAGE_TOML` 等已有配置来源。因此新模板的可选项**不会自动加入你的实际部署配置**。未读取实际 Secrets，也未验证线上配置。

| 变更 | 影响与建议 |
| --- | --- |
| 数据库版本 v0.0.7 → v0.0.9 | 备份 D1 后，在升级后的 Admin 数据库界面执行“升级数据库 Schema”（或带管理员认证调用 `POST /admin/db_migration`）。增加 `raw_mails.is_unread` 与 `redeem_codes` 表/索引；不要对已有库直接重跑完整初始化 Schema。对应补丁为 `db/2026-08-30-mail-read-status.sql`、`db/2026-09-01-redeem-codes.sql`，选择后台迁移或 SQL 补丁一种方式，避免重复加列。 |
| `AI_EXTRACT_MODE` | **行为变化**：未设置时现在默认 `local`，即使已绑定 Workers AI 也只做本地验证码提取。要继续 AI 识别验证码和链接，设置 `AI_EXTRACT_MODE="ai"`，保留 `ENABLE_AI_EMAIL_EXTRACT`、AI 绑定及允许名单。允许名单未命中时改为本地验证码回退。 |
| `ENABLE_MAIL_READ_STATUS` | 新增，默认关闭。先迁移数据库，再开启已读/未读功能。 |
| `ENABLE_REDEEM_CODE` / `REDEEM_CODE_URL` | 新增兑换码功能，默认关闭；外部获取链接可选。需要数据库迁移。 |
| `ADMIN_API_IP_WHITELIST` | 新增精确 IP 白名单；未配置/空数组不限制。开启后要求 Cloudflare 的 `CF-Connecting-IP`，同时限制管理员密码和管理员用户令牌；代理部署需验证，否则可能锁住管理入口。 |
| `DISABLE_ADDRESS_UPDATED_AT` | 默认 false；开启后停止主动保活，同时禁用内置不活跃地址清理。即使不开启，活跃时间更新也改为约每天一次，不能再当作精确的最后访问时间。 |
| `CLEANUP_BATCH_SIZE` | 默认 3000，范围 1–5000；邮件、发件箱及创建/活跃时间地址清理改为分批，积压数据可能需要多次运行。模板注释不在 `[vars]` 区域，实际配置请放在 `[vars]` 中。 |
| `BACKEND_URL` | 新增 Webhook 附件签名链接的后端公网根地址；不填时附件 URL 为空。已有 `FRONTEND_URL` 不被替代。附件链接是限时访问凭证，应按敏感链接处理。 |
| `VITE_DEFAULT_LANG` 与运行时 `app-config` | 新示例加入默认语言。原 `VITE_*` 构建变量仍有效；`index.html` 的 `app-config` 同名字段（去掉 `VITE_`）优先覆盖构建值。不要在前端配置中放 Secrets。 |
| SMTP/IMAP 代理 `imap_flag_db_path` | 新增持久化邮件标记的 SQLite 路径。上游 Compose 增加 `/app/data` 卷；自定义容器部署要确保路径可写且持久化，否则重建后丢失标记。 |

其他值得关注的兼容性变化：

- `/user_api/bind_address` 由返回全部地址改为分页，默认每页 20 条；外部调用方需传 `limit`、`offset` 并读取分页结果。
- 邮箱 JWT 增加邮箱存在性/凭证校验，已删除邮箱的旧 JWT 不能继续访问；部分认证错误由纯文本改为包含 `code`、`message` 的 JSON，外部脚本不要固定按纯文本解析。
- 正式 Worker 移除 E2E 测试入口配置 `E2E_TEST_MODE`，测试 API 转移到独立 fixture；如自行调用这些测试接口需调整。
- 垃圾邮件 SPF/DKIM/DMARC 检查按标准解释 `none`/`neutral` 等结果；发件人黑名单同时检查 SMTP 信封与解析后的 From，可能拒收以前放行的邮件。
- 新增用户发件箱/发信、邮件状态、兑换码、Webhook 附件/测试邮件选择、手动子域名、数据库容量展示；Pages 中间件增加 `/redeem_api/` 路由，部署时应同步升级。
- 外部图片加载策略、公告 HTML 清理、编辑器和管理界面修复会改变渲染行为；依赖多次升级，部署使用现有 Node 24 流程。WASM 补丁需做应用检查，不能仅假设源码行号不变。

## 自动同步策略

- 每周一 **03:17 UTC（北京时间 11:17）** 运行，也可手动触发；固定同步 `main`，并发任务串行执行。
- 使用 `git merge-tree` 生成三方合并树，通过 GitHub Git API 创建提交，不修改 runner 的 Git 身份配置。
- 以下路径始终恢复为 fork 当前版本，**不只处理冲突**：同步/后端/两种前端/标签构建工作流、WASM 补丁、`frontend/.env.pages`、同步测试/对象上传脚本、本中英文说明及保活文件。新增专有文件时同步维护工作流中的 `fork_owned_paths`。其他文件保留正常三方合并行为。
- 更新日志冲突采用上游结构，并把 fork 相对共同祖先新增的 bullet 条目放回当前版本 Bug Fixes，避免丢失 fork 更新记录。该策略用于新增条目，不适合自动迁移任意修改过的历史段落。
- 未知业务代码冲突停止，不发布合并提交、不强推。`force=false` 防止运行过程中 main 已更新时覆盖其他提交；此时重新运行即可。
- 每次成功同步都更新 `.github/config/upstream-sync-heartbeat` 并提交，即使上游没有更新也产生仓库活动，避免公开仓库约 60 天无活动后定时工作流被停用。这会产生每周保活提交；现有 `workflow_run` 部署监听成功同步，因此无更新的保活运行也会触发部署。
- 此机制不是外部调度器：持续失败、Actions 被关闭或权限不足时，仍可能被禁用。检查失败通知和运行记录。

## 首次恢复与升级顺序

1. 将工作流和说明提交并推送至 fork 默认分支 `main`（本地编辑不会恢复远端工作流）。
2. 在 GitHub **Actions → Upstream Sync → Enable workflow** 手动重新启用。已经禁用的工作流无法依靠自己的 cron 唤醒。
3. 由于成功同步会自动触发现有部署，**首次运行前先备份 D1 并准备迁移窗口**；若要先审查合并而不部署，暂时手动禁用部署工作流，完成后再恢复。
4. 手动 Run workflow；成功后检查差异及受保护配置，再执行 Schema 迁移，按需增加新变量，验证收件、登录、发件、Webhook 和 WASM 解析。
5. 保留现有 `contents: write` 权限；分支保护/Ruleset 必须允许该同步方式。`GITHUB_TOKEN` 不能任意改写工作流文件：如果未来上游修改未保护的其他 `.github/workflows/*` 导致拒绝写入，应人工审查/合并，不要无审查扩大 Token 权限。

本次隔离验证只检查合并树、配置保护、日志保留、未知冲突中止及 WASM 补丁兼容性；未执行真实 GitHub API 写入、Cloudflare 部署或生产数据库迁移。

创建远端提交前，`scripts/upload-sync-tree.py` 将暂存区变更的 blob（含二进制内容）上传到 GitHub，并以上一提交的远端 tree 为基底创建新 tree。校验远端 tree SHA 与本地合并树一致后才创建提交；上传失败或 SHA 不一致时不更新 main，避免引用 runner 本地对象造成 HTTP 422。同步测试使用隔离的远端 Git 对象库模拟 API，而不是直接返回本地 tree SHA。

## 审查的 60 个上游提交（按时间顺序）

```text
c924b71a fix: compact HTML before AI email extraction (#1057)
41105ed8 fix: add page header padding for mobile layout (#1056)
7c575927 docs: add Resend DNS-only proxy warning to prevent #515-style verification failures (#1062)
2d501d82 chore: upgrade dependencies (#1068)
1a1dd720 chore: upgrade smtp proxy and e2e dependencies (#1069)
70b30c24 chore: upgrade Twisted to stable 26.4.0 (#1071)
3f1d800e fix: validate AI extracted link domains (#1075)
dbd1f870 feat: 增加全宽列表视图功能 (#1079)
565bb839 fix: hide mobile preview line setting (#1080)
99b33234 fix: clean up related mails before deleting address in admin API (#1081)
4ce22ef2 fix: 按规范处理 SPF、DKIM 和 DMARC 认证结果 (#1085)
0581632b docs: add Japanese README (#1088)
7eaa3b3b test: |Worker| add junk_mail_policy regression tests for issue #1084 (#1089)
4c1e593d fix(imap-proxy): persist IMAP flags and mark mail as read (#1090)
8c883b26 fix(frontend): sanitize announcement HTML (#1039)
342fe22e chore: upgrade dependencies (#1093)
e4992111 feat: add setting to disable auto-loading external images in emails (#1092)
b3666c09 fix: harden remote content policy edge cases (#1095)
95badf5a chore: upgrade dependencies (#1097)
2dcbad40 chore: upgrade e2e dependencies (#1098)
116ddc73 feat: add admin mail detail API (#1099)
d04c1a86 feat: upgrade version to v1.11.0 (#1100)
5553c648 perf: throttle address activity updates (#1104)
f9281818 chore: upgrade dependencies (#1106)
a09ede89 perf: paginate user addresses and optimize ownership queries (#1105)
12152fc8 perf: limit indexed cleanup task batches (#1107)
624fc9bb docs: fix broken star history chart (#1111)
a3c62de4 feat: allow custom subdomains in create UI (#1109)
3bcc0c19 fix: clarify account and address terminology (#1113)
3ffa1623 chore: upgrade dependencies (#1115)
27627412 feat: upgrade version to v1.12.0 (#1116)
108b8ef4 feat: show D1 storage capacity in admin (#1117)
aeb2ac68 fix: 修复 Admin 二级标签页状态丢失 (#1118)
005d74bf feat: improve send mail composer (#1120)
dccca929 fix: align send mail fields and editor caret (#1121)
5dbb6107 feat: add user send mail and sent box (#1122)
f92b059a feat: add Admin random address name generation (#1127)
70206c61 feat: add single-mail read status (#1125)
5fd181d9 refactor(frontend): unify mailbox terminology
3c505db9 feat: add admin API IP whitelist (#1131)
806ec1ae fix: prevent Admin password dialog flash (#1134)
fc363cc9 feat: support runtime frontend configuration (#1135)
8e1bfb8a fix: load AdSense script without event attributes (#1136)
66ab5a05 feat: add redemption code system (#1133)
406d1956 feat: add address activity disable switch (#1138)
4ddd502a chore: upgrade dependencies (#1137)
0a0a56e6 fix: clarify cleanup failures and test address activity controls (#1139)
f88852a3 fix: refresh access tokens from structured API errors (#1140)
066fcfc8 fix: validate mailbox credentials and isolate E2E APIs (#1141)
479bb945 fix(frontend): format created_at and updated_at with local date across admin views (#1146)
5e4823a1 feat: select email for webhook tests (#1147)
39db6bad feat: add signed webhook attachment links (#1144)
952ab6f5 feat: upgrade version to v1.13.0 (#1148)
25ca019f chore: upgrade dependencies (#1149)
cea2a8c7 docs: clarify database migration notes in release skill (#1150)
1e054578 feat: add AI_EXTRACT_MODE for email extraction
4dda1eb8 fix: fallback to local extract on ai allowlist miss
ce9e98e0 feat: 用户邮箱地址管理支持搜索 (#1157)
04463db9 fix: check both envelope and parsed From sender blacklists (#1158)
be7ba25a chore: remove temporary sender diagnostics (#1160)
```
