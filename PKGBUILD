# Maintainer: iamnetuseragent
pkgname=demo-ghostprovider
pkgver=0.0.13
pkgrel=1
pkgdesc="TUI for self-hosting & localhost management"
arch=('x86_64')
url="https://github.com/iamnetuseragent/demo-ghostprovider"
license=('custom:Source-Available')
depends=('systemd' 'git')
makedepends=('rust' 'cargo')
# Pinned to the release tag — update pkgver together with the tag.
source=("$pkgname-$pkgver.tar.gz::git+https://github.com/iamnetuseragent/demo-ghostprovider.git#tag=v$pkgver")
sha256sums=('SKIP')

build() {
  cd "$srcdir/$pkgname-$pkgver.tar.gz"
  export CARGO_HOME="$srcdir/cargo-home"
  cargo build --release --locked
}

check() {
  cd "$srcdir/$pkgname-$pkgver.tar.gz"
  export CARGO_HOME="$srcdir/cargo-home"
  ./target/release/$pkgname --version
}

package() {
  install -Dm755 "$srcdir/$pkgname-$pkgver.tar.gz/target/release/$pkgname" \
    "$pkgdir/usr/bin/$pkgname"
}
