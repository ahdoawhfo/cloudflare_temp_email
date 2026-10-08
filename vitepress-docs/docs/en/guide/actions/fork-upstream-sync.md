# Fork upstream sync and upgrade review

## Reviewed baseline

Fork `2d584e41` versus `dreamhunter2333/cloudflare_temp_email:main` at `be7ba25a`: 5 fork-only and 60 upstream-only commits, touching 227 files, upgrading v1.10.0 to v1.13.0. This task reviews compatibility and changes synchronization; it does **not** merge or deploy upstream directly.

The five fork commits customize deployment diagnostics, configuration writing, optional WASM patching and sync. Do not freeze all of `worker/src/common.ts`: it needs upstream business-code improvements.

## Configuration compatibility

Existing domain, D1/KV binding, JWT secret, admin password, Cloudflare credential and deployment Secret names are not replaced in these commits. Sync does not edit GitHub Secrets or Cloudflare dashboard settings. Deployments still use `BACKEND_TOML`, `FRONTEND_ENV`, `PAGE_TOML`, etc.; optional variables added to templates do **not** automatically reach your deployment configuration. Actual Secrets and production configuration were not inspected.

| Change | Impact and action |
| --- | --- |
| Database v0.0.7 → v0.0.9 | Back up D1 and run the upgraded Admin Schema migration, or authenticated `POST /admin/db_migration`. Adds `raw_mails.is_unread` and `redeem_codes` plus its index. Do not initialize an existing database again. SQL alternatives: `db/2026-08-30-mail-read-status.sql` and `db/2026-09-01-redeem-codes.sql`; choose migration OR patches, avoiding duplicate column additions. |
| `AI_EXTRACT_MODE` | **Changed default:** unset now means `local`, even with Workers AI bound. Set `"ai"` to retain AI code/link extraction, keeping `ENABLE_AI_EMAIL_EXTRACT`, binding and allowlist. Allowlist misses fall back to local code extraction. |
| `ENABLE_MAIL_READ_STATUS` | Optional, disabled by default; migrate before enabling read/unread tracking. |
| `ENABLE_REDEEM_CODE`, `REDEEM_CODE_URL` | Optional redemption feature, disabled by default; acquisition URL optional. Requires migration. |
| `ADMIN_API_IP_WHITELIST` | Exact IP allowlist; unset/empty array is unrestricted. Requires Cloudflare's `CF-Connecting-IP` when configured; applies to both admin passwords and admin user tokens. Verify proxy forwarding before enabling to avoid locking yourself out. |
| `DISABLE_ADDRESS_UPDATED_AT` | Defaults false; true disables activity refresh AND built-in inactivity cleanup. Even when false, refreshes are throttled to approximately daily: timestamps are not exact last-access times. |
| `CLEANUP_BATCH_SIZE` | Defaults 3000, range 1–5000; mail, sent-mail and creation/activity-based address cleanup becomes batched. Backlogs may need multiple runs. Put actual configuration under `[vars]`, not where the template's early comment appears. |
| `BACKEND_URL` | Public backend root for signed webhook attachment links; URLs are empty if unset. Does not replace `FRONTEND_URL`. Treat time-limited signed links as sensitive credentials. |
| `VITE_DEFAULT_LANG`, runtime `app-config` | Existing `VITE_*` build values remain supported; corresponding `index.html` runtime fields without `VITE_` override them. Never include Secrets in frontend configuration. |
| SMTP/IMAP `imap_flag_db_path` | New SQLite file for persistent mail flags. Upstream Compose mounts `/app/data`; custom containers need writable persistent storage or lose flags on recreation. |

Other compatibility considerations:

- `/user_api/bind_address` now paginates, default 20 entries; external clients need `limit`/`offset` and pagination handling.
- Mailbox JWT validation now verifies mailbox existence/credentials; old JWTs for deleted mailboxes stop working. Some authentication errors change from text to `{code, message}` JSON.
- Production `E2E_TEST_MODE` entrypoints are removed; tests use an isolated fixture API.
- SPF/DKIM/DMARC checks interpret `none`/`neutral` according to standards. Blacklists check both envelope and parsed From senders, potentially rejecting previously accepted mail.
- Added user sending/sent box, read status, redemption, webhook attachment links/test-mail selection, manual subdomains and database capacity views. Update Pages middleware for `/redeem_api/` too.
- Remote image policy, announcement sanitization, editor/admin fixes alter rendering. Dependencies are upgraded repeatedly; existing deployment uses Node 24. Verify WASM patch applicability rather than relying on original source line numbers.

## Sync policy

Runs every Monday **03:17 UTC**, or manually, targeting `main` with serialized concurrent runs. `git merge-tree` computes the merge without changing Git identity; GitHub Git API creates commits.

Protected paths always retain their current fork contents, even for clean merges: sync/backend/both frontend/tag-build workflows, WASM patch, `frontend/.env.pages`, sync test/object-upload scripts, these bilingual guides and heartbeat file. Maintain `fork_owned_paths` when adding custom files. Other files receive ordinary three-way merges.

Conflicting changelogs retain upstream structure and reinsert fork-added bullets since the common ancestor into the current Bug Fixes section. This handles added entries, not arbitrary edits to historical sections. Unknown code conflicts fail without publishing or force-pushing. `force=false` also rejects an update if another commit advanced main during the run; rerun afterward.

Every successful run commits `.github/config/upstream-sync-heartbeat`, including when upstream is unchanged. Repository activity prevents the public-repository scheduled-workflow inactivity timeout (approximately 60 days); scheduled runs alone are insufficient. Expect weekly heartbeat commits. Existing successful `workflow_run` deployment listeners also deploy on heartbeat-only runs.

This is not an external scheduler: persistent failures, disabled Actions or missing permissions can still result in inactivity. Monitor failures and run history.

## Initial recovery and upgrade

1. Commit and push these changes to the fork's default `main` branch; local edits do not enable remote workflows.
2. Manually select **Actions → Upstream Sync → Enable workflow**. A disabled workflow cannot wake itself using cron.
3. **Back up D1 and prepare a migration window before the first run**, since successful sync triggers deployments. To review the merge first, temporarily disable deployment workflows manually and restore them afterward.
4. Run manually, review the result and protected configuration, migrate Schema, add optional variables as needed, then verify receiving/login/sending/webhook/WASM parsing.
5. Keep `contents: write`; branch protection/Rulesets must permit synchronization. `GITHUB_TOKEN` cannot freely rewrite workflow files. If future changes to unprotected `.github/workflows/*` are rejected, manually review/merge them rather than blindly broadening token privileges.

Isolated checks cover merge trees, protected files, changelogs, unknown-conflict rejection and WASM patch compatibility. No live GitHub API writes, Cloudflare deployments or production database migrations were performed.

Before creating a remote commit, `scripts/upload-sync-tree.py` uploads changed staged blobs (including binary content) and creates a remote tree based on the previous commit’s tree. The remote tree SHA must match the local merge tree before committing. Upload failures or SHA mismatches leave main unchanged, preventing HTTP 422 from runner-local objects. Tests model the API with an isolated remote Git object database instead of returning a local tree SHA.

## Reviewed 60 upstream commits (chronological order)

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
