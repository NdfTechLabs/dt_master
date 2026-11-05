#!/usr/bin/env bash
# ============================================================
# lib/mail_stack.sh — mail server domain (postfix/dovecot/
# rspamd/opendkim + virtual mailbox users on MySQL)
#
# Entry points (used by bin/mail/* wrappers):
#   mail::stack_install
#   mail::config_postfix   mail::config_dovecot
#   mail::config_rspamd    mail::config_opendkim
#   mail::config_dns_records
#   mail::user_add         mail::user_remove
#   mail::user_list        mail::user_update_password
#   mail::verify
# ============================================================

MAIL_DB="mailserver"

_mail_env_domain() {
    require_env MAIL_DOMAIN MAIL_HOSTNAME
    [[ "$MAIL_DOMAIN" =~ ^[A-Za-z0-9][A-Za-z0-9.-]*$ ]] || die "Invalid mail domain"
    [[ "$MAIL_HOSTNAME" =~ ^[A-Za-z0-9][A-Za-z0-9.-]*$ ]] || die "Invalid mail hostname"
}

mail::_validate_user() {
    require_env MAIL_DOMAIN MAIL_USER
    [[ "$MAIL_DOMAIN" =~ ^[A-Za-z0-9][A-Za-z0-9.-]*$ ]] || die "Invalid mail domain"
    [[ "$MAIL_USER" =~ ^[A-Za-z0-9_+.-]+$ && "$MAIL_USER" != . && "$MAIL_USER" != .. ]] || die "Invalid mailbox user"
}

# ============================================================
# mail::stack_install
# Env: MAIL_DOMAIN MAIL_HOSTNAME MAIL_IP
# ============================================================
mail::stack_install() {
    require_env MAIL_DOMAIN MAIL_HOSTNAME MAIL_IP
    _mail_env_domain

    log "Starting mail stack installation"

    log "Updating package index"
    apt-get update -y

    # ------------------------------------------------------------
    # Required packages
    # ------------------------------------------------------------
    local packages=(
        postfix
        postfix-mysql
        dovecot-core
        dovecot-imapd
        dovecot-lmtpd
        dovecot-mysql
        rspamd
        redis-server
        opendkim
        opendkim-tools
        certbot
        mailutils
        dnsutils
    )

    log "Installing mail packages"
    apt_install "${packages[@]}"

    # ------------------------------------------------------------
    # Ensure services exist
    # ------------------------------------------------------------
    local services=(postfix dovecot rspamd opendkim redis-server)

    local svc
    for svc in "${services[@]}"; do
        log "Enabling service $svc"
        systemctl enable "$svc"
    done

    # ------------------------------------------------------------
    # Ensure directories
    # ------------------------------------------------------------
    log "Preparing mail directories"

    mkdir -p /var/mail
    mkdir -p /var/spool/postfix
    mkdir -p /etc/opendkim/keys

    chmod 755 /var/mail
    chmod 755 /etc/opendkim/keys

    # ------------------------------------------------------------
    # Basic hostname sanity
    # ------------------------------------------------------------
    local current_host
    current_host=$(hostname)

    if [[ "$current_host" != "$MAIL_HOSTNAME" ]]; then
        log "Setting system hostname to $MAIL_HOSTNAME"
        hostnamectl set-hostname "$MAIL_HOSTNAME"
    fi

    # ------------------------------------------------------------
    # Install completed
    # ------------------------------------------------------------
    log "Mail stack packages installed successfully"

    emit "mail_stack_installed" "true"
    emit "mail_domain" "$MAIL_DOMAIN"
    emit "mail_hostname" "$MAIL_HOSTNAME"
    emit "mail_ip" "$MAIL_IP"
}

