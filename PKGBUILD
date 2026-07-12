pkgname=demo-ghostprovider
pkgver=1.0.0
pkgrel=1
pkgdesc="Demo version: TUI for self-hosting & localhost management (limited to 3 services)"
arch=('any')
url="https://github.com/iamnetuseragent/demo-ghostprovider"
license=('MIT')
depends=('python' 'git' 'python-pip')
makedepends=('git')
# Pinned to commit b152865 — update pkgver and sha256sums on each release
_commit=b15286543b3d24bd516668ba1504ec814db5a475
source=("$pkgname-$pkgver.tar.gz::git+ssh://git@github.com/iamnetuseragent/demo-ghostprovider.git#commit=$_commit")
sha256sums=('SKIP')

package() {
  cd "$srcdir/$pkgname-$_commit"

  install -dm755 "$pkgdir/usr/bin"
  cat > "$pkgdir/usr/bin/demo-ghostprovider" << 'EOF'
#!/bin/bash
exec /opt/demo-ghostprovider/.venv/bin/python3 -m demo_ghostprovider "$@"
EOF
  chmod 755 "$pkgdir/usr/bin/demo-ghostprovider"

  install -d "$pkgdir/opt/$pkgname"

  cp -r demo_ghostprovider "$pkgdir/opt/$pkgname/"
  cp pyproject.toml "$pkgdir/opt/$pkgname/"

  python -m venv "$pkgdir/opt/$pkgname/.venv"
  "$pkgdir/opt/$pkgname/.venv/bin/pip" install --no-cache-dir "$pkgdir/opt/$pkgname"
}
