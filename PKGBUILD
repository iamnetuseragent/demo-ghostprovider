# Maintainer: iamnetuseragent
pkgname=demo-ghostprovider
pkgver=0.0.14
pkgrel=1
pkgdesc="TUI for self-hosting & localhost management"
arch=('x86_64')
url="https://github.com/iamnetuseragent/demo-ghostprovider"
license=('custom:Source-Available')
depends=('systemd' 'git')
# Not listed as makedepends on purpose: any cargo on PATH works (pacman's
# rust or a rustup install); hard-requiring the pacman packages would
# needlessly break rustup-only systems.
makedepends=()
# Pinned to the release tag — update pkgver together with the tag.
# Mirror: https://codeberg.org/netuser/demo-ghostprovider (same tags).
# makepkg cannot try sources sequentially; swap the URL manually if
# github.com is unreachable.
source=("$pkgname-$pkgver.tar.gz::git+https://github.com/iamnetuseragent/demo-ghostprovider.git#tag=v$pkgver")
sha256sums=('SKIP')

build() {
  cd "$srcdir/$pkgname-$pkgver.tar.gz"
  export CARGO_HOME="$srcdir/cargo-home"
  # Distro flags poison ring's C/asm objects (_FORTIFY_SOURCE without -O
  # compiles some translation units down to nothing -> undefined symbols).
  # Cargo/rustc bring their own, correct flags.
  unset CFLAGS CXXFLAGS LDFLAGS CPPFLAGS
  cargo build --release --locked
}

check() {
  cd "$srcdir/$pkgname-$pkgver.tar.gz"
  export CARGO_HOME="$srcdir/cargo-home"
  unset CFLAGS CXXFLAGS LDFLAGS CPPFLAGS
  ./target/release/$pkgname --version
}

package() {
  install -Dm755 "$srcdir/$pkgname-$pkgver.tar.gz/target/release/$pkgname" \
    "$pkgdir/usr/bin/$pkgname"
}