# ============================================================
# mail::config_postfix
# Env: MAIL_DOMAIN MAIL_HOSTNAME MAIL_IP DKIM_SELECTOR
#      MYSQL_ROOT_USER MYSQL_ROOT_PASSWORD
# ============================================================
mail::config_postfix() {
    require_env MAIL_DOMAIN MAIL_HOSTNAME MAIL_IP DKIM_SELECTOR
    require_env MYSQL_ROOT_USER MYSQL_ROOT_PASSWORD
    _mail_env_domain

    log "Configuring Postfix"

    local postfix_main="/etc/postfix/main.cf"
    local postfix_master="/etc/postfix/master.cf"
    local postfix_sql_dir="/etc/postfix/sql"

    local tls_cert="/etc/letsencrypt/live/${MAIL_HOSTNAME}/fullchain.pem"
    local tls_key="/etc/letsencrypt/live/${MAIL_HOSTNAME}/privkey.pem"

    # ------------------------------------------------------------
    # Set hostname and mailname
    # ------------------------------------------------------------
    log "Setting Postfix hostname"

    postconf -e "myhostname = ${MAIL_HOSTNAME}"
    postconf -e "mydomain = ${MAIL_DOMAIN}"
    postconf -e "myorigin = \$mydomain"

    # ------------------------------------------------------------
    # Network settings
    # ------------------------------------------------------------
    log "Configuring network interfaces"

    postconf -e "inet_interfaces = all"
    postconf -e "inet_protocols = ipv4"
    postconf -e "mynetworks = 127.0.0.0/8"

    # ------------------------------------------------------------
    # Mail routing
    # ------------------------------------------------------------
    log "Configuring mail routing"

    postconf -e "mydestination = localhost.\$mydomain, localhost"
    postconf -e "relay_domains ="

    # ------------------------------------------------------------
    # Prepare SQL lookup directory
    # ------------------------------------------------------------
    log "Preparing Postfix SQL lookup configuration"

    mkdir -p "${postfix_sql_dir}"

    cat > "${postfix_sql_dir}/virtual_domains.cf" <<EOF
user=${MYSQL_ROOT_USER}
password=${MYSQL_ROOT_PASSWORD}
hosts=127.0.0.1
dbname=${MAIL_DB}
query=SELECT 1 FROM virtual_users WHERE SUBSTRING_INDEX(email,'@',-1)='%s' LIMIT 1;
EOF

    cat > "${postfix_sql_dir}/virtual_users.cf" <<EOF
user=${MYSQL_ROOT_USER}
password=${MYSQL_ROOT_PASSWORD}
hosts=127.0.0.1
dbname=${MAIL_DB}
query=SELECT email FROM virtual_users WHERE email='%s';
EOF

    chmod 640 ${postfix_sql_dir}/*.cf
    chown root:postfix ${postfix_sql_dir}/*.cf

    # ------------------------------------------------------------
    # Virtual mailbox configuration
    # ------------------------------------------------------------
    log "Configuring virtual mailbox system"

    postconf -e "virtual_mailbox_domains = mysql:/etc/postfix/sql/virtual_domains.cf"
    postconf -e "virtual_mailbox_maps = mysql:/etc/postfix/sql/virtual_users.cf"
    postconf -e "virtual_alias_maps = mysql:/etc/postfix/sql/virtual_users.cf"

    postconf -e "virtual_mailbox_base = /var/mail/vhosts"

    postconf -e "virtual_uid_maps = static:5000"
    postconf -e "virtual_gid_maps = static:8"

    postconf -e "virtual_transport = lmtp:unix:private/dovecot-lmtp"

    # ------------------------------------------------------------
    # TLS configuration
    # ------------------------------------------------------------
    log "Configuring TLS"

    if [[ -f "$tls_cert" ]]; then

        postconf -e "smtpd_tls_cert_file = ${tls_cert}"
        postconf -e "smtpd_tls_key_file = ${tls_key}"

        postconf -e "smtpd_tls_security_level = may"
        postconf -e "smtp_tls_security_level = may"

        postconf -e "smtpd_tls_protocols = !SSLv2,!SSLv3"
        postconf -e "smtp_tls_protocols = !SSLv2,!SSLv3"

        log "TLS configured"

    else
        log "TLS certificate not found yet, skipping TLS configuration"
    fi

    # ------------------------------------------------------------
    # Submission port (587)
    # ------------------------------------------------------------
    log "Ensuring submission service is enabled"

    local submission_service="${SMTP_PORT:-587}"
    [[ "$submission_service" =~ ^[0-9]+$ ]] && ((submission_service > 0 && submission_service <= 65535)) || die "Invalid SMTP_PORT"
    [ "$submission_service" != 587 ] || submission_service=submission
    if [ "$submission_service" != 25 ] && ! grep -q "^${submission_service}[[:space:]]" "$postfix_master"; then
        cat >> "$postfix_master" <<EOF

${submission_service} inet n       -       y       -       -       smtpd
  -o syslog_name=postfix/submission
  -o smtpd_tls_security_level=encrypt
  -o smtpd_sasl_auth_enable=yes
  -o smtpd_client_restrictions=permit_sasl_authenticated,reject
  -o smtpd_recipient_restrictions=permit_sasl_authenticated,reject
EOF
    fi

    # ------------------------------------------------------------
    # SMTP authentication (Dovecot)
    # ------------------------------------------------------------
    log "Configuring SMTP authentication"

    postconf -e "smtpd_sasl_type = dovecot"
    postconf -e "smtpd_sasl_path = private/auth"
    postconf -e "smtpd_sasl_auth_enable = yes"
    postconf -e "smtpd_sasl_security_options = noanonymous"

    # ------------------------------------------------------------
    # Rspamd integration
    # ------------------------------------------------------------
    log "Configuring Rspamd milter"

    postconf -e "milter_protocol = 6"
    postconf -e "milter_default_action = accept"

    postconf -e "smtpd_milters = inet:localhost:11332"
    postconf -e "non_smtpd_milters = inet:localhost:11332"

    # ------------------------------------------------------------
    # Prepare OpenDKIM socket for Postfix
    # ------------------------------------------------------------
    log "Preparing OpenDKIM socket directory"

    local opendkim_chroot_dir="/var/spool/postfix/opendkim"

    mkdir -p "$opendkim_chroot_dir"
    chown opendkim:opendkim "$opendkim_chroot_dir"
    chmod 750 "$opendkim_chroot_dir"

    # ------------------------------------------------------------
    # Configure OpenDKIM socket
    # ------------------------------------------------------------
    log "Configuring OpenDKIM socket"

    local opendkim_conf="/etc/opendkim.conf"

    if grep -q "^Socket" "$opendkim_conf"; then
        sed -i 's|^Socket.*|Socket local:/var/spool/postfix/opendkim/opendkim.sock|' "$opendkim_conf"
    else
        echo "Socket local:/var/spool/postfix/opendkim/opendkim.sock" >> "$opendkim_conf"
    fi

    systemctl restart opendkim

    # ------------------------------------------------------------
    # OpenDKIM integration
    # ------------------------------------------------------------
    log "Configuring OpenDKIM milter"

    postconf -e "smtpd_milters=inet:localhost:11332,unix:/opendkim/opendkim.sock"
    postconf -e "non_smtpd_milters=inet:localhost:11332,unix:/opendkim/opendkim.sock"

    # ------------------------------------------------------------
    # Allow Postfix access to OpenDKIM socket
    # ------------------------------------------------------------
    log "Granting Postfix access to OpenDKIM socket"

    if ! id -nG postfix | grep -qw opendkim; then
        usermod -aG opendkim postfix
        log "Postfix added to opendkim group"
    else
        log "Postfix already in opendkim group"
    fi

    # ------------------------------------------------------------
    # Security settings
    # ------------------------------------------------------------
    log "Applying security restrictions"

    postconf -e "smtpd_helo_required = yes"

    postconf -e "smtpd_helo_restrictions = permit_mynetworks,reject_invalid_helo_hostname,reject_non_fqdn_helo_hostname"

    postconf -e "smtpd_recipient_restrictions = permit_mynetworks,permit_sasl_authenticated,reject_unauth_destination"

    # ------------------------------------------------------------
    # Mail size
    # ------------------------------------------------------------
    postconf -e "message_size_limit = 52428800"

    # ------------------------------------------------------------
    # Reload postfix
    # ------------------------------------------------------------
    log "Restarting Postfix"

    systemctl restart postfix

    # ------------------------------------------------------------
    # Verify postfix
    # ------------------------------------------------------------
    log "Verifying Postfix configuration"

    postfix check

    log "Postfix configured successfully"

    emit "postfix_configured" "true"
    emit "mail_domain" "$MAIL_DOMAIN"
    emit "mail_hostname" "$MAIL_HOSTNAME"
}

# ============================================================
# mail::config_dovecot
# Env: MAIL_DOMAIN MAIL_HOSTNAME MYSQL_ROOT_USER MYSQL_ROOT_PASSWORD
# ============================================================
mail::config_dovecot() {
    require_env MAIL_DOMAIN MAIL_HOSTNAME
    require_env MYSQL_ROOT_USER MYSQL_ROOT_PASSWORD
    _mail_env_domain
    local imaps_port="${IMAP_PORT:-993}"
    [[ "$imaps_port" =~ ^[0-9]+$ ]] && ((imaps_port > 0 && imaps_port <= 65535)) || die "Invalid IMAP_PORT"
    # Port 143 is the existing plaintext listener, not a second TLS listener.
    [ "$imaps_port" != 143 ] || imaps_port=993

    log "Configuring Dovecot"

    local dovecot_dir="/etc/dovecot"
    local conf_d="${dovecot_dir}/conf.d"

    local tls_cert="/etc/letsencrypt/live/${MAIL_HOSTNAME}/fullchain.pem"
    local tls_key="/etc/letsencrypt/live/${MAIL_HOSTNAME}/privkey.pem"

    # ------------------------------------------------------------
    # Prepare mail database
    # ------------------------------------------------------------
    log "Preparing mail database"

    mysql_exec -e "SELECT 1" >/dev/null

    mysql_exec <<SQL
CREATE DATABASE IF NOT EXISTS ${MAIL_DB};
SQL

    mysql_query_db "$MAIL_DB" <<SQL
CREATE TABLE IF NOT EXISTS virtual_users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    email VARCHAR(255) UNIQUE NOT NULL,
    password VARCHAR(255) NOT NULL
);
SQL

    log "Mail database ready"

    # ------------------------------------------------------------
    # Ensure vmail system user exists
    # ------------------------------------------------------------
    log "Ensuring vmail user exists"

    if ! id vmail >/dev/null 2>&1; then
        useradd -r -u 5000 -g mail -d /var/mail vmail
    fi

    # ------------------------------------------------------------
    # Protocols
    # ------------------------------------------------------------
    log "Configuring mail protocols"

    cat > "${conf_d}/10-mail.conf" <<EOF
mail_location = maildir:/var/mail/vhosts/%d/%n

mail_uid = vmail
mail_gid = mail

namespace inbox {
  inbox = yes
}
EOF

    # ------------------------------------------------------------
    # Authentication (SQL)
    # ------------------------------------------------------------
    log "Configuring SQL authentication"

    cat > "${conf_d}/10-auth.conf" <<EOF
disable_plaintext_auth = yes
auth_mechanisms = plain login

!include auth-sql.conf.ext
EOF

    cat > "${conf_d}/auth-sql.conf.ext" <<EOF
passdb {
  driver = sql
  args = /etc/dovecot/dovecot-sql.conf.ext
}

userdb {
  driver = static
  args = uid=vmail gid=mail home=/var/mail/vhosts/%d/%n
}
EOF

    cat > "/etc/dovecot/dovecot-sql.conf.ext" <<EOF
driver = mysql

connect = host=127.0.0.1 dbname=${MAIL_DB} user=${MYSQL_ROOT_USER} password=${MYSQL_ROOT_PASSWORD}

default_pass_scheme = SHA512-CRYPT

password_query = SELECT email as user, password FROM virtual_users WHERE email='%u';
EOF
    chmod 640 /etc/dovecot/dovecot-sql.conf.ext
    chown root:dovecot /etc/dovecot/dovecot-sql.conf.ext

    # ------------------------------------------------------------
    # LMTP for Postfix delivery
    # ------------------------------------------------------------
    log "Configuring LMTP"

    cat > "${conf_d}/20-lmtp.conf" <<EOF
protocol lmtp {
  postmaster_address = postmaster@${MAIL_DOMAIN}
}
EOF

    # ------------------------------------------------------------
    # IMAP
    # ------------------------------------------------------------
    log "Configuring IMAP"

    cat > "${conf_d}/20-imap.conf" <<EOF
protocol imap {
  mail_plugins = \$mail_plugins
}
EOF

    # ------------------------------------------------------------
    # Mailbox settings
    # ------------------------------------------------------------
    log "Configuring mailboxes"

    cat > "${conf_d}/15-mailboxes.conf" <<EOF
namespace inbox {
  inbox = yes

  mailbox Drafts {
    special_use = \\Drafts
  }

  mailbox Sent {
    special_use = \\Sent
  }

  mailbox Trash {
    special_use = \\Trash
  }

  mailbox Junk {
    special_use = \\Junk
  }
}
EOF

    # ------------------------------------------------------------
    # Master services
    # ------------------------------------------------------------
    log "Configuring services"

    cat > "${conf_d}/10-master.conf" <<EOF
service imap-login {
  inet_listener imap {
    port = 143
  }

  inet_listener imaps {
    port = ${imaps_port}
    ssl = yes
  }
}

service lmtp {
  unix_listener /var/spool/postfix/private/dovecot-lmtp {
    mode = 0600
    user = postfix
    group = postfix
  }
}

service auth {
  unix_listener /var/spool/postfix/private/auth {
    mode = 0660
    user = postfix
    group = postfix
  }
}
EOF

    # ------------------------------------------------------------
    # TLS configuration
    # ------------------------------------------------------------
    log "Configuring TLS"

    if [[ ! -f "$tls_cert" || ! -f "$tls_key" ]]; then

        log "TLS certificate not found, attempting certbot"

        local port80_service stopped_service=""
        port80_service=$(ss -lntp 2>/dev/null | awk '/:80 / {print $NF}' | sed -E 's/.*users:\(\("([^"]+)".*/\1/' | head -n1 || true)

        if [[ -n "$port80_service" ]]; then
            systemctl stop "$port80_service" || true
            stopped_service="$port80_service"
        fi

        trap 'if [[ -n "$stopped_service" ]]; then systemctl start "$stopped_service"; fi' EXIT

        certbot certonly \
            --standalone \
            --non-interactive \
            --agree-tos \
            --register-unsafely-without-email \
            -d "${MAIL_HOSTNAME}" || true

        if [[ -n "$stopped_service" ]]; then
            systemctl start "$stopped_service" || true
            stopped_service=""
        fi

        trap - EXIT
    fi

    if [[ -f "$tls_cert" && -f "$tls_key" ]]; then

        cat > "${conf_d}/10-ssl.conf" <<EOF
ssl = required
ssl_cert = <${tls_cert}
ssl_key = <${tls_key}
ssl_min_protocol = TLSv1.2
ssl_prefer_server_ciphers = yes
EOF

        log "TLS enabled"

    else

        cat > "${conf_d}/10-ssl.conf" <<EOF
ssl = no
EOF

        log "TLS certificate not available, IMAPS disabled"

    fi

    # ------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------
    log "Configuring logging"

    cat > "${conf_d}/10-logging.conf" <<EOF
log_path = /var/log/dovecot.log
info_log_path = /var/log/dovecot-info.log
EOF

    # ------------------------------------------------------------
    # Mail storage
    # ------------------------------------------------------------
    log "Preparing mail directories"

    mkdir -p /var/mail/vhosts
    chown -R vmail:mail /var/mail
    chmod -R 750 /var/mail/vhosts

    # ------------------------------------------------------------
    # Restart Dovecot
    # ------------------------------------------------------------
    log "Restarting Dovecot"

    systemctl enable dovecot
    systemctl restart dovecot

    # ------------------------------------------------------------
    # Verify configuration
    # ------------------------------------------------------------
    log "Verifying Dovecot configuration"

    dovecot -n

    log "Dovecot configured successfully"

    emit "dovecot_configured" "true"
    emit "mail_domain" "$MAIL_DOMAIN"
    emit "mail_hostname" "$MAIL_HOSTNAME"
}

