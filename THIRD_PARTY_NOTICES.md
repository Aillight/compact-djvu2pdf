# Third-party notices

This application incorporates or invokes third-party software. The notices below identify the principal runtime and build components; each component remains subject to its own license. Refer to the upstream project for the complete license text and the exact terms applicable to the version you use.

## Runtime components

- **DjVuLibre** — GNU GPL version 2 or later. The application invokes `ddjvu`, `djvused`, and `djvudump`; the macOS bundle build copies these executables and their required shared libraries.
  - Project: <https://djvu.sourceforge.net/>
  - Source code: <https://github.com/DjVuLibre/djvulibre>
  - Source archives: <https://sourceforge.net/projects/djvu/files/DjVuLibre/>
- **jbig2enc** — Apache License 2.0. The application invokes `jbig2`; the macOS bundle build copies the executable and its required shared libraries.
  - Source code: <https://github.com/agl/jbig2enc>
- **Qt and PySide6** — Qt is offered under LGPLv3, GPLv3, and commercial terms; PySide6 is the Qt for Python binding. The applicable license depends on the Qt modules and distribution used.
  - Qt licensing: <https://www.qt.io/licensing/>
  - Qt for Python: <https://wiki.qt.io/Qt_for_Python>
  - PySide6 on PyPI: <https://pypi.org/project/PySide6/>
- **pikepdf** — Mozilla Public License 2.0.
  - Source code and license: <https://github.com/pikepdf/pikepdf>
- **qpdf** — Artistic License 2.0. qpdf is used by pikepdf and may be included in its binary wheels.
  - Source code and license: <https://github.com/qpdf/qpdf>
- **Pillow** — MIT-CMU License.
  - Source code and license: <https://github.com/python-pillow/Pillow>

## Build and test components

- **PyInstaller** — GPL version 2 or later with a special exception permitting distribution of applications bundled with PyInstaller.
  - Source code and license: <https://github.com/pyinstaller/pyinstaller>
- **pytest** — MIT License.
  - Source code and license: <https://github.com/pytest-dev/pytest>

## Distribution notes

The dependency versions used by the project are pinned in `requirements.txt`. A built macOS application also contains transitive Python, Qt, and native-library dependencies. Before redistributing a built application or disk image, inspect the exact build's bundled components, include any notices required by their licenses, and provide corresponding source where those licenses require it. In particular, distributions containing DjVuLibre must comply with the applicable GPL source and license-notice obligations.
