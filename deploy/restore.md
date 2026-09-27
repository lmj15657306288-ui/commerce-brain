# Control Plane Backup and Restore

## Backup

在 `deploy/.env` 填入真实配置后，执行：

```bash
cd deploy
./backup.sh
```

脚本通过 `pg_dump -Fc` 创建 PostgreSQL custom-format 备份，备份目录权限为
`700`，文件权限为 `600`，默认保留最近 7 个 daily backup。备份文件不能提交
Git，也不能写入聊天、日志或 issue。

## Restore test

恢复必须在隔离的 PostgreSQL 数据库或临时服务器执行，不直接覆盖当前生产库：

```bash
createdb -h <private-host> -U <admin> commerce_brain_restore_test
pg_restore --clean --if-exists \
  --dbname "$DATABASE_URL_RESTORE_TEST" \
  backups/commerce-brain-<timestamp>.dump
```

恢复后检查：

1. `schema_migrations` 版本存在且等于应用支持版本；
2. Context Registry、Task、Alert、Approval、Event Store 可读；
3. `event_id`、`idempotency_key`、cursor 顺序仍完整；
4. `/readiness` 在迁移完成后才返回 ready；
5. 恢复测试数据库不会暴露公网端口。

## Rollback

应用回滚使用带 `APP_VERSION` 和 `GIT_SHA` 的旧镜像 tag。数据库迁移必须先
保持向后兼容；本阶段没有 destructive migration。回滚顺序是先恢复旧应用镜像，
再处理只新增字段/索引的兼容迁移，不使用 `latest` 作为生产回滚依据。