# ============================================================
# mail::config_rspamd
# Env: MAIL_DOMAIN MAIL_HOSTNAME
# ============================================================
mail::config_rspamd() {
    _mail_env_domain

    log "Configuring Rspamd"

    local rspamd_dir="/etc/rspamd"
    local local_d="${rspamd_dir}/local.d"

    mkdir -p "$local_d"

    # ------------------------------------------------------------
    # Worker configuration
    # ------------------------------------------------------------
    log "Configuring Rspamd worker"

    cat > "${local_d}/worker-proxy.inc" <<EOF
bind_socket = "127.0.0.1:11332";
milter = yes;
timeout = 120s;
upstream "local" {
  default = yes;
  self_scan = yes;
}
EOF

    # ------------------------------------------------------------
    # Redis configuration
    # ------------------------------------------------------------
    log "Configuring Redis backend"

    cat > "${local_d}/redis.conf" <<EOF
servers = "127.0.0.1";
EOF

    # ------------------------------------------------------------
    # DKIM signing placeholder
    # ------------------------------------------------------------
    log "Preparing DKIM signing configuration"

    cat > "${local_d}/dkim_signing.conf" <<EOF
path = "/var/lib/rspamd/dkim/\$domain.\$selector.key";
selector = "mail";
allow_envfrom_empty = true;
allow_hdrfrom_mismatch = true;
sign_authenticated = true;
sign_local = true;
use_domain = "header";
EOF

    # ------------------------------------------------------------
    # Spam filtering tuning
    # ------------------------------------------------------------
    log "Applying spam filtering policy"

    cat > "${local_d}/actions.conf" <<EOF
reject = 15;
add_header = 6;
greylist = 4;
EOF

    # ------------------------------------------------------------
    # Ensure directories
    # ------------------------------------------------------------
    log "Preparing Rspamd directories"

    mkdir -p /var/lib/rspamd
    mkdir -p /var/lib/rspamd/dkim

    chown -R _rspamd:_rspamd /var/lib/rspamd

    # ------------------------------------------------------------
    # Enable service
    # ------------------------------------------------------------
    log "Enabling Rspamd service"

    systemctl enable rspamd
    systemctl restart rspamd

    # ------------------------------------------------------------
    # Verify service
    # ------------------------------------------------------------
    log "Verifying Rspamd service"

    systemctl is-active --quiet rspamd || {
        log "Rspamd failed to start"
        exit 1
    }

    log "Rspamd configured successfully"

    emit "rspamd_configured" "true"
    emit "mail_domain" "$MAIL_DOMAIN"
    emit "mail_hostname" "$MAIL_HOSTNAME"
}

