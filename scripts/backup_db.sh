#!/bin/bash
# =============================================================================
# Legal Intelligent Assistance System - Database Backup Script
# =============================================================================
# Usage:
#   ./scripts/backup_db.sh              # Full backup
#   ./scripts/backup_db.sh --compress   # Compressed backup
#   ./scripts/backup_db.sh --restore /path/to/backup.sql  # Restore
#
# Cron example (daily at 2am):
#   0 2 * * * /path/to/scripts/backup_db.sh --compress
# =============================================================================

set -euo pipefail

# Configuration
BACKUP_DIR="${BACKUP_DIR:-./backups}"
DB_NAME="${DB_NAME:-legal_assistant}"
DB_USER="${DB_USER:-postgres}"
DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-5432}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $(date '+%Y-%m-%d %H:%M:%S') $*"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $(date '+%Y-%m-%d %H:%M:%S') $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $(date '+%Y-%m-%d %H:%M:%S') $*" >&2; }

# Create backup directory
mkdir -p "$BACKUP_DIR"

# Generate backup filename with timestamp
TIMESTAMP=$(date '+%Y%m%d_%H%M%S')
BACKUP_FILE="$BACKUP_DIR/${DB_NAME}_${TIMESTAMP}.sql"

# =============================================================================
# Functions
# =============================================================================

do_backup() {
    local compress=false
    if [[ "${1:-}" == "--compress" ]]; then
        compress=true
    fi

    log_info "Starting database backup: $DB_NAME"
    log_info "Host: $DB_HOST:$DB_PORT, User: $DB_USER"

    if $compress; then
        BACKUP_FILE="${BACKUP_FILE}.gz"
        log_info "Compressed backup: $BACKUP_FILE"
        pg_dump -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
            --format=custom --compress=9 \
            --file="$BACKUP_FILE" \
            --verbose 2>&1 | tail -1
    else
        log_info "Plain backup: $BACKUP_FILE"
        pg_dump -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
            --format=plain \
            --file="$BACKUP_FILE" \
            --verbose 2>&1 | tail -1
    fi

    if [[ -f "$BACKUP_FILE" ]]; then
        local size=$(du -h "$BACKUP_FILE" | cut -f1)
        log_info "Backup completed successfully: $BACKUP_FILE ($size)"
    else
        log_error "Backup failed: file not created"
        exit 1
    fi
}

do_restore() {
    local restore_file="${1:-}"
    if [[ -z "$restore_file" ]]; then
        log_error "Usage: $0 --restore /path/to/backup.sql[.gz]"
        exit 1
    fi

    if [[ ! -f "$restore_file" ]]; then
        log_error "Backup file not found: $restore_file"
        exit 1
    fi

    log_warn "WARNING: This will OVERWRITE the database '$DB_NAME'!"
    log_warn "File: $restore_file"
    read -p "Are you sure? (yes/no): " confirm
    if [[ "$confirm" != "yes" ]]; then
        log_info "Restore cancelled."
        exit 0
    fi

    log_info "Starting database restore from: $restore_file"

    if [[ "$restore_file" == *.gz ]]; then
        pg_restore -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
            --clean --if-exists --no-owner \
            "$restore_file" \
            --verbose 2>&1 | tail -5
    else
        psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
            -f "$restore_file" \
            --verbose 2>&1 | tail -5
    fi

    log_info "Restore completed successfully."
}

do_cleanup() {
    log_info "Cleaning up backups older than $RETENTION_DAYS days..."
    local count=$(find "$BACKUP_DIR" -name "${DB_NAME}_*.sql*" -mtime +${RETENTION_DAYS} | wc -l)
    find "$BACKUP_DIR" -name "${DB_NAME}_*.sql*" -mtime +${RETENTION_DAYS} -delete
    log_info "Removed $count old backup(s)."
}

do_list() {
    log_info "Available backups in $BACKUP_DIR:"
    echo ""
    ls -lhS "$BACKUP_DIR"/${DB_NAME}_*.sql* 2>/dev/null || echo "  No backups found."
    echo ""
    local total=$(ls "$BACKUP_DIR"/${DB_NAME}_*.sql* 2>/dev/null | wc -l)
    local size=$(du -sh "$BACKUP_DIR" 2>/dev/null | cut -f1)
    log_info "Total: $total backup(s), $size"
}

# =============================================================================
# Main
# =============================================================================

case "${1:-}" in
    --compress)
        do_backup --compress
        do_cleanup
        ;;
    --restore)
        do_restore "${2:-}"
        ;;
    --list)
        do_list
        ;;
    --cleanup)
        do_cleanup
        ;;
    --help|-h)
        echo "Usage: $0 [options]"
        echo ""
        echo "Options:"
        echo "  (no args)            Full plain-text backup"
        echo "  --compress           Compressed backup (pg_custom format)"
        echo "  --restore <file>     Restore from backup file"
        echo "  --list               List available backups"
        echo "  --cleanup            Remove old backups (>${RETENTION_DAYS} days)"
        echo "  --help               Show this help"
        echo ""
        echo "Environment variables:"
        echo "  BACKUP_DIR           Backup directory (default: ./backups)"
        echo "  DB_NAME              Database name (default: legal_assistant)"
        echo "  DB_USER              Database user (default: postgres)"
        echo "  DB_HOST              Database host (default: localhost)"
        echo "  DB_PORT              Database port (default: 5432)"
        echo "  RETENTION_DAYS       Backup retention days (default: 30)"
        ;;
    "")
        do_backup
        do_cleanup
        ;;
    *)
        log_error "Unknown option: $1"
        echo "Use --help for usage information."
        exit 1
        ;;
esac
