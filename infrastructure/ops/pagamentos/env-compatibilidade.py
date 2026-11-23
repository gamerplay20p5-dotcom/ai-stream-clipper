"""Na 210, como asc: confere se o /etc/asc/asc.env atual passaria por um futuro `asc-ctl env-install`.

Usa a saída do `asc-ctl env-show` (segredos já mascarados) e as regras do asc-ctl do repositório
(~/ai-stream-clipper/infrastructure/admin/asc-ctl). Valores secretos não são lidos, então só a chave é conferida.
Uso: python3 env-compatibilidade.py
"""
import os
import subprocess
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader

path = os.path.expanduser("~/ai-stream-clipper/infrastructure/admin/asc-ctl")
loader = SourceFileLoader("ascctl", path)
ctl = module_from_spec(spec_from_loader("ascctl", loader))
loader.exec_module(ctl)
out = subprocess.run(["sudo", "-n", "/usr/local/sbin/asc-ctl", "env-show"], capture_output=True, text=True, check=True).stdout
problems = 0
for n, line in enumerate(out.splitlines(), 1):
    if not line.strip() or line.lstrip().startswith("#"):
        continue
    k, _, v = line.partition("=")
    k, v = k.strip(), v.strip()
    if k not in ctl.ENV_KEYS:
        print(f"ERRO linha {n}: chave {k} não é aceita pelo asc-ctl {ctl.VERSION}")
        problems += 1
        continue
    if k in ctl.SECRET_KEYS:
        continue  # mascarado: o valor não é lido
    rule = ctl.LOOSE_VALUE_RE if k in ctl.LOOSE_VALUE_KEYS else ctl.ENV_VALUE_RE
    if not rule.match(v):
        print(f"ERRO linha {n}: valor de {k} tem caracteres que o env-install recusa")
        problems += 1
print(f"asc-ctl {ctl.VERSION}: {'COMPATÍVEL' if not problems else f'{problems} problema(s)'} ({len(out.splitlines())} linhas; segredos não conferidos)")
