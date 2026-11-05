# Offline corrective layer only; preserve the original retained image.
FROM ndf-local/exact-runtime@sha256:c498d968d41e675c3ab27a6aabaef679720e118e2be919923590b4b7e5b37147
USER root
COPY --chown=frappe:frappe yarn/ /opt/ndf-native/yarn/
COPY --chmod=0755 ndf-native-exec /usr/local/bin/ndf-native-exec
USER frappe