# ============================================================
# mail::config_opendkim
# Env: MAIL_DOMAIN MAIL_HOSTNAME DKIM_SELECTOR
# ============================================================
mail::config_opendkim() {
    require_env MAIL_DOMAIN MAIL_HOSTNAME DKIM_SELECTOR
    _mail_env_domain
    [[ "$DKIM_SELECTOR" =~ ^[A-Za-z0-9_-]+$ ]] || die "Invalid DKIM selector"

    log "Configuring OpenDKIM"

    local opendkim_dir="/etc/opendkim"
    local key_dir="${opendkim_dir}/keys/${MAIL_DOMAIN}"

    local key_file="${key_dir}/${DKIM_SELECTOR}.private"
    local dns_file="${key_dir}/${DKIM_SELECTOR}.txt"

    local key_table="${opendkim_dir}/KeyTable"
    local signing_table="${opendkim_dir}/SigningTable"
    local trusted_hosts="${opendkim_dir}/TrustedHosts"
    local opendkim_conf="/etc/opendkim.conf"

    mkdir -p "$key_dir"

    # ------------------------------------------------------------
    # Generate DKIM key (idempotent)
    # ------------------------------------------------------------
    log "Ensuring DKIM key exists"

    if [[ ! -f "$key_file" ]]; then

        log "Generating DKIM key for ${MAIL_DOMAIN}"

        opendkim-genkey \
            -D "$key_dir" \
            -d "$MAIL_DOMAIN" \
            -s "$DKIM_SELECTOR"

    else

        log "DKIM key already exists for ${MAIL_DOMAIN}, skipping generation"

    fi

    # ------------------------------------------------------------
    # Ownership and permissions
    # ------------------------------------------------------------
    log "Applying OpenDKIM permissions"

    chown -R opendkim:opendkim "$opendkim_dir"

    chmod 600 "$key_file"

    # ------------------------------------------------------------
    # Configure signing tables (append-only, idempotent)
    # ------------------------------------------------------------
    log "Configuring signing tables"

    local key_entry="${DKIM_SELECTOR}._domainkey.${MAIL_DOMAIN} ${MAIL_DOMAIN}:${DKIM_SELECTOR}:${key_file}"
    local signing_entry="*@${MAIL_DOMAIN} ${DKIM_SELECTOR}._domainkey.${MAIL_DOMAIN}"

    touch "$key_table"
    touch "$signing_table"

    if ! grep -Fqx "$key_entry" "$key_table"; then
        echo "$key_entry" >> "$key_table"
        log "Added KeyTable entry for ${MAIL_DOMAIN}"
    else
        log "KeyTable entry already exists for ${MAIL_DOMAIN}"
    fi

    if ! grep -Fqx "$signing_entry" "$signing_table"; then
        echo "$signing_entry" >> "$signing_table"
        log "Added SigningTable entry for ${MAIL_DOMAIN}"
    else
        log "SigningTable entry already exists for ${MAIL_DOMAIN}"
    fi

    # ------------------------------------------------------------
    # Configure TrustedHosts
    # ------------------------------------------------------------
    log "Configuring trusted hosts"

    if [[ ! -f "$trusted_hosts" ]]; then
        touch "$trusted_hosts"
        log "Created ${trusted_hosts}"
    fi

    local host
    for host in 127.0.0.1 localhost "$MAIL_HOSTNAME"; do
        if ! grep -Fqx "$host" "$trusted_hosts"; then
            echo "$host" >> "$trusted_hosts"
            log "Added trusted host: ${host}"
        else
            log "Trusted host already exists: ${host}"
        fi
    done

    # ------------------------------------------------------------
    # Main OpenDKIM configuration
    # ------------------------------------------------------------
    log "Writing OpenDKIM configuration"

    cat > "$opendkim_conf" <<EOF
Syslog                  yes
UMask                   002

Canonicalization        relaxed/simple
Mode                    sv
SubDomains              no

Socket                  local:/var/spool/postfix/opendkim/opendkim.sock

KeyTable                refile:${key_table}
SigningTable            refile:${signing_table}
InternalHosts           refile:${trusted_hosts}
ExternalIgnoreList      refile:${trusted_hosts}
EOF

    # ------------------------------------------------------------
    # Restart service
    # ------------------------------------------------------------
    log "Ensuring OpenDKIM service is enabled"

    systemctl enable opendkim

    log "Restarting OpenDKIM"

    systemctl restart opendkim

    # ------------------------------------------------------------
    # Verify service
    # ------------------------------------------------------------
    log "Verifying OpenDKIM"

    systemctl is-active --quiet opendkim || {
        log "OpenDKIM failed to start"
        exit 1
    }

    # ------------------------------------------------------------
    # Extract DNS record
    # ------------------------------------------------------------
    log "Extracting DKIM DNS record"

    local dns_record
    dns_record=$(sed -n '/TXT/,$p' "$dns_file" \
        | sed 's/.*TXT[[:space:]]*(//' \
        | sed 's/).*//' \
        | tr -d '"' \
        | tr -d '\n' \
        | sed 's/[[:space:]]\+/ /g')

    echo
    echo "======================================================"
    echo "DKIM DNS record (copy into your DNS provider)"
    echo "======================================================"
    echo

    echo "Original file generated by OpenDKIM"
    echo "-----------------------------------"
    cat "$dns_file"
    echo
    echo "-----------------------------------"
    echo

    echo "DNS fields for provider"
    echo "-----------------------"
    echo

    echo "Type"
    echo "TXT"
    echo

    echo "Name"
    echo "${DKIM_SELECTOR}._domainkey"
    echo

    echo "Content"
    echo "\"${dns_record}\""
    echo

    echo "======================================================"

    log "OpenDKIM configured successfully"

    emit "opendkim_configured" "true"
    emit "mail_domain" "$MAIL_DOMAIN"
    emit "mail_hostname" "$MAIL_HOSTNAME"
}

