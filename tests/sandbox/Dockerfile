ARG FRAPPE_IMAGE=frappe/erpnext:v15.124.1@sha256:28a430272ae04ee55c36818423e2233c7ac43481e121f7e110811b3c3754261c
FROM ${FRAPPE_IMAGE}

USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
      sudo openssh-server jq python3-venv \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /run/sshd /home/frappe/automation-logs /home/frappe/backups \
    && printf 'frappe ALL=(ALL) NOPASSWD:ALL\n' > /etc/sudoers.d/frappe \
    && chmod 440 /etc/sudoers.d/frappe

# Preserve image virtualenv shebangs via the original path while providing
# the real project layout expected by the lifecycle scripts.
RUN groupadd -f frappe
RUN mv /home/frappe/frappe-bench /home/frappe/bench \
    && ln -s bench /home/frappe/frappe-bench \
    && python3 -m venv --system-site-packages /home/frappe/.venv \
    && chown -R frappe:frappe /home/frappe/.venv /home/frappe/automation-logs /home/frappe/backups

COPY --chown=frappe:frappe pyproject.toml README.md license.txt /home/frappe/bench/apps/dt_master/
COPY --chown=frappe:frappe dt_master /home/frappe/bench/apps/dt_master/dt_master
COPY tests/sandbox/setup.sh tests/sandbox/runner.sh /opt/dt-sandbox/

USER frappe
WORKDIR /home/frappe/bench
RUN env/bin/pip install --no-cache-dir --no-deps -e apps/dt_master \
    && printf 'frappe\nerpnext\ndt_master\n' > sites/apps.txt \
    && bench setup requirements --node \
    && bench build --app dt_master \
    && bash -lc 'source /home/frappe/.venv/bin/activate; bench --version'

ENV DT_DOCKER_SANDBOX=1 PROJECT_BASE_DIR=/home/frappe \
    PROJECT_LOGS_DIR=/home/frappe/automation-logs SCRIPT_LIB_DIR=/opt/dt-scripts/lib
