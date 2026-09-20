#!/bin/bash
# 数据库备份脚本
# 加入crontab: 0 2 * * * /opt/skillforge/scripts/backup_db.sh

BACKUP_DIR=/data/backup
KEEP_DAYS=30

mkdir -p $BACKUP_DIR

# 备份
docker exec skillforge-db pg_dump -U skillforge | gzip > "$BACKUP_DIR/$(date +%Y%m%d_%H%M).sql.gz"

# 清理旧备份
find $BACKUP_DIR -name "*.sql.gz" -mtime +$KEEP_DAYS -delete

echo "[$(date)] Backup done, cleaned files older than $KEEP_DAYS days"
