FROM ubuntu:24.04
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    openssh-server sudo python3 python3-apt ca-certificates procps \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /bin/bash lab \
    && passwd -d lab \
    && printf 'lab ALL=(ALL) NOPASSWD: ALL\n' > /etc/sudoers.d/lab \
    && chmod 0440 /etc/sudoers.d/lab
RUN printf 'PasswordAuthentication no\nKbdInteractiveAuthentication no\nPermitRootLogin no\nAllowUsers lab\n' \
    > /etc/ssh/sshd_config.d/dva.conf
COPY entrypoint.sh /usr/local/bin/dva-entrypoint
RUN chmod 0755 /usr/local/bin/dva-entrypoint
EXPOSE 22 8080
ENTRYPOINT ["/usr/local/bin/dva-entrypoint"]
