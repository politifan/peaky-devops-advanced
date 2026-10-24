"""Hosted runner preflight: compile the no-payload BPF, no packet processing."""
import subprocess
from routing_observer import SYN_FILTER
subprocess.run(['sudo','-n','tcpdump','-p','-i','any','-ddd',SYN_FILTER],stdout=subprocess.DEVNULL,check=True,timeout=5)
