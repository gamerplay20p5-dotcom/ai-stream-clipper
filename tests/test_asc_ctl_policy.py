"""Isolation policy of infrastructure/admin/asc-ctl (the sudo-allowed deployment tool)."""

from __future__ import annotations

import copy
import importlib.machinery
import importlib.util

from conftest import ROOT

_loader = importlib.machinery.SourceFileLoader("asc_ctl", str(ROOT / "infrastructure" / "admin" / "asc-ctl"))
_spec = importlib.util.spec_from_loader("asc_ctl", _loader)
asc_ctl = importlib.util.module_from_spec(_spec)
_loader.exec_module(asc_ctl)

CONF = {"SERVER": "210", "LAN_IP": "192.168.1.210", "RENDER_GID": "993", "ASC_USER": "asc", "REPO": "/home/asc/x"}

GOOD = {
    "name": "asc",
    "networks": {"asc_net": {"name": "asc_net_210"}},
    "volumes": {"asc_storage": {"name": "asc_asc_storage"}, "asc_work": {"name": "asc_asc_work"}},
    "services": {
        "api": {
            "container_name": "asc-api", "image": "ai-stream-clipper:0.2.1", "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "ports": [{"mode": "ingress", "host_ip": "192.168.1.210", "target": 8780, "published": "8780",
                       "protocol": "tcp"}],
            "volumes": [{"type": "volume", "source": "asc_storage", "target": "/data/storage"},
                        {"type": "bind", "source": "/etc/asc/tls", "target": "/tls", "read_only": True}],
            "deploy": {"resources": {"limits": {"cpus": 2, "memory": "2147483648"}}},
        },
        "worker-vision": {
            "container_name": "asc-worker-vision", "image": "ai-stream-clipper-vision:0.2.1",
            "devices": [{"source": "/dev/dri/renderD128", "target": "/dev/dri/renderD128", "permissions": "rwm"}],
            "group_add": ["993"],
            "volumes": [{"type": "bind", "source": "/opt/asc/models", "target": "/models", "read_only": True},
                        {"type": "volume", "source": "asc_work", "target": "/data/work"}],
            "deploy": {"resources": {"limits": {"cpus": 4, "memory": "12884901888"}}},
        },
        "postgres": {"container_name": "asc-postgres", "image": "postgres:16.4-bookworm",
                     "deploy": {"resources": {"limits": {"cpus": 2, "memory": "4294967296"}}}},
    },
}


def _with(path, value):
    cfg = copy.deepcopy(GOOD)
    node = cfg
    for k in path[:-1]:
        node = node[k]
    node[path[-1]] = value
    return cfg


def test_project_config_passes():
    assert asc_ctl.check_policy(GOOD, CONF) == []


def test_policy_blocks_escalation_and_interference():
    api = ("services", "api")
    cases = {
        "privileged": _with(api + ("privileged",), True),
        "cap_add": _with(api + ("cap_add",), ["SYS_ADMIN"]),
        "network_mode": _with(api + ("network_mode",), "host"),
        "pid=host": _with(api + ("pid",), "host"),
        "docker socket": _with(api + ("volumes",), [{"type": "bind", "source": "/var/run/docker.sock",
                                                     "target": "/var/run/docker.sock"}]),
        "host root": _with(api + ("volumes",), [{"type": "bind", "source": "/opt/asc/../../", "target": "/h"}]),
        "rw tls": _with(api + ("volumes",), [{"type": "bind", "source": "/etc/asc/tls", "target": "/tls"}]),
        "other project volume": _with(("volumes", "asc_storage"), {"name": "caddy_data", "external": True}),
        "port on all interfaces": _with(api + ("ports",), [{"host_ip": "", "published": "8780", "target": 8780}]),
        "port 443": _with(api + ("ports",), [{"host_ip": "192.168.1.210", "published": "443", "target": 8780}]),
        "foreign image": _with(api + ("image",), "nginx:latest"),
        "container name": _with(api + ("container_name",), "caddy"),
        "no limits": _with(api + ("deploy",), {}),
        "unconfined": _with(api + ("security_opt",), ["seccomp=unconfined"]),
        "root user": _with(api + ("user",), "0:0"),
        "build": _with(api + ("build",), {"context": "/"}),
        "other device": _with(("services", "worker-vision", "devices"), ["/dev/sda:/dev/sda"]),
        "docker group": _with(("services", "worker-vision", "group_add"), ["988"]),
        "external network": _with(("networks", "asc_net"), {"name": "caddy_net", "external": True}),
        "project name": _with(("name",), "caddy"),
    }
    for label, cfg in cases.items():
        assert asc_ctl.check_policy(cfg, CONF), f"policy accepted: {label}"


