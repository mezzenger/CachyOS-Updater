pkgname=cachyos-update-center
pkgver=0.7.3
pkgrel=1
pkgdesc="Update dashboard for Pacman and Flatpak"
arch=('x86_64' 'aarch64')
url="https://github.com/mezzenger/CachyOS-Updater"
license=('custom:all-rights-reserved')
depends=('pacman-contrib' 'gtk4' 'libadwaita' 'polkit')
optdepends=(
  'flatpak: Flatpak update support'
  'piper-tts: spoken update summaries'
)
makedepends=('rust')
checkdepends=('python')
# Build this checkout: the Rust rewrite has not been published as an upstream tag.
# This local recipe snapshots the source during prepare().
source=()
sha256sums=()

prepare() {
  mkdir -p "$srcdir/$pkgname/tests"
  cp "$startdir/Cargo.toml" "$startdir/Cargo.lock" "$srcdir/$pkgname/"
  cp -a "$startdir/rust" "$startdir/assets" "$startdir/packaging" "$srcdir/$pkgname/"
  cp -a "$startdir/tests/fixtures" "$srcdir/$pkgname/tests/"
  cp "$startdir/io.github.alvin.Cuc.desktop" "$srcdir/$pkgname/"
}

build() {
  cd "$srcdir/$pkgname"
  cargo build --release --locked
}

check() {
  cd "$srcdir/$pkgname"
  cargo test --locked
}

package() {
  cd "$srcdir/$pkgname"
  install -Dm755 target/release/cuc-desktop "$pkgdir/usr/bin/cuc-desktop"
  install -Dm755 target/release/cuc-privileged \
    "$pkgdir/usr/lib/cachyos-update-center/cuc-privileged"
  install -Dm644 assets/source-icons/pacman.png \
    "$pkgdir/usr/lib/cachyos-update-center/assets/source-icons/pacman.png"
  install -Dm644 assets/source-icons/flatpak.png \
    "$pkgdir/usr/lib/cachyos-update-center/assets/source-icons/flatpak.png"
  install -Dm644 io.github.alvin.Cuc.desktop \
    "$pkgdir/usr/share/applications/io.github.alvin.Cuc.desktop"
  install -Dm644 assets/io.github.alvin.Cuc.svg \
    "$pkgdir/usr/share/icons/hicolor/scalable/apps/io.github.alvin.Cuc.svg"
  install -Dm644 packaging/io.github.alvin.Cuc.policy \
    "$pkgdir/usr/share/polkit-1/actions/io.github.alvin.Cuc.policy"
}
