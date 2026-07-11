#!/bin/bash
# Copy baked-in configs into writable tmpfs at /home/agent/.pi
cp -r /opt/pi-configs/* /home/agent/.pi/
chown -R agent:agent /home/agent/.pi

# Switch to agent user and exec the CMD
exec runuser -u agent -- "$@"