# ============================================================
# mail::config_dns_records — print required DNS records
# Env: MAIL_DOMAIN MAIL_HOSTNAME MAIL_IP
# ============================================================
mail::config_dns_records() {
    require_env MAIL_DOMAIN MAIL_HOSTNAME MAIL_IP
    _mail_env_domain

    log "Preparing DNS records"

    local mail_subdomain
    mail_subdomain=$(echo "$MAIL_HOSTNAME" | cut -d'.' -f1)

    echo
    echo "======================================================"
    echo "DNS RECORDS REQUIRED FOR MAIL SERVER"
    echo "======================================================"
    echo

    echo "--------------------------------------"
    echo "A RECORD"
    echo "--------------------------------------"
    echo "Type";  echo "A";   echo
    echo "Name";  echo "$mail_subdomain"; echo
    echo "Content"; echo "$MAIL_IP"; echo

    echo "--------------------------------------"
    echo "MX RECORD"
    echo "--------------------------------------"
    echo "Type";  echo "MX";  echo
    echo "Name";  echo "@";   echo
    echo "Content"; echo "$MAIL_HOSTNAME"; echo
    echo "Priority"; echo "10"; echo

    echo "--------------------------------------"
    echo "SPF RECORD"
    echo "--------------------------------------"
    echo "Type";  echo "TXT"; echo
    echo "Name";  echo "@";   echo
    echo "Content"; echo "\"v=spf1 mx a ip4:${MAIL_IP} ~all\""; echo

    echo "--------------------------------------"
    echo "DMARC RECORD"
    echo "--------------------------------------"
    echo "Type";  echo "TXT"; echo
    echo "Name";  echo "_dmarc"; echo
    echo "Content"
    echo "\"v=DMARC1; p=quarantine; rua=mailto:postmaster@${MAIL_DOMAIN}; ruf=mailto:postmaster@${MAIL_DOMAIN}; fo=1\""
    echo

    echo "======================================================"

    log "DNS configuration output generated"

    emit "dns_records_generated" "true"
    emit "mail_domain" "$MAIL_DOMAIN"
    emit "mail_hostname" "$MAIL_HOSTNAME"
}

