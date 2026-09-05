#!/usr/bin/env bash
# Backup harian data non-regenerable → commit ke branch 'backups' (terpisah dari main)
# Retensi: 30 hari (file lama dihapus otomatis). Repo = off-VM, SSD lokal tetap ramping.
set -euo pipefail
REPO=/home/ubuntu/hema-repo
SRC=/home/lenovo/Sync/Seno/Hermes-Outputs
BR=backups
STAMP=$(date +%Y%m%d)
export GIT_SSH_COMMAND="ssh -i /home/ubuntu/.ssh/hema_github -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
cd "$REPO"

# working tree sementara di branch backups (main tidak tersentuh)
git worktree add -B "$BR" /tmp/hema-backup-wt "$BR" 2>/dev/null || git worktree add -B "$BR" /tmp/hema-backup-wt origin/"$BR" 2>/dev/null || git worktree add -B "$BR" /tmp/hema-backup-wt main
cd /tmp/hema-backup-wt

tar -czf "hema-data-$STAMP.tar.gz" -C "$SRC" \
  catatan_hemato.json catatan_hemato \
  referat_hdn_irsyam.docx referat_hdn_irsyam.md referat_hdn_irsyam.html \
  index_notulensi.md index_notulensi.xlsx \
  telegram-vault-workflow.md _README.md checkpoints 2>/dev/null || true
cp /home/lenovo/patients_29agustus.py "patients-$STAMP.py" 2>/dev/null || true

# retensi 30 hari
find . -maxdepth 1 -name 'hema-data-*.tar.gz' -mtime +30 -delete
find . -maxdepth 1 -name 'patients-*.py'    -mtime +30 -delete

git add -A
if ! git diff --cached --quiet; then
  git commit -q -m "backup $STAMP"
  git push -q origin "$BR"
  echo "$(date -Is) OK pushed $STAMP"
else
  echo "$(date -Is) SKIP no changes"
fi
cd "$REPO"; git worktree remove --force /tmp/hema-backup-wt 2>/dev/null || true
