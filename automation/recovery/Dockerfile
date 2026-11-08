ARG HERMES_IMAGE
FROM ${HERMES_IMAGE}
USER root
COPY --chmod=0600 snapshot.zip manifest.json /opt/nix-recovery/
COPY --chmod=0444 recovery.py /opt/nix-recovery/recovery.py
ENTRYPOINT ["python3", "/opt/nix-recovery/recovery.py"]
CMD ["verify"]