# ============================================================
# mail::user_add
# Env: MAIL_USER MAIL_PASSWORD MAIL_DOMAIN MYSQL_ROOT_USER
#      MYSQL_ROOT_PASSWORD
# ============================================================
mail::user_add() {
    require_env MAIL_USER MAIL_PASSWORD MAIL_DOMAIN
    require_env MYSQL_ROOT_USER MYSQL_ROOT_PASSWORD
    mail::_validate_user

    log "Creating virtual mail user"

    local email="${MAIL_USER}@${MAIL_DOMAIN}"
    local mail_dir="/var/mail/vhosts/${MAIL_DOMAIN}/${MAIL_USER}"

    local password_hash
    password_hash=$(doveadm pw -s SHA512-CRYPT -p "${MAIL_PASSWORD}")

    _mail_user_db_ensure() {
        local email="$1" hash="$2"

        local user_exists
        user_exists=$(mysql_query_db "$MAIL_DB" -N -e \
            "SELECT COUNT(*) FROM virtual_users WHERE email='${email}'")

        if [[ "$user_exists" -eq 0 ]]; then

            log "Creating database user ${email}"

            mysql_query_db "$MAIL_DB" <<SQL
INSERT INTO virtual_users (email,password)
VALUES ('${email}','${hash}');
SQL

        else

            log "User already exists, updating password"

            mysql_query_db "$MAIL_DB" <<SQL
UPDATE virtual_users
SET password='${hash}'
WHERE email='${email}';
SQL

        fi
    }

    _mail_user_db_ensure "$email" "$password_hash"

    # ------------------------------------------------------------
    # Ensure mailbox directory exists
    # ------------------------------------------------------------
    mkdir -p "/var/mail/vhosts/${MAIL_DOMAIN}"

    if [[ -d "${mail_dir}" ]]; then
        log "Mailbox directory already exists"
    else
        log "Creating mailbox directory"
        mkdir -p "${mail_dir}"
    fi

    # ------------------------------------------------------------
    # Ensure Maildir structure exists
    # ------------------------------------------------------------
    if [[ -d "${mail_dir}/cur" && -d "${mail_dir}/new" && -d "${mail_dir}/tmp" ]]; then
        log "Maildir structure already exists"
    else
        log "Creating Maildir structure"
        maildirmake.dovecot "${mail_dir}"
    fi

    # ------------------------------------------------------------
    # Fix ownership and permissions
    # ------------------------------------------------------------
    log "Fixing mailbox permissions"

    chown -R vmail:mail "${mail_dir}"
    chmod -R 700 "${mail_dir}"

    # ------------------------------------------------------------
    # Display mailbox information
    # ------------------------------------------------------------
    log "Listing mailbox directory"

    ls -la "${mail_dir}"

    log "Mail user ready"

    emit "mail_user_created" "true"
    emit "email" "$email"
    emit "mail_dir" "$mail_dir"
}

