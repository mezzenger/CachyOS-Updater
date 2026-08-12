pkgname=cachyos-update-center
pkgver=0.5.1
pkgrel=1
pkgdesc="Multi-source update dashboard for Pacman, AUR, Flatpak, and Snap"
arch=('any')
url="https://github.com/alvin/CachyOS-Updater"
license=('custom:all-rights-reserved')
depends=('python' 'python-rich' 'python-cryptography' 'pacman-contrib' 'python-gobject' 'gtk4' 'libadwaita' 'polkit')
optdepends=(
  'flatpak: Flatpak update support'
  'snapd: Snap update support'
  'yay: AUR update support'
  'paru: AUR update support'
  'pikaur: AUR update support'
  'piper-tts: spoken update summaries'
)
makedepends=('git')
source=("$pkgname::git+$url.git#tag=v$pkgver")
sha256sums=('SKIP')

package() {
  cd "$pkgname"

  install -d "$pkgdir/usr/lib/cachyos-update-center"
  install -m755 cuc.py cuc_desktop.py cuc_privileged.py \
    "$pkgdir/usr/lib/cachyos-update-center/"
  install -m644 cuc_advisory.py cuc_controller.py cuc_diagnostics.py \
    cuc_providers.py "$pkgdir/usr/lib/cachyos-update-center/"
  install -Dm644 data/aur-security-advisories.json \
    "$pkgdir/usr/lib/cachyos-update-center/data/aur-security-advisories.json"

  install -d "$pkgdir/usr/bin"
  ln -s /usr/lib/cachyos-update-center/cuc.py "$pkgdir/usr/bin/cuc"
  ln -s /usr/lib/cachyos-update-center/cuc_desktop.py "$pkgdir/usr/bin/cuc-desktop"

  install -Dm644 io.github.alvin.Cuc.desktop \
    "$pkgdir/usr/share/applications/io.github.alvin.Cuc.desktop"
  install -Dm644 packaging/io.github.alvin.Cuc.policy \
    "$pkgdir/usr/share/polkit-1/actions/io.github.alvin.Cuc.policy"
  install -Dm755 cuc_privileged.py \
    "$pkgdir/usr/lib/cachyos-update-center/cuc-privileged"
}
