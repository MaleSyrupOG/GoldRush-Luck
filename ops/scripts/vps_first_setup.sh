#!/usr/bin/env bash
# =============================================================================
# DeathRoll — VPS first-time setup script.
#
# Run as root on the VPS the FIRST time we deploy. Idempotent: re-running
# does not regenerate secrets, does not clobber the repo, does not recreate
# users that already exist.
#
# Usage (as root on the VPS):
#     ssh -i ~/.ssh/id_ed25519 root@91.98.234.106
#     cd /tmp && curl -LO https://raw.githubusercontent.com/MaleSyrupOG/DeathRoll-Luck/main/ops/scripts/vps_first_setup.sh
#     chmod +x vps_first_setup.sh
#     ./vps_first_setup.sh
#
# What it does:
#   1. Creates the `deathroll` system user with home /opt/deathroll.
#   2. Adds the user to the `docker` group.
#   3. Creates the directory layout under /opt/deathroll/ with secure perms.
#   4. Generates the shared secrets in /opt/deathroll/secrets/.env.shared
#      (admin and per-bot Postgres passwords, button signing key, audit chain
#      key). Idempotent: if the file already exists, leaves it alone.
#   5. Stages a placeholder /opt/deathroll/secrets/.env.dw with PASTE_*
#      placeholders that Aleix must fill in manually with the Discord token.
#   6. Clones (or pulls) the repository into /opt/deathroll/repo.
#   7. Generates a GPG key for backup encryption if missing, and prints its
#      fingerprint for safekeeping outside the VPS.
#   8. Prints the next steps.
#
# This script never logs secrets to stdout, never writes secrets anywhere
# other than /opt/deathroll/secrets/, and uses umask 077 throughout.
# =============================================================================

set -euo pipefail

# ----- 0. Sanity --------------------------------------------------------------
if [ "$(id -u)" -ne 0 ]; then
    echo "Error: this script must run as root." >&2
    exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
    echo "Error: docker is not installed on the VPS. Aborting." >&2
    exit 1
fi

if ! command -v gpg >/dev/null 2>&1; then
    echo "Error: gpg is not installed. Run 'apt-get install -y gpg' first." >&2
    exit 1
fi

REPO_URL="${REPO_URL:-https://github.com/MaleSyrupOG/DeathRoll-Luck.git}"
REPO_BRANCH="${REPO_BRANCH:-main}"

echo "==================================================================="
echo " DeathRoll VPS first-time setup"
echo " repo : ${REPO_URL}"
echo " branch: ${REPO_BRANCH}"
echo "==================================================================="

# ----- 1. Create deathroll user -----------------------------------------------
if id -u deathroll >/dev/null 2>&1; then
    echo "[1/8] user 'deathroll' already exists — skipping creation"
else
    echo "[1/8] creating user 'deathroll'"
    adduser --system --group --shell /bin/bash --home /opt/deathroll deathroll
fi

# Ensure docker group membership
if id -nG deathroll | tr ' ' '\n' | grep -qx docker; then
    echo "      already in docker group"
else
    usermod -aG docker deathroll
    echo "      added to docker group"
fi

# ----- 2. Directory layout ---------------------------------------------------
echo "[2/8] creating directory layout under /opt/deathroll/"
install -d -o deathroll -g deathroll -m 750 /opt/deathroll
install -d -o deathroll -g deathroll -m 700 /opt/deathroll/secrets
install -d -o deathroll -g deathroll -m 750 /opt/deathroll/backups
install -d -o deathroll -g deathroll -m 750 /opt/deathroll/backups/daily
install -d -o deathroll -g deathroll -m 750 /opt/deathroll/backups/monthly
install -d -o deathroll -g deathroll -m 750 /opt/deathroll/logs
install -d -o deathroll -g deathroll -m 750 /opt/deathroll/scripts

# ----- 3. Generate shared secrets if missing ---------------------------------
SHARED_ENV="/opt/deathroll/secrets/.env.shared"
if [ -f "${SHARED_ENV}" ]; then
    echo "[3/8] ${SHARED_ENV} already exists — keeping existing secrets"
else
    echo "[3/8] generating shared secrets at ${SHARED_ENV}"
    umask 077
    cat > "${SHARED_ENV}" <<EOF
# DeathRoll shared secrets — generated $(date -u +%Y-%m-%dT%H:%M:%SZ)
PG_ADMIN_USER=deathroll_admin
PG_ADMIN_PASSWORD=$(openssl rand -base64 32 | tr -d '+/=' | head -c 40)
PG_LUCK_PASSWORD=$(openssl rand -base64 32 | tr -d '+/=' | head -c 40)
PG_DW_PASSWORD=$(openssl rand -base64 32 | tr -d '+/=' | head -c 40)
PG_POKER_PASSWORD=disabled
PG_READONLY_PASSWORD=$(openssl rand -base64 32 | tr -d '+/=' | head -c 40)
BUTTON_SIGNING_KEY=$(openssl rand -hex 32)
AUDIT_HASH_CHAIN_KEY=$(openssl rand -hex 32)
EOF
    chown deathroll:deathroll "${SHARED_ENV}"
    chmod 600 "${SHARED_ENV}"
fi