# ============================================================
# mail::user_remove
# Env: MAIL_USER MAIL_DOMAIN MYSQL_ROOT_USER MYSQL_ROOT_PASSWORD
# ============================================================
mail::user_remove() {
    require_env MAIL_USER MAIL_DOMAIN
    require_env MYSQL_ROOT_USER MYSQL_ROOT_PASSWORD
    mail::_validate_user

    log "Removing virtual mail user"

    local email="${MAIL_USER}@${MAIL_DOMAIN}"
    local mail_dir="/var/mail/vhosts/${MAIL_DOMAIN}/${MAIL_USER}"

    # ------------------------------------------------------------
    # Check if user exists in database
    # ------------------------------------------------------------
    log "Checking if mailbox exists in database"

    local user_exists
    user_exists=$(mysql_query_db "$MAIL_DB" -N -e \
        "SELECT COUNT(*) FROM virtual_users WHERE email='${email}'")

    if [[ "$user_exists" -eq 0 ]]; then
        log "User does not exist in database: ${email}"
        emit "mail_user_exists" "false"
        exit 1
    fi

    # ------------------------------------------------------------
    # Delete user from database
    # ------------------------------------------------------------
    log "Removing user from database"

    mysql_query_db "$MAIL_DB" <<SQL
DELETE FROM virtual_users
WHERE email='${email}';
SQL

    # ------------------------------------------------------------
    # Remove mailbox directory
    # ------------------------------------------------------------
    if [[ -d "${mail_dir}" ]]; then
        log "Removing mailbox directory ${mail_dir}"
        rm -rf "${mail_dir}"
    else
        log "Mailbox directory not found"
    fi

    # ------------------------------------------------------------
    # Verify deletion
    # ------------------------------------------------------------
    user_exists=$(mysql_query_db "$MAIL_DB" -N -e \
        "SELECT COUNT(*) FROM virtual_users WHERE email='${email}'")

    if [[ "$user_exists" -eq 0 ]]; then
        log "Mailbox deleted successfully"

        emit "mail_user_deleted" "true"
        emit "email" "$email"
    else
        log "Failed to delete mailbox from database"

        emit "mail_user_deleted" "false"
        exit 1
    fi
}

# ============================================================
# mail::user_list
# Env: MAIL_DOMAIN MYSQL_ROOT_USER MYSQL_ROOT_PASSWORD
# ============================================================
mail::user_list() {
    require_env MAIL_DOMAIN
    require_env MYSQL_ROOT_USER MYSQL_ROOT_PASSWORD
    [[ "$MAIL_DOMAIN" =~ ^[A-Za-z0-9][A-Za-z0-9.-]*$ ]] || die "Invalid mail domain"

    log "Listing virtual mail users"
    log "Querying database for users in domain ${MAIL_DOMAIN}"

    mysql_query_db "$MAIL_DB" -N -e "
SELECT email
FROM virtual_users
WHERE email LIKE '%@${MAIL_DOMAIN}'
ORDER BY email;
"

    log "User listing completed"

    emit "mail_users_listed" "true"
    emit "mail_domain" "$MAIL_DOMAIN"
}

