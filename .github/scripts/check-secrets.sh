#!/usr/bin/env bash
# Fails when tracked files contain live-looking payment keys, private keys or sensitive files.
# Test fixtures use obviously fake values (sk_test_unit, whsec_test_..., inchk_secretkey123) that do not match.
set -euo pipefail

status=0
patterns=(
  'sk_live_[A-Za-z0-9]{16,}'            # Stripe live secret key
  'rk_live_[A-Za-z0-9]{16,}'            # Stripe live restricted key
  'sk_test_[A-Za-z0-9]{24,}'            # real-looking Stripe test key (fixtures are short)
  'whsec_[A-Za-z0-9]{32,}'              # real-looking Stripe webhook secret
  'inchk_[A-Za-z0-9_-]{32,}'            # real-looking InfinitePay API key
  '-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----'
)
for pattern in "${patterns[@]}"; do
  if git grep -nIE -- "$pattern" -- ':!*.lock' ':!package-lock.json' ':!.github/scripts/check-secrets.sh'; then
    echo "::error::possible secret matching: $pattern"
    status=1
  fi
done

# Files that must never be committed
if git ls-files | grep -nE '(^|/)\.env$|(^|/)\.env\.(local|production)$|\.sqlite$|\.db$|painel-credenciais|\.pem$|\.key$'; then
  echo "::error::sensitive file tracked by git"
  status=1
fi

[ "$status" -eq 0 ] && echo "No secrets or sensitive files found"
exit "$status"