def test_gpu_devices_only_on_gpu_hosts():
    assert asc_ctl.check_policy(GOOD, {**CONF, "SERVER": "206", "LAN_IP": "192.168.1.206"})


def test_image_archive_tag_allowlist():
    assert asc_ctl.OWN_IMAGE_RE.match("ai-stream-clipper:0.2.1")
    assert asc_ctl.OWN_IMAGE_RE.match("docker.io/library/ai-stream-clipper-vision:0.2.1")
    for bad in ("postgres:16", "ghcr.io/ptero-eggs/games:valheim", "ai-stream-clipper:x;rm", "caddy:2"):
        assert not asc_ctl.OWN_IMAGE_RE.match(bad)


TUNNEL = {
    "container_name": "asc-tunnel", "image": "cloudflare/cloudflared:2025.4.0", "read_only": True,
    "cap_drop": ["ALL"], "security_opt": ["no-new-privileges:true"],
    "command": ["tunnel", "--no-autoupdate", "run", "--token-file", "/etc/asc/tunnel/token"],
    "volumes": [{"type": "bind", "source": "/etc/asc/tunnel/token", "target": "/etc/asc/tunnel/token", "read_only": True},
                {"type": "bind", "source": "/etc/asc/tls/ca.crt", "target": "/etc/asc/tls/ca.crt", "read_only": True}],
    "deploy": {"resources": {"limits": {"cpus": 0.5, "memory": "268435456"}}},
}


def test_tunnel_connector_is_allowed_only_in_its_exact_shape():
    cfg = copy.deepcopy(GOOD)
    cfg["services"]["tunnel"] = copy.deepcopy(TUNNEL)
    assert asc_ctl.check_policy(cfg, CONF) == []
    bad = {
        "unpinned image": ("image", "cloudflare/cloudflared:latest"),
        "other image": ("image", "evil/cloudflared:2025.4.0"),
        "published port": ("ports", [{"host_ip": "192.168.1.210", "published": "8780", "target": 8780}]),
        "writable fs": ("read_only", False),
        "caps kept": ("cap_drop", []),
        "writable token": ("volumes", [{"type": "bind", "source": "/etc/asc/tunnel/token", "target": "/t",
                                        "read_only": False}]),
        "renamed": ("container_name", "asc-other"),
    }
    for label, (k, v) in bad.items():
        c = copy.deepcopy(cfg)
        c["services"]["tunnel"][k] = v
        assert asc_ctl.check_policy(c, CONF), f"policy accepted: {label}"
    # never on 201/206, and the token dir cannot be mounted by other services
    assert asc_ctl.check_policy(cfg, {**CONF, "SERVER": "206", "LAN_IP": "192.168.1.206"})
    c = copy.deepcopy(GOOD)
    c["services"]["api"]["volumes"].append({"type": "bind", "source": "/etc/asc/tunnel", "target": "/x", "read_only": True})
    assert asc_ctl.check_policy(c, CONF)


def test_tunnel_token_never_from_arguments():
    import pytest

    with pytest.raises(asc_ctl.Denied):
        asc_ctl.cmd_tunnel_token_install(CONF, ["eyJhIjoiYiJ9" * 10])
    with pytest.raises(asc_ctl.Denied):
        asc_ctl.cmd_tunnel_token_install({**CONF, "SERVER": "201"}, [])