# ============================================================
# mail::user_update_password
# Env: MAIL_USER MAIL_PASSWORD MAIL_DOMAIN MYSQL_ROOT_USER
#      MYSQL_ROOT_PASSWORD
# ============================================================
mail::user_update_password() {
    require_env MAIL_USER MAIL_PASSWORD MAIL_DOMAIN
    require_env MYSQL_ROOT_USER MYSQL_ROOT_PASSWORD
    mail::_validate_user

    log "Updating virtual mail user password"

    local email="${MAIL_USER}@${MAIL_DOMAIN}"

    # ------------------------------------------------------------
    # Check if mailbox exists
    # ------------------------------------------------------------
    log "Checking if mailbox exists"

    local user_exists
    user_exists=$(mysql_query_db "$MAIL_DB" -N -e \
        "SELECT COUNT(*) FROM virtual_users WHERE email='${email}';")

    if [[ "$user_exists" -eq 0 ]]; then
        log "Mailbox does not exist: ${email}"
        emit "mail_user_exists" "false"
        exit 1
    fi

    # ------------------------------------------------------------
    # Generate password hash
    # ------------------------------------------------------------
    log "Generating password hash"

    local password_hash
    password_hash=$(doveadm pw -s SHA512-CRYPT -p "${MAIL_PASSWORD}")

    # ------------------------------------------------------------
    # Update password in database
    # ------------------------------------------------------------
    log "Updating password for ${email}"

    mysql_query_db "$MAIL_DB" <<SQL
UPDATE virtual_users
SET password='${password_hash}'
WHERE email='${email}';
SQL

    log "Password updated successfully"

    emit "mail_user_password_updated" "true"
    emit "email" "$email"
}

# ============================================================
# mail::verify
# Env: MAIL_DOMAIN MAIL_HOSTNAME MAIL_IP DKIM_SELECTOR
# ============================================================
mail::verify() {
    require_env MAIL_DOMAIN MAIL_HOSTNAME MAIL_IP DKIM_SELECTOR
    _mail_env_domain

    log "Verifying mail server"

    # ------------------------------------------------------------
    # DNS checks
    # ------------------------------------------------------------
    log "Checking DNS records"

    local mx_record dkim_record spf_record
    mx_record=$(dig +short MX "${MAIL_DOMAIN}" | awk '{print $2}' | sed 's/\.$//')
    dkim_record=$(dig +short TXT "${DKIM_SELECTOR}._domainkey.${MAIL_DOMAIN}")
    spf_record=$(dig +short TXT "${MAIL_DOMAIN}" | grep spf || true)

    if [[ -z "$mx_record" ]]; then
        log "MX record not found"
        exit 1
    fi

    if [[ "$mx_record" != "$MAIL_HOSTNAME" ]]; then
        log "MX record does not match expected hostname"
        exit 1
    fi

    if [[ -z "$dkim_record" ]]; then
        log "DKIM record not found"
        exit 1
    fi

    if [[ -z "$spf_record" ]]; then
        log "SPF record not found"
        exit 1
    fi

    log "DNS checks passed"

    # ------------------------------------------------------------
    # Service checks
    # ------------------------------------------------------------
    log "Checking services"

    local services=(postfix dovecot rspamd opendkim)
    local svc
    for svc in "${services[@]}"; do
        if ! systemctl is-active --quiet "$svc"; then
            log "Service $svc is not running"
            exit 1
        fi
    done

    log "All services running"

    # ------------------------------------------------------------
    # Port checks
    # ------------------------------------------------------------
    log "Checking required ports"

    local ports=(25 "${SMTP_PORT:-587}" 143 "${IMAP_PORT:-993}")
    local port
    for port in "${ports[@]}"; do
        if ! port_listening "$port"; then
            log "Port $port not listening"
            exit 1
        fi
    done

    log "Required ports are open"

    # ------------------------------------------------------------
    # SMTP connectivity test
    # ------------------------------------------------------------
    log "Testing SMTP connectivity"

    if ! timeout 5 bash -c "echo QUIT | nc localhost 25" >/dev/null 2>&1; then
        log "SMTP test failed"
        exit 1
    fi

    log "SMTP responding"

    # ------------------------------------------------------------
    # DKIM test (basic)
    # ------------------------------------------------------------
    log "Checking DKIM key"

    local key_file="/etc/opendkim/keys/${MAIL_DOMAIN}/${DKIM_SELECTOR}.private"

    if [[ ! -f "$key_file" ]]; then
        log "DKIM private key missing"
        exit 1
    fi

    log "DKIM key present"

    # ------------------------------------------------------------
    # Rspamd test
    # ------------------------------------------------------------
    log "Checking Rspamd"

    if ! rspamadm configtest >/dev/null 2>&1; then
        log "Rspamd configuration invalid"
        exit 1
    fi

    log "Rspamd configuration valid"

    # ------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------
    log "Mail server verification successful"

    emit "mail_server_verified" "true"
    emit "mail_domain" "$MAIL_DOMAIN"
    emit "mail_hostname" "$MAIL_HOSTNAME"
}
