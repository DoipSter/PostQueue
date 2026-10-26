# PyInstaller build definition for PostQueue: one Analysis, and per platform:
#   Windows (Build_Windows_EXE.cmd): a portable single file (PQ_MODE=onefile) and a one-folder build (the MSI installs this)
#   macOS (build_macos.sh):          a one-folder build wrapped in PostQueue.app (PQ_MODE=app)
# The build scripts set PQ_NAME. Unused Qt parts are filtered out to keep the package small.
import os
import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent
NAME = os.environ['PQ_NAME']
MAC = sys.platform == 'darwin'
ICON = str(ROOT / 'assets' / ('app.icns' if MAC else 'app.ico'))
sys.path.insert(0, str(ROOT))
from version import PRODUCT, VERSION
# Qt pieces this Widgets app never loads: software OpenGL, extra display back-ends, Qt's image formats (photos are
# read with Pillow), SVG support, the TUIO touch plugin and Qt's own translations.
UNUSED = {'opengl32sw.dll', 'qdirect2d.dll', 'qminimal.dll', 'qoffscreen.dll', 'qtuiotouchplugin.dll', 'qsvg.dll', 'qsvgicon.dll',
          'Qt6Svg.dll', 'Qt6Network.dll', 'qgif.dll', 'qicns.dll', 'qjpeg.dll', 'qtga.dll', 'qtiff.dll', 'qwbmp.dll', 'qwebp.dll',
          # the same pieces on macOS
          'libqminimal.dylib', 'libqoffscreen.dylib', 'libqtuiotouchplugin.dylib', 'libqsvg.dylib', 'libqsvgicon.dylib', 'libqgif.dylib',
          'libqicns.dylib', 'libqjpeg.dylib', 'libqtga.dylib', 'libqtiff.dylib', 'libqwbmp.dylib', 'libqwebp.dylib', 'libqmacjp2.dylib'}
UNUSED_DIRS = ('/translations/', '/QtSvg.framework/', '/QtNetwork.framework/')

def wanted(entry):
    dest = entry[0].replace('\\', '/')
    return Path(dest).name not in UNUSED and not any(d in f'/{dest}' for d in UNUSED_DIRS)

a = Analysis([str(ROOT / 'PostQueue.pyw')], pathex=[str(ROOT)],
             datas=[(str(ROOT / 'assets'), 'assets'), (str(ROOT / 'licenses'), 'licenses'), (str(ROOT / 'THIRD_PARTY_NOTICES.txt'), '.')],
             hiddenimports=['__future__'],  # needed by PySide6 6.9's signature bootstrap; without it the app aborts at start-up
             excludes=['tkinter', 'PySide6.QtNetwork', 'PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.QtTest', 'PySide6.QtSvg',
                       'PySide6.QtMultimedia', 'PySide6.QtOpenGL', 'PySide6.QtPdf', 'PySide6.QtPrintSupport', 'PySide6.QtSql', 'PySide6.QtXml',
                       'PIL.ImageQt', 'PIL.ImageTk',
                       'numpy', 'requests', 'charset_normalizer', 'urllib3', 'certifi'],   # pulled in by hooks when installed; the app never uses them
             optimize=1)
a.binaries = [b for b in a.binaries if wanted(b)]
a.datas = [d for d in a.datas if wanted(d)]
pyz = PYZ(a.pure)
if os.environ.get('PQ_MODE') == 'onefile':   # portable single file (Windows)
    EXE(pyz, a.scripts, a.binaries, a.datas, [], name=NAME, console=False, icon=ICON, upx=False)
elif os.environ.get('PQ_MODE') == 'app':     # PostQueue.app (macOS)
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name=PRODUCT, console=False, icon=ICON, upx=False, target_arch=os.environ.get('PQ_TARGET_ARCH') or None)
    coll = COLLECT(exe, a.binaries, a.datas, name=PRODUCT, upx=False)
    BUNDLE(coll, name=f'{PRODUCT}.app', icon=ICON, bundle_identifier='com.postqueue.app',
           info_plist={'CFBundleName': PRODUCT, 'CFBundleDisplayName': PRODUCT, 'CFBundleShortVersionString': VERSION, 'CFBundleVersion': VERSION,
                       'NSHighResolutionCapable': True, 'LSMinimumSystemVersion': '12.0', 'NSHumanReadableCopyright': 'PostQueue',
                       'LSApplicationCategoryType': 'public.app-category.productivity'})
else:                                         # one-folder build (the MSI installs this)
    onedir = EXE(pyz, a.scripts, [], exclude_binaries=True, name=NAME, console=False, icon=ICON, upx=False)
    COLLECT(onedir, a.binaries, a.datas, name=NAME, upx=False)
