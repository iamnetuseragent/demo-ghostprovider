pkgname=demo-ghostprovider
pkgver=1.7.4
pkgrel=1
pkgdesc="TUI for self-hosting & localhost management"
arch=('any')
url="https://github.com/iamnetuseragent/demo-ghostprovider"
license=('custom:Source-Available')
depends=('python' 'git' 'python-pip')
makedepends=('git' 'python-virtualenv')
# Pinned to the v1.7.4 release commit — update pkgver and _commit on each release
_commit=6c3125c
source=("$pkgname-$pkgver.tar.gz::git+https://github.com/iamnetuseragent/demo-ghostprovider.git#commit=$_commit")
sha256sums=('SKIP')

package() {
  cd "$srcdir/$pkgname-$pkgver.tar.gz"

  install -dm755 "$pkgdir/usr/bin"
  cat > "$pkgdir/usr/bin/demo-ghostprovider" << 'EOF'
#!/bin/bash
exec /opt/demo-ghostprovider/.venv/bin/python3 -m demo_ghostprovider "$@"
EOF
  chmod 755 "$pkgdir/usr/bin/demo-ghostprovider"

  install -d "$pkgdir/opt/$pkgname"

  cp -r software "$pkgdir/opt/$pkgname/"
  cp pyproject.toml "$pkgdir/opt/$pkgname/"

  python -m venv "$pkgdir/opt/$pkgname/.venv"
  "$pkgdir/opt/$pkgname/.venv/bin/pip" install --no-cache-dir "$pkgdir/opt/$pkgname"
}
