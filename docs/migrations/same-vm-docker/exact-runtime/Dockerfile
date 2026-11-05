# Evaluation-only draft. Inputs must be separately filtered and validated.
# No external frontend, network acquisition, live site tree or service startup.
FROM frappe/erpnext@sha256:eaf1da1478c2b99339a0a8bc37af723a67ecf9cd4f84b313a0c3765743486fcc

USER root
COPY runtime/python3.14 /usr/bin/python3.14
COPY runtime/stdlib/ /usr/lib/python3.14/
COPY runtime/sitecustomize.py /etc/python3.14/sitecustomize.py
COPY runtime/compatibility/ /opt/ndf-native/lib/
COPY runtime/native-clients/ /opt/ndf-native/bin/
COPY --chown=frappe:frappe runtime/application-env/ /home/frappe/bench/env/
COPY --chown=frappe:frappe runtime/bench-env/ /home/frappe/.venv/
COPY --chown=frappe:frappe runtime/node/ /home/frappe/.nvm/versions/node/v24.14.0/
COPY --chown=frappe:frappe apps/ /home/frappe/bench/apps/
COPY --chmod=0755 ndf-native-exec /usr/local/bin/ndf-native-exec

WORKDIR /home/frappe/bench
ENV PYTHONDONTWRITEBYTECODE=1
USER frappe
ENTRYPOINT ["/usr/local/bin/ndf-native-exec"]
CMD ["/home/frappe/bench/env/bin/python", "--version"]