# ----- 4. Stage .env.dw with placeholders if missing -------------------------
DW_ENV="/opt/deathroll/secrets/.env.dw"
if [ -f "${DW_ENV}" ]; then
    echo "[4/8] ${DW_ENV} already exists — keeping existing values"
else
    echo "[4/8] staging placeholder ${DW_ENV} (you must edit it)"
    umask 077
    cat > "${DW_ENV}" <<'EOF'
# DeathRoll Deposit/Withdraw — bot-specific secrets
# IMPORTANT: replace the PASTE_* placeholders before starting the container.
DISCORD_TOKEN_DW=PASTE_YOUR_DW_BOT_TOKEN_HERE
GUILD_ID=PASTE_YOUR_DISCORD_GUILD_ID_HERE
LOG_LEVEL=info
LOG_FORMAT=json
EOF
    chown deathroll:deathroll "${DW_ENV}"
    chmod 600 "${DW_ENV}"
fi

# ----- 5. Clone or pull the repository ---------------------------------------
REPO_DIR="/opt/deathroll/repo"
if [ -d "${REPO_DIR}/.git" ]; then
    echo "[5/8] repo already cloned — pulling latest from ${REPO_BRANCH}"
    sudo -u deathroll git -C "${REPO_DIR}" fetch --quiet origin "${REPO_BRANCH}"
    sudo -u deathroll git -C "${REPO_DIR}" checkout --quiet "${REPO_BRANCH}"
    sudo -u deathroll git -C "${REPO_DIR}" reset --hard --quiet "origin/${REPO_BRANCH}"
else
    echo "[5/8] cloning ${REPO_URL} into ${REPO_DIR}"
    sudo -u deathroll git clone --branch "${REPO_BRANCH}" --quiet "${REPO_URL}" "${REPO_DIR}"
fi

# ----- 6. GPG key for backups (root-owned) -----------------------------------
GPG_RECIPIENT_NAME="DeathRoll Backup"
if gpg --list-secret-keys --batch 2>/dev/null | grep -q "${GPG_RECIPIENT_NAME}"; then
    echo "[6/8] GPG backup key already present"
    FINGERPRINT="$(gpg --list-keys --with-colons "${GPG_RECIPIENT_NAME}" | awk -F: '/^fpr/ {print $10; exit}')"
else
    echo "[6/8] generating GPG backup key (this can take 30–60 s)"
    # --pinentry-mode loopback + empty passphrase = unattended generation,
    # no TTY needed. The key has NO passphrase intentionally because the
    # backup script must use it from cron without operator interaction.
    # Confidentiality is provided by the key being only readable by root.
    gpg --batch --quiet \
        --pinentry-mode loopback \
        --passphrase '' \
        --quick-generate-key \
        "${GPG_RECIPIENT_NAME} <backup@deathroll.local>" \
        rsa4096 sign,encrypt 10y
    FINGERPRINT="$(gpg --list-keys --with-colons "${GPG_RECIPIENT_NAME}" | awk -F: '/^fpr/ {print $10; exit}')"
fi

# Export public key for Aleix to keep outside the VPS
PUBKEY_EXPORT="/opt/deathroll/secrets/backup-gpg-public.asc"
if [ ! -f "${PUBKEY_EXPORT}" ]; then
    gpg --export --armor "${GPG_RECIPIENT_NAME}" > "${PUBKEY_EXPORT}"
    chown deathroll:deathroll "${PUBKEY_EXPORT}"
    chmod 644 "${PUBKEY_EXPORT}"
fi

# ----- 7. Permissions audit ---------------------------------------------------
echo "[7/8] permissions audit"
ls -la /opt/deathroll/ | sed 's/^/      /'
echo
ls -la /opt/deathroll/secrets/ | sed 's/^/      /'

# ----- 8. Next-step instructions ---------------------------------------------
echo "==================================================================="
echo " [8/8] DONE. Next steps:"
echo
echo "  1. Edit /opt/deathroll/secrets/.env.dw and replace the PASTE_*"
echo "     placeholders with your real Discord bot token and your"
echo "     Discord server (guild) ID."
echo
echo "       sudo -u deathroll nano /opt/deathroll/secrets/.env.dw"
echo
echo "  2. Confirm placeholders are gone:"
echo
echo "       grep -c PASTE_ /opt/deathroll/secrets/.env.dw"
echo "       (expected output: 0)"
echo
echo "  3. Build and start the stack:"
echo
echo "       cd /opt/deathroll/repo"
echo "       sudo -u deathroll docker compose --env-file /opt/deathroll/secrets/.env.shared -f ops/docker/compose.yml up -d --build"
echo
echo "  4. Tail logs until you see 'placeholder process running':"
echo
echo "       sudo -u deathroll docker compose -f /opt/deathroll/repo/ops/docker/compose.yml logs -f deathroll-deposit-withdraw"
echo
echo "  5. (Important) save the GPG public key fingerprint OFF-VPS"
echo "     (1Password / hardware token). If the VPS dies and you have"
echo "     this fingerprint, you can identify and re-authenticate the"
echo "     backup key on a new VPS:"
echo
echo "       Fingerprint: ${FINGERPRINT}"
echo
echo "==================================================================="
