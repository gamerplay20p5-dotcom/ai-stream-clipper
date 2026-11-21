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


DB212 = {"SERVER": "212", "LAN_IP": "192.168.1.212", "RENDER_GID": "", "ASC_USER": "asc", "REPO": "/home/asc/x"}
PG212 = {
    "name": "asc",
    "networks": {"asc_net": {"name": "asc_net_212"}},
    "volumes": {"asc_pgdata": {"name": "asc_pgdata"}},
    "services": {"postgres": {
        "container_name": "asc-postgres", "image": "postgres:16.4-bookworm",
        "ports": [{"mode": "ingress", "host_ip": "192.168.1.212", "target": 5432, "published": "55432", "protocol": "tcp"}],
        "volumes": [{"type": "volume", "source": "asc_pgdata", "target": "/var/lib/postgresql/data"},
                    {"type": "bind", "source": "/opt/asc/pg", "target": "/etc/asc-pg", "read_only": True}],
        "deploy": {"resources": {"limits": {"cpus": 4, "memory": "34359738368"}}}}},
}


def test_db_host_212_runs_only_the_isolated_postgres():
    assert asc_ctl.check_policy(PG212, DB212) == []
    bad = copy.deepcopy(PG212)
    bad["services"]["postgres"]["ports"][0]["published"] = "5432"  # other projects' port
    assert any("55432" in x or "not allowed" in x for x in asc_ctl.check_policy(bad, DB212))
    bad = copy.deepcopy(PG212)
    bad["services"]["postgres"]["ports"][0]["host_ip"] = "0.0.0.0"
    assert asc_ctl.check_policy(bad, DB212)
    bad = copy.deepcopy(PG212)
    bad["services"]["api"] = copy.deepcopy(GOOD["services"]["api"])
    assert any("only the asc PostgreSQL" in x for x in asc_ctl.check_policy(bad, DB212))
    bad = copy.deepcopy(PG212)
    bad["services"]["postgres"]["volumes"][1]["read_only"] = False
    assert asc_ctl.check_policy(bad, DB212)
    assert asc_ctl.check_policy(PG212, CONF)  # 55432 is not a 210 port


def test_pg_hba_allows_only_the_api_host():
    hba = asc_ctl.pg_hba()
    assert "host    asc   asc   192.168.1.210/32   scram-sha-256" in hba
    assert hba.strip().endswith("reject") and "trust" not in hba.split("local")[1].split("\n", 1)[1]


def test_db_restore_only_on_212_and_never_over_data(monkeypatch, tmp_path):
    import pytest

    with pytest.raises(asc_ctl.Denied):
        asc_ctl.cmd_db_restore(CONF, ["x.sql.gz"])
    monkeypatch.setattr(asc_ctl, "user_file", lambda conf, p: str(tmp_path / p))
    monkeypatch.setattr(asc_ctl, "_db_tables", lambda conf: 38)
    with pytest.raises(asc_ctl.Denied, match="not empty"):
        asc_ctl.cmd_db_restore(DB212, ["asc.sql.gz"])


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
